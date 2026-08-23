"""Tiny git helpers shared by the differ and the invariant tools (avoids import cycles)."""
import hashlib
import os
import subprocess

CACHE_ROOT = os.environ.get("PRISM_CACHE", os.path.expanduser("~/.cache/prism/repos"))


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def is_url(s):
    return s.startswith(("http://", "https://", "git@", "ssh://")) or s.endswith(".git")


def ensure_local(repo, update=True):
    """Accept a local path OR a git URL. URLs are cloned into a cache and reused; returns a
    local path either way — so every command that takes a `repo` also takes a GitHub link."""
    if not is_url(repo):
        return repo
    key = hashlib.sha1(repo.encode()).hexdigest()[:16]
    dest = os.path.join(CACHE_ROOT, key)
    if os.path.isdir(os.path.join(dest, ".git")):
        if update:
            sh("git", "-C", dest, "fetch", "--all", "--tags", "--quiet")
    else:
        os.makedirs(CACHE_ROOT, exist_ok=True)
        r = subprocess.run(["git", "clone", "--quiet", repo, dest], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed for {repo}: {r.stderr.strip()}")
    return dest


def export(repo, ref, dest):
    """Extract a clean snapshot of `ref` into `dest` — working tree untouched."""
    p = subprocess.Popen(["git", "-C", repo, "archive", ref], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", dest], stdin=p.stdout, check=True)
    p.wait()
