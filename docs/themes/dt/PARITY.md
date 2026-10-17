# pm v7 parity map (M5)

Written 2026-10-15 (updated after the M5 gap fixes) for PRD §10 M5 and goal 1 ("every current pm function is
reachable in v7"), the first cutover step. Input: every `FEATURES.md` entry outside "v7 (dt theme)" (137) plus 3
core-only API entries (140 in all). v7 side: `docs/themes/dt/README.md` (A1–A26), the "v7 (dt theme)" entries,
`src/pm/web/dt/*.py`, `src/pm/web/themes/dt/templates/**`, `shell/_classic.html`.

**How it was checked.** Each entry was mapped from the code. Then a dev instance ran the dt theme: `tools/devpm.py
start --port 8501` with this worktree's pm, plus `terminal_enabled = true`, cookie `pm_ui=dt`, and the fixture project
`fixproj` imported into the tracker (`pmctl import`). A Playwright script loaded every route and asserted the
selectors and text. That was 156 assertions, plus 10 open/close checks of search, bell, Quick chat, Launch, `?`, the aside
embed, `/term/s0` and the phone bottom nav. Result: 159 pass. The 7 fails are the expected absences marked `dev ✗`
below, plus the Notifications Review button: the dev fixtures have no ntfy row tied to a project agent. It also tested the classic deep links (`#issue=`, `#issues`, `#browse=`,
`#view=`, `?qchat=`, `#draft=`). Nothing mutating was pressed. **Checked:** `dev` = asserted on the dev instance;
`dev ✗` = confirmed absent there; `code` = read in code only (needs fixture data or a mutating click to show).

**Status.** `ported` = a dt page or part does it. `classic-in-shell` = reachable under `?ui=dt`, but the content is
still classic markup or JS (removal debt). `partial` = reachable, but part of the function has no dt way in.
`missing` = not reachable under `?ui=dt`, or only by typing a URL. `n/a` = backend, CLI, tool or config, the same in
both UIs.

| Status | Count |
|---|---|
| ported | 77 |
| classic-in-shell | 33 |
| partial | 6 |
| missing | 0 |
| n/a | 24 |
| **total** | **140** |

## Navigation

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Left sidebar | ported | `shell/_sidebar.html`, `_sessions.html`; project list = project view sidebar (`project/_sidebar.html`) | dev | Account footer → account menu + sidebar foot |
| Account menu | ported | `shell/_top.html` `#x-acct` | dev | Agents / Servers moved into the sidebar |
| Top bar shortcuts | ported | `shell/_top.html`: Launch, bell, Quick chat, search, aside; Stop all in the account menu | dev | Backlog / Audit in the sidebar |
| Phone tab bar | ported | `shell/_bnav.html` (More = sidebar drawer) | dev | asserted at 390 px |
| Global search | ported | search dialog `#x-pal` (`js/search.js`, dt `_search.html`) | dev | M5: from 3 characters adds the core `GET /search` groups (projects, tmux, ended agents, chats, Quick chat, files) |
| Command palette | ported | `#x-pal` with `>` / ⌘⇧P (`keys.js` commands) | dev | "Add an issue" opens the Backlog's Add issue dialog on any page (`GET /dt/add-issue`; the page's project preselected); classic has no such command |
| Chat drawer | ported | chat aside (`shell/_aside.html`, `js/aside.js`, `/a/<id>?embed=1`) with the session's view tabs and panels (README A29) | dev | `#chat=` hash replaced by remembered aside state |
| Jump rail | ported | `session/_frame.html` `[data-c-jump]` | dev | |
| Installable app | classic-in-shell | manifest in dt `base.html` | dev | SW registration (`/static/pm.js`) and app badge (classic `inbox.js`) come from `_classic.html` |
| Live updates | ported | dt `js/live.js` | dev | Inbox / chat still morph with classic `nodiff.js` |
| Service reload banner | ported | `js/reload.js` bar at the top of `#main` | dev | M5: after Reload pm waits on `/healthz` (bounded) and reloads; on 502/503/504 offers Reload now; "New code: restart pm to load it" (+ commit subject) from the core's `pm_code` verdict, Restart pm after a confirm (or the systemctl command when reload is off) |
| Sortable, filterable lists | ported | `parts/_table.html` (`a.p-sort`), `parts/_filter.html` | dev | |
| List filter box | ported | `parts/_filter.html`, `js/filter.js` | dev | |
| Notices | ported | toast `#x-toast` (`pm7.toast`) | dev | composer notice line is classic |
| Menus and dialogs | ported | `js/layers.js`: `#x-menu`, `dialog#x-dlg`, `#x-sheet` | dev | confirms inside classic content still use classic `dialog.js` |
| Tab rows | ported | `parts/_tabs.html`, `js/tabs.js` | dev | |

