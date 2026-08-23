# Semantic Review

**Git tells you what *lines* changed. This tool tells you what those changes *mean* — and where to look.**

Semantic Review reads a Django/Python codebase and, for any pull request, turns a huge diff into
a short, ranked list of the things that actually matter: new endpoints, new database writes, new
external calls, data that now leaves the system, and anything that breaks a rule your team cares
about. It points you at the exact lines to read — and it's honest about what it couldn't figure
out.

---

## Why this exists

AI writes a lot of code now. A single PR can be 60 files and 18,000 lines. Reading it top to
bottom is no longer realistic, and a normal line-diff actively hides the one important change
inside thousands of mechanical ones.

You don't need to read all of it. You need to know:

1. **What actually changed** at the level of system behavior?
2. **How does that behavior flow** through the code?
3. **Where exactly do I look** to verify it?
4. **Did it break a promise** we made (e.g. "user data never leaves without auth")?

That's what this tool answers. It does **not** replace your code, generate architecture for you,
or ask you to maintain diagrams. It reads the real code every time, so its view can never go
stale.

---

## What it looks at (the "lens")

For every HTTP endpoint it follows six things — we call them **edges**:

| Edge | Question it answers |
|------|---------------------|
| **route → handler** | which URL maps to which view? |
| **auth** | does this endpoint require login/permission? |
| **db tables** | which tables does it read and write? |
| **external calls** | does it call out to another service (payment gateway, email, etc.)? |
| **async** | does it kick off a background job / thread / signal? |
| **PII** | does personal data (email, phone, address…) travel toward something that leaves the app? |

### It never pretends to be sure

Every fact carries a status, and this is the most important design choice in the whole tool:

- **✓ verified** — resolved with confidence
- **⚠ potential** — found, but only by following a call, or the destination is behind a config
  value; treat as "probably, look here"
- **? unknown** — couldn't resolve statically; needs a human or runtime
- **n/a** — doesn't apply

It will **never** say "no personal data leaves here" when it simply didn't see it. Absence is
shown as *unknown*, not as *safe*. A wrong "all clear" is dangerous; a loud "not sure, look
here" is just useful.

---

## The three layers

```
        WHAT IS                      WHAT SHOULD BE
     (derived facts)     ── gap ──   (rules you confirmed)
                            │
                       the product
```

1. **Observation** — read the code, derive the facts. No human upkeep.
2. **Attention** — for a PR, show what changed, how it flows, and where to look.
3. **Intent** — learn the rules your codebase has always followed, let you confirm them, then
   check every PR against them.

The product is the **gap** between what the code does and what you said it should do.

---

## What's in the box

```
semantic-review/
├── extractor/analyzer.py   # reads code → facts (the 6-edge lens). Pure Python, no installs.
├── run.py                  # run the extractor on a repo; prints coverage + writes facts.json
├── diff_pr.py              # the main command: semantic diff of a PR (+ enforce + outputs)
├── invariants.py           # learn candidate rules from git history
├── enforce.py              # check a PR against confirmed rules
├── serve.py                # live web service: pick a PR, review on demand, graph view
├── gitutil.py              # small git helpers
├── web/
│   ├── app.html            # the live web app (with the interactive graph)
│   └── viewer.template.html# the self-contained, generate-and-open report
└── ci/
    ├── semantic-review.yml # GitHub Action (drop into any repo)
    └── post_review.py      # posts the review as one sticky PR comment
```

---

## Requirements

- **Python 3.9+** (tested on 3.10) — **no pip packages needed**, standard library only.
- **git** and **tar** (used to snapshot a commit without touching your working copy).
- To review a **GitHub URL / PR**: network access; optionally `GITHUB_TOKEN` in the environment
  (needed for private repos and to avoid API rate limits). Local repos need none of this.
- For posting PR comments in CI: the **`gh`** CLI (already present on GitHub runners).
- The code it analyzes should be **Django / Django REST Framework** (it understands Django URLs,
  the ORM, DRF permissions, Celery, and signals).

