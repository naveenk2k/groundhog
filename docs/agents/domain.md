# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the domain glossary (what a "verdict," "corpus," "watch threshold," etc. mean and how the pieces relate).
- **`DECISIONS.md`** at the repo root — this repo's ADR log (used in place of a `docs/adr/` directory; same Decision/Why structure, just kept as one running file rather than one-file-per-decision).

If either file is missing something you need, that's a signal — either you're inventing language/reasoning the project doesn't use (reconsider) or there's a real gap worth noting.

## File structure

Single-context repo (this repo):

```
/
├── CONTEXT.md
├── DECISIONS.md   ← plays the docs/adr/ role for this repo
├── PLAN.md        ← narrative design doc, not always kept in sync with the code
├── extension/
└── companion/
```

This repo has no `CONTEXT-MAP.md` and is not a monorepo — don't look for per-context docs under `src/`.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

## Flag decision-log conflicts

If your output contradicts an existing entry in `DECISIONS.md`, surface it explicitly rather than silently overriding:

> _Contradicts the "Transcript retrieval" decision in DECISIONS.md — but worth reopening because…_

## Known stale spot

`PLAN.md` describes the scoring model as Claude/Haiku; the shipped code (`companion/verdict.py`) uses Gemini instead. `CONTEXT.md` documents the actual (Gemini) behavior — trust it over `PLAN.md` on this point.
