# Dev testing plan: a UI check agents can always run

Problem (2026-10-09): agents cannot verify UI on the live pm because logging in needs
pm's real token/TOTP, and reading it is (rightly) denied. Result: "not verified in a browser" after
every UI change. Fix it once: a **dev instance with its own throwaway credentials and fake data**, so no
real secret is ever read and the real service is never restarted for a check.

## Principles
- Never read, print or copy the real token/TOTP/session. The dev instance has its **own** state dir and
  a **throwaway token** generated at start; agents may read that one.
- No auth bypass in the real code path. Auth stays on; the dev instance just uses a different
  `token_file`. (Do not add a "no-auth" flag that could ever reach prod; the old backlog idea is replaced.)
- Dev instance can never touch real data: own `state_dir`, fake tmux (`tmux -L pmtest-*` private socket or the
  fake from tests), temp `dev_root` with fixture projects, `~/.claude` / `~/.cursor` plan and transcript roots
  pointed at fixtures.
- The live pm stays read-only by the user's own browser; agents check it only through the dev instance.

## Pieces
1. **`tools/devpm.py`** (start/stop/status): writes a dev config TOML (`binds=127.0.0.1`, port 8095+N,
   `state_dir=<tmp>/state`, `token_file=<tmp>/token`, `dev_root=<tmp>/projects`, `allowed_origins` for the dev
   URL), seeds fixtures, launches uvicorn from the **current worktree** (`git worktree add ../pm-wt-<branch>`
   when asked), prints `DEV_URL` and the token **file path** (not the token). Refuses to start if `state_dir`
   equals `~/.local/state/pm` or the port is 8093. Banner "DEV" in the page head so screenshots are never
   mistaken for prod.
2. **Fixtures** (`tests/fixtures/dev/`): fake agents in every state (running, idle, waiting, failed, ended with
   and without session id, auto-exited, manual exit), a project with TODO/BACKLOG/plans, ended records in a
   dev `pm.db`, transcripts with models and usage, a `claude-limits.json` at 24/40 % plus warn/red variants,
   high and low context, long names, protected session, ntfy feed items.
3. **`tools/uishot.py`**: `uv run tools/uishot.py /a/<id> /tmux / --themes light,dark --widths 1300,390`
   starts (or reuses) the dev instance, logs in with the throwaway token via Playwright, saves screenshots
   to the session scratchpad, prints paths, fails on console errors, horizontal overflow or client-errors.jsonl
   entries. This is the one command every UI task ends with.
4. **Interaction checks** run only on the dev instance (Exit, Resume, Fork, compose, drawer, sidebar
   toggle, transitions): a small Playwright suite `tests/ui/` (marker `ui`, skipped by default, `uv run pytest -m ui`).
5. **Rules update** (CLAUDE.md "Claude Code notes" + AGENTS.md): UI verification = `tools/uishot.py` on the dev
   instance; "live page check" becomes the user's job for the final look, and agents must say so in one line
   instead of "could not verify". Include in sub-agent briefs.
6. **Parallel agents**: each takes its own port and worktree, so several UI agents never fight over
   `pm.service` restarts or the same tmux server.

## Order
P1 `devpm.py` + minimal fixtures + `uishot.py` (smoke: / (Inbox), /a/<id> live+ended, /tmux, /agents, ended page).
P2 full fixture set + interaction suite. P3 rules + worktree flag + CI-style `make ui`.

## Acceptance
- A fresh agent runs one command and gets light/dark/390px screenshots of any page with no credentials prompt.
- Running it never changes `~/.local/state/pm`, real tmux sessions or `pm.service`.
- Exit/Resume/Fork flows can be clicked end to end in the dev instance.

## P1 contract (2026-10-09)

Shared by `tools/devpm.py`, `tests/fixtures/dev/`, `tools/uishot.py`. Change here first.

**Instance dir** `<root>` = `${TMPDIR:-/tmp}/pm-dev/<port>/` (default port 8095; `--port N`, N >= 8095, never 8093).
It holds `home/` (the dev instance runs with `HOME=<root>/home`, so `~/.claude`, `~/.config/pm` are fixtures),
`home/.config/pm/pm.toml`, `home/.config/pm/token` (throwaway, mode 600), `state/` (state_dir), `projects/`
(dev_root), `instance.json`, `pm.log`, `pid`.

