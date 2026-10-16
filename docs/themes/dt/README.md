# The dt theme (pm v7): start here

`dt` is the second UI theme that ships with polymux. It is a keyboard-driven shell: one sidebar, session tabs,
a chat aside on any page, one search dialog and a `?` keys sheet. Internally it is called **pm v7**; rule ids
(`V7-*`), CSS prefixes and storage keys (`pm.v7.*`) use that name. `classic` stays the built-in default and the
fallback.

## Turn it on

The UI theme is chosen per request, in this order (`pm.web.themes.UIThemeMiddleware`):

1. `?ui=dt` on any URL (`?ui=classic` to go back). A valid value is remembered in the cookie `pm_ui`.
2. The cookie `pm_ui`, set from Settings › UI theme (each browser keeps its own choice).
3. `theme = "dt"` in pm.toml: the default for browsers that have not chosen (`docs/CONFIG.md` "Theme and
   plugins").

dt is loaded as a first-party plugin (`pm.web.dt`, entry point `pm.plugins: dt`). If it fails to load, or
`plugins_enabled = false`, the name `dt` is unknown and every choice falls back to classic.

This folder is the only source of rules for dt. Read this file first, then the file for your task. "The user"
in these files is the person who owns the design decisions; their dated decisions are in `PRD.md` §9. The
original design mock these files were written from is not published; where a file says "the mock", it means that
reference.

## Two themes, never mixed

| | **classic** (built-in default) | **dt** (pm v7) |
|---|---|---|
| Look | v4 theme + v5 shell (top tabs, contextual sidebar, drawer) | v7 shell: one sidebar, session tabs, chat aside, keyboard first |
| Rules | `docs/UI-RULES.md`, `docs/UI-CONDUCT.md`, `docs/UI-GLOSSARY.md`, `docs/ARCHITECTURE.md` | **this folder** (`docs/themes/dt/*.md`) + `docs/ARCHITECTURE.md` for domain code |
| Code | `src/pm/web`, `src/pm/web/themes/classic/templates`, `src/pm/static` | `src/pm/web/themes/dt/` (templates, static, `theme.toml`) + `src/pm/web/dt/` (routes, view models, stores) |
| Process | the pm process | the same process; each browser picks its UI with the cookie `pm_ui` (Settings › UI theme, or `?ui=dt` / `?ui=classic`) |
| CSS / JS names | `.v5-*`, `pm.v5.*` storage keys, `window.pm` | v7 prefixes (`DESIGN.md` §2), `pm.v7.*` storage keys, `window.pm7` |

The one-line test: **if a file path, class, storage key, URL or doc does not say v7 (or `dt`), it belongs to
classic and dt work must not change it** (exceptions: `CONDUCT.md` §2).

**Theme model.** dt is not a separate package tree or service: it is a theme plus a first-party plugin inside
polymux (decided 2026-10-15; moved from a private plugin into polymux 2026-10-16). One process serves both UIs;
the theme is chosen per browser. A `dt` template overrides the classic template of the same name; a page not
ported yet renders its classic content inside the dt shell. Routes hand both themes the same view-model dicts;
core changes that dt needs are theme-neutral (`CONDUCT.md` V7-ISO-4).

## Reading order

| Task | Read |
|---|---|
| Anything dt | this file, then `CONDUCT.md` (the strict rules) |
| What to build, scope, acceptance | `PRD.md` |
| Look, tokens, components, interaction | `DESIGN.md` |
| Building a page from the shared parts (macros, data hooks, JS APIs) | `PARTS.md` |
| The shell's Python / template / JS contract | `SHELL.md` |
| Shell geometry, breakpoints, page layouts | `LAYOUT.md` |
| What classic does that dt does (or does not) do yet | `PARITY.md` |
| Where code goes, naming, limits | `docs/ARCHITECTURE.md`, `CONDUCT.md` §5 |

## Precedence (dt only)

1. An explicit user decision (dated, written down in `PRD.md` §9 or here).
2. `CONDUCT.md` (process and hard rules; security rules from `AGENTS.md` "Hard rules" always apply).
3. `DESIGN.md` and `LAYOUT.md`.
4. `PRD.md` (what), `docs/ARCHITECTURE.md` (how the code is kept).
5. The original design mock (not published): the visual and behavioural reference the docs were written from.
   Where it and these files differ, these files win.
6. After the port, the dt code is the truth for details; fix the doc in the same change when they differ.

Classic's rule files (`docs/UI-RULES.md`, `docs/UI-CONDUCT.md`) do **not** apply to dt, except where a dt file
points to them. `AGENTS.md` "Hard rules" (security, tmux, git, tokens) apply to both, always.

## Status (2026-10-16)

- Design: every page was mocked first (shell, Inbox, session tab, Projects, Backlog, tmux, Notifications, Agents,
  Servers, Audit, Settings, Quick chat, Launch) in the unpublished design mock.
- App: milestones M1–M5 built (`PRD.md` §10); what classic still does that dt does not: `PARITY.md`. Moved from a
  private plugin into polymux on 2026-10-16 (`PRD.md` §9 item 16). Classic stays the default (A1).
- Decided by the user: see `PRD.md` §9. Open: see below.

## Open decisions (ask the user; do not build these into the app until answered)

Each item has the default the docs assume. The mock may keep showing the default.

| # | Question | Default assumed in these docs |
|---|---|---|
| O3 | Project view's second sidebar (projects with their agents, ⌘⇧B) vs the "one sidebar" decision | keep it, Projects page only, collapsible, remembered |
| O4 | Working words setting: Display › Running status (code) or Chat colours (review page) | Display › Running status |
| O5 | Display and Chat colour settings: per user on the server ("all devices") or per browser | per user on the server, as the mock labels say |
| O6 | Keys: `KeyboardEvent.code` everywhere (review page) or `e.key` for mod combos (mock code) | `code` for letters and brackets, `key` for `?` `/` `>` and Esc |
| O7 | Project Issues / TODO tabs vs the Backlog page | the project tabs are the Backlog component scoped to that project |
| O8 | Row actions: Backlog shows Dispatch + Done + ⋯ on one row | one visible action + ⋯ per row; the rest in ⋯ |
| O9 | Cutover: when `dt` becomes the default UI (`theme = "dt"` in pm.toml) | after every page in `PRD.md` §6 passes its acceptance list and the user says so |
| O10 | Wide screens (2560, 3440): page column capped at 1280 px and left-aligned, while session chats centre at 880 px | keep as mocked (`LAYOUT.md` §7) |

## Agent decisions taken while the user was away (2026-10-15; confirm or reverse)

The user asked for M2–M5 to be built and merged per milestone without interruption. Where a choice needed the
user, the agent took the conservative option below. Each is reversible; reversing one is a normal change.

| # | Decision | Why |
|---|---|---|
| A1 | Classic stays the default UI (no `theme = "dt"` in pm.toml); M5 maps parity but does not cut over | README O9 needs the user's yes |
| A2 | Classic bell unchanged (5 system rows); dt's bell reads `GET /ntfy/panel?scope=all` (the Notifications list) and shows the newest 6 of every source, unread first (PRD §6.7). The core's `/ntfy/bell` is system-only and has no source parameter; a `scope=all` there would be a core change | changing the classic bell needs a user yes |
| A3 | "Mark unread" is an override that wins over "read up to" in every Inbox bucket; classic has no button for it | PRD §6.2 E key; classic unchanged |
| A4 | Pins stay the shared stores (`agents/pinned.py`, `notify/pins.py`), not a v7 store | V7-ISO-8 (one writer path) wins over PRD §8 |
| A5 | Session tab M2 ports the frame (header, tabs, layout, aside); chat items and composer keep their classic markup and JS, loaded through a page flag in `shell/_classic.html` | the full `c-`/`k-` port is larger; tracked as a follow-up, M2 acceptance for §6.3 is partial |
| A6 | Open decisions O3–O10 use the defaults in the table above | as documented |
| A7 | Page column widths may use `rem` and `fit-content()` in page CSS (grid subgrid); phone keeps the page stats line visible | DESIGN §3.2 lists no column widths; the phone top bar shows shell totals, not page stats |
| A8 | The session tab's three sizes are tokens: `--chat-cap` 880px, `--bld-chat-w` 440px, `--side-float-max` 600px (`DESIGN.md` §3.2 row "Session") | they were local custom properties on `.c-sess` (V7-UI-2); the values are the mock's, only the names are new |
| A9 | ⌘L on a session tab (aside closed) puts the cursor into that tab's own composer (again: leaves it), as the mock does; elsewhere ⌘L opens the chat aside as before. btw opens in the aside (`pm7.aside.btw`, Quick chat's `POST /qchat/send` with `agent=<id>`, no tools); on phones btw is not available until Quick chat is ported | the mock's behaviour; opening the same chat beside its own tab showed it twice |
| A10 | Launch dialog and Quick chat sizes are tokens: `--launch-w` 760px, `--qchat-w` 720px × `--qchat-h` 640px, `--qchat-list-w` 220px (`DESIGN.md` §3.2 row "Shell layers") | LAYOUT §5 names the sizes; they were rem literals in page CSS (V7-UI-2) |
| A11 | Form dialogs (Add issue, Add task, New project) reuse `--pal-w` 680px as their width | no form-dialog width token; one width for every form dialog |
| A12 | Git tab: stage / unstage boxes act at once (no dialog; reversible in one click); Fetch posts without a plan dialog; Commit is a plain button, not green | Fetch changes no file or branch; the page's one green stays Launch agent |
| A13 | Backlog Dispatch confirm reads "Starts a new claude agent in <project> with the task as its prompt: …" | says what the click starts before it starts |
| A14 | New project shows only the steps the core's plan sends (no Docker stack / hostname / VM steps) | the core's `/p/new/plan` sends none of them |
| A15 | Issue Pin (PRD §6.5) not built | no pin store for issues in the core |
| A16 | Projects list: the Stack column is not sortable | the core has no `stack` sort key |
| A17 | Quick chat: deleting one conversation needs its title typed (up to 60 characters); Delete all needs `delete` typed | V7-SEC-6 names the object; the mock's Settings uses `delete` for history |
| A18 | Project view Issues tab lists tracker issues only; a project that keeps BACKLOG.md as a file shows a note saying so and how to import it (`pmctl import`); the tile still counts its open items | the core's issue and TODO lists come from the tracker |
| A19 | M3 page sizes are tokens (`DESIGN.md` §3.2 row "Page sizes"): `--field-w` 16rem, `--field-w-sm` 12.5rem, `--pj-side-w` 260px / `--pj-side-w-sm` 220px, `--empty-w` 22.5rem, `--prompt-min-h` 6rem, `--prompt-max-h` 40vh — confirm or reverse | they were rem / calc literals in page CSS (V7-UI-2); values unchanged except the Quick chat chip cap (14rem → `--field-w-sm` 12.5rem). Grid column templates keep `rem` / `fit-content()` (A7) |
| A20 | Touch sizes (M3 screen check, `DESIGN.md` §3.2 rows "Controls" and "Touch"): on `pointer: coarse` `--seg-h` is 40px (was 34px; = `--ctl-h`), `.x-btn.sm` and interactive chips (a link or button chip, or one holding a button) are at least `--ctl-h` 40px; new token `--hit` 44px = the touch target of a control drawn smaller than that (TODO tick, issue select box, New project check rows; M4 adds switches, `.p-hit` icon buttons, colour inputs and plain links in running text, one rule in `css/parts.css`) and the min height of a one-line phone row (a row whose meta line is empty). Also A18 refined: the Issues tile of a BACKLOG.md project reads "3 in file · BACKLOG.md" and the Issues tab shows no count — confirm or reverse | LAYOUT §3 asks for 40px controls and 44px rows on touch; the validator measured 34px segments, 26px small buttons, 24px chips and a 16px tick on the phone |
| A21 | Servers refreshes `#sv-panel` every 10 s and after its own actions; `js/servers.js` skips the poll while the tab is hidden and refreshes once when it shows (`PARTS.md` §13); the VMs list has no timer — confirm or reverse | the core has no servers stream event; htmx `allowEval` is off, so the visibility check is page JS |
| A22 | Agents › one agent keeps the classic login output (`_agents_auth.html`, its 2 s poll only while a login runs) in a `.x-cl.pg-agents` box with classic `agents-page.css` (`PARTS.md` §13) — confirm or reverse | a dt port of the login flow is larger than M4; the poll stops when the login ends |
| A23 | The Settings popup (`sp-`, ⌘,) waits for M5: ⌘, opens the Settings page; Appearance and Display (this browser, `pm.v7.*`) apply before first paint in `js/head.js` — confirm or reverse. **Closed in M5:** the popup is built (`shell/_settings.html`, `js/settings-popup.js`; PRD §6.11 acceptance); ⌘, toggles it, the sidebar foot's Settings opens it, the account menu keeps the full page; on `/settings` there is no popup and ⌘, focuses the page's search | M4 builds the page only; the popup needs `keys.js` and `shell/_layers.html` work |
| A24 | Meter sizes are tokens: `--meter-w` 170px, `--bar-h` 4px, `--bar-in-w` 56px (`DESIGN.md` §3.2 "Meters", §6.7) — confirm or reverse | DESIGN §6.7 names 170 px cells and a 4 px bar; they were raw px in the mock (V7-UI-2) |
| A25 | Interface size and the "tint" chat background are tokens (`--fs-k`, `--bub-asst-tint`; `DESIGN.md` §3.2 "User looks"); fonts offer the browser's generic families; source-pill colours are not offered; "Open tabs follow you" shows on and disabled — confirm or reverse | V7-UI-2 (they were literals in `head.js`); no `--src-*` tokens yet; tabs are always server-side |
| A26 | Settings › Display › Shortcut helper: "Off" hides every toast that carries a key chip, also the "nothing to do" answers ("The home tab stays", "No session tab to move"); "Action only" drops the chip; toasts without a key always show — confirm or reverse | a toast with a key is a shortcut's answer by contract (`pm7.toast(text, key)`); one rule in `layers.js` instead of a check in every caller |
| A27 | Settings popup size tokens `--sp-w` 1040px × `--sp-h` 760px (`DESIGN.md` §3.2 row "Shell layers"; height `min(--sp-h, 100dvh − --s6)`, full screen under 700px) — confirm or reverse | LAYOUT §5 names 1040 × 760 for the popup; a size needs a token (V7-UI-2), as A10 did for Launch and Quick chat |
