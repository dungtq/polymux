# Contributing to pm

Thanks for helping. Bug reports, fixes and small features are welcome.

## How changes flow

Send pull requests to this repo (github.com/dungtq/polymux) against `main`.
This repo is the source of truth for pm's code and docs.

## Dev setup

Linux, uv 0.12+, tmux, git and Node.js 20+ (the JS unit tests are skipped without node).

```sh
git clone https://github.com/dungtq/polymux.git
cd polymux
uv sync --group dev
```

Run a local instance with `uv run pm` (see [docs/INSTALL.md](docs/INSTALL.md)). For the optional
Playwright UI suite and screenshots, install the browsers once: `uv run playwright install chromium webkit`.
To keep your own host names, LAN addresses and user name out of commits, list them in
`~/.config/pm/private-patterns.txt` (format: `tools/leakcheck.sh --help`) and run `./tools/leakcheck.sh --install` once.
The hooks check staged lines (pre-commit), the commit message (commit-msg) and every outgoing commit's diff and full
message (pre-push). Lines prefixed `msg:` apply to commit messages only: for example `msg:#[0-9]+` if you track issues
in a private tracker whose numbers would link to unrelated public issues.

## Checks before a PR

| Command | What it does |
|---|---|
| `uv run pytest` | Test suite (UI tests are skipped by default) |
| `uv run ruff check src tests tools` | Lint (`make lint`) |
| `./tools/check.sh` (= `make check`) | ruff, the public leak scan (`tools/leakcheck.sh --tree` with `tools/leak-public.txt`) and `tools/archcheck.py`, the architecture gate (also the dt static tree) |
| `./tools/ui.sh` (= `make ui`) | Optional: screenshot smoke (`tools/uishot.py --smoke`) plus `uv run pytest -m ui`, the Playwright UI suite against a dev instance |

All of them must pass (the UI suite when you change the UI). CI runs `./tools/check.sh` and pytest on every PR.

## Rules

- **Tests never touch your default tmux server.** Use the fake tmux in
  `tests/` or a private socket (`-L pmtest-*`).
- **Add tests** with every change.
- **Placement.** Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) first. It
  says which package owns what, which shared helper to use (`run_argv`,
  `atomic_write`, `mutation_guard`, ...) and the size limits (module 600 lines,
  function 80, route handler 40, JS/CSS file 500). Never add a new top-level
  module in `src/pm/`. `archcheck` enforces this; its baseline may only shrink.
- **Subprocesses**: argv lists, fixed binaries, timeouts, no shell, user values
  after `--`.
- **Mutations**: POST + `mutation_guard` + audit line. No hand-written CSRF or
  Origin checks.
- **Pane text** goes out only through `security.mask.render_pane`.
- **UI**: classic follows `docs/UI-RULES.md`; the dt theme follows
  `docs/themes/dt/CONDUCT.md` (start at `docs/themes/dt/README.md`). Never change
  one theme's files for the other. No `alert()` / `confirm()` / `prompt()`:
  classic uses `static/js/dialog.js` (`pmConfirm` / `pmPrompt`), dt uses its
  layers (`pm7.confirm` / `pm7.dialog` in
  `src/pm/web/themes/dt/static/js/layers.js`).
- **Settings**: a new option is a typed field in `pm.core.config.Settings`,
  documented in `docs/CONFIG.md`.
- **Secrets**: never commit tokens, keys, `.env` files or `*.db` files.

## Generated files

`CHANGELOG.md` and `FEATURES.md` are generated from the maintainer's pm
tracker database (`pmctl change` / `pmctl feature`), so do not edit them by
hand. In a PR, describe user-visible changes in the PR text; the maintainer
adds the CHANGELOG and FEATURES entries.

## Commit and PR style

- One topic per PR. Short, plain commit subjects.
- Say what you tested.
- For UI changes, add a screenshot (light and dark if relevant).

## License

By contributing you agree that your work is released under the MIT License
(see [LICENSE](LICENSE)).
