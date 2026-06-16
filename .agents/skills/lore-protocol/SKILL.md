---
name: lore-protocol
description: Use Lore Protocol decision context in this repo. Trigger before modifying files, expanding a change scope, reviewing existing rationale, staging or committing changes, or deciding whether to add constraints, rejected alternatives, directives, tested gaps, or other Lore trailers.
---

# Lore Protocol

Use Lore to query structured decision context stored in git trailers and to create Lore-enriched commits. Keep this as a CLI and instruction workflow unless the project later adds a real Lore MCP server.

## Before Editing

Identify the files or directories you are about to change. For each target, run:

```sh
lore constraints <path> --json
lore rejected <path> --json
lore directives <path> --json
```

Apply the results directly:

- Treat `Constraint` as a hard requirement.
- Treat `Rejected` as an approach already tried and abandoned in the form `alternative | reason`.
- Treat `Directive` as standing future guidance.
- If the change scope grows, query the newly affected paths before editing them.

Use broader targets such as a feature directory only when exact files are not known yet. Once exact files are known, query those exact paths.

## During Investigation

Use these commands when rationale or decision history matters:

```sh
lore context <path> --json
lore why <file>:<line> --json
lore search --text "query" --json
lore stale <path> --json
lore trace <lore-id> --json
```

Do not add Codex MCP config for Lore unless a real MCP server is available. Lore is currently configured here through `.lore/config.toml`, this skill, and Cursor rules.

## When Committing

Stage only the intended files, then create the commit through `lore commit`. Prefer JSON input for deterministic commits:

```sh
echo '{
  "intent": "fix: handle null user in auth middleware",
  "body": "Previously threw 500 on null user. Now returns 401.",
  "trailers": {
    "Constraint": ["must not throw -- return 401 instead"],
    "Rejected": ["silent redirect to login | breaks API clients"],
    "Directive": ["keep API auth failures explicit"],
    "Confidence": "high",
    "Scope-risk": "narrow",
    "Reversibility": "clean",
    "Tested": ["null user returns 401", "valid user still works"],
    "Not-tested": ["concurrent request race condition"]
  }
}' | lore commit
```

Only include trailers that add useful future context:

- `Constraint`: a rule future changes must preserve.
- `Rejected`: an alternative plus reason, formatted as `alternative | reason`.
- `Directive`: future instruction for maintainers or agents.
- `Confidence`: `low`, `medium`, or `high`.
- `Scope-risk`: `narrow`, `moderate`, or `wide`.
- `Reversibility`: `clean`, `migration-needed`, or `irreversible`.
- `Tested`: what was verified.
- `Not-tested`: known gaps.
- `Supersedes`, `Depends-on`, `Related`: 8-character Lore IDs when linking decisions.

Run `lore doctor` after Lore-related configuration changes or when validating Lore metadata.
