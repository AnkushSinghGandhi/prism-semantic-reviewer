"""The semantic diff: added/removed endpoints and severity. We build a 'base' by filtering the
analyzed 'head' endpoints — that exercises the real diff() code path without a second fixture."""
from conftest import BLOG
from extractor import analyze_repo
from diff_pr import diff, parse_changed_lines, build_review, build_sarif, intent_summary


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


def test_changed_lines_maps_right_side_only():
    """The head-side line numbers a review comment can anchor to: added + context lines, in
    order; removed lines don't advance the counter."""
    diff_text = (
        "diff --git a/app/views.py b/app/views.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app/views.py\n"
        "+++ b/app/views.py\n"
        "@@ -10,3 +10,4 @@ def handler():\n"
        " ctx = {}\n"                       # context  -> right 10
        "-    old_call()\n"                 # removed  -> no right line
        "+    new_call()\n"                 # added    -> right 11
        "+    requests.post(url)\n"          # added    -> right 12
        " return ctx\n"                     # context  -> right 13
        "diff --git a/app/new.py b/app/new.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/app/new.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+import os\n"                       # right 1
        "+x = 1\n"                           # right 2
    )
    cl = parse_changed_lines(diff_text)
    assert cl["app/views.py"] == [10, 11, 12, 13]
    assert cl["app/new.py"] == [1, 2]


def test_changed_lines_skips_deleted_files():
    diff_text = ("--- a/gone.py\n"
                 "+++ /dev/null\n"
                 "@@ -1,2 +0,0 @@\n"
                 "-a = 1\n"
                 "-b = 2\n")
    assert parse_changed_lines(diff_text) == {}   # a deleted file has no anchorable head lines


def _blog_review():
    head = analyze_repo(BLOG)
    base = [e for e in head if "open-write" not in e.route]      # open-write is "new"
    changes = diff(base, head)
    meta = dict(title="t", base="b", head="h", shortstat="", inv_section="")
    return build_review(changes, [], meta, changed={})


def test_intent_summary_counts_new_endpoint_and_write():
    head = analyze_repo(BLOG)
    base = [e for e in head if "open-write" not in e.route]      # open-write is "new"
    s = intent_summary(diff(base, head), [])
    assert s.startswith("This PR adds ") and "endpoint" in s
    assert "DB write" in s                                       # open-write writes a table


def test_intent_summary_no_change_is_honest():
    head = analyze_repo(BLOG)
    assert intent_summary(diff(head, head), []) == \
        "This PR makes no changes to routing, auth, data, or external calls (internal logic only)."


def test_intent_summary_reports_invariant_events():
    head = analyze_repo(BLOG)
    base = [e for e in head if "open-write" not in e.route]
    findings = [{"kind": "NEW VIOLATION", "sev": "🔴", "stmt": "x"},
                {"kind": "WEAKENING", "sev": "🔴", "stmt": "y"}]
    s = intent_summary(diff(base, head), findings)
    assert "introduces 1 invariant violation" in s and "weakens 1 invariant" in s


def test_sarif_is_valid_2_1_0_and_maps_severity():
    review = _blog_review()
    s = build_sarif(review)
    assert s["version"] == "2.1.0" and s["runs"][0]["tool"]["driver"]["name"] == "Prism"
    results = s["runs"][0]["results"]
    assert results, "every semantic change should produce a SARIF result"
    # each result carries a valid code-scanning level and a location
    assert all(r["level"] in ("error", "warning", "note") for r in results)
    assert all(r["locations"] and "artifactLocation" in r["locations"][0]["physicalLocation"]
               for r in results)
    # the new unauthenticated write (🔴) maps to error
    crit = [r for r in results if "open-write" in r["message"]["text"]]
    assert crit and crit[0]["level"] == "error"


def test_sarif_in_diff_flag_tracks_changed_lines():
    """A finding whose line the PR changed is marked in-diff (annotates the Files-changed view);
    one outside the diff is not (Security-tab only) — the honest guard, computed, not guessed."""
    head = analyze_repo(BLOG)
    base = [e for e in head if "open-write" not in e.route]
    changes = diff(base, head)
    meta = dict(title="t", base="b", head="h", shortstat="", inv_section="")

    # with an empty diff, nothing anchors inline (all Security-tab only)
    off = build_sarif(build_review(changes, [], meta, changed={}))
    assert all(r["properties"]["in-diff"] is False for r in off["runs"][0]["results"])

    # mark every investigate line of the top change as changed → its result becomes in-diff
    top = build_review(changes, [], meta, changed={})["changes"][0]
    changed = {}
    for it in top["investigate"]:
        path, _, line = it["where"].rpartition(":")
        if line.isdigit():
            changed.setdefault(path, []).append(int(line))
    on = build_sarif(build_review(changes, [], meta, changed=changed))
    assert any(r["properties"]["in-diff"] for r in on["runs"][0]["results"])
