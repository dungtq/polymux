---
name: features-update
description: >-
  Maintainer only (needs pmctl with the maintainer's tracker DB). After implementing, changing or removing a
  user-visible feature in pm, spawn the features-keeper sub-agent to add or update its entry in FEATURES.md.
  Use when you finish a feature or behaviour change, before the final report, or when the maintainer says
  "update FEATURES.md" / "document this feature". Contributors describe the change in the PR instead.
---

# Update FEATURES.md after a feature

**Maintainer only:** needs `pmctl` with the maintainer's tracker DB (project `polymux`). Contributors: describe
the change in the PR instead; the maintainer adds the FEATURES and CHANGELOG entries. Without `pmctl`
(`command -v pmctl` fails), skip this skill and say so in the PR text.

`FEATURES.md` (repo root) is the catalogue of what pm can do **now**. `CHANGELOG.md` says what changed
and when; FEATURES.md says what exists. Both are updated for a user-visible change. Both files are generated
from the tracker DB: entries change only through `pmctl feature set/rm` and `pmctl change add`, never by
editing the file.

## When to run

Run for any new, changed or removed user-visible behaviour: a page, route, button, setting, slash
command, notification, keyboard/touch gesture, MCP/agent action, in either theme. Skip for refactors, tests,
docs-only, and pure bug fixes that restore documented behaviour.

## How (main agent)

1. After the code is done and tests pass, launch **one** sub-agent:
   `Agent(subagent_type="features-keeper", prompt=...)`. The prompt holds: the feature in one or two
   sentences, the theme (classic or dt), the files/routes/templates/settings it touched (`git diff --stat`
   output is fine), and whether it is new, changed or removed. Do not paste its desired wording; let it read
   the code.
2. Read its report. If it flags something it could not verify, answer or fix the code.
3. Include `FEATURES.md` in the same commit as the feature (the pre-commit hook re-renders and stages it).
   Mention it in the final report.

If `FEATURES.md` does not exist yet, the sub-agent creates it by running the audit skill
(`.claude/skills/features-audit/SKILL.md`) instead.

## Commands (the sub-agent uses these)

- `pmctl feature list [--area Chat]`, `pmctl feature show "<Area>" "<Name>"`: what is there now.
- `pmctl feature set "<Area>" "<Name>" --text "<text>"` (or `--text-file -`): add or replace one entry; a new
  name goes at the end of its area, a new area at the end of the file. `<text>` is the line after
  `- **<Name>** — `.
- `pmctl feature rm "<Area>" "<Name>"`: delete an entry. The `Summary:` line is recomputed after a change.

## Entry format (the sub-agent follows this)

`FEATURES.md` is grouped by area (Navigation, Inbox, Agents, Chat, Projects, Git, tmux, Servers / VMs, Settings,
Notifications, Security, Phones / Builder, Background, Tools, dt theme). Everything that only the dt theme does
goes in **`dt theme`** (`docs/themes/dt/CONDUCT.md` V7-GATE-6); a core feature both themes show stays in its
usual area. Within an area, one bullet per feature:

```
- **Name** — one sentence on what the user can do. Where: `/route` or UI spot. Code: `src/pm/x.py`, `templates/y.html`, `static/js/z.js`. Since: 2026-10-11.
```

Rules: present tense, user's words not internal names; one line per feature (a short sub-list only for
distinct modes); keep `Where` concrete (route, button label, setting key; dt: also `?ui=dt`); update `Since`
only for new features, add `Changed: <date>` for behaviour changes; delete the bullet when the feature is removed
(the changelog keeps the history); keep areas alphabetical-by-feature unless the file says otherwise.
Top of file: a `Last audited: <date>` line, which only the audit skill changes (`pmctl feature audited`).

## Checks before finishing (sub-agent)

- Every route/setting/file named in the entry exists (`Grep`).
- No duplicate entry for the same feature; merge instead.
- Hard-rule features (CSRF, audit, masking, no native popups) are stated as guarantees, not duplicated per page.
