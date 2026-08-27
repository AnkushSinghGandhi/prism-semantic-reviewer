"""Dependency capability-delta (#5): parse a manifest bump, scan each version's source for what it
can DO, and diff. Pure pieces are tested offline with a fake source_provider (no PyPI network)."""
import json
import subprocess
import deps
from deps import (parse_requirements, parse_pipfile_lock, parse_poetry_lock, diff_manifests,
                  scan_source_caps, capability_delta, severity_for, analyze_dependency_changes,
                  render_deps_section, pypi_source_provider, is_manifest)


# ---- manifest parsing ----

def test_parse_requirements_pins_and_specs():
    m = parse_requirements("requests==2.31.0\nDjango>=4.0  # comment\n-e .\ngit+https://x/y\nFlask[async]==3.0")
    assert m["requests"] == "2.31.0" and m["django"] == ">=4.0" and m["flask"] == "3.0"
    assert "-e" not in m and "y" not in m


def test_parse_pipfile_lock_reads_default_and_develop():
    text = json.dumps({"default": {"requests": {"version": "==2.31.0"}},
                       "develop": {"pytest": {"version": "==8.0.0"}}})
    m = parse_pipfile_lock(text)
    assert m == {"requests": "2.31.0", "pytest": "8.0.0"}


def test_parse_poetry_lock_reads_package_blocks():
    text = ('[[package]]\nname = "requests"\nversion = "2.31.0"\n\n'
            '[[package]]\nname = "urllib3"\nversion = "2.2.1"\n')
    assert parse_poetry_lock(text) == {"requests": "2.31.0", "urllib3": "2.2.1"}


def test_diff_manifests_reports_only_changes():
    d = diff_manifests({"a": "1", "b": "1"}, {"a": "2", "b": "1", "c": "1"})
    assert d == {"a": ("1", "2"), "c": (None, "1")}       # bump + add; unchanged b omitted


def test_is_manifest_matches_known_files():
    assert is_manifest("requirements.txt") and is_manifest("app/requirements-dev.txt")
    assert is_manifest("Pipfile.lock") and is_manifest("poetry.lock")
    assert not is_manifest("setup.py")


# ---- capability scanning ----

def _pkg(tmp_path, name, code):
    d = tmp_path / name
    d.mkdir()
    (d / "mod.py").write_text(code)
    return str(d)


def test_scan_source_caps_detects_each_capability(tmp_path):
    code = ("import subprocess, requests, ctypes\n"
            "import os\n"
            "def f():\n"
            "    os.system('ls')\n"
            "    open('x','w')\n"
            "    eval('1')\n"
            "    os.getenv('SECRET')\n")
    caps = scan_source_caps(_pkg(tmp_path, "p", code))
    assert caps == {"subprocess", "network", "native-code", "filesystem-write",
                    "dynamic-exec", "env-access"}


def test_scan_ignores_reads_and_unparseable(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "ok.py").write_text("import os\nopen('x')\nopen('y','r')\n")    # reads only
    (d / "py2.py").write_text("print 'hi'\n")                            # syntax error → skipped
    assert scan_source_caps(str(d)) == set()


def test_capability_delta_and_severity():
    assert capability_delta({"network"}, {"network", "subprocess"}) == {"subprocess"}
    assert severity_for({"subprocess"}) == "🔴"
    assert severity_for({"network"}) == "🟠"
    assert severity_for(set()) == "🟢"


# ---- top-level analysis with an injected (offline) provider ----

def test_analyze_flags_newly_gained_capability(tmp_path):
    old = _pkg(tmp_path, "old", "import requests\n")                      # network only
    new = _pkg(tmp_path, "new", "import requests, subprocess\n")          # + subprocess
    provider = lambda n, v: {"1.0": old, "2.0": new}.get(v)               # noqa: E731
    out = analyze_dependency_changes({"leftpad": "1.0"}, {"leftpad": "2.0"},
                                     source_provider=provider)
    assert len(out) == 1 and out[0]["sev"] == "🔴" and out[0]["gained"] == ["subprocess"]


def test_analyze_clean_bump_is_green(tmp_path):
    same = _pkg(tmp_path, "s", "import requests\n")
    out = analyze_dependency_changes({"requests": "2.28"}, {"requests": "2.31"},
                                     source_provider=lambda n, v: same)
    assert out[0]["sev"] == "🟢" and out[0]["gained"] == [] and "no new capabilities" in out[0]["why"]


def test_analyze_unresolved_is_warned_not_assumed_safe():
    out = analyze_dependency_changes({"x": "1.0"}, {"x": "2.0"}, source_provider=None)
    assert out[0]["unresolved"] is True and "not statically resolved" in out[0]["why"]
    assert "⚠" in render_deps_section(out)


def test_pypi_provider_skips_non_pinned_versions_offline():
    assert pypi_source_provider("requests", "*") is None      # a range → nothing to scan, no network
    assert pypi_source_provider("requests", "") is None


# ---- integration through git (offline: PRISM_SCAN_DEPS=0 → ⚠ unresolved) ----

def test_dependency_findings_detects_a_bump(tmp_path, monkeypatch):
    import diff_pr
    d = str(tmp_path / "r")

    def git(*a):
        subprocess.run(["git", "-C", d, *a], check=True, capture_output=True, text=True)

    subprocess.run(["mkdir", "-p", d], check=True)
    subprocess.run(["git", "init", "-q", d], check=True)
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "t")
    (tmp_path / "r" / "requirements.txt").write_text("requests==2.28.0\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    (tmp_path / "r" / "requirements.txt").write_text("requests==2.31.0\n")
    git("commit", "-aqm", "bump")

    monkeypatch.setenv("PRISM_SCAN_DEPS", "0")                # force offline → ⚠, no network
    out = diff_pr.dependency_findings(d, "HEAD~1", "HEAD")
    assert len(out) == 1 and out[0]["name"] == "requests"
    assert out[0]["old"] == "2.28.0" and out[0]["new"] == "2.31.0"
    assert out[0]["unresolved"] and out[0]["manifest"] == "requirements.txt"