## Inbox

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Agent inbox | ported | `/` (`_inbox.html`, `parts/_agentrow.html`) | dev | |
| Last message column | ported | agent row `.i-src` pill | dev | |
| Row detail | ported | row details (chevron / X / →) | dev | the recap is the core's `/a/<id>/recap-row` fragment (classic `rows.css` markup) |
| Idle agent exit | ported | row ⋯ › Exit… | dev | |
| Ended list | ported | `/?view=ended` (Resume, Show all) | dev | |
| Unread badges | ported | sidebar / bell / bottom-nav badges, Inbox unread | dev | app-icon badge via classic `inbox.js` |
| ntfy page | ported | `/ntfy` (`ntfy.html`, `_ntfy.html`) | dev | |
| Notifications bell | ported | top-bar bell popover, `/notifications` | dev | README A2 |
| Live ntfy stream | n/a | backend | — | |
| Pinned sessions | ported | Inbox Pinned band, pin button / P | dev | |

## Agents

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Launch form | ported | Launch dialog (`shell/_launch.html`, `_launch_form.html`, `GET /launch/form`) | dev | classic `/p/<n>/launch` still renders in the shell (used by Review) |
| Bypass modes | ported | Launch dialog, "I understand" | dev | |
| Remembered launch choices | ported | Launch dialog foot; Settings › Agents | dev | |
| Dispatch TODO item | ported | Launch dialog › From TODO; Backlog Dispatch | dev | |
| Stop agents (PANIC) | ported | account menu › Stop all agents… (`dialog#x-stop`); project head Stop agents | dev | |
| Review | classic-in-shell | Notifications row Review, session ⋯ › Review the changes → classic `/p/<n>/launch?review=<id>` | dev | the launch page is classic content; the Notifications button is code-checked (no fixture row) |
| Fork | ported | session ⋯ › Fork (live and ended) | dev | composer Fork / Fork and send are classic |
| New Chat | ported | session ⋯ › New chat | dev | clicks the classic composer form |
| Continue… | classic-in-shell | session ⋯ › Continue… → classic Continue… popup (`continuepop.js`) | dev | |
| Origin link | ported | session head meta line (`sf.meta_line`) | code | no forked fixture |
| Exit and Resume | ported | session ⋯ › Exit / Exit and kill window…; ended ⋯ › Resume | dev | |
| Ended agent record | ported | `agent_ended.html` frame and record | dev | transcript and ended composer are classic |
| Agent page header | ported | `session/_frame.html` | dev | |
| Model and effort switch | classic-in-shell | composer model pill (classic `_composer_macros.html`) | dev | |
| Pane tab | classic-in-shell | session Pane tab (classic `_pane.html`; also in the chat aside, README A29) | dev | |
| Running sub-agents strip | classic-in-shell | `#sa-live` in the session chat | code | |
| Sub-agents tab | classic-in-shell | session Sub-agents tab (also in the chat aside, README A29) | dev | |
| Agents catalogue (Installed) | ported | `/agents` | dev | |
| Skills, rules and scripts library | ported | `/agents?t=library`, `/agents/<name>?s=skills` | dev | |
| MCP servers overview | ported | `/agents?t=mcp` | dev | |
| Usage statistics | ported | `/agents?t=usage` | dev | |
| Per-agent detail | ported | `/agents/<name>` | dev | |
| Agent login (Auth row) | classic-in-shell | `/agents/<name>`: `_agents_auth.html` in `.x-cl.pg-agents` | dev | README A22 |
| Edit skills, rules, scripts | ported | Agents ⋯ › Edit… / Review / Apply (`agents/_mode.html`) | code | not clicked |

