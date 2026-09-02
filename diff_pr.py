#!/usr/bin/env python3
"""
Milestone 2 — semantic diff of a PR.

    python diff_pr.py <repo> --merge <mergecommit>       # base=^1 head=^2
    python diff_pr.py <repo> --base <ref> --head <ref>

Snapshots both refs with `git archive` (working tree untouched), runs the Milestone 1
extractor on each, and diffs the *facts* — keyed on the route (a name the code doesn't own),
so a handler rename with unchanged behaviour produces no noise. Emits a ranked semantic
review: what changed, how it flows, where to look, and what stayed unknown.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import shutil
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractor import analyze_repo             # noqa: E402
from gitutil import (sh, export, ensure_local, git_toplevel,   # noqa: E402
                     current_branch, default_branch)
import enforce as enforce_mod                  # noqa: E402
import deps as deps_mod                        # noqa: E402

CRIT, HIGH, MED, LOW = "🔴", "🟠", "🟡", "🟢"
EDGES = [("e3_db_tables", "db"), ("e4_external", "external"),
         ("e5_async", "async"), ("e6_pii", "pii")]

INFO_URI = "https://github.com/AnkushSinghGandhi/pryti-semantic-reviewer"
# GitHub code-scanning alert level per Pryti severity tier.
SARIF_LEVEL = {CRIT: "error", HIGH: "error", MED: "warning", LOW: "note"}
# Optional numeric on each result for the Security-tab Critical/High/Medium/Low sort.
SARIF_SECURITY = {CRIT: "9.0", HIGH: "7.0", MED: "4.0", LOW: "2.0"}


def _pryti_version():
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("pryti-semantic-reviewer")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    return "0"


def norm(s):
    """Fact identity: strip inter-procedural marker and source location."""
    return s.split(" @ ")[0].replace(" (via call)", "").strip()


def items(ep, edge):
    e = getattr(ep, edge)
    return {norm(x) for x in (e.items or [])}


def locs_for(ep, edge, fact_norms):
    """Full item strings (with `@ file:line`) whose fact identity is in fact_norms."""
    e = getattr(ep, edge)
    return [x for x in (e.items or []) if norm(x) in fact_norms]


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---- blast radius (hotspot tie-breaker) ------------------------------------------------------
# The shared state an endpoint touches — table names, external destinations, async targets. Two
# endpoints touching the same resource are in each other's blast radius. Used only to break ties
# *within* a severity tier, so the change more code depends on is read first — no new colors.

def _resources(ep):
    res = set()
    for t in items(ep, "e3_db_tables"):
        res.add("table:" + t.split(":")[0])
    for x in items(ep, "e4_external"):
        res.add("ext:" + x.split(" -> ", 1)[-1])
    for a in items(ep, "e5_async"):
        res.add("async:" + a)
    return res


def fanin_index(eps):
    """resource -> set of routes that touch it, across one snapshot."""
    idx = {}
    for ep in eps:
        for r in _resources(ep):
            idx.setdefault(r, set()).add(ep.route)
    return idx


def blast_radius(ep, idx):
    """How many *other* routes share a resource with `ep` — its fan-in / blast radius."""
    reached = set()
    for r in _resources(ep):
        reached |= idx.get(r, set())
    reached.discard(ep.route)
    return len(reached)


def shared_external_destinations(eps, min_routes=2):
    """External destinations reached by several endpoints — the egress fan-in, most-shared first.
    Collapses the 'N endpoints funnel through the same destination' case (usually one shared helper)
    into a single group, so a reviewer verifies a destination once instead of once per route. Each
    group is `(dest, [routes])`."""
    idx = fanin_index(eps)
    groups = [(res[len("ext:"):], sorted(routes))
              for res, routes in idx.items()
              if res.startswith("ext:") and len(routes) >= min_routes]
    groups.sort(key=lambda g: (-len(g[1]), g[0]))
    return groups


def _egress_status(dest):
    """How pinned-down a shared destination is: a {placeholder} host is 'partial', a bare '?' is
    'unresolved', anything else is 'ok' (a full literal). Same honesty as the E4 edge status."""
    if "{" in dest:
        return "partial"
    if dest.strip() == "?":
        return "unresolved"
    return "ok"


def egress_payload(groups, only_routes=None):
    """Structured shared-egress groups for the JSON/web outputs: one entry per destination reached
    by ≥2 endpoints, `{dest, routes, count, status}`. `only_routes` keeps just the groups the change
    participates in (PR view); None keeps all (full-repo view)."""
    return [{"dest": dest, "routes": routes, "count": len(routes), "status": _egress_status(dest)}
            for dest, routes in groups
            if only_routes is None or (set(routes) & only_routes)]


def render_egress_section(groups, only_routes=None, cap=8):
    """Markdown for the shared-egress groups. `only_routes` keeps just groups touching those routes
    (so a PR report shows only egress the change participates in); None shows all (full-repo view)."""
    rows = egress_payload(groups, only_routes)
    if not rows:
        return ""
    flag = {"partial": " · ⚠ partial", "unresolved": " · ? unresolved", "ok": ""}
    L = ["### 🌐 Shared egress (fan-in)\n",
         "_Endpoints funnelling through the same outbound call — verify the destination once, "
         "not once per route._\n"]
    for g in rows[:cap]:
        routes = g["routes"]
        L.append(f"- **{g['count']} endpoints → `{g['dest']}`**{flag[g['status']]}")
        L.append("    " + ", ".join(f"`{r}`" for r in routes[:12]) + (" …" if len(routes) > 12 else ""))
    return "\n".join(L)


def parse_changed_lines(diff_text):
    """Right-side (head) line numbers present in a unified diff, keyed by file path.

    These are the *only* lines a GitHub review comment can anchor to — a finding whose location
    isn't in this set can't be pinned (GitHub 422s), so the caller rolls it into the summary
    instead. Pure string parsing (unit-tested); removed ('-') and no-newline ('\\') lines don't
    advance the right-side counter, and a deleted file (`+++ /dev/null`) contributes nothing."""
    out, path, right = {}, None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p.startswith("b/"):
                p = p[2:]
            path = None if p == "/dev/null" else p
        elif line.startswith("@@"):
            try:                                         # @@ -a,b +c,d @@
                seg = line.split("+", 1)[1].split(" ", 1)[0]
                right = int(seg.split(",", 1)[0])
            except (IndexError, ValueError):
                right = 0
        elif path is None:
            continue
        elif line.startswith("+") and not line.startswith("+++"):
            out.setdefault(path, set()).add(right); right += 1
        elif line.startswith(" "):
            out.setdefault(path, set()).add(right); right += 1
    return {p: sorted(v) for p, v in out.items()}


def changed_lines(repo, base, head):
    """{file: [head-side line numbers touched]} for `base..head` (see parse_changed_lines)."""
    return parse_changed_lines(sh("git", "-C", repo, "diff", "--no-color", base, head))


def dependency_findings(repo, base, head):
    """Capability-delta findings for any dependency manifest changed in `base..head` (feature #5).

    Manifest bump detection is always on (cheap, offline); the capability scan fetches each version
    from PyPI unless `PRYTI_SCAN_DEPS=0`, degrading to ⚠ unresolved when offline — never assuming
    safe. Returns [] when no manifest changed."""
    names = [p for p in sh("git", "-C", repo, "diff", "--name-only", base, head).splitlines()
             if deps_mod.is_manifest(p)]
    if not names:
        return []
    provider = None if os.environ.get("PRYTI_SCAN_DEPS") == "0" else deps_mod.pypi_source_provider
    findings = []
    for path in names:
        old_map = deps_mod.parse_manifest(path, sh("git", "-C", repo, "show", f"{base}:{path}"))
        new_map = deps_mod.parse_manifest(path, sh("git", "-C", repo, "show", f"{head}:{path}"))
        for f in deps_mod.analyze_dependency_changes(old_map, new_map, source_provider=provider):
            findings.append(dict(f, manifest=path))
    return findings


def auth_str(ep):
    e = ep.e2_auth
    if e.status == "?":
        return "unspecified"
    if any("AllowAny" in x for x in e.items):
        return "open (AllowAny)"
    return " ".join(e.items) or "unspecified"


def is_open(ep):
    return ep.e2_auth.status == "?" or any("AllowAny" in x for x in ep.e2_auth.items)


def index(eps):
    d = {}
    for e in eps:
        d.setdefault(e.route, e)   # first handler on a route wins
    return d


def flow(ep):
    parts = [ep.route, ep.handler]
    tabs = sorted(items(ep, "e3_db_tables"))
    if tabs:
        parts.append("{" + ", ".join(tabs) + "}")
    ext = sorted(items(ep, "e4_external"))
    if ext:
        parts.append("ext: " + ", ".join(ext))
    return " → ".join(parts)


def unknowns(ep):
    out = []
    for edge, label in EDGES + [("e2_auth", "auth")]:
        e = getattr(ep, edge)
        if e.status in {"⚠", "?"} and e.items:
            vals = ", ".join(sorted({norm(x) for x in e.items}))   # identities; locs are in investigate
            out.append(f"{label} {e.status}: {vals}" + (f" — {e.note}" if e.note else ""))
    return out


def diff(base_eps, head_eps):
    base, head = index(base_eps), index(head_eps)
    changes = []

    # added / removed endpoints
    for route, ep in head.items():
        if route not in base:
            resolved = ep.e1_route_handler.status == "✓"
            pii = bool(ep.e6_pii.items) and ep.e6_pii.status in {"✓", "⚠", "?"}
            writes = any(":write" in t for t in items(ep, "e3_db_tables"))
            ext = bool(items(ep, "e4_external"))
            money = any(k in route for k in ("payment", "order", "recharge", "invoice", "coupon"))
            if not resolved:
                sev, why = MED, "new endpoint — handler not resolved in repo (library/external view)"
            elif pii and is_open(ep):
                sev, why = CRIT, "new endpoint exposes a PII path with open/unspecified auth"
            elif is_open(ep) and writes:
                sev, why = CRIT, "new *unauthenticated* endpoint performs DB writes"
            elif pii:
                sev, why = CRIT, "new endpoint reads PII and has an egress path"
            elif money:
                sev, why = HIGH, "new endpoint on a money/payment path"
            elif ext or writes:
                sev, why = MED, "new endpoint with a DB write / external call"
            else:
                sev, why = LOW, "new read-only endpoint"
            risky = (locs_for(ep, "e4_external", items(ep, "e4_external"))
                     + locs_for(ep, "e6_pii", items(ep, "e6_pii"))
                     + [x for x in (ep.e3_db_tables.items or []) if ":write" in norm(x)])
            changes.append(dict(sev=sev, kind="NEW ENDPOINT", route=route, ep=ep, why=why,
                                detail=f"auth: {auth_str(ep)}", locs=risky))
    for route, ep in base.items():
        if route not in head:
            changes.append(dict(sev=MED, kind="REMOVED ENDPOINT", route=route, ep=ep,
                                why="endpoint no longer routed", detail=f"was: {ep.handler}"))

    # changed endpoints
    for route in set(base) & set(head):
        b, h = base[route], head[route]
        sub, locs = [], []
        # auth change
        if auth_str(b) != auth_str(h):
            weakened = (not is_open(b)) and is_open(h)
            sub.append((CRIT if weakened else LOW,
                        f"auth {'WEAKENED' if weakened else 'changed'}: {auth_str(b)} → {auth_str(h)}"))
        # per-edge new facts
        for edge, label in EDGES:
            new = items(h, edge) - items(b, edge)
            if not new:
                continue
            locs += locs_for(h, edge, new)
            if label == "pii":
                sub.append((CRIT, f"new PII signal: {', '.join(sorted(new))}"))
            elif label == "external":
                money = any(k in route for k in ("payment", "order", "recharge"))
                sub.append((HIGH if money else MED, f"new external call: {', '.join(sorted(new))}"))
            elif label == "db":
                writes = [t for t in new if ":write" in t]
                sub.append((MED if not (writes and is_open(h)) else CRIT,
                            f"new DB {'write' if writes else 'access'}: {', '.join(sorted(new))}"))
            else:
                sub.append((MED, f"new async dispatch: {', '.join(sorted(new))}"))
        if sub:
            sev = min((s for s, _ in sub), key=lambda x: [CRIT, HIGH, MED, LOW].index(x))
            changes.append(dict(sev=sev, kind="CHANGED", route=route, ep=h, base_ep=b,
                                why="; ".join(t for _, t in sub), detail="", locs=locs,
                                sub=[t for _, t in sub]))
        elif b.handler != h.handler:
            changes.append(dict(sev=LOW, kind="REFACTOR", route=route, ep=h, base_ep=b,
                                why=f"handler renamed {b.handler} → {h.handler}, semantics unchanged",
                                detail="(refactor-stable: no semantic diff)"))

    head_fan, base_fan = fanin_index(head_eps), fanin_index(base_eps)
    for c in changes:
        idx = base_fan if c["kind"] == "REMOVED ENDPOINT" else head_fan
        c["blast"] = blast_radius(c["ep"], idx)
    order = {CRIT: 0, HIGH: 1, MED: 2, LOW: 3}
    # severity first (colors unchanged); within a tier, the change more code depends on floats up
    changes.sort(key=lambda c: (order[c["sev"]], -c["blast"]))
    return changes


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _english_join(parts):
    parts = list(parts)
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _names(names, cap=3):
    xs = sorted(names)
    shown = ", ".join(f"`{x}`" for x in xs[:cap])
    return shown + (f" +{len(xs) - cap} more" if len(xs) > cap else "")


def intent_summary(changes, findings):
    """One deterministic sentence describing this PR's behavioral change, computed from the same
    verified facts — every number traces to a `file:line`, so (unlike an LLM summary) it can't
    invent a change that isn't there or miss one that is."""
    new_eps = sum(1 for c in changes if c["kind"] == "NEW ENDPOINT")
    removed = sum(1 for c in changes if c["kind"] == "REMOVED ENDPOINT")
    writes, externals, pii = set(), set(), set()
    for c in changes:
        if c["kind"] not in ("NEW ENDPOINT", "CHANGED"):
            continue
        ep, base = c["ep"], c.get("base_ep")
        def new(edge):                                    # facts on head not already on base
            cur = items(ep, edge)
            return cur if base is None else cur - items(base, edge)
        writes |= {t.split(":")[0] for t in new("e3_db_tables") if ":write" in t}
        externals |= new("e4_external")
        pii |= new("e6_pii")
    findings = findings or []
    violations = sum(1 for f in findings if f["kind"] == "NEW VIOLATION")
    weakenings = sum(1 for f in findings if f["kind"] == "WEAKENING")

    adds = []
    if new_eps:
        adds.append(_plural(new_eps, "endpoint"))
    if writes:
        adds.append(_plural(len(writes), "new DB write") + " to " + _names(writes))
    if externals:
        adds.append(_plural(len(externals), "new external call") + " to " + _names(externals))
    if pii:
        adds.append(_plural(len(pii), "new PII signal"))
    segments = []
    if adds:
        segments.append("adds " + _english_join(adds))
    if removed:
        segments.append("removes " + _plural(removed, "endpoint"))
    if violations:
        segments.append("introduces " + _plural(violations, "invariant violation"))
    if weakenings:
        segments.append("weakens " + _plural(weakenings, "invariant"))
    if not segments:
        return "This PR makes no changes to routing, auth, data, or external calls (internal logic only)."
    return "This PR " + _english_join(segments) + "."


# ---- Intent vs. behavior (feature #4) -------------------------------------------------------
# The PR title/description is a *declared* intent — a contract the author wrote. Pryti already
# knows what the code does. Cross-check the two and flag disagreements. Competitors fake this by
# asking an LLM to eyeball the diff; every contradiction below is derived from a verified fact
# that traces to a `file:line`, so it can't be hallucinated. (#3 is the neutral summary; #4 is
# the contradiction.)
_INTENT_PHRASES = {
    "refactor": ("refactor", "no behavior change", "no behaviour change",
                 "no functional change", "no logic change"),
    "docs-only": ("docs only", "docs-only", "documentation only", "doc only"),
    "bugfix": ("bugfix", "bug fix", "hotfix"),
}
# conventional-commit prefixes (e.g. "refactor(auth): …") mapped to the same claim tags
_INTENT_PREFIXES = {"refactor": ("refactor", "style"), "docs-only": ("docs",),
                    "bugfix": ("fix", "hotfix", "bugfix")}


def parse_intent(text):
    """Declared-intent claims parsed from a PR title/description → a set of tags
    (`refactor` / `docs-only` / `bugfix`). Conservative on purpose: only unambiguous phrases or
    conventional-commit prefixes count, so a flagged contradiction is real, not a keyword accident."""
    t = (text or "").lower()
    head = t.split("\n", 1)[0].strip()          # a conventional-commit prefix lives on line 1
    if head.startswith("pr #") and ":" in head:  # strip our "PR #123:" display decoration first
        head = head.split(":", 1)[1].strip()
    claims = set()
    for tag, phrases in _INTENT_PHRASES.items():
        if any(p in t for p in phrases):
            claims.add(tag)
    for tag, prefixes in _INTENT_PREFIXES.items():
        if any(head.startswith(p + sep) for p in prefixes for sep in (":", "(", "!")):
            claims.add(tag)
    return claims


def _behavior_evidence(changes, findings, include_refactor):
    """(label, route, loc) for every fact that constitutes a behavioral change — the evidence a
    'nothing really changed' claim contradicts. A pure handler rename (kind REFACTOR) is behavior-
    preserving, so it counts only for a `docs-only` claim (include_refactor=True), not `refactor`."""
    kind_label = {"NEW ENDPOINT": "adds an endpoint", "REMOVED ENDPOINT": "removes an endpoint",
                  "REFACTOR": "renames a handler"}
    ev = []
    for c in changes:
        if c["kind"] == "REFACTOR" and not include_refactor:
            continue
        label = kind_label.get(c["kind"]) or c["why"]      # CHANGED → its spelled-out sub-changes
        ev.append((label, c["route"], f"{c['ep'].file}:{c['ep'].line}"))
    for f in (findings or []):
        if f["kind"] == "NEW VIOLATION":
            ev.append((f"introduces an invariant violation ({f['stmt']})", "", ""))
        elif f["kind"] == "WEAKENING":
            ev.append((f"weakens an invariant ({f['stmt']})", "", ""))
    return ev


def _where(route, loc):
    if route and loc:
        return f" — `{route}` @ `{loc}`"
    return f" — `{route or loc}`" if (route or loc) else ""


def intent_contradictions(title, changes, findings=None, body=""):
    """Flag where the PR's declared intent (title/description) disagrees with Pryti's own facts.
    Returns finding dicts (`sev`/`claim`/`why`/`route`/`loc`) — a finding type competitors fake
    with an LLM, produced here from structured facts. Empty when no claim is declared, or the
    facts are consistent with it."""
    claims = parse_intent((title or "") + "\n" + (body or ""))
    if not claims:
        return []
    # docs-only is the strongest claim (no code at all): any change, even a rename, contradicts it.
    if "docs-only" in claims:
        ev = _behavior_evidence(changes, findings, include_refactor=True)
        return [dict(sev=CRIT, claim="docs-only", route=r, loc=l,
                     why=f"claims docs-only, but {lab}{_where(r, l)}") for lab, r, l in ev]
    if "refactor" in claims:
        ev = _behavior_evidence(changes, findings, include_refactor=False)
        return [dict(sev=CRIT, claim="refactor / no behavior change", route=r, loc=l,
                     why=f"claims refactor / no behavior change, but {lab}{_where(r, l)}")
                for lab, r, l in ev]
    if "bugfix" in claims:                      # softer: a bugfix that grows the API surface
        out = []
        for c in changes:
            if c["kind"] == "NEW ENDPOINT":
                loc = f"{c['ep'].file}:{c['ep'].line}"
                out.append(dict(sev=HIGH, claim="bugfix", route=c["route"], loc=loc,
                                why=f"claims bugfix, but adds an endpoint{_where(c['route'], loc)} "
                                    "(scope creep?)"))
        return out
    return []


def render_intent_section(contradictions):
    """Markdown block — declared intent vs. observed behavior. Sits beside the invariant panel."""
    if not contradictions:
        return ""
    claim = contradictions[0]["claim"]
    L = ["## 🎭 Intent vs. behavior",
         f"**The PR describes itself as _{claim}_, but the facts disagree** — "
         f"{_plural(len(contradictions), 'contradiction')}:\n"]
    for c in contradictions:
        L.append(f"- {c['sev']} {c['why']}")
    L.append("\n_Declared intent = the PR title/description; each line is derived from Pryti's own "
             "facts (traceable to `file:line`), not an LLM reading the diff._\n")
    return "\n".join(L)


def _mm_id(s):
    return ("N" + re.sub(r"[^A-Za-z0-9]", "", str(s))[:24]) or "N"


def _mm_label(s):
    """Sanitize text for a Mermaid message label (no `;` `#` newlines, no `->` arrow confusion)."""
    return re.sub(r"[;#`\n<>|]", " ", str(s).replace("->", "→")).strip() or "?"


def mermaid_sequence(changes, cap=12):
    """A Mermaid `sequenceDiagram` of the PR's before→after flow — built from our own facts, not an
    LLM drawing. Wrapped in a ```mermaid fence so GitHub renders it inline in the PR comment.
    Only changed endpoints (new / changed / removed) participate; capped so large PRs stay legible."""
    drawn = [c for c in changes if c["kind"] in ("NEW ENDPOINT", "CHANGED", "REMOVED ENDPOINT")]
    if not drawn:
        return ""
    tag = {"NEW ENDPOINT": "🆕", "REMOVED ENDPOINT": "➖"}
    L = ["```mermaid", "sequenceDiagram", "    participant Client"]
    seen = set()
    for c in drawn[:cap]:
        ep = c["ep"]
        hid = _mm_id(ep.handler or c["route"])
        if hid not in seen:
            L.append(f"    participant {hid} as {_mm_label(ep.handler or c['route'])}")
            seen.add(hid)
        meth = ",".join(ep.methods) or "?"
        L.append(f"    Client->>{hid}: {tag.get(c['kind'], c['sev'])} {_mm_label(meth)} "
                 f"{_mm_label(c['route'])}")
        for t in sorted(items(ep, "e3_db_tables")):
            name, _, kind = t.partition(":")
            L.append(f"    {hid}->>Database: {_mm_label((kind or 'access') + ' ' + name)}")
        for x in sorted(items(ep, "e4_external")):
            L.append(f"    {hid}->>External: {_mm_label(x.split(' -> ', 1)[-1])}")
    L.append("```")
    return "\n".join(L)


def render(changes, meta):
    L = []
    L.append(f"# Semantic Review — {meta['title']}")
    L.append(f"\n`{meta['base']}` → `{meta['head']}`  |  line diff: {meta['shortstat']}\n")
    if meta.get("summary"):
        L.append(f"> **{meta['summary']}**\n")
    if meta.get("intent_section"):
        L.append(meta["intent_section"])
    if meta.get("deps_section"):
        L.append(meta["deps_section"])
    if meta.get("inv_section"):
        L.append(meta["inv_section"])
    if meta.get("egress_section"):
        L.append(meta["egress_section"])
    buckets = {CRIT: [], HIGH: [], MED: [], LOW: []}
    for c in changes:
        buckets[c["sev"]].append(c)
    tally = "  ".join(f"{s} {len(buckets[s])}" for s in (CRIT, HIGH, MED, LOW) if buckets[s])
    L.append(f"**{len(changes)} semantic changes** — {tally}\n")
    L.append("| | change | route |")
    L.append("|--|--------|-------|")
    for c in changes:
        L.append(f"| {c['sev']} | {c['kind']} | `{c['route']}` |")
    mer = mermaid_sequence(changes)
    if mer:
        L.append("\n### 🔀 Flow (before → after)\n")
        L.append(mer)
    L.append("\n---\n")
    for c in changes:
        ep = c["ep"]
        L.append(f"### {c['sev']} {c['kind']} — `{c['route']}`")
        L.append(f"- **why:** {c['why']}")
        if c.get("detail"):
            L.append(f"- {c['detail']}")
        L.append(f"- **flow:** {flow(ep)}")
        if c.get("blast"):
            L.append(f"- **blast radius:** shares data/services with {c['blast']} other endpoint(s)")
        L.append(f"- **investigate:**")
        L.append(f"    - `{ep.file}:{ep.line}` — handler `{ep.handler}` ({','.join(ep.methods) or '?'})")
        for loc in _dedupe(c.get("locs") or []):
            fact, _, where = loc.partition(" @ ")
            L.append(f"    - `{where}` — {fact}")
        uk = unknowns(ep)
        if uk:
            L.append(f"- **unknown:** " + " · ".join(uk))
        L.append("")
    L.append("---")
    L.append("_Blindspot: this diff sees routing/auth/db/external/async at the endpoint lens; "
             "it does not see logic inside a function, ordering, concurrency, or off-by-one._")
    return "\n".join(L)


def build_graph(ep):
    """Node-link graph of one endpoint's behavior, from its facts (for the graph view)."""
    nodes, edges, ids = [], [], set()

    def add(nid, label, ntype, **kw):
        if nid not in ids:
            ids.add(nid)
            nodes.append({"id": nid, "label": label, "type": ntype, **kw})

    rid, hid = f"route:{ep.route}", f"handler:{ep.handler}"
    add(rid, ep.route, "route")
    add(hid, ep.handler, "handler", loc=f"{ep.file}:{ep.line}", auth=auth_str(ep),
        pii=bool(ep.e6_pii.items) and ep.e6_pii.status in {"✓", "⚠", "?"})
    edges.append({"from": rid, "to": hid, "kind": ",".join(ep.methods) or "route"})

    def leaf(items, ntype, kind_of):
        for it in (items or []):
            fact, _, loc = it.partition(" @ ")
            clean = fact.replace(" (via call)", "")
            via = "via call" in fact
            if ntype == "table":
                label, _, k = clean.partition(":")
                kind = k
            else:
                label = clean.split(" -> ", 1)[-1]
                kind = kind_of
            nid = f"{ntype}:{label}"
            add(nid, label, ntype, loc=loc, kind=kind)
            edges.append({"from": hid, "to": nid, "kind": kind + (" (via)" if via else "")})

    leaf(ep.e3_db_tables.items, "table", "")
    leaf(ep.e4_external.items, "external", "calls")
    leaf(ep.e5_async.items, "async", "dispatches")
    return {"nodes": nodes, "edges": edges}


def _state_all(g, state):
    for n in g["nodes"]:
        n["state"] = state
    for e in g["edges"]:
        e["state"] = state
    return g


def build_diff_graph(base_ep, head_ep):
    """Before/after graph: each node/edge tagged added / removed / same."""
    base = build_graph(base_ep) if base_ep else {"nodes": [], "edges": []}
    head = build_graph(head_ep) if head_ep else {"nodes": [], "edges": []}
    bn = {n["id"] for n in base["nodes"]}
    hn = {n["id"] for n in head["nodes"]}

    def ek(e):
        return (e["from"], e["to"], e["kind"].replace(" (via)", "").strip())
    be = {ek(e) for e in base["edges"]}
    he = {ek(e) for e in head["edges"]}

    nodes = [dict(n, state="added" if n["id"] not in bn else "same") for n in head["nodes"]]
    nodes += [dict(n, state="removed") for n in base["nodes"] if n["id"] not in hn]
    edges = [dict(e, state="added" if ek(e) not in be else "same") for e in head["edges"]]
    edges += [dict(e, state="removed") for e in base["edges"] if ek(e) not in he]
    return {"nodes": nodes, "edges": edges}


def build_review(changes, findings, meta, changed=None):
    """Structured review for the JSON / HTML / web outputs."""
    def cd(c):
        ep = c["ep"]
        investigate = [{"where": f"{ep.file}:{ep.line}",
                        "fact": f"handler {ep.handler} ({','.join(ep.methods) or '?'})"}]
        for loc in _dedupe(c.get("locs") or []):
            fact, _, where = loc.partition(" @ ")
            investigate.append({"where": where, "fact": fact})
        if c["kind"] == "REMOVED ENDPOINT":
            graph = _state_all(build_graph(ep), "removed")
        elif c.get("base_ep") is not None:
            graph = build_diff_graph(c["base_ep"], ep)     # CHANGED / REFACTOR → before vs after
        else:
            graph = _state_all(build_graph(ep), "added")   # NEW ENDPOINT → all new
        return {"sev": c["sev"], "kind": c["kind"], "route": c["route"], "why": c["why"],
                "detail": c.get("detail", ""), "auth": auth_str(ep), "flow": flow(ep),
                "blast": c.get("blast", 0),
                "investigate": investigate, "unknowns": unknowns(ep), "graph": graph}
    return {"title": meta["title"], "base": meta["base"], "head": meta["head"],
            "shortstat": meta["shortstat"], "summary": meta.get("summary", ""),
            "invariants": findings or [], "contradictions": meta.get("contradictions", []),
            "dependencies": meta.get("dependencies", []), "egress": meta.get("egress", []),
            "changed_lines": changed or {}, "changes": [cd(c) for c in changes]}


def _split_where(where):
    """'path/file.py:123' -> ('path/file.py', 123). ('path', None) if no numeric line;
    (None, None) if empty. Mirrors ci/post_review._split_loc but tolerates a bare path."""
    if not where:
        return None, None
    path, sep, line = where.rpartition(":")
    if sep and line.isdigit():
        return path, int(line)
    return where, None


def _best_location(change, changed):
    """(path, line, in_diff) for a change — the single line a SARIF result points at.

    Prefers a *fact* location that lands on a changed line (a write/external/PII line the PR
    actually touched), then the first fact location, then the handler definition. Every change
    carries the handler loc, so this always resolves. `in_diff` records whether that line is in
    the PR diff — SARIF only renders inline annotations for changed lines (honest guard); the
    rest still surface as Security-tab alerts."""
    locs = []
    for i, it in enumerate(change.get("investigate", [])):
        p, ln = _split_where(it.get("where", ""))
        if p and ln is not None:
            locs.append((i, p, ln))            # index 0 is the handler def; >=1 is a fact
    if not locs:
        return None, None, False
    facts = [l for l in locs if l[0] >= 1]
    for i, p, ln in facts:                      # a fact line inside the diff wins
        if ln in changed.get(p, ()):
            return p, ln, True
    _, p, ln = (facts or locs)[0]
    return p, ln, ln in changed.get(p, ())


def build_sarif(review):
    """Serialize a structured review into SARIF 2.1.0 for GitHub code scanning.

    Pure re-shaping of facts we already computed: each semantic change and each PR-introduced
    invariant finding becomes a `result` anchored at its `file:line`. `result.level` carries the
    severity (error/warning/note); a `security-severity` property drives the Security-tab sort.
    No new analysis — this is the Security-tab / Files-changed surface for the same findings."""
    changed = {p: set(v) for p, v in (review.get("changed_lines") or {}).items()}
    rules, seen, results = [], set(), []

    def rule(rid, desc):
        if rid not in seen:
            seen.add(rid)
            rules.append({"id": rid, "name": rid.split("/")[-1],
                          "shortDescription": {"text": desc},
                          "properties": {"tags": ["pryti"]}})

    def emit(rid, sev, text, path, line, in_diff, fp):
        loc = []
        if path:
            phys = {"artifactLocation": {"uri": path}}
            if line is not None:
                phys["region"] = {"startLine": line}
            loc = [{"physicalLocation": phys}]
        results.append({
            "ruleId": rid, "level": SARIF_LEVEL.get(sev, "warning"),
            "message": {"text": text}, "locations": loc,
            "partialFingerprints": {"pryti/v1": fp},
            "properties": {"pryti-severity": sev, "in-diff": in_diff,
                           "security-severity": SARIF_SECURITY.get(sev, "4.0")},
        })

    for c in review.get("changes", []):
        rid = "pryti/" + c["kind"].lower().replace(" ", "-")
        rule(rid, f"Pryti semantic change: {c['kind']}")
        path, line, in_diff = _best_location(c, changed)
        text = f"{c['kind']} {c['route']} — {c.get('why', '')}".strip()
        emit(rid, c.get("sev", MED), text, path, line, in_diff, f"{c['kind']}::{c['route']}")

    for f in review.get("invariants", []):
        if f["kind"] not in ("NEW VIOLATION", "WEAKENING", "STILL-VIOLATING", "STILL-OUT"):
            continue                            # only real alerts; HELD/RESOLVED aren't findings
        rid = "pryti/invariant/" + f["kind"].lower().replace(" ", "-")
        rule(rid, f"Pryti invariant alert: {f['kind']}")
        sev, stmt = f.get("sev", MED), f.get("stmt", "")
        located = []
        for loc in f.get("locs", []):
            fact, _, where = loc.partition(" @ ")
            p, ln = _split_where(where)
            if p and ln is not None:
                located.append((fact, p, ln))
        if located:
            for fact, p, ln in located:
                emit(rid, sev, f"{stmt} — {fact}", p, ln, ln in changed.get(p, ()),
                     f"{f['kind']}::{stmt}::{p}:{ln}")
        else:                                   # e.g. WEAKENING has no single line — attribute to root
            emit(rid, sev, f"{stmt} — {f.get('detail', '')}", None, None, False,
                 f"{f['kind']}::{stmt}")

    for c in review.get("contradictions", []):  # intent-vs-behavior (feature #4)
        rid = "pryti/intent-contradiction"
        rule(rid, "Pryti intent-vs-behavior contradiction")
        p, ln = _split_where(c.get("loc", ""))
        in_diff = bool(p and ln is not None and ln in changed.get(p, ()))
        emit(rid, c.get("sev", HIGH), c.get("why", "intent contradiction"),
             p, ln, in_diff, f"intent::{c.get('claim', '')}::{c.get('loc', '')}")

    for d in review.get("dependencies", []):    # dependency capability-delta (feature #5)
        if not (d.get("gained") or d.get("unresolved")):
            continue                            # a bump with no new capabilities isn't an alert
        rid = "pryti/dependency-capability"
        rule(rid, "Pryti dependency capability change")
        path = d.get("manifest")
        text = f"{d['name']} {d.get('old') or '(new)'}→{d['new']} — {d.get('why', '')}"
        emit(rid, d.get("sev", MED), text, path, None, False,
             f"dep::{d['name']}::{d.get('new', '')}")

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "Pryti", "informationUri": INFO_URI,
                                "version": _pryti_version(), "rules": rules}},
            "results": results,
        }],
    }


