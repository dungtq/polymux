---
name: features-audit
description: >-
  Maintainer only (needs pmctl with the maintainer's tracker DB). Check FEATURES.md for completeness against
  the code and fix it: find features missing from the file, stale or removed entries, and create the file if it
  does not exist. Use when the maintainer asks to check/audit/refresh FEATURES.md, when FEATURES.md is missing,
  or periodically. Contributors describe changes in the PR instead.
---

# Audit FEATURES.md

**Maintainer only:** needs `pmctl` with the maintainer's tracker DB (project `polymux`). Contributors: describe
the change in the PR instead. Without `pmctl` (`command -v pmctl` fails), stop and say so.

Goal: `FEATURES.md` lists every user-facing feature exactly once and nothing that is gone. Run by the
`features-keeper` sub-agent (`Agent(subagent_type="features-keeper", prompt="audit FEATURES.md")`).
Entry format, area list and the `pmctl feature` commands: `.claude/skills/features-update/SKILL.md`.
FEATURES.md is generated from the tracker DB: change it only with `pmctl feature set/rm/audited`, never by editing.

## Inventory (build the list from code, not from memory)

Work area by area and collect candidate features from each source:

| Source | Command / place | Gives |
|---|---|---|
| Routes | `Grep '@router\.(get\|post)' src/pm` | every page and action; group by path prefix (`/a`, `/p`, `/tmux`, `/agents`, `/settings`, `/dt`, ...) |
| Classic templates | `src/pm/web/themes/classic/templates/*.html` (`_name.html` are partials of the page that includes them) | visible sections, buttons, tables |
| Classic static JS | `src/pm/static/js/*.js` | gestures, shortcuts, composer/snippets/dialog behaviour |
| dt templates | `src/pm/web/themes/dt/templates/**/*.html` (a page here overrides the classic template of the same name; `shell/` = the dt shell) | area `dt theme`: shell, ported pages, layers |
| dt Python | `src/pm/web/dt/*.py` (`routes.py` = `/dt/...`, the rest are view models and v7 stores) | area `dt theme`: tabs, prefs, page behaviour |
| dt static JS | `src/pm/web/themes/dt/static/js/*.js` (`keys.js` = the key table) | area `dt theme`: shortcuts, search, aside, layers |
| Settings | `src/pm/core/config.py`, `settings.html`, `_settings_*.html` (both themes) | every user-tunable option |
| Slash commands | `src/pm/agents/slash.py`, `src/pm/static/js/slash.js` | chat commands |
| Background | `src/pm/agents/autoexit.py`, `notify/alerts.py`, `notify/ntfy.py`, `notify/feed.py`, `agents/recap.py`, `agents/limits.py` | automatic behaviour |
| Tools | `tools/`, `Makefile`, `deploy/` | dev/ops helpers |
| History | `CHANGELOG.md`, `docs/themes/dt/PARITY.md` (what dt does yet) | hints only; confirm in code |

## Steps

1. Read the entries (`pmctl feature list`, or `FEATURES.md`). If the project has no FEATURES.md yet, create it
   with the area headings and `Last audited:` line, then `pmctl import --file features`.
2. Build the inventory. For each candidate decide: user-visible feature, or internal plumbing (skip).
3. **Missing**: candidate with no entry -> read its code and add an entry in the standard format
   (`pmctl feature set "<Area>" "<Name>" --text "..."`; dt-only behaviour in the area `dt theme`).
   `Since` from `git log --diff-filter=A --format=%ad --date=short -1 -- <file>` or the changelog; if unknown, omit.
4. **Stale**: for each existing entry, Grep that its route / setting / file still exists and the sentence
   still matches the code. Fix wording (`pmctl feature set`), or delete the entry when the feature is gone
   (`pmctl feature rm`).
5. **Duplicates / misplaced**: merge duplicates (`set` the kept one, `rm` the other); move an entry to the
   right area by `set` in the new area and `rm` in the old one.
6. `pmctl feature audited` (sets `Last audited: <today>`); the `Summary: N features in M areas.` line is
   recomputed by pm after any change.
7. Report: added, changed, removed (names), plus anything unclear that needs the maintainer's call.

## Do not

- Document planned work (open issues, the "Open decisions" in `docs/themes/dt/README.md`): only shipped behaviour.
- Describe security internals beyond the guarantee a user sees.
- Guess: when a feature's purpose is unclear from the code, list it in the report instead of inventing text.
