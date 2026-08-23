"""
Milestone 5 — enforce a confirmed invariant corpus against a PR (base → head).

Re-evaluates each confirmed invariant on both snapshots and reports the *deltas* a PR review
cares about:

  🔴 NEW VIOLATION   a subject the PR added/changed now breaks the invariant (not baselined)
  🔴 WEAKENING       the definition of "acceptable" expanded — a new egress destination, or a
                     newly-permitted exception. Per the design docs this is a first-class event,
                     rendered MORE prominently than the code that motivated it (ratchet guard).
  🟢 RESOLVED        a baselined exception no longer violates (candidate to drop from baseline)
  ✓ HELD             invariant still satisfied

Nothing asserts safety beyond what the facts show; "auth unspecified" subjects are reported as
violations-to-check, not proven breaches.
"""
from invariants import (eval_auth_before_write, eval_auth_by_group, eval_pii_egress, ext_dests)

CRIT, HIGH, MED, LOW = "🔴", "🟠", "🟡", "🟢"
SEV = {"critical": CRIT, "high": HIGH, "medium": MED, "low": LOW}


def _violators(inv_id, eps):
    """(route, why, location) triples that violate invariant `inv_id` in snapshot `eps`."""
    if inv_id == "auth-before-write":
        return eval_auth_before_write(eps)["auth-before-write"][2]
    if inv_id == "pii-egress-authed":
        return eval_pii_egress(eps)["pii-egress-authed"][2]
    if inv_id.startswith("auth-group:"):
        return eval_auth_by_group(eps).get(inv_id, (0, 0, []))[2]
    return []


def _all_dests(eps):
    out = set()
    for e in eps:
        out |= ext_dests(e)
    return out


def enforce(corpus, base_eps, head_eps):
    findings = []
    for inv in corpus:
        if not inv.get("confirmed"):
            continue
        sev = SEV.get(inv.get("severity"), MED)
        stmt = inv["statement"]

        if inv["id"] == "external-egress-allowlist":
            allowed = set(inv.get("observed", {}).get("destinations", []))
            base_d, head_d = _all_dests(base_eps), _all_dests(head_eps)
            new = sorted((head_d - allowed) - base_d)          # introduced by this PR
            preexisting = sorted((head_d - allowed) & base_d)
            if new:
                findings.append(dict(kind="WEAKENING", sev=CRIT, stmt=stmt,
                                     detail=f"egress allowlist would expand — new destination(s) "
                                            f"not among the {len(allowed)} approved: {', '.join(new)}",
                                     locs=[]))
            elif preexisting:
                findings.append(dict(kind="STILL-OUT", sev=MED, stmt=stmt,
                                     detail=f"unapproved destination(s) present (pre-existing): "
                                            f"{', '.join(preexisting)}", locs=[]))
            else:
                findings.append(dict(kind="HELD", sev=LOW, stmt=stmt, detail="", locs=[]))
            continue

        baseline = {e["route"] for e in inv.get("baseline_exceptions", [])}
        base_v = {r for (r, _, _) in _violators(inv["id"], base_eps)}
        head_v = _violators(inv["id"], head_eps)
        head_routes = {r for (r, _, _) in head_v}

        new_viol = [(r, w, loc) for (r, w, loc) in head_v if r not in baseline and r not in base_v]
        still = [(r, w, loc) for (r, w, loc) in head_v if r not in baseline and r in base_v]
        resolved = [e for e in inv.get("baseline_exceptions", []) if e["route"] not in head_routes]

        if new_viol:
            findings.append(dict(kind="NEW VIOLATION", sev=sev, stmt=stmt,
                                 detail=f"{len(new_viol)} endpoint(s) introduced/changed by this PR "
                                        f"break this invariant",
                                 rows=new_viol,
                                 locs=[f"{r} — {w} @ {loc}" for (r, w, loc) in new_viol]))
        if still:
            findings.append(dict(kind="STILL-VIOLATING", sev=MED, stmt=stmt,
                                 detail=f"{len(still)} pre-existing violation(s) (not this PR)",
                                 rows=still, locs=[]))
        if resolved:
            findings.append(dict(kind="RESOLVED", sev=LOW, stmt=stmt,
                                 detail=f"{len(resolved)} baselined exception(s) no longer violate",
                                 locs=[e["route"] for e in resolved]))
        if not (new_viol or still or resolved):
            findings.append(dict(kind="HELD", sev=LOW, stmt=stmt, detail="", locs=[]))

    order = {"NEW VIOLATION": 0, "WEAKENING": 0, "STILL-OUT": 2, "STILL-VIOLATING": 2,
             "RESOLVED": 3, "HELD": 4}
    findings.sort(key=lambda f: order.get(f["kind"], 5))
    return findings


def render_section(findings):
    """Markdown block to prepend above the semantic-change list."""
    if not findings:
        return ""
    alerts = [f for f in findings if f["kind"] in ("NEW VIOLATION", "WEAKENING")]
    held = [f for f in findings if f["kind"] == "HELD"]
    L = ["## 🛡 Invariants"]
    if alerts:
        L.append(f"**{len(alerts)} invariant alert(s)** — a confirmed property of this codebase "
                 "is affected by this PR:\n")
    for f in findings:
        if f["kind"] == "HELD":
            continue
        icon = {"NEW VIOLATION": "🔴 NEW VIOLATION", "WEAKENING": "🔴 WEAKENING",
                "STILL-VIOLATING": "🟡 pre-existing", "STILL-OUT": "🟡 pre-existing",
                "RESOLVED": "🟢 RESOLVED"}.get(f["kind"], f["kind"])
        L.append(f"- **{icon} — {f['stmt']}**")
        if f["detail"]:
            L.append(f"    - {f['detail']}")
        for loc in (f.get("locs") or []):
            L.append(f"    - `{loc}`")
    if held:
        L.append(f"- ✓ {len(held)} other confirmed invariant(s) held.")
    L.append("")
    return "\n".join(L)
