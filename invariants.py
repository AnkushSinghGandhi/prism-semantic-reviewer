#!/usr/bin/env python3
"""
Milestone 4 — historical invariant discovery (the moat layer).

    python invariants.py <repo> [--snapshots N] [--out invariants.discovered.json]

Samples N commits across history, extracts the endpoint facts at each (git archive + the
Milestone 1 extractor), and surfaces *candidate* invariants — ranked by historical persistence,
with near-misses (142/147 + the 5 exceptions) first, because those are either a rule-with-
exceptions or five latent bugs. Discovery, not authoring: the system proposes what's already
true; a human confirms which were on purpose (baseline-and-guard).

Nothing here asserts safety. Every candidate is evidence for a human, with the exceptions and
their exact locations attached.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extractor import analyze_repo                              # noqa: E402
from gitutil import export, sh, ensure_local, git_toplevel     # noqa: E402


def _norm(s):
    return s.split(" @ ")[0].replace(" (via call)", "").strip()


def writes(ep):
    return {_norm(x).split(":")[0] for x in (ep.e3_db_tables.items or []) if ":write" in _norm(x)}


def ext_dests(ep):
    out = set()
    for x in (ep.e4_external.items or []):
        d = _norm(x).split(" -> ", 1)[-1]
        if d and d != "?":
            out.add(d)
    return out


def authed(ep):
    e = ep.e2_auth
    return e.status == "✓" and not any("AllowAny" in x for x in e.items)


def auth_label(ep):
    e = ep.e2_auth
    if e.status == "?":
        return "auth unspecified"
    if any("AllowAny" in x for x in e.items):
        return "open (AllowAny)"
    return "authenticated"


def prefix(route, depth=3):
    segs = [s for s in route.split("/") if s and not s.startswith("<") and not s.startswith("api")]
    return "/" + "/".join(segs[:depth])


# ---- candidate model -----------------------------------------------------------------

@dataclass
class Candidate:
    id: str
    statement: str
    kind: str                       # near-miss | universal | boundary
    severity: str
    scope: str
    total: int = 0
    ok: int = 0
    exceptions: list = field(default_factory=list)   # (route, why, location)
    history: list = field(default_factory=list)      # (sha, ratio)
    extra: dict = field(default_factory=dict)

    @property
    def ratio(self):
        return self.ok / self.total if self.total else 0.0


# ---- property evaluators (run per snapshot) ------------------------------------------

def eval_auth_before_write(eps):
    subjects = [e for e in eps if writes(e)]
    ok = [e for e in subjects if authed(e)]
    exc = [(e.route, auth_label(e), f"{e.file}:{e.line}") for e in subjects if not authed(e)]
    return {"auth-before-write": (len(subjects), len(ok), exc)}


def eval_auth_by_group(eps):
    groups = {}
    for e in eps:
        groups.setdefault(prefix(e.route), []).append(e)
    out = {}
    for g, members in groups.items():
        if len(members) < 4:
            continue
        ok = [e for e in members if authed(e)]
        exc = [(e.route, auth_label(e), f"{e.file}:{e.line}") for e in members if not authed(e)]
        out[f"auth-group:{g}"] = (len(members), len(ok), exc)
    return out


def eval_pii_egress(eps):
    # "endpoints that read PII + have an egress path require auth"
    subjects = [e for e in eps if e.e6_pii.status in {"✓", "⚠", "?"} and e.e6_pii.items]
    ok = [e for e in subjects if authed(e)]
    exc = [(e.route, auth_label(e), f"{e.file}:{e.line}") for e in subjects if not authed(e)]
    return {"pii-egress-authed": (len(subjects), len(ok), exc)}


RATIO_EVALS = [eval_auth_before_write, eval_auth_by_group, eval_pii_egress]

STATEMENTS = {
    "auth-before-write": ("Authenticated access before any DB write", "critical",
                          "all endpoints performing a DB write"),
    "pii-egress-authed": ("PII read + egress only on authenticated endpoints", "critical",
                          "endpoints reading PII with an egress path"),
}


# ---- history sampling ----------------------------------------------------------------

def sample_commits(repo, n):
    shas = sh("git", "-C", repo, "rev-list", "--first-parent", "HEAD").splitlines()
    if len(shas) <= n:
        picks = shas
    else:
        step = (len(shas) - 1) / (n - 1)
        picks = [shas[round(i * step)] for i in range(n)]
    # oldest -> newest
    return list(reversed([s[:10] for s in picks]))


def snapshot_facts(repo, sha):
    d = tempfile.mkdtemp()
    try:
        export(repo, sha, d)
        return analyze_repo(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---- discovery -----------------------------------------------------------------------

def discover(repo, n):
    shas = sample_commits(repo, n)
    per_sha_ratio = {}          # cand_id -> [(sha, ratio)]
    latest = {}                 # cand_id -> (total, ok, exceptions) at HEAD
    dest_history = {}           # dest -> first sha seen
    for sha in shas:
        eps = snapshot_facts(repo, sha)
        for ev in RATIO_EVALS:
            for cid, (total, ok, exc) in ev(eps).items():
                ratio = ok / total if total else 1.0
                per_sha_ratio.setdefault(cid, []).append((sha, round(ratio, 3)))
                latest[cid] = (total, ok, exc)
        # external egress boundary (set growth)
        for e in eps:
            for d in ext_dests(e):
                dest_history.setdefault(d, sha)

    cands = []
    for cid, (total, ok, exc) in latest.items():
        if total == 0:
            continue
        hist = per_sha_ratio[cid]
        ratio = ok / total
        near_miss = 0.70 <= ratio < 1.0 and 1 <= len(exc) <= 6
        persistent = sum(1 for _, r in hist if r >= 0.99)
        if cid in STATEMENTS:
            stmt, sev, scope = STATEMENTS[cid]
        elif cid.startswith("auth-group:"):
            g = cid.split(":", 1)[1]
            stmt, sev, scope = f"All endpoints under {g} require authentication", "high", g
        else:
            stmt, sev, scope = cid, "medium", "—"
        kind = "near-miss" if near_miss else ("universal" if ratio == 1.0 and total >= 6 else "weak")
        if kind == "weak":
            continue
        cands.append(Candidate(cid, stmt, kind, sev, scope, total, ok, exc, hist,
                               extra={"persistent_snapshots": persistent, "n_snapshots": len(hist)}))

    # external egress boundary as a first-class candidate
    if dest_history:
        newest = shas[-1]
        added_recently = sorted([d for d, s in dest_history.items() if s == newest])
        cands.append(Candidate(
            "external-egress-allowlist",
            f"External/PII egress limited to {len(dest_history)} known destinations",
            "boundary", "critical", "all outbound calls",
            total=len(dest_history), ok=len(dest_history), exceptions=[],
            history=[], extra={"destinations": sorted(dest_history),
                               "added_at_head": added_recently}))

    order = {"near-miss": 0, "boundary": 1, "universal": 2}
    cands.sort(key=lambda c: (order.get(c.kind, 9),
                              -c.extra.get("persistent_snapshots", 0), -c.total))
    return shas, cands


# ---- render + corpus -----------------------------------------------------------------

def trend(hist):
    return " → ".join(f"{int(r*100)}%" for _, r in hist)


def render(shas, cands):
    L = [f"# Candidate Invariants — discovered from {len(shas)} snapshots",
         f"\nhistory sampled `{shas[0]}` … `{shas[-1]}` (oldest→newest). "
         "Ranked: near-misses first (rule-with-exceptions or latent bugs), then boundaries, "
         "then persistent universals.\n"]
    for c in cands:
        icon = {"near-miss": "⚠ NEAR-MISS", "boundary": "🔒 BOUNDARY",
                "universal": "✓ PERSISTENT"}.get(c.kind, c.kind)
        L.append(f"## {icon} — {c.statement}")
        if c.kind == "boundary":
            L.append(f"- **destinations ({c.total}):** {', '.join(c.extra['destinations'])}")
            if c.extra.get("added_at_head"):
                L.append(f"- **added at HEAD:** {', '.join(c.extra['added_at_head'])} "
                         "→ a *new* destination in a PR is a boundary change (M5 guards this)")
        else:
            L.append(f"- **holds:** {c.ok}/{c.total} endpoints ({int(c.ratio*100)}%)  ·  "
                     f"**persistence:** {trend(c.history)} across {c.extra['n_snapshots']} snapshots")
            if c.exceptions:
                L.append(f"- **exceptions — look here ({len(c.exceptions)}):**")
                for route, why, loc in c.exceptions[:8]:
                    L.append(f"    - `{route}` — {why}  (`{loc}`)")
        L.append(f"- **suggested:** severity={c.severity}, scope={c.scope}")
        L.append(f"- _confirm → freeze as invariant (baseline the exceptions or fix them)_\n")
    L.append("---\n_Discovery only. Historical persistence is the prior that a property is "
             "intentional; a snapshot-universal is often structural trivia. Confirm the ones "
             "that were on purpose — that confirmed corpus is the compounding asset._")
    return "\n".join(L)


def corpus(cands):
    out = []
    for c in cands:
        out.append({
            "id": c.id, "statement": c.statement, "kind": c.kind,
            "severity": c.severity, "scope": c.scope,
            "observed": {"ok": c.ok, "total": c.total, "ratio": round(c.ratio, 3),
                         "history": c.history, **({"destinations": c.extra["destinations"]}
                                                  if c.kind == "boundary" else {})},
            "baseline_exceptions": [{"route": r, "why": w, "location": loc}
                                    for r, w, loc in c.exceptions],
            "confirmed": False, "owner": None,
        })
    return out


# ---- confirm (moat part A: turn discovery into a confirmed corpus without hand-editing JSON) ----

def confirm_candidate(cand, *, owner=None, keep_baseline=True):
    """A confirmed copy of a discovered candidate: `confirmed=True`, owner recorded, and (default)
    its discovered exceptions frozen as `baseline_exceptions` so enforce() treats them as known —
    not as new violations this PR introduced. `keep_baseline=False` says 'these are latent bugs,
    not accepted exceptions' and clears them, so enforce() will surface them as violations to fix."""
    c = dict(cand)
    c["confirmed"] = True
    c["owner"] = owner if owner is not None else c.get("owner")
    if not keep_baseline:
        c["baseline_exceptions"] = []
    return c


