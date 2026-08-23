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
from extractor import analyze_repo          # noqa: E402
from gitutil import export, sh              # noqa: E402


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
    subjects = [e for e in eps if e.e6_pii.status in {"⚠", "?"} and e.e6_pii.items]
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--snapshots", type=int, default=6)
    ap.add_argument("--out", default="invariants.discovered.json")
    ap.add_argument("--report", default="invariants_report.md")
    args = ap.parse_args()

    shas, cands = discover(args.repo, args.snapshots)
    report = render(shas, cands)
    print(report)
    open(args.report, "w").write(report)
    json.dump(corpus(cands), open(args.out, "w"), indent=2, ensure_ascii=False)
    print(f"\n[sampled {len(shas)} commits; {len(cands)} candidates; "
          f"wrote {args.report} + {args.out}]")


if __name__ == "__main__":
    main()
