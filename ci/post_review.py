#!/usr/bin/env python3
"""
Post the semantic review to a PR: a sticky summary comment, and (optionally) an inline comment
pinned to the exact changed line for every finding we can anchor.

    python ci/post_review.py <pr_number> <body_file> [--json review.json] [--inline] [--dry-run]

Sticky comment (always): find the prior comment carrying the hidden marker and PATCH it, else
POST a new one — repeated pushes update in place instead of piling up.

Inline (opt-in; needs --json + --inline): read the structured review and, for every finding whose
location lands on a line this PR actually changed, post a review comment anchored there. A finding
we can't anchor (its line isn't in the diff — GitHub would 422) is left to the sticky comment,
which always carries the full review. Prior Prism inline comments (marker-tagged) are deleted
first so pushes don't stack duplicates. We only pin a comment where we can prove the line changed.

Uses `gh api` (present on GitHub runners) with GITHUB_TOKEN.
"""
import json
import os
import subprocess
import sys

MARKER = "<!-- semantic-review -->"


def gh_api(args, input_text=None):
    return subprocess.run(["gh", "api", *args], input=input_text, text=True,
                          capture_output=True)


def post_sticky(repo, pr, body_file, dry):
    """Upsert the one summary comment (unchanged behavior)."""
    body = MARKER + "\n" + open(body_file, encoding="utf-8").read()
    existing_id = None
    if not dry:
        r = gh_api([f"repos/{repo}/issues/{pr}/comments", "--paginate"])
        if r.returncode == 0 and r.stdout.strip():
            for c in json.loads(r.stdout):
                if MARKER in (c.get("body") or ""):
                    existing_id = c["id"]
                    break

    payload = json.dumps({"body": body})
    if existing_id:
        method, url = "PATCH", f"repos/{repo}/issues/comments/{existing_id}"
    else:
        method, url = "POST", f"repos/{repo}/issues/{pr}/comments"

    if dry:
        print(f"[dry-run] {method} {url}\n[dry-run] body bytes: {len(body)}")
        print("-" * 60)
        print(body[:800] + ("…" if len(body) > 800 else ""))
        return

    r = gh_api(["-X", method, url, "--input", "-"], input_text=payload)
    if r.returncode != 0:
        sys.exit(f"gh api failed: {r.stderr}")
    print(f"{'updated' if existing_id else 'created'} sticky comment on PR #{pr}")


def _split_loc(where):
    """'path/to/file.py:123' -> ('path/to/file.py', 123); None if there's no usable line."""
    path, sep, line = (where or "").rpartition(":")
    if not sep or not line.isdigit():
        return None
    return path, int(line)


def build_inline_comments(review):
    """(comments, n_unanchored): at most one anchored comment per change.

    A change is anchored to the first of its `investigate` locations that falls on a changed
    line — preferring a specific fact line (a write/external/PII) over the handler definition.
    Changes with no in-diff location are counted as unanchored and left to the sticky comment."""
    changed = {p: set(v) for p, v in (review.get("changed_lines") or {}).items()}
    comments, unanchored = [], 0
    for c in review.get("changes", []):
        hits = []
        for i, inv in enumerate(c.get("investigate", [])):
            loc = _split_loc(inv.get("where", ""))
            if loc and loc[1] in changed.get(loc[0], ()):
                hits.append((i, loc, inv.get("fact", "")))
        if not hits:
            unanchored += 1
            continue
        facts = [h for h in hits if h[0] >= 1]          # index 0 is the handler def; >=1 is a fact
        _, (path, line), fact = (facts or hits)[0]
        head = f"{c.get('sev', '')} **{c.get('kind', '')}** — {c.get('why', '')}".strip()
        body = f"{MARKER}\n{head}" + (f"\n\n`{fact}`" if fact else "")
        comments.append({"path": path, "line": line, "side": "RIGHT", "body": body})
    return comments, unanchored


def post_inline(repo, pr, review, dry):
    """Post one PR review whose comments are pinned to changed lines; roll the rest into its body."""
    comments, unanchored = build_inline_comments(review)
    if not comments:
        print(f"[inline] nothing anchorable — {unanchored} finding(s) left to the sticky comment")
        return

    header = f"{MARKER}\n### ◭ Prism — {len(comments)} finding(s) pinned to changed lines"
    if unanchored:
        header += (f"\n\n{unanchored} more finding(s) touch code outside this diff — "
                   "see the summary comment for the full review.")
    payload = json.dumps({"event": "COMMENT", "body": header, "comments": comments})

    if dry:
        print(f"[dry-run] POST repos/{repo}/pulls/{pr}/reviews  "
              f"({len(comments)} inline, {unanchored} rolled up)")
        for c in comments:
            print(f"[dry-run]   {c['path']}:{c['line']}  {c['body'].splitlines()[1]}")
        return

    # delete our prior inline comments so repeated pushes don't stack duplicates
    r = gh_api([f"repos/{repo}/pulls/{pr}/comments", "--paginate"])
    if r.returncode == 0 and r.stdout.strip():
        for c in json.loads(r.stdout):
            if MARKER in (c.get("body") or ""):
                gh_api(["-X", "DELETE", f"repos/{repo}/pulls/comments/{c['id']}"])

    r = gh_api(["-X", "POST", f"repos/{repo}/pulls/{pr}/reviews", "--input", "-"], input_text=payload)
    if r.returncode != 0:
        print(f"::warning::could not post inline review: {r.stderr.strip()}")
        return
    print(f"posted {len(comments)} inline comment(s) on PR #{pr} ({unanchored} left to sticky)")


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    inline = "--inline" in args
    json_path = args[args.index("--json") + 1] if "--json" in args else None
    positional = [a for a in args if not a.startswith("--") and a != json_path]
    if len(positional) < 2:
        sys.exit("usage: post_review.py <pr_number> <body_file> "
                 "[--json review.json] [--inline] [--dry-run]")
    pr, body_file = positional[0], positional[1]
    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")

    post_sticky(repo, pr, body_file, dry)

    if inline:
        if not json_path or not os.path.exists(json_path):
            print("::warning::--inline needs --json <review.json>; skipping inline comments")
        else:
            post_inline(repo, pr, json.load(open(json_path, encoding="utf-8")), dry)


if __name__ == "__main__":
    main()