def merge_corpus(existing, confirmed):
    """Merge newly-confirmed invariants into an existing confirmed corpus, keyed by `id` (update in
    place, append new), preserving the order of `existing` then appending genuinely new ones.
    Neither input list is mutated."""
    by_id = {c["id"]: dict(c) for c in existing}
    order = [c["id"] for c in existing]
    for c in confirmed:
        if c["id"] not in by_id:
            order.append(c["id"])
        by_id[c["id"]] = dict(c)
    return [by_id[i] for i in order]


def _fmt_candidate(cand):
    """One-screen summary of a candidate for the interactive prompt."""
    obs = cand.get("observed", {})
    L = [f"{cand['statement']}  [{cand.get('kind', '?')}, {cand.get('severity', '?')}]"]
    if "ratio" in obs:
        L.append(f"  holds {obs.get('ok')}/{obs.get('total')} ({int(obs.get('ratio', 0) * 100)}%)")
    exc = cand.get("baseline_exceptions", [])
    if exc:
        L.append(f"  {len(exc)} exception(s):")
        for e in exc[:6]:
            L.append(f"    - {e['route']} — {e.get('why', '')} ({e.get('location', '')})")
    return "\n".join(L)


def run_confirm(in_path, corpus_path, *, ids=None, confirm_all=False, owner=None,
                keep_baseline=True, input_fn=input, print_fn=print):
    """Confirm discovered candidates into a confirmed corpus. Non-interactive when `ids` or
    `confirm_all` is given; otherwise prompts per unconfirmed candidate
    ([c]onfirm / [f]ix-not-baseline / [s]kip / [q]uit). Returns (merged_corpus, newly_confirmed)."""
    discovered = json.load(open(in_path, encoding="utf-8"))
    existing = json.load(open(corpus_path, encoding="utf-8")) if os.path.exists(corpus_path) else []
    newly = []
    for cand in discovered:
        if confirm_all or (ids and cand["id"] in ids):
            newly.append(confirm_candidate(cand, owner=owner, keep_baseline=keep_baseline))
            continue
        if ids is not None or confirm_all or cand.get("confirmed"):
            continue                                    # non-interactive & unselected, or already done
        print_fn("\n" + _fmt_candidate(cand))
        ans = (input_fn("confirm? [c]onfirm / [f]ix / [s]kip / [q]uit: ") or "").strip().lower()[:1]
        if ans == "q":
            break
        if ans in ("c", "f"):
            newly.append(confirm_candidate(cand, owner=owner, keep_baseline=(ans == "c")))
    merged = merge_corpus(existing, newly)
    json.dump(merged, open(corpus_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    return merged, newly


# ---- semantic blame (moat part B: which commit first broke this rule) --------------------------

def _eval_one(inv_id, eps):
    """(total, ok, exceptions) for one invariant on a snapshot, or None if the id isn't evaluable.
    Reuses the same evaluators discovery and enforce.py run — one source of truth for 'what holds'."""
    if inv_id == "auth-before-write":
        return eval_auth_before_write(eps)["auth-before-write"]
    if inv_id == "pii-egress-authed":
        return eval_pii_egress(eps)["pii-egress-authed"]
    if inv_id.startswith("auth-group:"):
        return eval_auth_by_group(eps).get(inv_id)
    return None


def _broke(inv_id, eps, route=None):
    """Does `inv_id` break on this snapshot (for `route`, or overall)?"""
    res = _eval_one(inv_id, eps)
    if res is None:
        return False
    total, ok, exc = res
    return (route in {r for (r, _, _) in exc}) if route else (total > 0 and ok < total)


def bisect_break(inv_id, commits, facts_at, *, route=None):
    """First sha in `commits` (oldest→newest, all after a known-holding baseline) where `inv_id`
    breaks — binary search assuming a single hold→broke transition in the window. `commits` and
    `facts_at` are injected, so this is pure and unit-testable. None if none break."""
    if not commits or not _broke(inv_id, facts_at(commits[-1]), route):
        return None
    lo, hi = 0, len(commits) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if _broke(inv_id, facts_at(commits[mid]), route):
            hi = mid
        else:
            lo = mid + 1
    return commits[lo]


def blame_invariant(repo, inv_id, *, route=None, n=12, exact=False, shas=None, facts_at=None):
    """Semantic blame: the earliest sampled snapshot where `inv_id` first broke — overall (ratio
    drops below 1.0) or, with `route`, where that specific endpoint first became an exception.
    Returns a dict (or None if it never broke in the sampled history) with the snapshot `commit`
    and the `window` (prev..commit) it falls in. With `exact=True` and a known-good `prev`, it
    binary-searches the commits in that window and adds `commit_exact` — the precise breaking commit.

    `shas`/`facts_at` are injectable so the walk is unit-testable without a real multi-commit repo."""
    shas = shas if shas is not None else sample_commits(repo, n)      # oldest -> newest
    facts_at = facts_at or (lambda s: snapshot_facts(repo, s))
    prev, result = None, None
    for sha in shas:
        res = _eval_one(inv_id, facts_at(sha))
        if res is not None and _broke(inv_id, facts_at(sha), route):
            total, ok, exc = res
            result = {"invariant": inv_id, "route": route, "commit": sha, "window": [prev, sha],
                      "ok": ok, "total": total,
                      "exceptions": [{"route": r, "why": w, "location": loc} for (r, w, loc) in exc]}
            break
        if res is not None:
            prev = sha
    if result is None:
        return None
    if exact and result["window"][0]:                                # narrow within prev..commit
        lo, hi = result["window"]
        commits = sh("git", "-C", repo, "rev-list", "--first-parent", "--reverse",
                     f"{lo}..{hi}").splitlines() if repo else []
        exact_sha = bisect_break(inv_id, commits, facts_at, route=route)
        if exact_sha:
            result["commit_exact"] = exact_sha
    return result


def render_blame(result, inv_id, route=None):
    if result is None:
        who = f" for `{route}`" if route else ""
        return f"✓ `{inv_id}`{who} held across every sampled snapshot — no break found."
    prev, sha = result["window"]
    window = f"{prev}..{sha}" if prev else f"(first snapshot) {sha}"
    if result.get("commit_exact"):
        L = [f"🩸 `{inv_id}` first broke at `{result['commit_exact']}`  (exact, in window `{window}`)",
             f"   holds {result['ok']}/{result['total']} at the snapshot."]
    else:
        L = [f"🩸 `{inv_id}` first broke at `{sha}`  (window `{window}`)",
             f"   holds {result['ok']}/{result['total']} there."]
    rows = ([e for e in result["exceptions"] if e["route"] == route] if route
            else result["exceptions"])
    if rows:
        L.append("   offending endpoint(s):")
        for e in rows[:8]:
            L.append(f"     - {e['route']} — {e['why']} ({e['location']})")
    if not result.get("commit_exact"):
        L.append("   _snapshot-granular: the break is somewhere in that window; "
                 "add --exact (or more --snapshots) to narrow it._")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Discover, confirm, and blame codebase invariants.")
    ap.add_argument("repo", nargs="?", help="repo path or URL (discover & blame; default: cwd repo)")
    ap.add_argument("--snapshots", type=int, default=6)
    ap.add_argument("--out", default="invariants.discovered.json")
    ap.add_argument("--report", default="invariants_report.md")
    # confirm mode (part A)
    ap.add_argument("--confirm", action="store_true",
                    help="confirm discovered candidates into a corpus (interactive unless --id/--all)")
    ap.add_argument("--in", dest="in_path", default="invariants.discovered.json",
                    help="discovered corpus to confirm from (--confirm)")
    ap.add_argument("--corpus", default="invariants.confirmed.json",
                    help="confirmed corpus to create/update (--confirm)")
    ap.add_argument("--id", action="append", metavar="INVARIANT_ID",
                    help="confirm this id non-interactively (repeatable)")
    ap.add_argument("--all", action="store_true", help="confirm every candidate non-interactively")
    ap.add_argument("--owner", help="record this owner on confirmed invariants")
    ap.add_argument("--drop-baseline", action="store_true",
                    help="treat current exceptions as bugs to fix (don't baseline them)")
    # blame mode (part B)
    ap.add_argument("--blame", metavar="INVARIANT_ID",
                    help="semantic blame: find the commit where INVARIANT_ID first broke")
    ap.add_argument("--route", help="blame one endpoint route (with --blame)")
    ap.add_argument("--exact", action="store_true",
                    help="bisect the blame window for the precise breaking commit (slower)")
    args = ap.parse_args()

    if args.confirm:
        merged, newly = run_confirm(args.in_path, args.corpus, ids=args.id, confirm_all=args.all,
                                    owner=args.owner, keep_baseline=not args.drop_baseline)
        confirmed_total = sum(1 for c in merged if c.get("confirmed"))
        print(f"[confirmed {len(newly)} candidate(s); {confirmed_total} invariant(s) now confirmed "
              f"in {args.corpus}]")
        return

    repo = (ensure_local(args.repo) if args.repo else git_toplevel(os.getcwd()))
    if not repo:
        ap.error("no repo given and the current directory is not a git repo")

    if args.blame:
        result = blame_invariant(repo, args.blame, route=args.route, n=args.snapshots,
                                 exact=args.exact)
        print(render_blame(result, args.blame, args.route))
        return

    shas, cands = discover(repo, args.snapshots)
    report = render(shas, cands)
    print(report)
    open(args.report, "w").write(report)
    json.dump(corpus(cands), open(args.out, "w"), indent=2, ensure_ascii=False)
    print(f"\n[sampled {len(shas)} commits; {len(cands)} candidates; "
          f"wrote {args.report} + {args.out}]")


if __name__ == "__main__":
    main()
