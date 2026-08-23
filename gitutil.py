"""Tiny git helpers shared by the differ and the invariant tools (avoids import cycles)."""
import hashlib
import os
import subprocess
import tempfile

CACHE_ROOT = os.environ.get("PRISM_CACHE", os.path.expanduser("~/.cache/prism/repos"))
GIT_TIMEOUT = int(os.environ.get("PRISM_GIT_TIMEOUT", "180"))

# Never let git block forever on a credential prompt (private repo w/o token) — fail fast.
os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")


def sh(*args, check=False, timeout=GIT_TIMEOUT):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git command timed out after {timeout}s: {' '.join(args)}")
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git command failed: {' '.join(args)}")
    return r.stdout.strip()


def is_url(s):
    return s.startswith(("http://", "https://", "git@", "ssh://")) or s.endswith(".git")


def ensure_local(repo, update=True):
    """Accept a local path OR a git URL. URLs are cloned into a cache and reused; returns a
    local path either way — so every command that takes a `repo` also takes a GitHub link."""
    if not is_url(repo):
        return repo
    key = hashlib.sha1(repo.encode()).hexdigest()[:16]
    roots = [CACHE_ROOT]
    if "PRISM_CACHE" not in os.environ:
        roots.append(os.path.join(tempfile.gettempdir(), "prism-cache", "repos"))
    last_err = None
    for root in roots:
        dest = os.path.join(root, key)
        if os.path.isdir(os.path.join(dest, ".git")):
            if update:
                sh("git", "-C", dest, "fetch", "--quiet", "origin", check=True)
            return dest
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            last_err = e
            continue
        try:
            r = subprocess.run(["git", "clone", "--quiet", "--depth=1",
                                repo, dest], capture_output=True, text=True, timeout=GIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            last_err = RuntimeError(f"git clone timed out after {GIT_TIMEOUT}s for {repo}")
            continue
        if r.returncode == 0:
            return dest
        last_err = RuntimeError(f"git clone failed for {repo}: {r.stderr.strip()}")
    raise last_err or RuntimeError(f"git clone failed for {repo}")


def export(repo, ref, dest):
    """Extract a clean snapshot of `ref` into `dest` — working tree untouched."""
    if not ref:
        raise RuntimeError("cannot export an empty git ref")
    p = subprocess.Popen(["git", "-C", repo, "archive", ref], stdout=subprocess.PIPE)
    try:
        subprocess.run(["tar", "-x", "-C", dest], stdin=p.stdout, check=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        p.kill()
        raise RuntimeError(f"git archive timed out after {GIT_TIMEOUT}s for {ref}")
    finally:
        if p.stdout:
            p.stdout.close()
    if p.wait() != 0:
        raise RuntimeError(f"git archive failed for {ref}")
