"""Tiny git helpers shared by the differ and the invariant tools (avoids import cycles)."""
import subprocess


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def export(repo, ref, dest):
    """Extract a clean snapshot of `ref` into `dest` — working tree untouched."""
    p = subprocess.Popen(["git", "-C", repo, "archive", ref], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", dest], stdin=p.stdout, check=True)
    p.wait()