## Chat

All of these sit in the session tab's classic content (README A5), except where noted.

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Chat view | classic-in-shell | `/a/<id>` Chat (`.c-msgs.x-cl`) | dev | |
| Detail \| Compact chat view | classic-in-shell | toggle in the dt header; behaviour from classic `chat-compact.js` | dev | |
| Live strip | classic-in-shell | `#chat` in the session chat | code | |
| Needs you card | classic-in-shell | live strip | code | |
| Queued message | classic-in-shell | live strip | code | |
| Context-full suggestion | classic-in-shell | live strip | code | |
| Recap | classic-in-shell | session chat; Inbox row details (core fragment) | code | |
| Composer | classic-in-shell | `.c-comp.x-cl [data-composer]` | dev | |
| Message outbox | classic-in-shell | session chat | code | |
| Slash-command palette | classic-in-shell | session composer (`slash.js`) | code | the dt Launch dialog and Quick chat have no `/` list |
| @ file picker | classic-in-shell | session composer | code | ported in the Launch dialog |
| Snippets | classic-in-shell | composer Send caret; Pane Send snippet | code | |
| Save as issue | classic-in-shell | composer caret menu (classic) | dev | M5: the "Open it" link `/p/<p>#issue=N` opens the issue reader; the composer itself is classic (A5) |
| Token statistics | classic-in-shell | `#chat-stats` | dev | |
| Claude plan limits | classic-in-shell | token panel | dev | also dt: Agents › Usage meters |
| Per-agent usage | classic-in-shell | session ⋯ › Usage opens `#chat-stats` (classic `_usage_chat.html`) | dev | |
| Canvas | classic-in-shell | newest reply's Canvas (classic `canvas.js`, `_mdpane.html`) | code | |
| Markdown side panel | classic-in-shell | classic `_mdpane.html`; in the aside `.md` opens a sheet | code | |
| Image viewer | classic-in-shell | classic `imgview.js` | code | |
| Chat text size | ported | session head Aa (`[data-c-font]`) | dev | |
| Resizable Markdown tables | classic-in-shell | classic `tablesize.js` | code | |
| Markdown text size | classic-in-shell | classic `_mdzoom.html` in project Docs and the md pane | code | |
| Quick chat | ported | `shell/_qchat.html` (Q, top-bar icon) | dev | M5: `?qchat=<id>` opens that conversation |
| btw (side question about an agent) | partial | session ⋯ › btw, composer btw, `/btw` → aside | code | phones: toast only (README A9) |

## Projects

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Projects list | ported | `/p` | dev | |
| New project | ported | `/p` › New project dialog (`projects/_new.html`) | dev | README A14 |
| Project page | ported | `/p/<name>` | dev | the tmux sessions table is under "Project tabs" |
| Project documents panel | ported | tabs Files, Git, TODO, Issues, Plans, Docs (+ Skills) | dev | M5: Docs › Skills (Local / Global), `#view=` / `#skill=` links; rendered docs are classic |
| Project settings | ported | project head gear → reader aside (`#settings`, dt `_project_settings.html`) | dev | M5 |
| Project files browser | ported | project Files tab (`project/_files.html`) | dev | file view classic; M5: `#browse=<path>` opens Files at that path |
| Project tabs: tmux \| Stack \| VM | ported | tmux, Stack and VMs tabs | dev | M5: tmux tab with Close all (typed project name) |
| TODO card | ported | project TODO tab (Backlog component) | dev | tracker and TODO.md file projects: list and tick (file items by line + text, `POST /p/<n>/todo/toggle`); Add task and Edit stay tracker-only; `#todo` links open the tab |
| Backlog | partial | `/backlog` | dev | BACKLOG.md file projects: items not listed; whole-file editor only via a `?ui=classic` link |
| Issues tab and issue detail | ported | project Issues tab, issue reader dialog | dev | M5: Edit (title, Markdown body) and Delete (typed `#N`); `#issue=N`, `#issues` links work |
| Issue tracker (BACKLOG.md / TODO.md from pm.db) | n/a | backend, `pmctl` | — | |
| Plans | classic-in-shell | project Plans tab + reader (dt) → All plans / New plan = classic `/p/<n>/plans` | dev | edit, rename and delete are classic only |

