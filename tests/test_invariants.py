"""The moat layer: confirming discovered candidates into a corpus (part A) and semantic blame —
which commit first broke a rule (part B). Confirm logic is pure/file-based; blame injects a fake
snapshot history so the walk is tested without building a multi-commit repo."""
import json
from extractor import analyze_repo
from conftest import BLOG
from invariants import (confirm_candidate, merge_corpus, run_confirm,
                        blame_invariant, render_blame, _eval_one)


def _cand(cid, exc=1):
    return {"id": cid, "statement": f"stmt {cid}", "kind": "near-miss", "severity": "critical",
            "baseline_exceptions": [{"route": f"/r{i}", "why": "open (AllowAny)",
                                     "location": f"f.py:{i}"} for i in range(exc)],
            "confirmed": False, "owner": None}


def test_confirm_candidate_sets_flags_and_keeps_baseline():
    c = confirm_candidate(_cand("x", exc=2), owner="sec")
    assert c["confirmed"] is True and c["owner"] == "sec"
    assert len(c["baseline_exceptions"]) == 2          # exceptions kept as accepted baseline


def test_confirm_candidate_drop_baseline_marks_exceptions_as_bugs():
    c = confirm_candidate(_cand("x", exc=2), keep_baseline=False)
    assert c["confirmed"] is True and c["baseline_exceptions"] == []


def test_merge_corpus_updates_by_id_and_appends():
    existing = [{"id": "a", "confirmed": True}, {"id": "b", "confirmed": True}]
    merged = merge_corpus(existing, [{"id": "b", "confirmed": True, "owner": "new"},
                                     {"id": "c", "confirmed": True}])
    assert [c["id"] for c in merged] == ["a", "b", "c"]     # order preserved, new appended
    assert next(c for c in merged if c["id"] == "b")["owner"] == "new"   # updated in place


def test_run_confirm_non_interactive_by_id(tmp_path):
    disc = tmp_path / "disc.json"
    disc.write_text(json.dumps([_cand("keep"), _cand("drop")]))
    corpus = tmp_path / "corpus.json"
    merged, newly = run_confirm(str(disc), str(corpus), ids=["keep"], owner="platform")
    assert [c["id"] for c in newly] == ["keep"]
    on_disk = json.loads(corpus.read_text())
    assert [c["id"] for c in on_disk] == ["keep"]
    assert on_disk[0]["confirmed"] is True and on_disk[0]["owner"] == "platform"


def test_run_confirm_all_merges_into_existing(tmp_path):
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"id": "old", "confirmed": True}]))
    disc = tmp_path / "disc.json"
    disc.write_text(json.dumps([_cand("new1"), _cand("new2")]))
    merged, newly = run_confirm(str(disc), str(corpus), confirm_all=True)
    assert [c["id"] for c in merged] == ["old", "new1", "new2"]


def test_eval_one_reports_ok_and_total():
    head = analyze_repo(BLOG)
    total, ok, exc = _eval_one("auth-before-write", head)
    assert total >= ok and any("open-write" in r for (r, _, _) in exc)


def test_blame_finds_the_breaking_snapshot():
    head = analyze_repo(BLOG)
    clean = [e for e in head if "open-write" not in e.route]      # invariant holds here
    facts = {"s1": clean, "s2": head}                             # then open-write breaks it
    r = blame_invariant(".", "auth-before-write", shas=["s1", "s2"],
                        facts_at=lambda s: facts[s])
    assert r["commit"] == "s2" and r["window"] == ["s1", "s2"]
    assert any("open-write" in e["route"] for e in r["exceptions"])


def test_blame_route_scoped_and_clean_history_returns_none():
    head = analyze_repo(BLOG)
    clean = [e for e in head if "open-write" not in e.route]
    # never broke → None → held message
    r = blame_invariant(".", "auth-before-write", shas=["s1"], facts_at=lambda s: clean)
    assert r is None and "held" in render_blame(r, "auth-before-write")
    # route-scoped blame pins the specific endpoint
    facts = {"s1": clean, "s2": head}
    r2 = blame_invariant(".", "auth-before-write", route="api/open-write",
                         shas=["s1", "s2"], facts_at=lambda s: facts[s])
    assert r2 and r2["commit"] == "s2"
