# Prism Semantic Reviewer

**Git tells you what *lines* changed. Prism tells you what those changes *mean*.**

Prism reads a Python web codebase and, for any pull request, commit, or branch, turns a huge diff
into a short, ranked list of the things that actually matter: new endpoints, new database writes,
new external calls, and data that now leaves the system. It cuts through refactor noise and points
you at the exact lines that matter — and it's honest about what it couldn't resolve.

## Install

```bash
pip install prism-semantic-reviewer
```

Zero third-party dependencies. Needs Python 3.8+, plus `git` and `tar` on the PATH.

## Commands

The repo argument can be a **local path** or a **GitHub URL** (URLs are cloned & cached).

### `prism review` — review a PR, commit, or range

```bash
prism review https://github.com/owner/repo --pr 481      # a GitHub PR by number
prism review /path/to/repo --commit 9cca6d24             # a single commit (vs its parent)
prism review /path/to/repo --merge <merge-sha>           # a merge commit
prism review /path/to/repo --base master --head feature  # any two refs / commits
```

Output formats and CI gating:

```bash
prism review <repo> --pr 481 \
    --out review.md \                     # Markdown (default; used by CI to post a PR comment)
    --json review.json --html review.html # structured JSON + a self-contained clickable report

prism review <repo> --pr 481 \
    --invariants prism-invariants.json \  # enforce your confirmed rules
    --fail-on violation                   # exit non-zero on a new violation (or: crit)
```

Set `GITHUB_TOKEN` for private repos / to avoid GitHub API rate limits.

### `prism serve` — interactive web UI (with graph view)

```bash
cd /path/to/repo && prism serve      # auto-selects the current git repo, opens a browser
```

In the UI: paste a **GitHub PR link**, or type a **PR #**, **commit sha**, or **base + head**, or
**browse** the repo's PRs. Each change gets an interactive **flow graph**
(`route → handler → tables / external / jobs`) and a list of exact `file:line` locations.

Preload a review straight from the command line:

```bash
prism serve --pr 481          # open a PR on load
prism serve --commit <sha>    # open a commit's diff on load
prism serve --base A --head B # open a range
```

Options: `--repo <path-or-url>` · `--port 8765` · `--invariants <file>` · `--no-open`.

### `prism invariants` — discover rules from git history

```bash
prism invariants /path/to/repo --snapshots 10
```

Ranks properties by how long they've held across history, writing `invariants_report.md` and
`invariants.discovered.json`. Confirm the ones you want and feed them to `prism review --invariants`.

## What it detects (without running your code)

- **API routes** (new / modified / removed) and their **auth level** (e.g. AllowAny vs Authenticated)
- **Database reads & writes**
- **External API calls** (Stripe, Twilio, PayTM, …)
- **Async task dispatches** (Celery, threads, signals)
- **PII egress** — personal data leaving the system

Each fact is marked `✓ verified` / `⚠ potential` / `? unknown` — it never reports "safe" for
something it didn't actually trace.

## GitHub Action

```yaml
# .github/workflows/prism.yml
name: Prism
on: { pull_request: {} }
permissions: { contents: read, pull-requests: write }
jobs:
  prism:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: AnkushSinghGandhi/prism@v1
        with:
          fail_on: violation   # violation | crit | none
```

Full documentation, the graph UI, and architecture details:
[GitHub repository](https://github.com/AnkushSinghGandhi/prism).

## License

Elastic License 2.0 — free to use, self-host, and modify; not to resell or offer as a hosted service.