---

## Install

There's nothing to build or install. Copy the `semantic-review/` folder somewhere and run it:

```bash
git clone <this> semantic-review     # or just copy the folder
cd semantic-review
python3 run.py --help
```

---

## Using it

### 1. See the facts for a whole codebase

```bash
python3 run.py /path/to/django-repo
```

You get a per-edge coverage table (how much it could resolve) and a `facts.json` you can feed to
anything else. Good for a first look and for measuring how well it "sees" your code.

### 2. Review a pull request, branch, or commit (the main use)

The repo can be a **local path or a GitHub URL** — a URL is cloned into a cache (`~/.cache/prism`)
and reused on later runs, so you don't have to clone anything by hand.

By merge commit:

```bash
python3 diff_pr.py /path/to/repo --merge <merge-commit-sha>
```

By two refs (branches or specific commits):

```bash
# Compare a local branch to master
python3 diff_pr.py /path/to/repo --base master --head feature-branch

# Compare two specific commit hashes
python3 diff_pr.py /path/to/repo --base 9cca6d24 --head 34e8237b
```

Straight from GitHub — by URL, and even by real PR number:

```bash
python3 diff_pr.py https://github.com/owner/repo --pr 481
python3 diff_pr.py https://github.com/owner/repo --merge <sha>
```

`--pr` looks up the PR's base and head via the GitHub API (set `GITHUB_TOKEN` to avoid rate
limits / for private repos) and fetches the PR head automatically.

You get a ranked review — **🔴 security/data-flow · 🟠 money/payment · 🟡 new write/external ·
🟢 read-only or pure refactor** — and for each item: *why it matters*, the *flow*, the exact
*file:line locations to inspect*, and any *unknowns*. A rename with no behavior change shows up
as a green "refactor, nothing to see," because facts are matched on the URL route, not on class
names — so refactors don't create noise.

Add outputs:

```bash
python3 diff_pr.py /path/to/repo --merge <sha> \
    --out review.md \        # Markdown (designed specifically to be posted as a comment in CI/CD)
    --json review.json \     # structured data (for any other UI)
    --html review.html       # a self-contained clickable report — just open it in a browser
```

*Note on Markdown files: Prism generates an `.md` report by default. This is specifically meant to be used by GitHub Actions (or other CI/CD tools) to automatically post a formatted semantic review as a sticky comment directly in the GitHub PR interface. If you are reviewing code locally, you should use the Web UI instead.*

### 3. Open the interactive report

Open the generated `review.html` in any browser (no server needed — the data is baked in). It's
a three-level drill-down:

- **WHAT** — the ranked list of changes, filterable by severity; rule violations pinned at the top.
- **HOW** — click a change to see the behavior flow as a chain: `route → handler → {tables} → external`.
- **WHERE** — a list of the exact `file:line` spots to read, reaching across files into services
  and models — plus the unresolved edges highlighted in amber.

### 4. Learn and enforce your team's rules

**Discover** the properties your codebase has consistently held (ranked by how long they've
survived in git history — a rule that held for 800 commits was almost certainly on purpose):

```bash
python3 invariants.py /path/to/repo --snapshots 8
```

This writes `invariants_report.md` (readable) and `invariants.discovered.json` (a corpus with
each rule, its history, and its current exceptions). Example finding:

> **"Authenticated access before any DB write"** held at 100% for most of the project's history,
> then dropped to 80% — and here are the 4 endpoints that broke it, with their locations.

**Confirm** the ones that were intentional by editing the JSON (`"confirmed": true`). That
confirmed file is the valuable part — it's your team's specific contract, and it compounds over
time.

**Enforce** it on every PR:

```bash
python3 diff_pr.py /path/to/repo --base <base> --head <head> \
    --invariants invariants.confirmed.json --fail-on violation
```

