# pm UI rules

> **`classic` theme only.** The `dt` theme has its own rules in `docs/themes/dt/` (start with `docs/themes/dt/README.md`); this file does not apply to dt.

Every page follows these. Read before touching a template or CSS; check a new page against
the list before finishing. Flat GitHub-style theme (v4): hairlines, whitespace, solid status dots.

## Changing the design

The theme is "v4" (flat GitHub style), approved by the user on 2026-10-08 and applied to the app
the same day; its design mock (static, not published) was for reference only. It can change again under
three conditions:

1. **Ask the user first.** Before changing the theme, a control style or any rule in this file,
   show what changes and get an explicit yes. A mock, a screenshot or an earlier request is not
   approval.
2. **Change it everywhere at once.** A new look is a system change: tokens and shared component
   rules in one place, every page and partial (login, HTMX partials, terminal) updated in the same
   change. Never style one page differently, never leave pages half-migrated.
3. **Update this file in the same change.**

## Theme
- Tokens live in `static/css/tokens.css` (`:root`, `light-dark()`: `--page --bg --subtle --head --fg
  --muted --line --hover --accent --accent-soft --running --waiting --failed --st-run --st-attn
  --st-err --btn-green --pane-bg`, `--ctl-radius`/`--box-r` 6px, `--ctl-h` 32px / 40px on touch, plus
  the spacing/size tokens `--fs-*`, `--s1..3`, `--row-h`), linked first. Colours follow
  `prefers-color-scheme`. No hard-coded colours or sizes in page CSS.
- Layering (`base.html`, order is the cascade, never reorder): `tokens.css`, `base.css` (element
  defaults, grids), `chat.css lists.css ntfy.css servers.css backlog.css inbox.css` (layout), then
  the theme layer: `chrome.css` (header, nav, controls, phone rules), `tabs.css` (the in-page tab bars `.tabs`/`.tab`: flat,
  `.tabs-seg`, `.tabs-line`; behaviour `tabs.js` = `pm.tabs`), `rows.css` (Inbox/Project
  lists, ntfy, Projects/tmux), `doctabs.css` (project page document tabs and panel), `data.css` (Servers/Backlog/Audit/Settings), `agent.css` (agent,
  chat, launch, terminal), `composer.css` (the one composer: dock, flow, ended; also quick chat, Launch, Continue), `shell.css` (sidebar + drawer), `icons.css` (section icons),
  `nav.css` (account menu, quick chat popup, agent head, search), `popup.css` (the popup family: popovers, menus,
  phone sheets, dialogs), `filespane.css qchat.css palette.css`. Page-only sheets are linked by their template (`pane canvas side-tmux settings audit
  agents-page usage plans projects gitremotes builder term launch`). Each file belongs to one area.
- Flat GitHub look: light grey header band with an orange underline on the current tab, white
  content column on a grey side background (page background), 1px hairlines, 6px radius controls,
  999px radius pills for chips/counters. No gradients. Soft shadows only on floating layers: menus, popovers, phone sheets,
  dialogs, the quick chat panel and pickers (`--pop-shadow`, radius `--pop-r`); never on rows, cards or page content.
- Stacking (`tokens.css` z scale, never a raw z-index for a new layer): `--z-sticky` sticky heads, `--z-pop` popovers and
  menus, `--z-drawer` drawers, `--z-panel` the quick chat panel, `--z-sheet` phone sheets, `--z-toast` `#flash`. Modal
  dialogs live in the browser's top layer (`showModal`), above all of them.
- Resize handles (sidebar, chat drawer, split dividers, builder preview): a 1px grey hairline at rest and a blue (`--accent`) 2-3px line on hover, focus and drag. Never a grey bar.
- Buttons: one style everywhere = the global `button` (grey `--subtle` fill, `--ctl-line` border, 6px radius, weight 500, `--chip-h` high when `.small`). Do not restyle a one-off button (fold toggles, Expand, Collapse); pills are only for chips and counters.
- One accent (blue) for links and focus; green only for the primary action and running; yellow for
  attention / waiting / unknown; red for failed and destructive actions. Contrast AA in both themes.
- Status is a solid 8px dot (green running, yellow attention/waiting/unknown, red failed, grey
  idle) with the word in `title`/screen-reader text, not a pill.
- Phone (<700px): 5-item bottom nav (Inbox, Projects, tmux, Servers, More... sheet with Backlog,
  Audit, Settings, Log out); header icon row (Backlog, Audit, Settings, Log out); weights 400
  (names and the active tab 500, headings 600); 15px text, 16px inputs (no iOS zoom), tap targets
  at least 36-44px.

## Shell: sidebar and drawer (v5)
- `base.html` wraps `main` in `.v5-shell`; a page passes `sidebar` (`models.Sidebar`) to get the left
  sidebar (`_side.html`) and `drawer=True` to get the right-hand drawer (`_drawer.html`). Styles:
  `static/css/shell.css`, behaviour: `side.js`, `side-init.js` (collapsed flag, blocking in `<head>`), `drawer.js`.
