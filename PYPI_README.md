# Prism Semantic Reviewer

**Git tells you what *lines* changed. Prism tells you what those changes *mean*.**

Prism reads a Python web codebase and, for any pull request, turns a huge diff into a short, ranked list of the things that actually matter: new endpoints, new database writes, new external calls, and data that now leaves the system. It cuts through the noise of refactors and points you to the exact lines that matter.

## Install

```bash
pip install prism-semantic-reviewer
```

## Quick Start

### 1. Review a Pull Request, Branch, or Commit

Run a semantic review on a local repository or directly via a GitHub URL. You can compare specific branches, commits, or just point it at a PR number:

```bash
# Compare a local branch against master
prism review /path/to/repo --base master --head feature-branch

# Compare two specific commits
prism review /path/to/repo --base 9cca6d24 --head 34e8237b

# Review a GitHub PR directly
prism review https://github.com/owner/repo --pr 123
```

*Note: The CLI outputs a Markdown (`.md`) file by default. This is specifically designed for **GitHub Actions** so that Prism can automatically post the semantic review as a formatted comment directly in your GitHub Pull Request UI.*

### 2. Start the Interactive Web UI

If you are reviewing code locally, you can skip the markdown and use the built-in web server. It provides an interactive node-link graph visualization of your code changes.

```bash
prism serve --port 8765
# Open http://localhost:8765
```

### 3. Discover Invariants

Automatically discover and baseline properties of your codebase from its git history.

```bash
prism invariants /path/to/repo --snapshots 10
```

## What it detects

Prism tracks cross-boundary flows without needing to run your code:
- **API Routes** (new, modified, removed)
- **External API Calls** (e.g. Stripe, Twilio)
- **Database Writes**
- **Async Task Dispatches** (e.g. Celery)
- **Authentication Level** (e.g. AllowAny vs Authenticated)
- **PII Egress** (Personal data leaving the system)

For full documentation, architecture details, and instructions on deploying Prism as a GitHub Action, please visit the [Official GitHub Repository](https://github.com/AnkushSinghGandhi/prism).
