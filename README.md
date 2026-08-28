# ◭ Prism — Semantic PR Review

**Git tells you what *lines* changed. Prism tells you what those changes *mean* — and where to look.**

Prism reads a Django/Python codebase and, for any pull request, branch, or commit, turns a huge
diff into a short, ranked list of the things that actually matter: new endpoints, new database
writes, new external calls, data that now leaves the system, and anything that breaks a rule your
team cares about. It points you at the exact lines to read — and it's honest about what it
couldn't figure out.

> One line: it makes it impossible for an important change to hide inside a large PR.

**📖 New here? Read [The Complete Guide](blog/prism-complete-guide.md)** — every feature, command,
flag, and concept explained in plain English, with screenshots.

**🏢 Want it on all your repos?** The guide's
[§10 — Set up Prism for all your repos](blog/prism-complete-guide.md#10-set-up-prism-for-all-your-repos)
is the step-by-step playbook — one central workflow, observe mode, the moat, and gating — for a
personal account, a team, or a whole org.

**What Prism finds:** new/changed endpoints & auth · new DB writes · external calls · **PII that
actually leaves** (traced, not guessed) · **dependency bumps that gain new powers** (network /
subprocess / native code) · PR **descriptions that contradict the code** · rules your codebase has
always followed (and the commit that broke one). Outputs to the terminal, a web UI, JSON, HTML,
**SARIF** (GitHub Security tab), inline PR comments, a triage label, and a Mermaid flow diagram.

---

## Install

```bash
pip install prism-semantic-reviewer      # gives you the `prism` command
```

Zero third-party dependencies (pure Python standard library). Needs **Python 3.8+**, plus **git**
and **tar** on the PATH.

*From source instead?* `git clone https://github.com/AnkushSinghGandhi/prism` and use
`python3 diff_pr.py …` / `python3 serve.py …` / `python3 invariants.py …` — the exact same
commands as `prism review` / `prism serve` / `prism invariants`.

---

## Commands

Prism has five commands:

| Command | What it does |
|---------|--------------|
| `prism review` | semantic diff of a PR / commit / branch range (for the terminal & CI) |
| `prism post` | post a review to a GitHub PR: sticky comment, inline comments, triage label |
| `prism serve`  | the interactive web UI (graph view), auto-selects your current repo |
| `prism invariants` | learn, **confirm**, enforce, and **blame** the rules your codebase follows |
| `prism digest` | org-wide roll-up: open PRs by `prism:*` label across every repo (for leadership) |

Run `prism review` with **no arguments** inside a repo to review the current branch vs. the default
branch. Full flag-by-flag reference: **[the Complete Guide](blog/prism-complete-guide.md)**.

The **repo** can be a **local path** *or* a **GitHub URL** (URLs are cloned into a cache at
`~/.cache/prism` and reused).

### `prism review`

```bash
# a GitHub Pull Request by number (works on a URL repo)
prism review https://github.com/owner/repo --pr 481

# a single commit (its diff vs its parent)
prism review /path/to/repo --commit 9cca6d24

# a merge commit (base = ^1, head = ^2)
prism review /path/to/repo --merge <merge-sha>

# any two refs — branches or commits
prism review /path/to/repo --base master --head feature-branch
prism review /path/to/repo --base 9cca6d24 --head 34e8237b
```

You get a ranked review — **🔴 security/data-flow · 🟠 money/payment · 🟡 new write/external ·
🟢 read-only or refactor** — and for each item: *why it matters*, the *flow*, the exact
*file:line* locations to inspect, and any *unknowns*. A rename with no behavior change shows as a
green "refactor, nothing to see" — facts are matched on the URL route, not class names, so
refactors don't create noise.

**Output formats** (Markdown is written by default, for CI comments):

```bash
prism review <repo> --pr 481 \
    --out review.md \        # Markdown (used by CI to post a sticky PR comment)
    --json review.json \     # structured data (for any other UI)
    --html review.html \     # a self-contained clickable report — open it in a browser
    --sarif review.sarif     # GitHub code scanning — findings on the changed line + Security tab
```

**Enforce your rules & gate CI:**

```bash
prism review <repo> --pr 481 \
    --invariants prism-invariants.json \   # confirmed rules to check
    --fail-on violation                    # exit non-zero on a new violation (or: crit)
```

With `--invariants`, the review *opens* with a 🛡 panel that separates violations this PR
**introduced** from pre-existing ones, and flags any **weakening** of a rule (e.g. a new allowed
external destination) as its own event.

Flags: `--pr` · `--commit` · `--merge` · `--base/--head` · `--out` · `--json` · `--html` ·
`--sarif` · `--invariants` · `--fail-on {violation,crit}`.
Set `GITHUB_TOKEN` for private repos / to avoid GitHub API rate limits.

### `prism serve` — the web UI

```bash
# run it inside a git repo → that repo is auto-selected, browser opens
cd /path/to/repo
prism serve
```

Three ways to open a review in the UI:

- **paste a GitHub PR link** (`https://github.com/owner/repo/pull/123`) and hit **Review PR**;
- **browse** a repo's PRs (Load PRs → pick one), or type a **PR #**, **commit sha**, or **base +
  head**;
- **preload from the command line** — the UI opens straight into it:

```bash
prism serve --pr 481             # open a PR on load
prism serve --commit 9cca6d24    # open a commit's diff on load
prism serve --merge <sha>        # open a merge commit
prism serve --base A --head B     # open a range
```

Each change shows an **interactive flow graph** (`route → handler → tables / external services /
background jobs`, colored by kind, PII flagged) plus a **Where-to-look** tab with exact
`file:line` locations.

Options: `--repo <path-or-url>` (default: the git repo of the current directory) · `--port 8765`
· `--invariants <file>` · `--no-open` (don't launch a browser).

### `prism invariants` — discover rules from history

```bash
prism invariants /path/to/repo --snapshots 8
```

Ranks properties by how long they've survived in git history (a rule that held for 800 commits
was almost certainly on purpose). Writes `invariants_report.md` (readable) and
`invariants.discovered.json` (a corpus with each rule, its history, and current exceptions). Set
`"confirmed": true` on the ones you want, then pass that file to `prism review --invariants`.

**Turn the moat on across many repos without hand-confirming each** —
`prism invariants <repo> --bootstrap --baseline org-baseline.json` auto-confirms the safe rules and
**freezes today's state as a baseline** (a ratchet — only *new* violations fire, existing debt is
grandfathered). Seed it with a shared org baseline (see
[`org-baseline.sample.json`](org-baseline.sample.json)) so every repo inherits the same universal
rules; each repo's own exceptions are filled in automatically. Auto-confirmed rules are tagged
`owner: auto-bootstrap` for later human review.