def _gh_owner_repo(url):
    """owner/repo from https://github.com/owner/repo(.git) or git@github.com:owner/repo.git"""
    u = url.rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    if u.startswith("git@"):
        u = u.split(":", 1)[1]
    elif "github.com/" in u:
        u = u.split("github.com/", 1)[1]
    parts = u.split("/")
    return parts[-2], parts[-1]


def _github_json(api):
    req = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "pryti"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return json.load(urllib.request.urlopen(req, timeout=30))


def _is_github_url(repo):
    return "github.com/" in repo or repo.startswith("git@github.com:")


def resolve_github_pr(url, local, n):
    """Look up a real GitHub PR: return (base_sha, head_sha, title). Fetches the PR head ref."""
    if not _is_github_url(url):
        raise RuntimeError("--pr requires a GitHub repo URL so Pryti can look up the PR metadata")
    owner, repo = _gh_owner_repo(url)
    api = f"https://api.github.com/repos/{owner}/{repo}/pulls/{n}"
    data = _github_json(api)
    base_sha, head_sha = data["base"]["sha"], data["head"]["sha"]

    def fetch_ref(label, local_ref, *refs):
        last = ""
        for ref in refs:
            try:
                sh("git", "-C", local, "fetch", "--quiet", "--no-tags",
                   "origin", f"{ref}:{local_ref}", check=True)
                return
            except RuntimeError as e:
                last = str(e)
        raise RuntimeError(f"could not fetch GitHub PR {n} {label}: {last}")

    # Fetch only the commits needed for this PR into local refs so git archive can find them.
    fetch_ref("base", "refs/pryti/base", base_sha, data["base"]["ref"])
    fetch_ref("head", "refs/pryti/head", f"pull/{n}/head", head_sha)
    return base_sha, head_sha, f"PR #{n}: {data.get('title', '')}", data.get("body") or ""


