# Changelog

## 2026-10-17 (Accounts: global default, add/remove in the UI, sessions of named accounts readable)

Named Claude/Codex accounts can now be added and removed in Agents > the agent > Auth (no pm.toml edit, no restart; Remove only unlists, the login folder is kept; pm.toml names stay read-only). One account can be the global default; project settings > Login account offers "Global default (X)", "CLI default" or a name. Chat, model, usage, recap, forks and past sessions of an agent running under a named account are now read from that account's folder. A project choice that points at a removed account is refused with an audited 400 (launch, continue, resume, fork, New chat).

## 2026-10-17 (dt: model pill in Inbox/project rows, Show the model pill setting works)

The dt Inbox and project agent rows now show the model pill after the agent name. Settings > Display > "Show the model pill" had no effect: head.js set html.no-mpill but no CSS used it. It now hides the pill in those rows, the chat header and the message headers. Rows hide the pill under 640 px of page width and on phones; the Agent column is wider so the id stays readable.

## 2026-10-17 (Finished sub-agents no longer shown as running (transcript request replays))

A finished Claude sub-agent no longer shows as running or "waiting" in the Sub-agents panel, the tab count and the agent rows for up to 30 minutes. Newer transcripts replay old tool calls inside `api-request*` entries; those replays re-opened the sub-agent's last call (usually its final hand-back) and inflated its call count. Tool calls are now read only from assistant entries and results only from user entries. The ntfy owner scan ignores the same replays.

## 2026-10-17 (Several tmux-agents messages in one bubble group)

A chat message that carries several `tmux-agents` envelopes at once (queued notices, a request and a reply arriving together, or an older notice without a sender) now shows one "Message from: <agent id>" block per envelope, each in its sender's colour, instead of one raw blue "You" bubble.

## 2026-10-17 (Messages from other agents show as "Message from: <agent id>")

A message another agent sent with `tmux-agents` (request, reply or no-reply notice) is no longer a blue "You" bubble in the chat. It shows as a bubble headed "Message from: <agent id>" in that agent's colour (claude, codex, devin, cursor-agent, antigravity; grey when the id is unknown), with the request / reply · status / notice tag and only the message text (the reply instructions are dropped). It stays out of the composer's Up-arrow history.

## 2026-10-17 (Project settings: import a Cursor / VS Code workspace into the links)

The project settings panel (both themes) has an import section when `*.code-workspace` files are found in the project folders or their parent folders. Importing one links the workspace folders that are pm projects, keeps the existing links and reports how many folders are not pm projects. The audit line `project_links` carries counts only.

## 2026-10-17 (dt: Switch agent… hands a chat over to another coding agent)

A session's ⋯ menu (live and ended) has Switch agent…. It opens the Launch dialog with the chat as the prompt: your messages and the agent's text replies, without tool calls, command output or thinking, at most 8000 characters (the first message and the last reply stay whole). Pick the agent kind, edit the prompt and launch; the old agent is left running.

## 2026-10-17 (Agent rows show the last ntfy message of any agent)

An agent's ntfy message now shows as the row's last message when the agent tagged it with another project's path (a cursor-agent working in a polymux worktree) and when its title names only the kind (`devin - ...`, no agent id): the project page maps messages over the whole feed, and a kind-only title goes to the one session of that kind active at that time.

## 2026-10-17 (Per-project login accounts for Claude and Codex)

pm.toml `[accounts]` names extra logins (`claude = ["work"]`, `codex = ["a1"]`); each gets its own login folder under the state dir, passed to the CLI as `CLAUDE_CONFIG_DIR` / `CODEX_HOME`. The agent's Auth row shows every account with its own status, Log in and Log out. Project settings choose the account new Claude/Codex agents use. Resume, fork and New chat keep the account the agent started with; an account removed from pm.toml is refused with a 400. Reading sessions and usage of non-default accounts is not supported yet.

## 2026-10-17 (dt: chat aside only on the main pages, not on a full chat page)

dt theme: the chat aside is now used on the main pages only (Inbox, Projects, tmux, ...). On a session's own full chat page (/a/<id>, Builder, /term/s<n>) the remembered aside is no longer restored beside the chat (it comes back on the next main page), the top-bar Chat aside button is hidden, and opening a chat from search, a notification or a list opens its tab instead; ⌘L / the toggle there only say so; btw still opens in the aside. ⌘⇧L ("to aside") now closes the session tab and goes to the home tab, where the aside shows it.

## 2026-10-17 (Agent thinking shows in the chat)

