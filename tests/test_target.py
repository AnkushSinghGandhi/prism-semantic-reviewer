"""Smart default target (#8): with no flags, `pryti review` auto-detects current branch vs. the
repo's default branch. Builds a throwaway git repo so the branch helpers exercise real git."""
import os
import subprocess
import pytest
from gitutil import current_branch, default_branch
from diff_pr import run_review


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A local repo with a `main` branch (one commit) and a `feature` branch checked out."""
    d = str(tmp_path)
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("hello\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "init")
    _git(d, "branch", "-M", "main")             # deterministic default-branch name across git versions
    _git(d, "checkout", "-q", "-b", "feature")
    (tmp_path / "a.txt").write_text("hello there\n")
    _git(d, "commit", "-aqm", "work")
    return d


def test_current_branch_reads_checked_out_branch(repo):
    assert current_branch(repo) == "feature"


def test_default_branch_falls_back_to_local_main(repo):
    assert default_branch(repo) == "main"       # no origin remote → local main


def test_run_review_auto_detects_feature_vs_main(repo):
    res = run_review(repo)                       # no target flags
    assert res["auto_target"] == ("main", "feature")


def test_run_review_errors_on_default_branch(repo):
    _git(repo, "checkout", "-q", "main")
    with pytest.raises(RuntimeError, match="default branch"):
        run_review(repo)
