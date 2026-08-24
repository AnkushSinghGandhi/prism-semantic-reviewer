"""Invariant enforcement: a PR that introduces an unauthenticated write should violate a confirmed
'authenticated access before any DB write' rule."""
from conftest import BLOG
from extractor import analyze_repo
from enforce import enforce

CORPUS = [{
    "id": "auth-before-write",
    "statement": "Authenticated access before any DB write",
    "severity": "critical",
    "confirmed": True,
    "baseline_exceptions": [],
}]


def test_new_violation_detected():
    head = analyze_repo(BLOG)
    base = [e for e in head if "open-write" not in e.route]   # open-write is introduced by "this PR"
    findings = enforce(CORPUS, base, head)
    kinds = [f["kind"] for f in findings]
    assert "NEW VIOLATION" in kinds
    nv = next(f for f in findings if f["kind"] == "NEW VIOLATION")
    assert "open-write" in " ".join(nv.get("locs", []))


def test_unconfirmed_rules_are_ignored():
    head = analyze_repo(BLOG)
    base = [e for e in head if "open-write" not in e.route]
    corpus = [dict(CORPUS[0], confirmed=False)]
    assert enforce(corpus, base, head) == []