Now the review *opens* with a 🛡 Invariants panel. It separates violations this PR **introduced**
from ones that were already there, and — importantly — flags any **weakening** of a rule (e.g.
adding a new allowed external destination) as its own loud event. `--fail-on violation` makes the
command exit non-zero so a CI check fails when a PR introduces a violation.

### 5. Run it as a live web app (with the graph view)

Instead of generating a file per PR, run a small local server and browse:

```bash
python3 serve.py --repo /path/to/repo --invariants invariants.confirmed.json
# → open http://127.0.0.1:8765
```

It's a normal web app (still zero dependencies — Python's built-in server):

- enter a **local path or a GitHub URL**, click **Load PRs**, pick a merged PR (or type a
  **GitHub PR #**, or a base + head), hit **Review**;
- the analysis runs on demand (a few seconds) and is cached;
- you get the ranked change list, the invariant panel, and — the new part — an **interactive
  flow graph** for each change: a node-link diagram showing `route → handler → tables / external
  services / background jobs`, colored by kind (table reads gray, writes amber, external calls
  red, async purple), with a red dot marking a handler that touches personal data. A second tab
  gives the exact `file:line` list. Hover a node for its auth or location.

This is the same data as the generated report — just live, clickable, and visual.

---

## Deploying it in CI (GitHub)

This makes every PR get an automatic semantic review as a comment, plus a downloadable
interactive report, plus a check that fails on new rule violations.

1. **Vendor the tool** into the target repo at `tools/semantic-review/` (copy the `extractor/`,
   `diff_pr.py`, `invariants.py`, `enforce.py`, `gitutil.py`, `web/`, and `ci/` here).
2. **Add the workflow**: copy `ci/semantic-review.yml` to `.github/workflows/semantic-review.yml`.
3. **(Optional) Add your rules**: drop your confirmed corpus at the repo root as
   `semantic-review-invariants.json`. If present, the workflow enforces it automatically.

That's it. On every PR the Action will:

- snapshot the base and head commits,
- generate the review (`md` + `json` + `html`),
- post/update **one sticky comment** on the PR,
- upload the interactive `html` as a build artifact,
- and **fail the check** if the PR introduces a rule violation (the comment still posts first).

No secrets to configure — it uses the built-in `GITHUB_TOKEN`.

---

## Reading the output

- **Severity:** 🔴 look first (security / data-flow) · 🟠 money path · 🟡 new write or external
  dependency · 🟢 safe/refactor.
- **Statuses:** ✓ sure · ⚠ probably (follow the location) · ? couldn't tell.
- **"via call":** the fact was found one function-hop away from the handler (e.g. a write hidden
  inside a service or model method) — still real, just indirect. The location points at the real
  line.

---

## What it does **not** see (on purpose)

Being clear about the blind spots is part of the trust:

- **Logic inside a function** — ordering, off-by-one, a wrong condition. Types and tests catch
  those; this tool routes attention, it doesn't verify correctness.
- **Very dynamic Python** — behavior generated at runtime, string-dispatched tasks, heavy
  metaprogramming. These show up as ⚠/? rather than being guessed.
- **Data flow across process boundaries** — once something goes through a queue or another
  service, static reading stops (that's a future "runtime" enrichment).
- **Object-level data flow** — when a whole `user` object is passed around and a field is read
  deep inside, PII is flagged as *potential*, never proven.

If it can't see something, it says so and points you at the code. That honesty is the point.

---

## Current limits & where it can grow

- Follows one function-hop today; a second hop would resolve more external-call destinations.
- Django/DRF only for now; the same approach extends to other frameworks and languages.
- The live service (`serve.py`) runs locally; hosting it as a shared team service, plus a
  whole-PR "blast radius" graph across endpoints, are natural next steps.
- Runtime traces (OpenTelemetry) would close the gaps static reading can't — the paths that
  cross services and queues.

---

*In one line: it makes it impossible for an important change to hide inside a large PR.*
