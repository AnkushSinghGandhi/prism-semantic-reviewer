#!/usr/bin/env python3
"""
prism digest — an org-wide leadership roll-up, built from the triage labels Prism already applies
to each PR. No re-analysis, no GitHub Advanced Security, no per-PR data collection: it just counts
the open PRs across an org that still carry each `prism:*` label.

    prism digest --org careers360
    prism digest --org careers360 --repos repos.txt --slack "$SLACK_WEBHOOK"

For each severity tier Prism labels a PR with — 🔴security / 🟠payment / 🟡new-write — count the
*open* PRs org-wide that still carry it. That's "risky changes sitting in review right now", in one
line, for someone who never opens a PR. With --repos it also reports adoption (how many repos have
the workflow live) and moat status (how many ship a confirmed invariants file).

Stay honest: a count we couldn't fetch is rendered `?`, never `0` — same rule as the analyzer, an
unknown is never shown as a zero.

Pure pieces (build_digest / slack_payload) take plain data so they unit-test offline; the GitHub
reads and the Slack POST are the only network calls and are injectable for tests.
"""
import argparse
import json
import os
import sys
import urllib.request
from urllib.parse import quote

# Single source of truth for the label names is the poster that applies them.
try:
    from ci.post_review import LABELS
except Exception:                                   # pragma: no cover - import fallback
    LABELS = {"🔴": "prism:🔴security", "🟠": "prism:🟠payment",
              "🟡": "prism:🟡new-write", "🟢": "prism:🟢safe-refactor"}

# Tiers a leader cares about (🟢 safe-refactor is noise for a digest — omitted).
DISPLAY = ["🔴", "🟠", "🟡"]
SEV_DESC = {"🔴": "security / PII / unauth write", "🟠": "money path",
            "🟡": "new DB write / external"}

WORKFLOW_PATH = ".github/workflows/prism.yml"       # adoption marker
INVARIANTS_PATH = "prism-invariants.json"           # moat marker


def github_get(url):
    """GET a GitHub REST URL as parsed JSON. Uses GITHUB_TOKEN (needed for org-wide search and
    private repos). Raises on any non-200 so callers can tell 'unknown' from 'zero'."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "prism"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    return json.load(urllib.request.urlopen(req, timeout=30))


def severity_counts(org, fetch=github_get):
    """{sev: open-PR-count} across `org`, one search per tier. A tier we couldn't fetch is None
    (unknown) rather than 0, so the digest never claims 'all clear' when it just failed to look."""
    counts = {}
    for sev in DISPLAY:
        q = f'org:{org} is:pr is:open label:"{LABELS[sev]}"'
        try:
            data = fetch("https://api.github.com/search/issues?q=" + quote(q) + "&per_page=1")
            counts[sev] = int(data.get("total_count", 0))
        except Exception:
            counts[sev] = None
    return counts


def repo_has_file(full_name, path, fetch=github_get):
    """True if `path` exists in `full_name` (owner/repo) on its default branch."""
    try:
        fetch(f"https://api.github.com/repos/{full_name}/contents/{quote(path)}")
        return True
    except Exception:
        return False


def adoption_counts(repos, fetch=github_get):
    """{total, live, moat} over an explicit repo list: how many have the workflow, how many ship a
    confirmed invariants file. Used only when --repos is given (org search can't see file paths)."""
    live = sum(1 for r in repos if repo_has_file(r, WORKFLOW_PATH, fetch))
    moat = sum(1 for r in repos if repo_has_file(r, INVARIANTS_PATH, fetch))
    return {"total": len(repos), "live": live, "moat": moat}


def build_digest(org, counts, adoption=None):
    """Render the digest text (pure). `?` for an unknown (unfetched) tier — never a fake 0."""
    def fmt(v):
        return "?" if v is None else str(v)
    lines = [f"*Prism digest — {org}*  (open PRs by risk)"]
    for sev in DISPLAY:
        lines.append(f"{sev} {fmt(counts.get(sev)):>3}  {SEV_DESC[sev]}")
    if adoption:
        lines.append(f"adoption: {adoption['live']}/{adoption['total']} repos live · "
                     f"invariants confirmed in {adoption['moat']}")
    return "\n".join(lines)


def slack_payload(text):
    return {"text": text}


def post_slack(webhook, text):
    body = json.dumps(slack_payload(text)).encode("utf-8")
    req = urllib.request.Request(webhook, data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30)


def _read_repo_list(path):
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def run(org, repos_file=None, slack=None):
    counts = severity_counts(org)
    adoption = adoption_counts(_read_repo_list(repos_file)) if repos_file else None
    text = build_digest(org, counts, adoption)
    print(text)
    if slack:
        post_slack(slack, text)
        print("\n[posted to Slack]")
    return text


def main():
    ap = argparse.ArgumentParser(
        prog="prism digest",
        description="Org-wide leadership roll-up from the labels Prism already applies to PRs.")
    ap.add_argument("--org", required=True, help="GitHub org/owner to summarize")
    ap.add_argument("--repos", help="optional file of owner/repo lines → adds an adoption line")
    ap.add_argument("--slack", help="Slack incoming-webhook URL to post the digest to")
    args = ap.parse_args()
    if not os.environ.get("GITHUB_TOKEN"):
        print("warning: no GITHUB_TOKEN set — org search will be rate-limited or empty",
              file=sys.stderr)
    run(args.org, repos_file=args.repos, slack=args.slack)


if __name__ == "__main__":
    main()
