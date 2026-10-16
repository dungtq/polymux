@AGENTS.md

## Claude Code notes

- Every code change: run the `arch-guard` sub-agent in mode `pre` before writing and `post` on the diff before
  commit; no commit on FAIL.
- After a UI change run the `screen-validator` skill (desktop sizes and iPhone WebKit, light and dark).
- After a user-visible feature change run the `features-update` skill so `FEATURES.md` stays true.
- Personal, host-specific rules go in an untracked `CLAUDE.local.md` (ignored by git), never in this file.
