@AGENTS.md

## Claude Code notes

- Every code change: run the `arch-guard` sub-agent in mode `pre` before writing and `post` on the diff before
  commit; no commit on FAIL.
- After a UI change run the `screen-validator` skill (desktop sizes and a WebKit phone, light and dark; dt changes
  with `--ui dt`).
- Maintainer only: after a user-visible change run the `features-update` skill (needs the tracker DB); contributors
  describe the change in the PR.
- Personal, host-specific rules go in an untracked `CLAUDE.local.md` (ignored by git), never in this file.