def resolve_refs(repo, merge=None, base=None, head=None):
    if merge:
        b = sh("git", "-C", repo, "rev-parse", "--short", f"{merge}^1", check=True)
        h = sh("git", "-C", repo, "rev-parse", "--short", f"{merge}^2", check=True)
        title = sh("git", "-C", repo, "log", "-1", "--pretty=%s", merge, check=True)
    else:
        b = sh("git", "-C", repo, "rev-parse", "--short", base, check=True)
        h = sh("git", "-C", repo, "rev-parse", "--short", head, check=True)
        title = f"{b}..{h}"
    return b, h, title


def list_merges(repo, n=30):
    """Recent merge PRs for the picker: [{sha, title}]. Accepts a local path or URL.

    GitHub URLs use the API so squash/rebase/linear histories still show PRs. Local paths fall
    back to merge commits because local git history has no portable PR-number metadata.
    """
    if _is_github_url(repo):
        owner, name = _gh_owner_repo(repo)
        out = []
        per_page = min(max(n * 3, 30), 100)
        try:
            for page in range(1, 6):
                api = (f"https://api.github.com/repos/{owner}/{name}/pulls"
                       f"?state=closed&sort=updated&direction=desc&per_page={per_page}&page={page}")
                pulls = _github_json(api)
                if not pulls:
                    break
                for pr in pulls:
                    if not pr.get("merged_at"):
                        continue
                    sha = (pr.get("merge_commit_sha") or "")[:12]
                    out.append({"kind": "pr", "number": str(pr["number"]), "sha": sha,
                                "title": f"PR #{pr['number']}: {pr.get('title') or '(untitled)'}"})
                    if len(out) >= n:
                        return out
        except Exception as e:
            raise RuntimeError(f"GitHub PR lookup failed for {owner}/{name}: {e}")
        return out

    repo = ensure_local(repo)
    out = []
    for line in sh("git", "-C", repo, "log", "--merges", "--pretty=%h\t%s", f"-{n}").splitlines():
        sha, _, title = line.partition("\t")
        if "pull request" in title or "Merge" in title:
            out.append({"kind": "merge", "sha": sha, "title": title})
    return out