**`instance.json`** (written by `devpm.py start`, printed by `devpm.py status --json`; never contains the token):
`{"url": "http://127.0.0.1:8095", "port": 8095, "root": "...", "token_file": "...", "state_dir": "...",
"socket": "pmtest-dev-8095", "pid": 123, "project": "fixproj", "agents": {"<key>": "<agent id>"},
"pm_bin": "", "extra_toml": "", "limits": "default"}`.
`agents` maps manifest keys to the real `/a/<id>` ids (live and ended). `pm_bin`, `extra_toml`, `limits` are the
start options the instance was started with ("" = this tree's `.venv/bin/pm` / no extra table).

**Commands**: `devpm.py start [--port N] [--limits L] [--pm-bin P] [--extra-toml F]` (idempotent: reuses a
running, current instance, prints instance.json), `devpm.py stop [--port N]` (kills uvicorn +
`tmux -L pmtest-dev-N kill-server` + removes `<root>`), `devpm.py status [--port N] [--json]`.

**Restarts keep the instance**: `--pm-bin`, `--extra-toml` and `--limits` are remembered in
instance.json; a later `start` that omits them (uishot, `tools/ui.sh`, uidiff) uses the remembered values, an explicit
flag changes them (`--pm-bin ''` = back to this tree's pm). When the code is newer than the instance, pm died, or an
option changed, `start` replaces **only the pm process**: the fixture projects (and anything an agent added to
them), `state/`, the token, the fake-agent tmux server and devntfy stay; `pm.toml` is rebuilt from `<root>/pm.base.toml`
plus the `--extra-toml` file. Only when that tmux server or the seeded files are gone does `start` re-seed from
scratch (still with the remembered options). For a clean slate: `devpm.py stop`, then `start`.

**Login (what uishot does)**: `GET <url>/login`, fill the `token` field with the contents of `token_file`,
submit (POST /login, Origin = url, `allowed_origins` includes the url). Auth stays on.

**Fixtures** `tests/fixtures/dev/`:
- `manifest.json`: `{"project": "fixproj", "agents": [{"key", "kind": "claude", "status": "busy|idle|waiting|failed",
  "live": true|false, "uuid": "<session uuid or null>", "transcript": "transcripts/<key>.jsonl or null",
  "cwd": "{PROJECTS}/fixproj", "label": "..."}]}` (`{PROJECTS}` = `<root>/projects`, replaced by the seeder).
  Keys: `running idle waiting failed ended_sid ended_nosid ended_auto highctx lowctx`; `live=false` = ended record.
- `transcripts/<key>.jsonl`: Claude transcript lines with `message.model` and `message.usage`
  (`input_tokens`, `cache_read_input_tokens`, `output_tokens`); `highctx` ~85 % of the context window,
  `lowctx` ~5 %.
- `limits/default.json` (five_hour 24, seven_day 40), `warn.json` (65/70), `red.json` (85/92). Window field
  `resets_in_s` replaces `resets_at` (the seeder converts to epoch).
- `projects/fixproj/` : `TODO.md`, `BACKLOG.md`, `PLAN.md`, plan files in the layout `pm.projects.plans` reads, a git-less
  tree (the seeder runs `git init`).

## Usage (P1, implemented 2026-10-09)
```
uv run tools/devpm.py start [--port 8095] [--limits default|warn|red]   # prints instance.json (no token)
uv run tools/uishot.py --smoke --out /tmp/pm-dev/shots                  # starts/reuses the instance
uv run tools/uishot.py '/a/{agent:running}' /tmux --themes dark --widths 390
uv run tools/uishot.py --smoke --no-dev-badge                           # README / site shots: no dev badge
uv run tools/devpm.py stop
```
Smoke: `/` (Inbox), live and ended `/a/<id>`, `/tmux`, `/agents`, `/p/fixproj`. Exit 1 on console errors,
HTTP >= 400, horizontal overflow or new `client-errors.jsonl` lines. P2 still open: more fixtures
(ntfy items, long names, protected session, manual exit) and the `tests/ui` interaction suite.

## P2 contract (2026-10-09)

Owners: **F** = `tools/devpm.py`, `tools/devfakeagent.sh`, `tools/devntfy.py`, `tests/fixtures/dev/**`,
`tests/test_dev_fixtures.py`, `tests/test_devpm.py`. **U** = `tests/ui/**`, `tools/ui.sh`, `Makefile`, the pytest
`ui` marker in `pyproject.toml`. Nobody else edits those. Change this section first.

**Fake agent** `tools/devfakeagent.sh <status> [--resume <uuid>]` (started as `exec -a claude bash devfakeagent.sh`
so pm detects it as `claude`): writes `<HOME>/.claude/sessions/<pid>.json` itself (pid, procStart from
`/proc/$$/stat`, sessionId = fixture uuid or the `--resume` uuid, status, cwd, updatedAt, statusUpdatedAt), reads stdin
lines, echoes them, and on a line `/exit` deletes its session file and **exits** (the pane/window closes, so the
live->ended transition is real). A resumed agent starts `idle`. `<HOME>/.local/bin/claude` is a symlink to the
script (pm's default `agent_bin`), so Resume and Launch start it.

**Extra manifest keys** (live unless noted): `longnames` (very long label, tmux window name, project dir name
`fixproj-with-a-very-long-name-...`, directory, plan title), `protected` (tmux session `dev-protected`, NOT in
`unprotected_sessions`), `ended_manual` (ended; `autoexit.json` info row `manual: true`),
`ended_oldmodel` (ended; its autoexit/db info has no model; the model must come from its transcript).
`instance.json["agents"]` has an id for each key.

**ntfy**: `tools/devntfy.py <port>` serves `GET /<topic>/json?poll=1` (JSON lines of ntfy messages, a few items for
topic `agent` incl. one tagged with the project path, one long title/message); `devpm start` launches it on
`port+100`, sets `ntfy_topic_url`, stops it in `stop`.

**UI suite** (`uv run pytest -m ui`; default run skips it via `addopts = -m "not ui"`): a session fixture starts
`devpm.py start --port 8105` (separate from uishot's 8095) with its own token, stops it at the end; each test gets a
logged-in Playwright page; screenshots of failures go to `$PM_UI_OUT` (default `/tmp/pm-dev/ui-out`). Tests never
touch the real pm. Some tests mutate (Exit kills a fixture agent), so each uses its own agent key, or a fixture
restarts the instance (`devpm.py stop/start`) per module.
`tools/ui.sh` = `uishot.py --smoke` then `pytest -m ui`; `make ui` calls it.

## Usage (P2, implemented 2026-10-09)
`make ui` (or `tools/ui.sh`): smoke screenshots on port 8095, then `uv run pytest -m ui` (16 tests, ~50 s) on
port 8105 with a fresh instance per module. Failure screenshots: `$PM_UI_OUT` (default `/tmp/pm-dev/ui-out`).
Fake agent `tools/devfakeagent.sh` exits on `/exit`; fake ntfy `tools/devntfy.py` on port+100. Not covered:
auto-exit sweeper, passkey login, terminal stream.
