"""The semantic diff: added/removed endpoints and severity. We build a 'base' by filtering the
analyzed 'head' endpoints — that exercises the real diff() code path without a second fixture."""
from conftest import BLOG
from extractor import analyze_repo
from diff_pr import diff


def test_new_unauth_write_endpoint_is_critical():
    head = analyze_repo(BLOG)
    base = [e for e in head if "open-write" not in e.route]   # pretend open-write is new
    changes = diff(base, head)
    new = [c for c in changes if "open-write" in c["route"]]
    assert new, "expected a NEW ENDPOINT for open-write"
    assert new[0]["kind"] == "NEW ENDPOINT"
    assert new[0]["sev"] == "🔴"     # unauthenticated + DB write → highest severity


def test_removed_endpoint_reported():
    base = analyze_repo(BLOG)
    head = [e for e in base if "open-write" not in e.route]   # removed in head
    changes = diff(base, head)
    assert any(c["kind"] == "REMOVED ENDPOINT" and "open-write" in c["route"] for c in changes)


def test_identical_snapshots_have_no_changes():
    eps = analyze_repo(BLOG)
    assert diff(eps, eps) == []      # no diff when nothing changed (refactor-stable baseline)