## Git

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Git actions | ported | project Git tab, head Git ▾ | dev | |
| History | partial | Git tab Commits; git badge in Projects | dev | the hash is not linked to the commit URL (`commit_url_base`) |
| Remotes config | ported | Git ▾ › Remotes… (`#remotes`, dt `_git_remotes.html`) | dev | M5: typed confirm for removing a remote |

## tmux

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| tmux sessions page | ported | `/tmux` | dev | |
| New session | ported | `/tmux` head New session | dev | |
| Kill session / window / pane | ported | `/tmux` row ⋯ › Kill…, typed name | dev | |
| Agent sidebar | ported | replaced by the sidebar sessions list (by project) | dev | the agent's tmux tree is on `/tmux`; ended agents are in the project Agents band |
| Read-only terminal | classic-in-shell | `/term/<sid>` as a tab; session Terminal tab | dev | classic `term.html` |

## Servers / VMs

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Servers overview | ported | `/servers` | dev | |
| Docker services | ported | `/servers?t=services` | dev | |
| Resources | ported | `/servers?t=resources` | dev | |
| Process stop / kill | ported | Resources row ⋯ / details | code | not clicked |
| VMs (libvirt) | ported | `/servers?t=vms` | dev | |

## Settings

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Appearance | ported | Settings › Appearance | dev | |
| Settings popup | ported | ⌘, / sidebar Settings → `dialog#sp-dlg` (same body as the page) | dev | M5 (A23, A27) |
| Agents defaults and notify rules | ported | Settings › Agents | dev | |
| Composer settings | ported | Settings › Composer | dev | |
| Quick chat settings | ported | Settings › Quick chat | dev | |
| Auto-exit idle agents | ported | Settings › Agents (Auto-exit) | dev | |
| SSH keys | ported | Settings › SSH keys | dev | |
| Remote servers | ported | Settings › Remote servers | dev | |
| Config file options | n/a | pm.toml | — | Settings › pm service shows a read-only subset |
| UI theme | ported | Settings › Appearance › UI theme; account menu › Classic UI | dev | the pm.toml key is n/a |
| Plugins | partial | plugin nav items in the dt sidebar | code | dt Settings does not render plugin Settings sections (`settings_sections_html`); none exist today |
| External links from plugins | ported | project links row under the head; agent folder link in the session meta | dev | M5: `ext_groups` rendered; "Full Transcript" only in the classic chat |

## Notifications

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Agent state notifications | n/a | backend | — | |
| Server health alerts | n/a | backend | — | rules in Settings › Agents (ported) |
| Claude hook | n/a | backend | — | |

## Security

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Login | n/a | `/login` is theme-neutral (core `security/auth.py`) | — | |
| Passkeys | ported | Settings › Access | dev | Register uses classic `passkey.js` |
| Sessions | ported | Settings › Access | dev | |
| Unlock (optional) | partial | lock bar in dt `base.html` | dev ✗ | no "Set up TOTP" (`/unlock/provision`); only matters with `require_unlock = true` |
| Audit log | ported | `/audit` | dev | |
| Client error log | ported | `/audit?tab=errors` | dev | |
| Health check | n/a | `/healthz` | — | |

## Phones / Builder

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Builder | classic-in-shell | `/a/<id>/builder`: dt frame + Builder toggle, classic preview / chat | dev | |
| Phone layout | ported | bottom nav, sidebar drawer (Launch row), sheets | dev | |
| Builder API pane | classic-in-shell | Builder preview toolbar (classic `builder-api.js`) | code | |

