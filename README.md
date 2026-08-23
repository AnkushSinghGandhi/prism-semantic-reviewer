# Prism

> Understand and control an AI-built codebase without reading it line by line.

## The idea

AI writes most of the syntax now. We work at the level of logic, architecture, and data flow —
and we've largely stopped reading the code the agent actually produced. We ship implementations
we don't fully understand. That's a control problem: code only gives us control if we understand
what it does.

**Prism** is a *continuously derived semantic layer* over your source code. It is **not** another
document to maintain — it is regenerated from the code on every commit, so it can never go stale.
For any pull request it answers three questions:

- **What actually changed?** — new endpoints, new database writes, new external calls, personal
  data that now leaves the system. The handful of things that matter, not thousands of lines of
  noise.
- **How does that behavior flow?** — the path through the code, with the exact lines to inspect.
- **Did it break a promise?** — rules your codebase has always followed, checked automatically,
  with any *weakening* of a rule flagged loudly.

## Why this approach works

> Don't create another source of truth. Derive a semantic view **of** the source of truth.

Every previous attempt at "abstracting the code" (UML, low-code, spec-first) died the same way:
the abstraction drifts from the code and they disagree forever. Prism avoids that by never
authoring anything — it only *derives*:

- **Can't go stale** — regenerated from the code on every commit.
- **Mechanically checked** — facts come from static analysis, not a model guessing.
- **Honest about the unknown** — it never says "safe" when it simply didn't see something.
  Absence is shown as *unknown*, never as *all clear*. A wrong "all clear" is dangerous; a loud
  "not sure, look here" is just useful.
- **Always drillable** — every fact links to the exact source line.

## The shape of it

```
        WHAT IS                      WHAT SHOULD BE
     (derived facts)     ── gap ──   (rules you confirmed)
                            │
                       the product
```

The product is the **gap** between what the code does and what you said it should do.

*In one line: it makes it impossible for an important change to hide inside a large PR.*

## Status

This `main` branch holds the vision. The working implementation — a static analyzer, a semantic
PR diff, historical invariant discovery + enforcement, a GitHub Action, and a live web viewer —
lives on the **[`dev`](../../tree/dev)** branch.