- Sidebar only inside one specific project (`/p/{name}`, `/p/{name}/launch`), tmux session (`/tmux/{sid}`)
  or agent (`/agents/{name}`). Overview pages and other tabs never have one. The current row is rendered
  by the server (`aria-current`); JS never decides the view.
- Pages without a sidebar must keep the v4 `main` position and width (390/1512/2560 px); `.v5-shell` is
  `display:contents` until the drawer opens.
- Drawer: plain click on a `/a/{id}` or `/term/s{n}` link in `main` opens it with `?embed=1`
  (`html.v5-embed`, CSP `frame-ancestors 'self'` for embed responses only). The row stays a real link.
- Lists inside a sidebar page or an open drawer drop columns by container width (`@container pg`).
- Agents and Servers are not top tabs on desktop: they live in the sidebar foot group "Manage" and the top-right Menu (same groups, `_acct.html`: Manage, Service, Account). Phones: Servers in the bottom nav, Agents in More. `/agents` overview has no contextual sidebar.
- The open chat aside is carried over every navigation by the one handler in `drawer.js` (pagehide stash + history wrappers); never add per-control hash patches.

## Controls
- Buttons: 6px, grey fill; one green primary action per form/page (`.primary`, `.launch-btn`);
  destructive actions are red. Row actions are icon or text links, at most one per list row
  (hover/focus, always visible on touch); anything else goes on the item's own page.
- Chips and counters are outlined pills; the active chip is filled. Sorting is by column header
  (no sort chips).
- Every mutation keeps POST + CSRF + Origin + audit; a text link may be a form with a button styled
  as a link. Destructive ones keep confirm / typed confirm.
- No native browser popups, ever: no `alert()`, `confirm()`, `prompt()`, `window.open` popups. Confirm and ask
  in the page: `hx-confirm` / `data-confirm` on a form (shown as the in-page modal by `static/js/dialog.js`), or
  `window.pmConfirm({title, body, choices})` / `window.pmPrompt({title, label})` from script. Several outcomes
  (e.g. Exit / Exit and kill window / Cancel) are `choices`, or a static `<dialog class="pmdlg">` opened with
  `data-dlg-open`. The modal works inside the drawer iframe (`?embed=1`). `tests/test_no_native_popups.py` enforces it.
- One popup family, nothing hand-made: anchored menus and popovers use `pm.popover` (`static/js/popup.js`: placement with
  flip and clamp, one layer stack, Esc closes only the top layer, outside click closes, arrow keys in `role=menu`, focus
  in and back to the opener) and `.pop` / `.pop-item` (`static/css/popup.css`); `<details data-pop>` dropdowns join the
  same stack and still work without JS. Modals use `window.pmDialog` (static `<dialog>` or built from
  `{title, body, foot, size, iframe}`) and the `templates/_popup.html` macros `dialog(id, title, size)` + `dlg_foot()`:
  title row (optional close x), body, foot with [Cancel][Primary] at the right; typed confirms use `data-dlg-typed`. A
  backdrop click, Esc and `[data-dlg-close]` close every dialog; the first field (or the safe button) gets focus. Never a
  second backdrop, Esc or outside-click handler in a feature file.
- Phones (<700px): menus marked `data-sheet` open as bottom sheets (`.pop.is-sheet`, full width, above the bottom nav) and
  dialogs are bottom-aligned with 12px margins (full-size and framed ones keep their size; the command palette stays at
  the top because of the keyboard).
- No new buttons for read/dismiss/open: the row is the link; opening marks read.
- Small behaviours are static files: `more.js` (phone More sheet), `newsess.js` (tmux New session Cancel).
- Scripts are static files only (CSP `script-src 'self'`); no inline script or style.

## ntfy block (Inbox and Project, one component)
Rows show date-time plus relative age, project/tag and title. Header: "ntfy - last N", unread chip,
"Mark all read", Show dropdown. No Expand all / Collapse all buttons.

## Lists and tables
- One row = one line (`white-space: nowrap; overflow: hidden; text-overflow: ellipsis`). Never
  wrap on desktop or 390px. On phones hide low-value columns (project, cpu/mem, author,
  stamp) instead of wrapping.
- Fixed column widths in a shared grid so columns align across rows; the header row uses the
  same grid and hides the same columns on phones. Never size columns per row.
- Header row cells are the sort controls: plain `?sort=` links, click again reverses
  (`-key`), arrow on the active column. Persist per page (cookie) like the existing lists.
- A row is a single link to its item; the title/stats of the page sits on the left.
- Timestamps: UTC+7 via `timefmt` helpers, full stamp in `title`, relative age in the row.
- Pending-question / long text: truncated on the same line.

