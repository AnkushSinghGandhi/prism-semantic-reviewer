#!/usr/bin/env python3
"""
Upsert the semantic review as a single sticky PR comment (create, or edit the previous one).

    python ci/post_review.py <pr_number> <body_file> [--dry-run]

Uses `gh api` (present on GitHub runners) with GITHUB_TOKEN. Finds a prior comment carrying
the hidden marker and PATCHes it, else POSTs a new one — so repeated pushes update in place
instead of piling up.
"""
import json
import os
import subprocess
import sys

MARKER = "<!-- semantic-review -->"


def gh_api(args, input_text=None):
    return subprocess.run(["gh", "api", *args], input=input_text, text=True,
                          capture_output=True)


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: post_review.py <pr_number> <body_file> [--dry-run]")
    pr, body_file = sys.argv[1], sys.argv[2]
    dry = "--dry-run" in sys.argv
    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
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


if __name__ == "__main__":
    main()