The thinking of Antigravity (agy), Devin, Cursor and Codex agents now shows in the chat as a folded "Thought" row with its first line. Click it to read the full text. The Compact view hides these rows. Claude is unchanged. Conversations are read once again so earlier thinking appears.

## 2026-10-17 (Auto-exit keeps the agent's window (autoexit_close_window))

- An idle auto-exit no longer closes the agent's tmux window: it stays with its last output, like the window of
  any agent that quits. New pm.toml key `autoexit_close_window` (default `false`); `true` restores the old
  behaviour (close it when it holds nothing but the left-over shell). The audit line says `window_close: kept`.

## 2026-10-17 (Agent with a busy sub-agent stays Running)

A Claude agent that has launched background sub-agents stays under Running while one of them is in a long quiet tool call, such as a test run of several minutes. Before, it moved to the Idle table after a minute without writes.

## 2026-10-17 (Composer sends to agy after an error)

Messages sent from the composer to an Antigravity (agy) agent are no longer held back by an old error above its prompt, such as "Individual quota reached". An agent that shows an empty prompt now counts as idle, and the message is sent. The same agent also no longer appears as failed (no "failed" ntfy, not under "Needs you") while it sits at an empty prompt.

## 2026-10-17 (Antigravity file links read cleanly in chat)

