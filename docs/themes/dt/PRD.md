# pm v7: product requirements

Status: written from the design mock (not published) on 2026-10-14; acceptance ticked as built. Rules: `CONDUCT.md`. Look: `DESIGN.md`. Geometry: `LAYOUT.md`.

## 1. Summary

pm is the control plane for projects, git, tmux and coding agents on one Linux host, used by one person from a Mac
(MBP 14", Dell 27", Dell 34") and an iPhone. v7 keeps every function of the current pm and changes how you
move around it:

- **one sidebar** instead of top tabs + contextual sidebar + rail;
- **session tabs**: several agent chats (and read-only terminals) open at once, synced across devices;
- **a chat aside** on any page (⌘L) and **one search dialog** (⌘⇧P / ⌘K, ⌘P for sessions);
- **keyboard first**: every common action has a key, shown in a `?` sheet;
- every page re-organised around "what needs a look" (status lines, Needs you groups, folded repeats).

## 2. Problems v7 solves (from the review of the current pm)

| Today | v7 |
|---|---|
| Three left columns in the proposals (rail + context list + sidebar); two toggles | one 248 px sidebar, ⌘B to a 48 px rail |
| Two search UIs (navbar search + command palette) with different looks | one dialog, `>` for commands |
| One session at a time; chat aside and full chat page are two homes for one chat | session tabs; the aside and the tab are the same chat, ⌘⇧L moves between them |
| Unread not visible on Inbox rows; running state lags behind ntfy; no pin | unread count on the row; state, message and unread arrive in one event; pins synced |
| Settings one 5 800 px page with eight Save buttons | one section at a time, search all settings, save on change |
| ntfy page + notifications page + bell | one Notifications page, bell = latest 6 |
| Audit one long table of 6 000 rows | grouped by day, folded repeats, denied/failed marked |
| Launch a full page per project | a dialog from anywhere (N) |

## 3. Goals and non-goals

Goals:
1. Every current pm function is reachable in v7 (parity list: `FEATURES.md`, each entry mapped to a v7 page).
2. The common loop (see who needs you → answer → move to the next agent) needs no mouse.
3. The same tabs, pins and per-user settings on every device, live.
4. Phone is a first-class layout, not a squeezed desktop.
5. v7 runs beside the current pm without changing it (`CONDUCT.md` §2) until the user switches over.

Non-goals (v7.0):
- Multi-user, roles, sharing. pm stays single-user.
- Typing into tmux from the browser terminal (it stays read-only).
- Drag to reorder tabs (later).
- A JS framework or a build step.
- Changing what agents do or how they are launched (domain stays; only the UI and small new stores change).

## 4. Users, devices, screens

One user (the owner). Verify on these screens (`LAYOUT.md` §1):

| Screen | Viewport | Engine |
|---|---|---|
| MacBook Pro 14" | 1512 × 982 | Chromium (Safari checks for keys) |
| Dell 27" | 2560 × 1440 | Chromium |
| Dell 34" ultrawide | 3440 × 1440 (and half 1720, two thirds 2293) | Chromium |
| iPhone 17 Pro Max | 440 × 956 (also 390 × 844) | real WebKit |

## 5. Information architecture

- **Sidebar** (top to bottom): Inbox · Projects · tmux · Notifications · Backlog · Agents · Servers; Sessions
  (grouped by project, filter box); foot: Quick chat (phone), Audit, Settings (popup), Collapse.
- **Top bar**: sidebar toggle, brand, Quick chat, tab strip, search, tabs count (phone), bell, Launch, aside
  toggle, account menu (Settings full page, Keyboard shortcuts, Dark/light, Reload pm, Stop ALL agents, Log out).
- **Tabs**: tab 0 = the current page (pinned, never closes, fixed width). Other tabs = one session each (agent
  chat, or a read-only tmux terminal). Opening an already open session focuses it.
- **Chat aside**: one agent chat at the right of any page; same composer as the tab.
- **Phone**: bottom nav Inbox · Projects · tmux · Alerts · More (= sidebar drawer); session tab hides the
  bottom nav; tabs button opens a sheet; swipe the title for next/previous tab.

## 6. Requirements by area

Each area lists **must** functions and the **acceptance** checks (all screens in §4, light and dark).
"Row details" = the chevron/`→` opens an in-place detail; "⋯" = the row menu (bottom sheet on phone).

### 6.1 Shell

Must:
- Sidebar open / 48 px rail (⌘B, remembered per user); overlay with scrim under 900 px; drawer on phone.
- Session list grouped by project: state dot, id, working word or age, blue dot when open in a tab; filter.
- Tabs: open (Enter in search, sidebar click, aside tab button, Inbox double-click, phone tap), close (⌥W,
  never tab 0), reopen (⌥⇧T), previous/next (⇧[ ⇧], ⌘⌥[ ⌘⌥] inside the composer), move to aside (⌘⇧L).
- Tabs stored **on the server per user**: open tabs, order, current tab, reopen list; a live event when they
  change so every device follows.
- Chat aside: ⌘L opens on the selected (else last) agent with the cursor in its composer; again closes; drag
  edge 20–50 % (remembered), double-click resets to 30 %.
- Search dialog: ⌘⇧P / ⌘K = everything (sessions, projects, messages, files, commands); `>` = commands only;
  ⌘P = sessions scope (open tabs, running and waiting, recent). ↵ opens in a tab, ⌘↵ in the aside.
- Shortcuts sheet (`?`), toast that names the shortcut that fired (Settings › Display › Shortcut helper).
- Esc order: dialog → menu/sheet → Quick chat → sidebar drawer → clear filter text → leave text field → aside.

Acceptance: tab strip does not move when switching pages (fixed home-tab width); a tab opened on the Mac shows
on the iPhone within 2 s; no key in §8 of `DESIGN.md` collides with a browser key that the page cannot take;
the rail shows a yellow dot for "needs you".

### 6.2 Inbox

Must:
- One table, groups as foldable bands: Pinned · Needs you (waiting + failed) · Running · Idle; or Group: project.
- Views with counts: All · Needs you · Running · Idle · Ended; Unread switch; the one filter box.
- Sort by header: Agent, Project, Age, CPU, RAM (click again reverses).
- Row: kind edge, state dot, unread count, ctx warning ≥ 80 %, project, tmux link (opens a terminal tab), last
  message with source (you / ntfy), pending question in yellow, error in red, running age ticking in place.
- Click = aside, double-click = tab (phone: tap = tab); opening marks read.
- Row details: answer buttons for a pending question (1 / 2 / 3), cached recap (Refresh in place, no flash),
  model, start, tmux, folder, context bar, CPU/RAM, Open chat / Open in a tab / Terminal / Mark read /
  Interrupt or Exit.
- ⋯: Pin, Mark unread, Copy id, Kill window (typed).
- Ended view: hollow dot, ran for, ended, Resume (dash when the kind cannot resume), first 8 + Show all.
- Keys: J K move, ↵ aside, O tab, X / → details, E read/unread, P pin.
- Pins stored per user on the server.

Acceptance: state, last message and unread change together in the event that brings the ntfy (no lag behind
ntfy, #185); running ages tick without redrawing the table; the recap never flashes (#162).

### 6.3 Session tab (full chat, Builder, reader, sub-agents, pane, terminal, composer)

Every function of today's agent page, Builder and chat drawer is kept; only the layout changes.

Must:
- Header (one 44 px line): state, name, model, project · tmux link · folder · Fork from / Continued from; tabs
  Chat · Sub-agents (Claude, running count) · Pane · Terminal; Detail | Compact; Aa; Builder toggle; ⋯ (New chat,
  Continue…, Fork, Review, Usage, btw, Copy id, Exit, Exit and kill window [typed]); move to aside.
- Chat items in order: recap fold, Load older, your bubble with outbox state (Saved, Waiting, Sending, In pane,
  In transcript, Not sent; Send now / Edit / Cancel), Working notes with one tool total, final reply ("took",
  Copy, Canvas, Continue…), live strip (working word, waiting tool with last pane lines, background shells,
  running sub-agents, queued message with Send now), **Needs you** card (2 px yellow frame, band, question,
  diff, numbered answers, Type something, Chat about this), context-full hint.
- Markdown reader: docked side panel with a resizable divider when the chat column is ≥ 1000 px, else a popup
  with "Dock beside the chat"; outline, A− / % / A+, copy, open as a tab, full width, Esc closes.
- Canvas in the same panel slot: Edit | Split | Preview, versions (restore), revise, autosave, Delete (asks).
- Builder = a mode of the tab: chat (resizable, 440 px) + preview; reload, open, Static files | URL (allowed
  hosts only), screen size, zoom with "Fit N%", Files; phone: Chat | Preview tabs.
- Sub-agents: filter, All / Running / Finished, groups with progress; a row opens a mini chat in the side panel.
- Pane: masked pane text, live; keypad (Answers, Move, Keys, Ctrl, snippets, Interrupt); behind Keys under
  1000 px and on phone.
- Terminal: read-only (window ‹ ›, Aa, History, Reconnect, Live); foot says nothing is sent.
- Composer (one component for tab, aside, Builder, Quick chat): token line (context meter amber 60 %, red 80 %
  with Compact; tokens, turns, 5 h and weekly limits; click = detail above); Enter sends; ↑ recalls; Esc restores
  the draft; drafts per chat; `/` commands (danger ones confirm) and `@` files; paste (long paste → file,
  "Paste as text instead"), drop cover, attachment chips with preview; Send caret menu (Attach files, image,
  Paste screenshot, btw, snippets, Save as issue); red **Stop** replaces Send while the agent runs and the box is
  empty; model and effort as plain text under the box (click = model / effort / more models).
- btw: opens in the aside next to the agent's tab; says nothing reaches the agent; Use in composer, Copy.
- Settings › Chat colours apply (agent message background, edge, model pill, user bubble, kind and tool colours).

Acceptance: nothing typed reaches the agent without the outbox showing its state; Stop/Send swap is instant;
reader docking follows the width rule; Builder preview obeys `CONDUCT.md` V7-SEC-8.

### 6.4 Projects

Must:
- List: stats line, New project; filter (name, folder, branch); segments All · Dirty · Has agents · Has TODO ·
  Pinned with counts; columns pin · Project · Agents · TODO · Issues · ntfy · Active · Git · Stack; every
  column sortable (Active included); pin synced per user (also from the phone, via ⋯).
- Project view: project sidebar (projects with their agents, filter, need-you badge, collapsible ⌘⇧B,
  remembered; README O3), head with name + stats line, actions Launch agent (the one green), Git ▾, Restart
  stack, Stop agents (typed PANIC); tiles Agents, Git, TODO, Issues, Stack, VMs (click = tab).
- Tabs: Agents (Inbox rows + Ended with Resume) · Files (browser, README below the listing, file views for
  Markdown with outline, code, image, diff with Stage / Discard [typed]) · Git (changes with stage boxes, commit
  bar, stash, branches with Checkout and ⋯ Merge / Push / Delete [typed], commits; push never
  forced; guards for dirty checkout, behind, diverged, clash → Stash, pull and pop) · Stack (services, Logs,
  Up / Restart / Down [confirm], Add a stack) · VMs (Start/Stop, Console read-only, ⋯ Snapshot / Revert
  [confirm] / Force off [typed] / Delete [typed]; New VM) · TODO · Issues (both = the Backlog component scoped to
  the project, README O7) · Plans · Docs.
- New project dialog: name, template, git + remote, docker stack + hostname, VM, first
  agent; live "What will happen" list; progress that streams real step results and stops cleanly on a failure.

Acceptance: every git action shows what it will do before it runs; no force push path exists; a failed step in
New project leaves a clear state and a message.

### 6.5 Backlog

Must:
- Tabs Issues · TODO with open counts; Add issue / Add task (a real form, issue reader for a row).
- Filter box; Status Open · Done · All; Project select; Group project / none.
- Issues: agent dot (running on / waiting about the issue), #, title, project (flat), notes and images count, age;
  one visible row action + ⋯ (Dispatch, Done/Reopen, Pin, Select, Copy pmctl command, Open project, Move to
  project… [confirm]); bulk bar (Done, Dispatch, Move) that never selects rows in folded groups.
- TODO: grouped by project and section, tick in place, Dispatch (prefills Launch with the task).
- Backed by the tracker (`pmctl issue …`, `pmctl todo …`). **New domain operation:** move an issue to another
  project (new number, notes and images move).

Acceptance: every change is a tracker write through `pm.projects.tracker`; the files regenerate as today.

### 6.6 tmux

Must: sessions table (Windows, Clients, Agents, Active; state dot = worst agent state; lock only where it
means something); filter (also window names); All · With agents · Attached · Protected; windows under a
session (window, pane, command, path, agent with state); ⋯ per window (Open terminal, Kill window/pane
[typed, never on protected]); New session (name + folder); session ⋯ (Open terminal, New window, Launch an
agent here, Copy attach command, Kill session [typed; disabled with the reason for protected and pm's own]);
read-only terminal tabs.

### 6.7 Notifications

Must: one page (G N, "Alerts" on phone); Source All · Agents · System · Pinned; Unread only; period dropdown;
grouped Today · Yesterday · Earlier with Mark read per day; rows with unread dot, kind edge, P-tag, folded
repeats (×N), "recovered after 3m"; row opens to message, tags, Open chat (aside), Recap, Review, Pin, Mark
read; ⋯ Open in a tab, Mute this title 24 h; keys J K ↵ E P O; bell = newest 6, unread first, Mark all read.

### 6.8 Agents

Must: Installed (version, login state, counts, findings badge, Not installed folded; side detail with paths,
log in/out, Skills · Rules · MCP · Scripts with a switch and ⋯ per item); Library (one row per item, agents
chips, "same / N differ", ⋯ View, Edit, Sync copies, Link into, Remove to the pm trash); MCP servers (one row per
server, configured-in chips, "key in args" warning, coverage dots with + to add); Usage (totals, by-day chart,
by-model share, Claude plan limits, top sessions); Findings tab (grouped; every fix shows the exact file
operations, then Apply with backups). Never touches `~/.agents-git` or `~/.cfg` internals.

### 6.9 Servers

Must: Overview (one status line, "worth a look" list linking to the right tab, meters with level colours, Hosts
with latency and ⋯); Services (filter, All / Running / Unhealthy / Exited, List / By stack with Restart stack,
Health, row detail with Restart / Stop / Kill; protected names locked); Resources (summary meters, one table at a
time, top 10 + Show all, ⋯ Stop / Kill / Open in tmux); VMs (sortable, Undefine typed, Images All / Unused, pool
meter).

### 6.10 Audit

Must: tabs Activity · Client errors; filter (action, target, detail, client); Result segments with counts
(Denied red); category select; Attempts switch; Grouped / Raw (runs folded with a summary); days as headings
with "N denied or failed"; row opens to every key, client, session, raw entries, Copy as JSON, Only this action;
targets link (tmux id → terminal tab, agent → chat, issue → project); Export CSV. Client errors: one row per
message and page with count, kind, pages, last seen; masked stack; Save as pm issue; Clear all (asks; never
touches the audit log). Audit is read-only.

### 6.11 Settings (page and popup, same code)

Must: groups You · Work · Security · System; one section at a time; Search settings across all sections (hit
counts); save on change with a toast (no Save buttons); each group says where it lives: **all devices**
(server, per user), **this browser**, or **pm.toml** (read-only). Sections and every setting: classic's Settings
page is the checklist (`PARITY.md` "Settings"). Phone: section list, then one section with ‹ Settings. ⌘, toggles the popup.

Acceptance (popup, M5, README A23): [x] ⌘, / Ctrl+, opens and closes the popup on every page but the Settings page
(there it focuses the page's search); [x] the sidebar foot's Settings opens it, the account menu keeps the full page;
[x] the popup body is the page's body (`GET /settings/panel?s=<id>`, one template, one `pm7.settings.init`), nav
switches inside without changing the URL, `pm7.settings.open(id)` deep-links a section; [x] saves toast as on the
page; [x] Esc closes a confirm inside first, then the popup, focus returns to the opener; [x] "Open as page" goes
to `/settings?s=<section>`; [x] phones: full screen, section list then one section.

### 6.12 Quick chat and Launch

Quick chat must: panel under its icon (Q), page stays usable; conversation list (search, Today / Yesterday /
Earlier); chips: model, Text only / read / full + folder (full asks once; fixed after the first message), MCP,
Skills; empty state with starters; ⋯ Rename, Copy as Markdown, Open as a tab, Delete; Expand docks over the page
with tabs visible; phone full screen.

Launch must: dialog from anywhere (Launch button, N, search); project picker in the title; running agents as one
warning line; agent cards with kind colour and login state; one model picker + Effort; Permissions with a
sentence per mode, bypass needs "I understand"; where it runs + optional worktree branch; prompt with templates,
`@` files, attachments; From TODO tab; footer: what is remembered, Launch (opens as a tab, ⌘↵) or Launch in
background.

## 7. Cross-cutting requirements

| Area | Requirement |
|---|---|
| Live | One SSE stream per page; agent state + last message + unread + ntfy in one event; tabs/pins change events; clocks from server timestamps via one ticker |
| Sync | Tabs, pins, "all devices" settings stored per user on the server (v7 store, `CONDUCT.md` V7-ISO-9) |
| Security | `CONDUCT.md` §3, unchanged bar from the current pm |
| Performance | first paint server-rendered with the right page, tab and selected row (no flash, works with JS blocked for reading); a table swap never resets filter text or scroll |
| Accessibility | WCAG AA contrast both themes; full keyboard; `aria-sort`, `aria-selected`, `aria-current`; dialogs focus-trapped and return focus |
| Phone | no horizontal scroll at 390; inputs 16 px; targets ≥ 40 px; two-line rows (`DESIGN.md` §6.8) |

## 8. Backend changes v7 needs (domain, shared with the current pm)

| Need | Where | Note |
|---|---|---|
| Tabs store (open, order, current, reopen list) + change event | `pm.web.dt.stores` via `JsonStore`; event through the core hub | v7-only state |
| Pins (sessions, projects, issues, notifications) | v7 store | v7-only state until cutover |
| v7 prefs ("all devices" settings) | v7 store | separate from `prefs.json` |
| Issue move between projects | `pm.projects.tracker` + `pmctl issue move` | used by both versions |
| Combined agent event (state + message + unread + ntfy) | `pm.notify` / `pm.agents` | produced by the core loops; both themes read it |
| Search index over sessions, projects, messages, files, commands | domain packages | messages: name-type fields only, masked |
| Project templates, stack templates, VM images catalogues | `pm.projects`, `pm.infra` | for New project / New VM |

## 9. User decisions (dated; do not re-ask)

From the design mock's decision notes (answered 2026-10-13) and this review:

1. One sidebar: yes. (2026-10-13)
2. Search: one dialog; ⌘P = go to session; no search field in the top bar, only the magnifier. (2026-10-13)
3. ⌘L: take it from the browser (Safari to be tried on a real Mac). (2026-10-13)
4. Tabs in the top bar, stored on the server: same tabs, order, current tab and closed list on every device. (2026-10-13)
5. Inbox row: click = aside, double-click = tab; the chevron expands the recap. (2026-10-13)
6. Quick chat: an icon top left after "pm DEV", before the tabs (phone: in the sidebar drawer). (2026-10-13)
7. Icons and names as proposed (tmux = split panes, Notifications = bell). (2026-10-13)
8. ⌥W / ⌥⇧T for close / reopen tab. (2026-10-13)
9. Page tab has one fixed width; the left block is as wide as the sidebar. (2026-10-13)
10. Full chat, Builder and aside: every function kept, layout changes only. (2026-10-13)
11. v7 is the next pm and stays isolated from the current one. (2026-10-14)
12. The v7 mock moves to its own folder, apart from classic's mocks. (2026-10-14)
13. ~~The v7 app is a separate process on its own host. (2026-10-14)~~ Replaced by 14 and 15.
14. v7 is built as the `dt` theme (a theme plus a plugin); no `src/pm/v7`, no second service. (2026-10-15)
15. dt runs beside classic in the same process; each browser picks its UI (cookie `pm_ui`, Settings › UI theme,
    `?ui=dt`). Answers README O1 and O2 (the theme runs no loops). (2026-10-15)
16. dt moves from a private plugin into polymux: `src/pm/web/dt` + `src/pm/web/themes/dt`, registered through
    polymux's own `pm.plugins` entry point; personal features stay in a private plugin. (2026-10-16)

Open decisions: `README.md` O3–O10.

## 10. Milestones

| Phase | Content | Exit |
|---|---|---|
| M0 Mock clean-up | fix the mock review's findings in the mock (tokens, layers, typed confirms, bugs) | user OK on the mock |
| M1 Skeleton | per-browser theme switch in the core; archcheck + tests extended; dt shell (sidebar, top bar, tabs store, search shell, layers, toasts, keys sheet); un-ported pages render classic content inside it | V7-GATE-1…7 green; `?ui=dt` shows the shell, classic unchanged |
| M2 Core loop | Inbox, session tab (chat, composer, aside), Notifications, live events | acceptance §6.2, §6.3, §6.7 |
| M3 Work pages | Projects, Backlog, tmux, Launch, Quick chat | §6.4–§6.6, §6.12 |
| M4 Admin pages | Agents, Servers, Audit, Settings | §6.8–§6.11 |
| M5 Parity and cutover | every `FEATURES.md` entry mapped and checked; user decision O9; switch the default (`theme = "dt"`) | user yes |
