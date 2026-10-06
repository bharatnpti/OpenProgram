# Agent Instructions

This repo uses Lore Protocol for structured decision context in git commits.

Before modifying files or creating commits, use the repo-local `$lore-protocol` skill. Keep detailed Lore workflow guidance in `.agents/skills/lore-protocol/SKILL.md`, not in this root instruction file.

## Demo-only branches

- `demo/checkin-rounds`: the `demo_checkin_rounds` setting. Every fan-out asks
  every member again (a new round on the same date) and resets their status to
  "Awaiting this round's reply." Used for live demos where a day's check-in has
  to run several times. Never merge it into `main`; rebase it onto `main` when
  a demo needs it again, and run with `DEMO_CHECKIN_ROUNDS=true`.
