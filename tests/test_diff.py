"""The semantic diff: added/removed endpoints and severity. We build a 'base' by filtering the
analyzed 'head' endpoints — that exercises the real diff() code path without a second fixture."""
from conftest import BLOG
from extractor import analyze_repo
from diff_pr import diff, parse_changed_lines


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