## Background

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Auto-exit sweeper | n/a | backend | — | |
| Poll loop and state tracker | n/a | backend | — | |
| Chat feed and deliverer | n/a | backend | — | |
| Event-loop watchdog | n/a | backend | — | |
| Reload-need check | n/a | backend; its watcher's `pm_code` verdict drives the "new code" bar (`js/reload.js`) | — | |
| pm.db daily backup | n/a | backend, `pmctl backup` | — | |

## Tools

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Dev instance | n/a | `tools/devpm.py` | — | used for this check |
| UI checks | n/a | `tools/uishot.py --ui dt` | — | |
| Mock server | n/a | `tools/mocksrv.py` | — | |
| Statusline and askpass | n/a | tools | — | |
| Deploy | n/a | `deploy/` | — | |
| `pmctl` CLI | n/a | CLI | — | |

## Core-only entries (core `FEATURES.md`)

| Feature | Status | v7 place | Checked | Note |
|---|---|---|---|---|
| Git tab API | n/a | API behind the dt Git tab | dev | |
| Project stack and VMs API | n/a | API behind the dt Stack / VMs tabs | dev | |
| Settings action replies | n/a | API behind dt Settings (`{ok, notice}`) | dev | |

## Missing and partial

"dt-only" = the core route and data exist, so only `pm.web.dt` / `themes/dt` change. "needs core" = a theme-neutral
core change first (V7-ISO-4).

Closed in M5 (2026-10-15): Project settings, Remotes config, Settings popup, Global search, Service reload
banner, Save as issue / Issues deep links, Project files deep links, Project documents Skills, Project tmux tab,
Issue Edit / Delete, `?qchat=`, plugin links, and the service worker / app badge entry in `_classic.html`'s
removal-debt list. Still open:

| Entry | Gap | What it takes |
|---|---|---|
| Backlog | file-mode BACKLOG.md items and whole-file editor | dt-only: list `entries` items of non-tracker projects; port the lease/lock editor (or keep the classic link until those projects are imported) |
| History | commit hash not linked | needs core: `/git/status` JSON has no commit URL; add `commit_url_base` (only for `git_allowed_hosts`) |
| btw | phones | dt-only: a full-screen btw panel on phones (Quick chat full-screen layout) |
| Plugins | plugin Settings sections | dt-only: a Settings section that renders `settings_sections_html` (no plugin uses it today) |
| Unlock (optional) | no TOTP set-up | dt-only: "Set up TOTP" in Settings › Access when `require_unlock` and not provisioned (`POST /unlock/provision`) |

## Classic in shell (removal debt)

Each needs a dt port before `shell/_classic.html` can be deleted (the last cutover step). The list in `_classic.html`
names the file-level hooks; this is the feature-level view.

- **Session chat stack (README A5):** Chat view, Detail | Compact, Live strip, Needs you card, Queued message,
  Context-full suggestion, Recap (also the Inbox row's core recap fragment), Composer, Message outbox, Slash-command
  palette, @ file picker, Snippets, Save as issue, Token statistics, Claude plan limits, Per-agent usage, Canvas,
  Markdown side panel, Image viewer, Resizable Markdown tables, Markdown text size, Model and effort switch,
  Continue…, the composer's Fork / Fork and send, and the ended composer.
- **Session sub-tabs:** Pane tab (keypad, Send snippet, Interrupt), Running sub-agents strip, Sub-agents tab, Terminal
  tab and `/term/<sid>` (Read-only terminal).
- **Builder:** Builder preview, screen / zoom / source selects, Builder API pane.
- **Classic pages still rendered in the shell:** the launch page `/p/<n>/launch` (Review), the plans pages
  `/p/<n>/plans`, `/p/<n>/plans/<plan>` (Plans: edit, rename, delete), `term.html`.
- **Parts on ported pages:** the Agents › one agent login box (A22), Register passkey and the lock bar's passkey
  (`passkey.js`), a file's view and the rendered documents in the project view (`_project_files.html`,
  `_project_doc.html`), the Inbox / chat `nodiff.js` morph, classic `dialog.js` for confirms inside classic content.
- **Service worker and app badge:** service-worker registration (`/static/pm.js`) and the app-icon badge (classic
  `inbox.js`) come from `_classic.html`; since M5 both are on its removal-debt list. Port them before deleting it.