def list_branches(repo):
    """Branch names for the compare picker, newest-first. Local branches first, then any
    remote-tracking branch whose short name isn't already a local branch."""
    repo = ensure_local(repo)
    local = [b.strip() for b in sh("git", "-C", repo, "for-each-ref", "--sort=-committerdate",
                                   "--format=%(refname:short)", "refs/heads").splitlines() if b.strip()]
    remotes = []
    for b in sh("git", "-C", repo, "for-each-ref", "--sort=-committerdate",
                "--format=%(refname:short)", "refs/remotes").splitlines():
        b = b.strip()
        if not b or b.endswith("/HEAD"):
            continue
        short = b.split("/", 1)[1] if "/" in b else b
        if short not in local:
            remotes.append(b)
    return local + remotes


def list_commits(repo, n=50):
    """Recent commits for the compare picker: [{sha, title}], newest-first."""
    repo = ensure_local(repo)
    out = []
    for line in sh("git", "-C", repo, "log", "--pretty=%h\t%s", f"-{n}").splitlines():
        sha, _, title = line.partition("\t")
        out.append({"sha": sha, "title": title})
    return out


def run_review(repo, base=None, head=None, merge=None, pr=None, commit=None, invariants_path=None):
    """Full review pipeline as a function (used by the CLI and the web service).

    `repo` may be a local path or a git URL (cloned/cached automatically). `pr` reviews a real
    GitHub pull request by number; `commit` reviews a single commit (its diff vs its parent).
    """
    url = repo
    repo = ensure_local(repo, update=not pr)
    title, body, auto = None, "", None                # body: PR description (GitHub PRs only)
    if not (pr or commit or merge or base or head):   # no target → current branch vs. default
        head, base = current_branch(repo), default_branch(repo)
        if not head or not base:
            raise RuntimeError("no target given and could not auto-detect current vs. default "
                               "branch — pass --base/--head, --pr, --commit, or --merge")
        if base.split("/")[-1] == head:
            raise RuntimeError(f"you are on the default branch ({head}) — check out a feature "
                               "branch or pass --base/--head explicitly")
        auto = (base, head)
    if pr:
        base, head, title, body = resolve_github_pr(url, repo, pr)
        merge = None
    elif commit:
        base, head, merge = f"{commit}^", commit, None
        title = sh("git", "-C", repo, "log", "-1", "--pretty=%s", commit) or f"commit {commit}"
    b, h, ttl = resolve_refs(repo, merge, base, head)
    title = title or ttl
    shortstat = sh("git", "-C", repo, "diff", "--shortstat", b, h)
    tb, th = tempfile.mkdtemp(), tempfile.mkdtemp()
    try:
        export(repo, b, tb)
        export(repo, h, th)
        base_eps, head_eps = analyze_repo(tb), analyze_repo(th)
    finally:
        shutil.rmtree(tb, ignore_errors=True)
        shutil.rmtree(th, ignore_errors=True)
    findings, inv_section = [], ""
    if invariants_path:
        findings = enforce_mod.enforce(json.load(open(invariants_path)), base_eps, head_eps)
        inv_section = enforce_mod.render_section(findings)
    changes = diff(base_eps, head_eps)
    changed = changed_lines(repo, b, h)              # head-side lines a PR comment can anchor to
    contradictions = intent_contradictions(title, changes, findings, body=body)
    deps_findings = dependency_findings(repo, b, h)
    egress_groups = shared_external_destinations(head_eps)
    changed_routes = {c["route"] for c in changes}
    meta = dict(title=title, base=b, head=h, shortstat=shortstat, inv_section=inv_section,
                intent_section=render_intent_section(contradictions), contradictions=contradictions,
                deps_section=deps_mod.render_deps_section(deps_findings), dependencies=deps_findings,
                egress_section=render_egress_section(egress_groups, only_routes=changed_routes),
                egress=egress_payload(egress_groups, only_routes=changed_routes),
                summary=intent_summary(changes, findings))
    return dict(review=build_review(changes, findings, meta, changed), report=render(changes, meta),
                findings=findings, changes=changes, n_base=len(base_eps), n_head=len(head_eps),
                auto_target=auto)


