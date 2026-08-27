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
import sys
import tempfile
import shutil
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractor import analyze_repo             # noqa: E402
from gitutil import sh, export, ensure_local   # noqa: E402
import enforce as enforce_mod                  # noqa: E402

CRIT, HIGH, MED, LOW = "🔴", "🟠", "🟡", "🟢"
EDGES = [("e3_db_tables", "db"), ("e4_external", "external"),
         ("e5_async", "async"), ("e6_pii", "pii")]

INFO_URI = "https://github.com/AnkushSinghGandhi/prism"
# GitHub code-scanning alert level per Prism severity tier.
SARIF_LEVEL = {CRIT: "error", HIGH: "error", MED: "warning", LOW: "note"}
# Optional numeric on each result for the Security-tab Critical/High/Medium/Low sort.
SARIF_SECURITY = {CRIT: "9.0", HIGH: "7.0", MED: "4.0", LOW: "2.0"}


def _prism_version():
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("prism-semantic-reviewer")
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
            pii = bool(ep.e6_pii.items) and ep.e6_pii.status in {"⚠", "?"}
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

    order = {CRIT: 0, HIGH: 1, MED: 2, LOW: 3}
    changes.sort(key=lambda c: order[c["sev"]])
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


def render(changes, meta):
    L = []
    L.append(f"# Semantic Review — {meta['title']}")
    L.append(f"\n`{meta['base']}` → `{meta['head']}`  |  line diff: {meta['shortstat']}\n")
    if meta.get("summary"):
        L.append(f"> **{meta['summary']}**\n")
    if meta.get("inv_section"):
        L.append(meta["inv_section"])
    buckets = {CRIT: [], HIGH: [], MED: [], LOW: []}
    for c in changes:
        buckets[c["sev"]].append(c)
    tally = "  ".join(f"{s} {len(buckets[s])}" for s in (CRIT, HIGH, MED, LOW) if buckets[s])
    L.append(f"**{len(changes)} semantic changes** — {tally}\n")
    L.append("| | change | route |")
    L.append("|--|--------|-------|")
    for c in changes:
        L.append(f"| {c['sev']} | {c['kind']} | `{c['route']}` |")
    L.append("\n---\n")
    for c in changes:
        ep = c["ep"]
        L.append(f"### {c['sev']} {c['kind']} — `{c['route']}`")
        L.append(f"- **why:** {c['why']}")
        if c.get("detail"):
            L.append(f"- {c['detail']}")
        L.append(f"- **flow:** {flow(ep)}")
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
        pii=bool(ep.e6_pii.items) and ep.e6_pii.status in {"⚠", "?"})
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
                "investigate": investigate, "unknowns": unknowns(ep), "graph": graph}
    return {"title": meta["title"], "base": meta["base"], "head": meta["head"],
            "shortstat": meta["shortstat"], "summary": meta.get("summary", ""),
            "invariants": findings or [],
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
                          "properties": {"tags": ["prism"]}})

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
            "partialFingerprints": {"prism/v1": fp},
            "properties": {"prism-severity": sev, "in-diff": in_diff,
                           "security-severity": SARIF_SECURITY.get(sev, "4.0")},
        })

    for c in review.get("changes", []):
        rid = "prism/" + c["kind"].lower().replace(" ", "-")
        rule(rid, f"Prism semantic change: {c['kind']}")
        path, line, in_diff = _best_location(c, changed)
        text = f"{c['kind']} {c['route']} — {c.get('why', '')}".strip()
        emit(rid, c.get("sev", MED), text, path, line, in_diff, f"{c['kind']}::{c['route']}")

    for f in review.get("invariants", []):
        if f["kind"] not in ("NEW VIOLATION", "WEAKENING", "STILL-VIOLATING", "STILL-OUT"):
            continue                            # only real alerts; HELD/RESOLVED aren't findings
        rid = "prism/invariant/" + f["kind"].lower().replace(" ", "-")
        rule(rid, f"Prism invariant alert: {f['kind']}")
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

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "Prism", "informationUri": INFO_URI,
                                "version": _prism_version(), "rules": rules}},
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
                                               "User-Agent": "prism"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return json.load(urllib.request.urlopen(req, timeout=30))


def _is_github_url(repo):
    return "github.com/" in repo or repo.startswith("git@github.com:")


def resolve_github_pr(url, local, n):
    """Look up a real GitHub PR: return (base_sha, head_sha, title). Fetches the PR head ref."""
    if not _is_github_url(url):
        raise RuntimeError("--pr requires a GitHub repo URL so Prism can look up the PR metadata")
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
    fetch_ref("base", "refs/prism/base", base_sha, data["base"]["ref"])
    fetch_ref("head", "refs/prism/head", f"pull/{n}/head", head_sha)
    return base_sha, head_sha, f"PR #{n}: {data.get('title', '')}"


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
    title = None
    if pr:
        base, head, title = resolve_github_pr(url, repo, pr)
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
    meta = dict(title=title, base=b, head=h, shortstat=shortstat, inv_section=inv_section,
                summary=intent_summary(changes, findings))
    return dict(review=build_review(changes, findings, meta, changed), report=render(changes, meta),
                findings=findings, changes=changes, n_base=len(base_eps), n_head=len(head_eps))


def write_html(review, path):
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "viewer.template.html")
    html = open(tpl, encoding="utf-8").read().replace(
        "__REVIEW_DATA__", json.dumps(review, ensure_ascii=False))
    open(path, "w", encoding="utf-8").write(html)


def main():
    ap = argparse.ArgumentParser(
        description="Semantic review of a PR. `repo` may be a local path or a git URL.")
    ap.add_argument("repo", help="local path OR git URL (https://github.com/owner/repo)")
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

    res = run_review(args.repo, base=args.base, head=args.head, merge=args.merge,
                     pr=args.pr, commit=args.commit, invariants_path=args.invariants)
    review, report, findings, changes = res["review"], res["report"], res["findings"], res["changes"]
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
