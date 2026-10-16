# polymux — agent and contributor guide

polymux (command `pm`) is a web control plane for projects, git, tmux and coding agents on one Linux host.
Setup: `docs/INSTALL.md`; every `pm.toml` key: `docs/CONFIG.md`; code layout and rules: `docs/ARCHITECTURE.md`.

## Where things are

| Area | Code | Rules / docs |
|---|---|---|
| Classic UI (built-in default theme) | `src/pm/web/themes/classic/templates`, `src/pm/static` | `docs/UI-RULES.md`, `docs/UI-CONDUCT.md`, `docs/UI-GLOSSARY.md` |
| dt UI (second theme, internally "pm v7") | `src/pm/web/dt`, `src/pm/web/themes/dt`, `tests/web/dt` | `docs/themes/dt/README.md` first, then `docs/themes/dt/CONDUCT.md` (`V7-*` ids), `docs/themes/dt/CODE.md` |
| Core Python (domain, routes, security) | `src/pm/**` | `docs/ARCHITECTURE.md` (packages, shared helpers, limits) |
| Plugins | `src/pm/web/plugins.py` (entry point `pm.plugins`) | `docs/CONFIG.md` "Theme and plugins" |
| Deploy examples | `deploy/` | `docs/INSTALL.md`, `docs/operations.md` |
| Dev instance, screenshots, UI tests | `tools/devpm.py`, `tools/uishot.py`, `tools/ui.sh` | `docs/DEV-TESTING-PLAN.md` |
| Agents and skills (Claude Code) | `.claude/agents`, `.claude/skills` | `arch-guard`, `screen-validator`, `builder-preview`; maintainer only (need the tracker DB): `features-keeper`, `features-update`, `features-audit` |

The two themes never mix: classic rules do not apply to dt files, dt rules do not apply to classic files, and work
for one theme does not change the other's files.

Checks: `./tools/check.sh` (= `make check`: ruff + architecture gate), `uv run pytest`, and for UI changes
`./tools/ui.sh` (= `make ui`: screenshot smoke + `uv run pytest -m ui`).

## Stack

Python 3.13, `uv`, FastAPI + Jinja2 + uvicorn, vendored HTMX (+ SSE ext), no JS build step. Runs on the host as a
systemd user unit (not Docker), behind a TLS reverse proxy.

## Hard rules

- Every tmux/git/compose call: argv list, no shell, timeout, fixed binary. tmux targets by id (`$ @ %`) only.
  User values after `--`.
- Every mutation: POST + CSRF + Origin check + logged-in session + tier check + audit line (+ unlocked session
  when `require_unlock = true`). Fail closed if the audit write fails. Plugin routes use the same guards
  (`mutation_guard`); the loader refuses plugins that do not.
- Pane text is ANSI-stripped and masked on every frame; never logged, indexed or sent to ntfy. Exception: the
  terminal stream is raw tmux output, unmasked, but still never logged, indexed or sent to ntfy.
- Markdown reader: `/a/<agent>/md` may open any `.md` file on disk (logged-in session only); paths with a hidden
  folder are refused except `~/.claude/plans`. Symlinks are resolved and re-checked.
- Never print, log, commit or send pm's token/TOTP (or the optional dev token).
- Tests use a fake tmux or a private `-L pmtest-*` socket; never the user's default tmux server.
- No host-specific values in code or defaults: hosts, URLs, paths and topics come from `pm.toml`. Private or
  personal features go into a plugin (`docs/CONFIG.md` "Theme and plugins"), not into the core.

## UI

- Themes live in `src/pm/web/themes/<name>/`; `classic` is the built-in one and the fallback. Follow
  `docs/UI-RULES.md`, `docs/UI-CONDUCT.md` and `docs/UI-GLOSSARY.md` for classic. A new theme brings its own rules
  and its own `tokens.css`; never change another theme's files for yours.
- The `dt` theme (pm v7, keyboard-driven shell) ships with polymux: templates and static in
  `src/pm/web/themes/dt/`, Python in `src/pm/web/dt/` (a first-party plugin), picked per browser with `?ui=dt` or
  Settings › UI theme. Its rules are `docs/themes/dt/CONDUCT.md` (start at `docs/themes/dt/README.md`); classic's
  `docs/UI-*.md` do not apply to it, and dt work does not change classic files.
- Classic row colours: hover `--hover`, selected/open row `--accent-soft`, nothing else filled or starred.
- Never use native browser popups (`alert`, `confirm`, `prompt`, `window.open`). Use `window.pmConfirm` /
  `window.pmPrompt` (`static/js/dialog.js`) or `hx-confirm` / `data-confirm` (dt: its own layers, V7-UI-4).
  `tests/test_no_native_popups.py` enforces it.
- UI changes: check them on the dev instance (`tools/devpm.py`, `tools/uishot.py --smoke`,
  `docs/DEV-TESTING-PLAN.md`) in light and dark, desktop and phone width, and read the screenshots.

## Working style

- Read `docs/ARCHITECTURE.md` before a code change and check your diff against it; `./tools/check.sh`
  (= `make check`) and `uv run pytest` must pass. Never add a new top-level module in `src/pm/` or a new copy of a
  shared helper.
- Add tests with every change. Do not edit `CHANGELOG.md` or `FEATURES.md` (generated); describe user-visible
  changes in the PR and the maintainer adds the entries.
- One topic per pull request; describe what changed and how you checked it (`CONTRIBUTING.md`).