def write_html(review, path):
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "viewer.template.html")
    html = open(tpl, encoding="utf-8").read().replace(
        "__REVIEW_DATA__", json.dumps(review, ensure_ascii=False))
    open(path, "w", encoding="utf-8").write(html)


def main():
    ap = argparse.ArgumentParser(
        description="Semantic review of a PR. `repo` may be a local path or a git URL.")
    ap.add_argument("repo", nargs="?",
                    help="local path OR git URL (default: the git repo of the current directory; "
                         "with no target flags, reviews the current branch vs. the default branch)")
    ap.add_argument("--merge", help="review a merge commit (base=^1, head=^2)")
    ap.add_argument("--base")
    ap.add_argument("--head")
    ap.add_argument("--pr", help="review a GitHub PR by number (needs a GitHub URL repo)")
    ap.add_argument("--commit", help="review a single commit (its diff vs its parent)")
    ap.add_argument("--out", default="pr_review.md")
    ap.add_argument("--invariants", help="confirmed invariant corpus JSON to enforce")
    ap.add_argument("--json", dest="json_out", help="write structured review JSON")
    ap.add_argument("--html", help="write a self-contained HTML viewer")
    ap.add_argument("--sarif", help="write SARIF 2.1.0 for GitHub code scanning")
    ap.add_argument("--fail-on", choices=["violation", "crit"], default=None,
                    help="exit non-zero on 🔴 invariant violations (violation) or any 🔴 (crit)")
    args = ap.parse_args()

    repo = args.repo or git_toplevel(os.getcwd())
    if not repo:
        ap.error("no repo given and the current directory is not a git repo — "
                 "pass a local path or a git URL")
    try:
        res = run_review(repo, base=args.base, head=args.head, merge=args.merge,
                         pr=args.pr, commit=args.commit, invariants_path=args.invariants)
    except RuntimeError as e:
        sys.exit(f"pryti: {e}")
    review, report, findings, changes = res["review"], res["report"], res["findings"], res["changes"]
    if res.get("auto_target"):
        b, h = res["auto_target"]
        print(f"[auto-detected target: current branch vs. default — {h} vs. {b}]\n")
    open(args.out, "w").write(report)
    print(report)

    if args.json_out:
        json.dump(review, open(args.json_out, "w"), ensure_ascii=False, indent=2)
    if args.html:
        write_html(review, args.html)
    if args.sarif:
        json.dump(build_sarif(review), open(args.sarif, "w"), ensure_ascii=False, indent=2)
    outs = ", ".join(x for x in [args.out, args.json_out, args.html, args.sarif] if x)
    print(f"\n[analyzed {res['n_base']}→{res['n_head']} endpoints; wrote {outs}]")

    # Task 2 — gate CI
    violations = [f for f in findings if f["kind"] in ("NEW VIOLATION", "WEAKENING")]
    crit_changes = [c for c in changes if c["sev"] == CRIT]
    if args.fail_on == "violation" and violations:
        sys.exit(f"FAIL: {len(violations)} invariant violation(s)")
    if args.fail_on == "crit" and (violations or crit_changes):
        sys.exit(f"FAIL: {len(violations)} invariant violation(s), {len(crit_changes)} 🔴 change(s)")


if __name__ == "__main__":
    main()