> Example finding: **"Authenticated access before any DB write"** held at 100% for most of the
> project's history, then dropped to 80% — here are the 4 endpoints that broke it, with locations.

### `prism digest` — org-wide roll-up for leadership

```bash
prism digest --org my-org --slack "$SLACK_WEBHOOK"
```

Turns per-PR reviews into one number a leader can read. It counts the **open** PRs across a whole
org that still carry each `prism:*` triage label — no re-analysis, and **no GitHub Advanced
Security** needed (it reads labels Prism already applied):

```
🔴  4  security / PII / unauth write
🟠  9  money path
🟡 12  new DB write / external
```

A tier it couldn't fetch shows `?`, never a fake `0`. Needs a `GITHUB_TOKEN` with org read
access. Add `--repos repos.txt` (one `owner/repo` per line) for an adoption line — how many repos
have the workflow live and a confirmed invariants file. Run it on a schedule and pipe it to Slack.

---

## GitHub Action

Get an automatic semantic review comment (and an optional merge gate) on every PR:

```yaml
# .github/workflows/prism.yml
name: Prism
on: { pull_request: {} }
permissions:
  contents: read
  pull-requests: write        # needed to post the comment
jobs:
  prism:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: AnkushSinghGandhi/prism@v1
        with:
          fail_on: violation                 # violation | crit | none (default: none)
          invariants: prism-invariants.json  # optional: enforce your confirmed rules
          comment: 'true'                     # post/update a sticky PR comment (default: true)
```

