---
name: arch-guard
description: >-
  Call the arch-guard sub-agent before and after every code change in pm so new code lands in the right
  package, reuses the shared helpers and stays within the size limits of docs/ARCHITECTURE.md. Use before
  writing or delegating any Python, template, CSS or JS change (mode pre) and after it is written, before
  commit/merge (mode post). Also use when the user asks to check architecture, structure, placement or
  duplication — even if they do not say the word skill.
---

# arch-guard: check placement before, verify structure after

`docs/ARCHITECTURE.md` is the rulebook (target layout, layer rules, shared helpers, size limits,
migration status). For dt theme files (`src/pm/web/dt`, `src/pm/web/themes/dt`, `tests/web/dt`) the sub-agent
also applies `docs/themes/dt/CONDUCT.md` (`V7-*` ids), `docs/themes/dt/SHELL.md` and `docs/themes/dt/CODE.md`;
classic UI files follow `docs/UI-RULES.md`. The two themes' rules never cross. The `arch-guard` sub-agent is
read-only and applies them. Skip only for changes that touch nothing but Markdown docs.

## Flow (main agent)

1. **Before** — `Agent(subagent_type="arch-guard", prompt="mode: pre\nworktree: <path>\ntask: <what and why>")`.
   Pass its **Owner**, **Reuse**, **Rules** and **Tests** sections to the agent that writes the code (or use
   them yourself) as the file list and contract. If it says to split an oversized file, that split is part of
   the task.
2. **Implement** — the agent that writes the code (or you), with tests.
3. **After** — `Agent(subagent_type="arch-guard", prompt="mode: post\nworktree: <path>\nbase: main\ntask: <same>")`.
   - `PASS` → continue with tests, screen-validator (UI changes), commit; describe user-visible changes in the
     PR (the maintainer adds the CHANGELOG / FEATURES entries).
   - `FAIL` → fix the findings (or hand them back to the agent that wrote the code), then run `post` again.
     Do not commit on FAIL. Stop after 3 rounds and show the remaining findings to the user.
4. Mention the final verdict in the report (one line).

Several independent tasks: one `pre` per task (they may run in parallel), one `post` per task's diff.

## Rules for the sub-agent

- Read-only: never edits, commits or runs the app. Never reads pm's token, TOTP or dev-token.
- Judges only what the diff introduces or worsens; old debt is a one-line note, not a FAIL.
- Uses helper names as they exist in the tree now (`docs/ARCHITECTURE.md` "Shared helpers").
- `./tools/check.sh` (= `make check`) must pass; it scans the dt static tree with zero tolerance.
- `tools/archcheck_baseline.json` may only shrink. Any added entry or raised count is a FAIL.

## Updating the rules

When a rule changes, update `docs/ARCHITECTURE.md` in the same commit; for dt, `docs/themes/dt/CONDUCT.md` or
`docs/themes/dt/CODE.md`. The agent and this skill read those files, so they need no edit for rule changes.