Links like [name](file:///path#L1-L3), which the Antigravity (agy) agent writes, no longer show as raw brackets and a URL in chat bubbles. They show as the name, with the path as a tooltip. They are not clickable.

## 2026-10-17 (Antigravity chat sync and plan usage limits)

- **Antigravity chat sync** — `antigravity.read_entries` and `read_latest` wait for in-progress streaming steps (`CORTEX_STEP_STATUS` 1/2/8/9 in field 4 of `step_payload`) to finish before advancing the ingest cursor, so replies and tool calls are never truncated mid-stream or dropped during the thinking phase (`items.FORMAT = 4`, `log.FORMAT = 2` re-ingest earlier truncated conversations). `locate` also matches open `presence/<uuid>.lock` file descriptors when `agy` is idle, and `classify_pane_text` recognizes Antigravity's 8-dot braille spinner and `esc to cancel` footer as `RUNNING`. (`src/pm/chat/sources/antigravity.py`, `src/pm/chat/items.py`, `src/pm/chat/log.py`, `src/pm/agents/state.py`, `tests/test_antigravity.py`)
- **Antigravity plan limits and usage stats** — `/a/<id>/stats` now shows 5-hour and weekly plan limits for Antigravity agents (from `agy -p /usage --output-format json`, cached in `antigravity-limits.json` via `SWR`, tagging the active model's weekly bucket such as Argon for the summary bar) as well as reply count, tool calls, duration and cache hit % in the usage section; `/usage` and `/quota` are added to the Antigravity slash palette, and `-p`/`--print`/`models` runs are ignored by agent detection. (`src/pm/agents/limits.py`, `src/pm/agents/usage.py`, `src/pm/agents/slash.py`, `src/pm/agents/detect.py`, `src/pm/web/routes/chat.py`, `src/pm/web/themes/classic/templates/_stats.html`)

## 2026-10-17 (Queued messages survive a pm restart)

A message waiting for an agent is no longer failed as "agent gone" right after pm restarts. The one-minute wait now counts from pm start, so the first agent scan can finish.

## 2026-10-17 (Fix Codex startup trust choice submission)

- Codex startup folder and hook trust choices now send the selected digit followed by Enter, so clicking the trust row submits it instead of only moving the terminal selection.
- Quit, Review hooks, and Continue without trusting keep their immediate digit shortcuts; no extra Enter reaches the next screen.
- Regression coverage checks startup prompt parsing, rendered choice form values, and key delivery through the authenticated route.

## 2026-10-17 (Composer messages reach an idle Cursor agent)

A message sent from the composer to a Cursor agent no longer waits forever. An idle Cursor agent shows an arrow prompt ("Add a follow-up"), which pm did not recognise as idle, so the message stayed queued. It now counts as idle and is sent.

## 2026-10-17 (Antigravity CLI launch from the web UI)

- Launch form and quick launch now start Antigravity (`agy`): model list from `agy models`, permission modes (`accept-edits`, `auto`, `plan`, bypass `dangerously-skip-permissions`), reasoning effort (`low`..`max`), optional `--sandbox`, and `-i` (`--prompt-interactive`) for the initial prompt instead of `--` (which `agy` rejects).
- Fork handoffs to Antigravity pass the handoff prompt with `-i`, and New Chat strips `-i` / `--prompt-interactive` and `--conversation` when restarting an Antigravity process.

## 2026-10-17 (Cursor launch follows the process's own chat)

Launching cursor-agent treated every process in a directory as the newest store.db there, so the new tmux window opened as an older chat that was still being written. A live process is now the store it has open. Until that file is open, it is not attached to another chat.

## 2026-10-17 (Per-project agent memory: pmctl memory, shared by every agent kind, synced with Claude Code's memory dirs)

- `pmctl memory add|set|show|list|rm|search|context|sync`: one memory per project in pm.db (`tk_memory`), shared by
  every agent kind and every git work tree of the project; type `user` memories with `--global` apply everywhere.
  Writes are audited (fail closed) and work while the web service is down.
- `pmctl memory context` prints a one-line-per-memory index for session start; `pmctl memory sync [--all]` keeps
  Claude Code's own memory dirs (main tree and each work tree) in step both ways: files Claude wrote are imported
  by hash (pm.db wins a conflict, the file version is kept as a `-conflict-<time>` copy), then every memory is
  rendered back with its `MEMORY.md` index. A summary goes to `state_dir/memory/<project>.md`. Nothing is ever
  written inside a repo; symlinks are refused; files are 0600.
- New pm.toml key `claude_dir` (default `~/.claude`). `encode_cwd` moved to `pm.core.fsutil`; `timefmt.iso` is the
  shared ISO helper.

## 2026-10-17 (Builder md links open; clearer not-found message)

- dt Builder: .md links in the chat open in the markdown reader (they did nothing: Builder has no docked reader).
- A .md link to a file pm cannot find now says "Not found: <path>" and where pm looked, instead of the old "File not available (only readable .md files outside hidden folders)".

## 2026-10-16 (Markdown reader finds files from the folders an agent worked in)

- The markdown reader also finds relative .md links an agent wrote while working in another folder or project: folders it cd'd into, the project a (deleted) worktree belongs to, a unique match in another project, or a unique file of that name inside its project. Hidden folders stay refused (#249).

## 2026-10-16 (Leak check for commits and pushes)

- `tools/leakcheck.sh`: checks the lines a commit or push adds against a private pattern file (`$PM_PRIVATE_PATTERNS`
  or `~/.config/pm/private-patterns.txt`, one regex per line, `path:` and `src:` kinds). `--install` adds a
  pre-commit step and a pre-push hook; hits show `file:line` and the pattern number with the match masked. Without a
  pattern file it warns and passes, so contributors are not blocked.
- `tools/check.sh` (and CI) scan the tracked tree with a small public set `tools/leak-public.txt`: private-key
  headers, `.env` / `*.db` / token files, `/home/<user>/` paths and 10.x addresses outside tests.
- `pmctl hooks install`: the pre-commit hook now also runs every executable in `.git/hooks/pre-commit.d/` after the
  tracker render, so other checks can chain onto it.

## 2026-10-16 (dt: row links, ended sessions in the aside, search popups and project quick look, backlog bulk add and quick edit, md reader)

- Rows: names in Inbox, project Agents, Projects and Agents tables are real links, so Ctrl/⌘/Shift or middle click opens a new browser tab (#247).
- Ended sessions open in the chat aside (click / Enter), O opens a tab; their ⋯ menu has Open chat / Open in a tab (#243).
- Search: Settings opens the Settings popup, sessions open in the aside, projects open a new project quick look (folder listing; files open in a new browser tab by default, Settings › Display "Quick look opens files in"); ⌘/Ctrl+Enter opens as a page; exact name matches come first (#244).
- Backlog: add several issues at once (one per line, Add another), paste or drop screenshots into Add issue, quick edit of issues and TODO tasks (⋯ Edit…, key R) (#245, #246, #248).
- Images in agent replies show large instead of as small thumbnails (#246).
- Markdown links from the chat aside open in a reader centred on the screen (#250); relative .md paths are also found in the project and among files the agent named (#249).

## 2026-10-16 (Agent coordination: pmctl claims and merge-check; every agent in its own persistent window)

- `pmctl claim add|list|show|update|done|drop|gc`: agents record what they are about to change (title, path globs,
  branch, work tree, optional issue) in pm.db (`tk_claim`) before implementing and close the claim after committing.
  `claim add` and `claim done` list the other agents' active claims that overlap; with `--issue N` a note goes on the
  issue. A claim whose tmux pane is gone counts as stale and never blocks. `pmctl` writes pm.db directly, so this
  works while the web service is down.
- `pmctl merge-check [--base B]`: run in a branch's work tree before merging. Compares the branch's changed files with
  other agents' active claims and with the other git work trees (uncommitted changes or commits not in the base);
  exit 1 on an overlap, a warning when the base branch has moved.
- Every agent launch (Launch, Continue, Fork, Resume) opens its own tmux window, and the window stays when the agent
  quits (`remain-on-exit`, set in the same tmux call that creates it). The split-pane Continue option is gone
  (`where=pane` is now 400). Dead panes are never shown as live agents or reported "still running".
- `deploy/tmux-agents`: rewritten in Python (stdlib only). Windows only, addressing by pm agent id, delivery that holds
  messages while the target shows an interactive prompt, `inbox`, `broadcast`, `reply --status`, and `claim` /
  `done` / `merge-check` / `board` wrappers around `pmctl` that message the agents whose claims overlap.

## 2026-10-16 (Faster tab switches and Inbox; account menu Settings opens the popup)

- dt tab switches (Shift+[ / ], tab clicks) no longer revalidate every script and stylesheet: versioned /static files (`?v=` equal to the file's current hash) are cached as immutable; a page switch makes a few requests instead of ~100.
- Inbox renders faster: notification owners are looked up in one query, and Devin context % reads the lock map once per pass (was ~550 ms on a busy host).
- dt: the top bar account menu's Settings opens the Settings popup like the sidebar foot and Ctrl+, (a plain link on /settings).

## 2026-10-16 (dt theme ships with polymux)

- The `dt` theme now ships with polymux: a keyboard-driven shell with tabs, a sessions sidebar, command menus and layered panels; vim-style list keys (J/K, G then a letter, `/`, `?`) and macOS-style search and switching (⌘K, ⌘P, ⌘B, ⌘,; Ctrl elsewhere). Pick it per browser in Settings or with `?ui=dt`, or server-wide with `theme = "dt"` in `pm.toml`; classic stays the default. Code: `src/pm/web/dt`, `src/pm/web/themes/dt`; rules and design: `docs/themes/dt/`.
- README: project status, why polymux exists, themes and dt screenshots (desktop and iPhone, light and dark).
- Dev tooling: `tools/uishot.py --engine webkit` logs in again (WebKit dropped the session cookie on the login redirect).

## 2026-10-15 (Agents, Servers, Audit and Settings data and actions for themes (v7 M4 core))

- Agents: review / apply answer JSON to fetch calls (classic forms unchanged); `POST /agents/rescan` drops the inventory and usage caches; `GET /agents/auth.json` gives every agent's login state from the cache (`?fresh=1` re-checks).
- Agents findings: each finding has a kind, an agent and a planned fix; `/agents?t=findings` groups them; new reviewed fixes link missing shared skills, drop stale `agents-index.md` lines and move an MCP key from the args into env (backup first, re-checked just before the write).
- Agents Usage: the Claude plan limits and the session ids of the top sessions.
- Servers: the compose stack of each container and Restart stack (members found by docker, protected containers skipped), Start for stopped containers, stop or kill every process of a program with a typed confirm.
- Settings: replies carry an ok / notice signal (`HX-Trigger pm:notice`, JSON for explicit JSON requests); Revoke all other sessions; Re-pin host key with the typed server name; `GET /settings` carries the panel data; the loaded pm.toml path is known.
- Audit: the filter matches target, client and detail too (all words); Refused and Failed result classes with counts; a category filter; Export CSV (`/audit/export.csv`); the Client errors tab renders on first paint (`?tab=errors`).
- Client errors: reports carry a masked stack, the browser family and CSP violations (kind `csp`, also listed in the classic kind select); a grouped view gives one row per message with its count and pages.
- Fix: client-error messages are masked again (masking had been skipped silently).


- Agents: an applied change clears the inventory cache, so the next page load shows it.
- Audit: a plugin's namespaced action (e.g. `dt.prefs.save`) gets the category of its bare name.
- Audit names: process, program and container actions log the attempt and the result under the same name; Clear client errors writes its audit line before clearing (fails closed); Re-pin host key needs tier 2.

## 2026-10-15 (Projects, Backlog, tmux and git operations for themes (v7 M3 core))

- Issues: move an issue to another project (`POST /p/{n}/issues/{num}/move`, `pmctl issue move`), dispatch one issue to a new agent; `/backlog` carries cross-project issue and TODO lists; the projects list has segment and open-issue counts.
- TODO: add an item to a tracker project from the web (`POST /p/{n}/todo/add`, text and optional section).
- tmux: list filters (agents / attached / protected) with counts, an optional start folder for a new session (under the projects root), pm's own session in the window rows; after a tmux new / rename / kill the lists refresh at once (snapshot rebuild + event).
- Git tab JSON API: detailed status, stage / unstage / discard, commit, stash, checkout, merge, branch delete, pull with stash, each with a "what will happen" plan; typed confirms for the destructive ones.
- Project stack (`/p/{n}/stack`: services, logs, up, down) and project VMs (count on the project page from the cached listing, snapshots, console info, start / shutdown / force off / delete).
- New project dialog: planner (`/p/new/plan`) and step runner (`/p/new/run`, NDJSON) with a plugin step hook; plugin steps run in their own bounded pool.
- Launch form partial `GET /launch/form` for themes.
- Dev tooling: `tools/devpm.py` remembers `--pm-bin`, `--extra-toml` and `--limits`; a restart (also the one uishot triggers) keeps them and the fixture data (#206).

## 2026-10-15 (Inbox and notification data for themes; mark unread; mute titles)

Theme-neutral additions used by the dt theme's Inbox and Notifications (classic looks the same):
- Inbox rows carry ctx %, unread count, error line, last message with its source, model and the pending question's options; sort key `ram`.
- Mark an Inbox item unread (`POST /inbox/mark-unread`); mute a notification title for 24 h (`POST /ntfy/mute`, `/ntfy/unmute`; muted titles drop out of every list and count).
- Live agent events carry unread and the last message; an ntfy for an agent publishes one combined inbox event.
- Notification rows know their day (today / yesterday / earlier), source and "recovered after"; `GET /ntfy/bell` takes `n` and `unread_first`.

## 2026-10-15 (Agents start without shell re-parsing their arguments (#204))

Launch, Fork, Resume, the agent login window and the New Chat respawn start the agent through tmux's default-shell (so fish's config.fish still sets SSH_AUTH_SOCK, mise and PATH) with the argv as positional arguments: no shell re-parses them, and fish no longer misreads sh quoting.
New Chat's typed relaunch in an interactive shell is quoted for that pane's shell (fish or sh/bash/zsh/dash); an argv that cannot be typed is refused before the agent is asked to exit.
Prompts, arguments and working directories ending in ';' arrive intact (tmux no longer reads them as a command separator).

## 2026-10-15 (Dates follow the display time zone (#202))

- Dates pm writes or shows follow the configured display offset (`display_utc_offset_h`) instead of the host timezone: issue/TODO title dates, imported item times, CHANGELOG and FEATURES dates, backup file names, the Servers panel time and the Usage day buckets. Same on a host whose timezone matches the offset.
- `pmctl issue show` prints times with their zone (e.g. `2026-10-15 03:28:45 UTC+7`). (#202)

## 2026-10-15 (New Chat keeps a launched agent's window (#201))

- New Chat on an agent started by Launch no longer closes its tmux window: when the agent has to quit and relaunch (non-Claude kinds, or Claude with an update pending), pm keeps the pane and restarts the agent in it (`respawn-pane` with an argv list). Panes where the agent runs under an interactive shell work as before. (#201)

## 2026-10-15 (Per-browser UI theme)

- Each browser can pick its UI theme: Settings › UI theme (shown when a plugin adds a theme) or `?ui=<name>`, remembered in the cookie `pm_ui`; pm.toml `theme` stays the default. Classic looks unchanged.
- Plugins: new hook `template_globals()` (keys prefixed with the plugin name); theme and plugin static files get `?v=` cache-busting like `/static`.
- Hub event type `ui` (`{area, rev}`) for a theme's own UI state.
- Tools: `archcheck.py --static-dir DIR` checks a theme's static tree (plus line length ≤ 120 and z-index tokens); `uishot.py --ui NAME`.

## 0.1.0 — 2026-10-14

First public release.

- Web control plane for one Linux host: projects, git, tmux sessions and
  coding agents (Claude Code, Codex, Devin, Cursor Agent, Antigravity).
- Agent Inbox, live chat view with composer, launch / fork / resume / exit,
  Quick chat, Builder with a sandboxed live preview.
- Projects with git status and actions (remote `gitdev`), TODO, backlog,
  plans and a files browser. Issue tracker in SQLite with the `pmctl` CLI.
- Servers page: host resources, processes, Docker containers, libvirt VMs and
  TCP checks of other hosts (`servers` in `pm.toml`).
- ntfy notifications and health alerts (off until you set an ntfy URL).
- Security: token or passkey login, optional TOTP unlock, CSRF + Origin
  checks, tiers, protected tmux sessions, fail-closed audit log, masked pane
  text.
- Host-neutral defaults: binds `127.0.0.1` only, `base_url`
  `http://127.0.0.1:8093`, no pv links, no extra projects, no git hosts, ntfy
  off, UTC display, no protected VM names.
- Docs: install, config reference, security model. Bundled extras: systemd
  unit, nginx example, Docker proxy + ntfy stack, `tmux-agents`.

