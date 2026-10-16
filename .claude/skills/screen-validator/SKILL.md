---
name: screen-validator
description: >-
  After any UI change (template, CSS, static JS), spawn the screen-validator sub-agent to check the
  change on every target screen: Dell 27" (2560 wide), Dell 34" ultrawide (3440), MacBook Pro 14" (1512) in
  Chromium and a phone in WebKit (classic 390, dt 440), light and dark, in the theme the change belongs to.
  Use when you finish a UI change, before the final report, or when the user asks to validate/verify screens,
  layouts or responsive behaviour.
---

# Screen validator (after every UI change)

Tests passing is not enough. When the UI change is done, run the validator as a **sub-agent** so the
screenshots stay out of the main context and the check is not anchored on your own view of the change.

## Screens

| Name | Engine | Viewport (CSS px) | Notes |
|---|---|---|---|
| `dell27` | Chromium | 2560 wide | wide; classic `main` width must stay capped (UI-RULES "Shell"); dt: LAYOUT §7 |
| `dell34` | Chromium | 3440 wide | ultrawide; check nothing stretches or floats in the middle |
| `mbp14` | Chromium | 1512 wide | default scaled MBP 14" |
| `phone` | **WebKit** | classic 390, dt 440 (V7-GATE-4) | touch sizes, bottom nav, < 700px layout |

Each in `light` and `dark`. Routes: the pages the change touches, plus the smoke set (`--smoke`: `/`,
`/a/{agent:running}`, `/a/{agent:ended_sid}`, `/tmux`, `/agents`, `/p/{project}`) when a shared file changed:

- classic: `themes/classic/templates/base.html`, `static/css/tokens.css`, `base.css`, `chrome.css`, `shell.css`,
  `static/js/side.js`, `side-init.js`, `drawer.js`, `drawer-init.js`;
- dt: `themes/dt/templates/base.html`, `templates/shell/*`, `static/css/tokens.css`, `base.css`, `shell.css`,
  `layers.css`, `parts.css`, `static/js/core.js`, `layers.js`, `keys.js`, `sidebar.js`, `tabs.js`.

## Which rules

- **classic** change: judged against `docs/UI-RULES.md` / `docs/UI-CONDUCT.md`.
- **dt** change (`src/pm/web/dt`, `src/pm/web/themes/dt`): shot with `--ui dt`, judged against
  `docs/themes/dt/DESIGN.md` and `docs/themes/dt/LAYOUT.md`, never against UI-RULES. Classic pages must stay
  unchanged.

## How to run (main agent)

1. Pick routes and say what changed in one or two lines (what the validator should look at), and the theme.
2. Launch **one** sub-agent: `Agent(subagent_type="screen-validator", prompt=...)`. The prompt must hold:
   the theme, the changed files, the routes, the intended visual result, and anything known-broken to ignore.
   Do not hand it your conclusion ("it looks fine"); ask it to find problems.
3. Read its report. Fix real findings, then launch it again on the failing screens only. Repeat until clean.
4. In the final report list per screen: ok / fixed / not checked.

Skip only for changes with no rendered effect (Python-only, tests, docs).

## What the sub-agent does (see `.claude/agents/screen-validator.md`)

- Needs the browsers once: `uv run playwright install chromium webkit`.
- Desktop: `uv run tools/uishot.py <routes> --themes light,dark --widths 2560,3440,1512 --out <scratch>/shots`
  (dev instance from `tools/devpm.py`, own token, fixtures; never a running pm, never the real token).
  `uishot` flags horizontal overflow, console errors, 4xx/5xx and client errors.
- Phone: `uv run tools/uishot.py <routes> --engine webkit --themes light,dark --widths 440 --out <scratch>/phone`
  (dt; classic `--widths 390`).
- dt: the same commands with `--ui dt`.
- Reads every screenshot and judges it against the theme's rules: single-line rows, header layout, no clipped
  or overlapping text, sidebar/drawer (dt: sidebar, tabs, aside) positions, phone tap targets (classic >= 36px;
  dt 40px controls, 44px rows), dark-mode contrast, empty space on the ultrawides.
- Read-only: never types into composers, never clicks mutating buttons, never prints tokens, keeps
  screenshots and scripts in a scratch directory (not the repo).

## Report format

One table, rows = route, columns = dell27 / dell34 / mbp14 / phone, each `ok` or a short defect
(`file:selector`, what is wrong, screenshot path), then a list of defects with the suggested fix.