## Page header layout (top to bottom)
1. Title row: page title + stats on the left; page-level actions (Launch, Stop agents) on the
   same row, right-aligned. On phones secondary actions may hide (the "+" button replaces Launch).
2. Live/priority panels (ntfy panel on Inbox) come before the list controls.
3. Controls row directly above the column headers: the one filter box first (first control under the title
   row, flexible width), Show dropdown at the far right. On phones (under 700px) the box takes its own full-width
   line and Show wraps below it.
4. Column header row, then the rows.
5. Below the table: a second Show dropdown at the same right edge, with "showing X of Y".
- Filter box (`templates/_filterbox.html` macro `filterbox(q)`, `static/js/filter.js`): `input[type=search][data-filter]`,
  placeholder `Filter...`, no Filter/Search/Clear button and no Enter-to-submit. The clear x (`.fbox-x`) is inside
  the field, its next sibling, and shows only while there is text (touch: 40px hit area); Esc or the x clears and
  refocuses. One query per page: every box and every table on the page (Inbox live + Idle + Ended, ntfy) follows
  it, rows hide as you type (`[data-frow][data-q]`; ntfy rows `.ntfy-row[data-q]`), `html[data-pmq]` is set while it is non-empty.
  It matches name-type text only (label, tmux window, project, id, kind, model, ntfy title/tags/agent), never message
  or body text, so build `data-q` from `sorting.agent_q` / `project_q` / `session_q` / `q_text`, not from messages. Any page
  whose box sits in a `form[data-filter-live][data-filter-target="#id"]` (Inbox, Projects, tmux) also sends the query to the
  server (`?q=`, restored into the box on reload; the target re-fetches its list) so counts and the Show cap follow. The
  project page has no such form: one box above the live block, client-only. A page may have several live forms
  (Audit: the log and Client errors, both follow the one query); only the form whose `action` is the page's path
  updates the address bar. Every list filter is this box: Audit, Client errors, the Agents library and an agent's
  Skills tab use it too (live, server-side).
- Exception to "one query per page": the contextual sidebar (`_side.html`) has its own box,
  `filterbox(scope="#v5side")` (`data-filter-scope`). It filters only the sidebar's `[data-q]` rows (name-type text:
  name, key, agent id, kind, model) with its own text, never the page's query, the address bar or the server.
- Show is a compact `<select>` (navigates on change, plain GET form fallback with a submit button). Never chip
  rows. Forms that refresh by polling are `data-keep` so typing survives, and the query survives every list swap.
  Markup: `templates/_limitbar.html` `showsel()` / `limitbar()` (ntfy's browser-side Show uses the same macros with
  `sel="ntfy-n"`). "Show" is only the row limit; a select that narrows the rows (Enabled / Disabled) is a Filter
  select (`_sortbar.html` `filtersel(opts, cur, name)`).
- Paged tables (Audit, Client errors) end with one pager, `_limitbar.html` `pager(page, newer, older, label, target)`:
  "newer" / "page N" / "older" centred under the table, a disabled end is a dimmed chip.
- Keep this order on Inbox, `/p`, `/tmux`; any new list page copies it.

## Row states (one colour set everywhere)
- Hover = `--hover`. Selected = `--accent-soft`: the item you are in (sidebar current link, `aria-current`) or the row open in
  the drawer (`.v5-active`, set by `drawer.js`). Selected stays filled on hover. Use these on every list (inbox, tmux,
  agents, projects, sidebar); never a second colour.
- Nothing else is filled or starred: no `*`/fill for tmux's active window, no sticky `:focus`/`:active` fill (use
  `:focus-visible` outline for keyboard focus). The selection must survive live refreshes (`nodiff:swapped`) and clear on close.
- `/ntfy` rows (`.ntfy-row`) follow the same rule: the row whose agent is open in the drawer is `--accent-soft` (also while open/hovered).
- Exception (user-requested): on `/ntfy` a row with no agent owner has a 3px `--ico-dir` inset edge on its left (`box-shadow`, not a border or fill).
- Test it: `tests/ui/test_row_focus.py` (hover colour, one selected row, clears on close).

## Detail pages
- Project and agent pages: header one line (name, state, project, since), actions as text
  links in one row. Git actions (fetch/pull/push) live on the project page, not in list rows.
- Side panels (markdown viewer) sit at the same top as the message list, `position: static`,
  own internal scroll; never sticky/floating. Stack below on phones.
- Don't show raw system/injected text (`<task-notification>`, `<system-reminder>`); hide a
  line if nothing meaningful remains.

## Process
- Verify every UI change on the live page with Playwright: light and dark, 1700, 1300 and 390px (WebKit iPhone for phones).
  Read the screenshots, fix, re-check. Tests passing is not enough.
- Never type into a live `/a/<agent>` composer.
- Update `CHANGELOG.md` for every visible change; update markup-coupled tests, don't delete them.