Inputs: `github_token` (default `${{ github.token }}`), `fail_on`, `invariants`, `comment`,
`inline`, `label`, `scan_deps`, `upload_sarif` (set `false` on private repos without GitHub
Advanced Security so the code-scanning upload is skipped and the check stays green). The Action
installs `prism-semantic-reviewer` from PyPI, reviews the PR, posts a sticky
comment, and (with `fail_on`) fails the check when the PR introduces a violation. With `inline`
(default `true`) it also pins each finding as an inline review comment on the exact changed line —
findings whose location isn't in the PR's diff stay in the sticky comment.

---

## Why this exists

AI writes a lot of code now. A single PR can be 60 files and 18,000 lines. A normal line-diff
hides the one important change inside thousands of mechanical ones. You don't need to read all of
it — you need to know **what changed** in behavior, **how it flows**, **where to look**, and
**whether it broke a promise** ("user data never leaves without auth"). Prism doesn't replace your
code or ask you to maintain diagrams; it re-reads the real code every time, so its view can't go
stale.

## What it looks at (the "lens")

For every HTTP endpoint it follows six **edges**:

| Edge | Question |
|------|----------|
| **route → handler** | which URL maps to which view? |
| **auth** | does it require login/permission? |
| **db tables** | which tables does it read and write? |
| **external calls** | does it call another service (payment, email, …)? |
| **async** | does it kick off a background job / thread / signal? |
| **PII** | does personal data travel toward something that leaves the app? |

**It never pretends to be sure.** Every fact is `✓ verified` / `⚠ potential` (found via a call,
or destination behind config) / `? unknown` / `n/a`. It will **never** say "no PII leaves here"
when it just didn't see it — absence is shown as *unknown*, not *safe*.

## The three layers

```
        WHAT IS                    WHAT SHOULD BE
     (derived facts)   ── gap ──   (rules you confirmed)
```

1. **Observation** — read the code, derive the facts (no upkeep).
2. **Attention** — for a PR, show what changed, how it flows, where to look.
3. **Intent** — learn the rules your code has always followed, confirm them, enforce them.

The product is the **gap** between what the code does and what you said it should do.

## Reading the output

- **Severity:** 🔴 look first (security/data-flow) · 🟠 money path · 🟡 new write/external · 🟢 safe/refactor.
- **Status:** ✓ sure · ⚠ probably (follow the location) · ? couldn't tell.
- **"via call":** the fact was found one hop from the handler (e.g. a write hidden in a service or
  model method) — still real; the location points at the actual line.

## What it does **not** see (on purpose)

- **Logic inside a function** — ordering, off-by-one, a wrong condition (tests catch those).
- **Very dynamic Python** — runtime-generated behavior, string-dispatched tasks — shown as ⚠/?.
- **Cross-process data flow** — once it goes through a queue/another service, static reading stops.
- **Object-level data flow** — a whole `user` object passed around, a field read deep inside → PII
  flagged as *potential*, never proven.

## What's in the repo

```
diff_pr.py     # prism review — the semantic diff engine (+ enforce + outputs)
serve.py       # prism serve  — the web UI / API
invariants.py  # prism invariants — discover rules from git history
digest.py      # prism digest — org-wide roll-up from prism:* labels (no GHAS needed)
enforce.py     # check a PR against confirmed rules
deps.py        # dependency capability-delta scan (bumped deps that gain new powers)
extractor/     # reads code → facts (the 6-edge lens)
gitutil.py     # git helpers (URL clone/cache, repo detection)
cli.py         # the `prism` entry point
web/           # app.html (live UI + graph), viewer.template.html (offline report)
ci/            # post_review.py (sticky PR comment), reusable workflow
action.yml     # the published GitHub Action
```

## License

[Elastic License 2.0](LICENSE) — free to use, self-host internally, and modify; you may **not**
resell it or offer it as a hosted service.
