# pm UI design conduct (v4)

> **`classic` theme only.** The `dt` theme has its own rules in `docs/themes/dt/` (start with `docs/themes/dt/README.md`); this file does not apply to dt.

For any coding agent (Claude, Codex, Devin, Cursor, ...) that adds or changes a pm feature or UI.
Read this and `docs/UI-RULES.md` before touching a template or CSS.

## 1. Purpose and precedence

1. `docs/UI-RULES.md`: hard layout rules (rows, headers, controls order). Wins on conflict.
2. This file: how to design and build in the v4 style.
3. The v4 design mock (static, not published): visual reference only, not app code.
4. After the port, real code is truth: `src/pm/static/css/` (`tokens.css`, `base.css`,
   `chrome.css`, `rows.css`, `data.css`, `agent.css`, `shell.css`, `nav.css`; was `v4*.css`) and `src/pm/web/themes/classic/templates/`. Where this file and
   the code differ, see `src/pm/static/css` and fix the doc.

Design changes (theme, a control style, any rule) need an explicit user yes first; change
everywhere at once; update UI-RULES and this file in the same change.

## 2. Principles

- Minimal flat GitHub style: hairlines, whitespace, no gradients; a soft shadow (`--pop-shadow`) only on floating layers
  (menus, popovers, sheets, dialogs, quick chat), never on page content (`docs/UI-RULES.md` "Theme").
- Dense: one list row = one line. Show the important columns; raw detail goes in a tooltip or More.
- One accent (blue) for links and focus. Green = primary action and running. Yellow = attention.
  Red = failed and destructive. Colour only ever means state or action; never decoration.
- Consistency over novelty. Before adding a component, find the existing one (section 4) and reuse
  it. A second look for the same thing is a bug.
- The row is the link. No extra read/open/dismiss buttons.

## 3. Design tokens (use tokens, never hex, in page CSS)

Defined in `tokens.css` `:root` with `light-dark(light, dark)`; colours follow `prefers-color-scheme`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--page` / `--bg` | #fff | #0d1117 | content column / boxes |
| `--side` | #eef1f4 | #090d13 | gutters beside the column |
| `--subtle` | #f6f8fa | #151b23 | control fill, table header band, code |
| `--head` | #f6f8fa | #151b23 | header band |
| `--head-fg` / `--fg` | #1f2328 | #f0f6fc | text |
| `--head-muted` | #3d444d | #c9d1d9 | inactive tabs, clock, header links |
| `--muted` | #59636e | #9198a1 | secondary text, idle dot |
| `--colh` | #59636e | #9198a1 | column header text |
| `--line` / `--ctl-line` | #d0d7de | #3d444d | borders |
| `--line2` | #eaeef2 | #21262d | row hairlines |
| `--hover` | #eaeef2 | #21262d | hover fill |
| `--accent` / `--accent-soft` | #0969da / #ddf4ff | #4493f8 / #1a2c4a | links, focus, active nav |
| `--st-run` (=`--running`) | #1a7f37 | #3fb950 | running dot/text |
| `--st-attn` | #bf8700 | #d29922 | waiting / unknown dot |
| `--waiting` / `--waiting-soft` | #9a6700 / #fff8c5 | #d29922 / #272115 | attention text / notice fill |
| `--st-err` (=`--failed`) | #d1242f | #f85149 | failed, danger |
| `--btn-green` | #1a7f37 | #238636 | primary button |
| `--ok-fg` | #116329 | #3fb950 | OK text |
| `--pane-bg` | #0d1117 | #010409 | terminal/pane |

Fixed (not themed): tab underline orange `--tab-on` #fd8c73 (top nav current tab; agent-kind fallback; the hex lives in `tokens.css` only), `--pane-fg` #e6edf3.

| Token | Value |
|---|---|
| `--r-ctl` / `--ctl-radius` / `--box-r` | 6px (controls, boxes, code, bubbles) |
| `--r-pill` | 999px (chips, counters) |
| `--pill-h` | 24px |
| `--ctl-h` | 32px; 40px under `pointer:coarse` and phone |
| `--ctl-pad` | 12px horizontal; `--ctl-font` .8125rem |
| `--tab-h` | 60px (phone bottom nav) |
| spacing | 4/8/12/16/24: row cell padding 8px 12px; main padding 8px 24px 24px (16px on phone); title row, ntfy, hairline, filter, header gaps 24/24/16/12 |

Fonts: body `-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial,
sans-serif`; mono `ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace`.
Sizes: body 14px (15px phone); h1 1.25rem; h2 .875rem desktop, 15px section heading on pages;
column headers .8125rem; chips/meta .75rem; code 85% (12.5px phone). Weights: 400 text, 500
buttons/names, 600 headings, active tab, column headers, primary button. Phone: everything 400,
names and active tab 500, headings 600 (enforced in `chrome.css`; do not add bold on phone).

## 4. Components (reuse, do not reinvent)

| Component | When | Contract |
|---|---|---|
| Header (`.topbar`) | every page | grey band, hairline below; brand left, clock + Backlog/Audit/Settings/Log out right as icon (`.hi`) + label (`.tl`). From 700px the icons are hidden (text links, Backlog/Audit live in the tabs); under 560px icon-only (44px targets); under 480px clock hidden |
| Modal (`dialog.pmdlg`, `static/js/dialog.js`, `templates/_popup.html`) | every confirm / ask / multi-choice / form popup | native `<dialog>.showModal()` via `pmDialog`: Esc, backdrop and Cancel close it, the first field or the safe button has focus; head (title, optional x), body, foot [Cancel][Primary] right; `pmConfirm({title, body, choices, danger})`, `pmPrompt`, `hx-confirm` / `data-confirm`; bottom-aligned under 700px. Never `alert`/`confirm`/`prompt` |
| Popover / menu (`.pop`, `static/js/popup.js`) | anchored menus, dropdowns, pickers | `pm.popover.open(anchor, el, {placement, role})`: flip + clamp, one layer stack (Esc closes the top one), outside click closes, arrows in `role=menu`; `--pop-r`, `--pop-shadow`, `--z-pop`; `data-sheet` = bottom sheet under 700px |
| Tabs (`.tabbar`, `a.on`) | desktop primary nav | text tabs, current = 600 + 2px orange underline; `.tabbadge` = unread count |
| Tab bar (`.tabs` > `.tab`, `static/css/tabs.css`, `static/js/tabs.js`) | every in-page tab bar | one rule file, three looks: flat (agent page, chat aside, Builder Chat\|Preview: muted text tabs, current `--fg` 600 + 2px inset underline, the agent kind colour `--k` in the agent head and aside, else `--accent`); `.tabs-seg` boxed group (Servers, Agents, ntfy: `--subtle` band with hairlines, current tab `--bg` 600 + `--accent` underline); `.tabs-line` doc tabs (project docs, tmux\|Stack\|VM, Git, Skills: 2px `--accent` border under the current tab, 500). Underline colours are tokens only (`--tab-line`). Markup: `role=tablist` + `role=tab` + `aria-selected`, or a `<nav>` of links with `aria-current="page"` (server-selected), or radio `label[for]` only where panels must switch in CSS alone. `pm.tabs`: select, Left/Right/Home/End, roving tabindex, `aria-controls` panels, `data-tabs-key` = remembered tab (re-applied after live refreshes), `pm:tab` event. Never a second tab rule file or select script |
| Phone bottom nav | under 700px / coarse | 5 items: Inbox, Projects, tmux, Servers, More. `.tabbar` is 60px, 26px mask icons (`--ic`), active = accent text + 3px top bar + `--accent-soft`; `.tabmore` opens `.moresheet` (a `.pop` menu, bottom sheet: Backlog, Audit, Settings, Log out in red, 48px rows) |
| Page title block (`.phead`) | every list/detail page | big `h1` (no own border), muted stats line below/beside, page actions right-aligned on the same row; hairline underneath |
| Buttons | actions | 6px, grey `--subtle` fill, 1px `--ctl-line`, 32px (40 touch). One green `.primary` / `.launch-btn` per page or form. Destructive = red text (`.danger`), fills red on hover, and needs confirmation. Quiet row actions (`.pact`, `.sess-actions`) are text-only until hover |
| Icon buttons (`.ibtn`) | row actions in dense tables | 28px, muted, hover fill; `aria-label` + `title` mandatory; `.warn` turns red on hover; secondary actions in a `<details>` More popover |
| Chips/pills | filters, counters, toggles | 24px high, 999px radius, 1px `--line`, .75rem; active = solid `--fg` fill with `--bg` text; touch hit area extended with `::after` inset -8px |
| Status dot (`.dot`) | any state | 8px solid circle: `dot-running` `--st-run`, `dot-waiting`/`dot-unknown` `--st-attn`, `dot-failed` `--st-err`, `dot-idle` `--muted`. Markup: `<span class="dot dot-x" title="x"></span><span class="sr-only">x</span>`. Never a pill for state |
| Lists/tables | all data | see below |
| Sections | grouping inside a page | hairline (`--line2`) separator, 15px heading, no card/box, no coloured top |
| ntfy block | Inbox and Project only (same markup) | head: "ntfy" + "N unread" chip + Mark all read + Show dropdown; rows: dot, date-time + relative age, project tag (folder icon), title; click row to expand |
| Filter row + Show | list pages | one filter box (`filterbox()`, placeholder `Filter...`, no button, in-field clear x only with text, one query per page for all tables, name-type text only; Inbox, Projects and tmux also ask the server via `form[data-filter-live]`, the project page filters in the browser) first under the title row, Show `<select>` far right above headers; on phones the box is full width on its own line; a second Show with "showing X of Y" below the table; never chip rows |
| Forms/inputs | all input | label above, inputs 6px, min-height `--ctl-h`, `--bg` fill, focus = accent border + inset 1px accent ring; 16px font on phone; checkboxes 16px (18 touch) with `accent-color` and enlarged hit area |
| Notices | info/warn | `.set-notice` / `.chat-ask`: 1px border; warn uses `--waiting-soft` + `--waiting` border mix; text always says what happened |
| Empty states | no rows | one muted line saying what is missing and how to add it; no illustration |
| Dates | everywhere | `YYYY-MM-DD HH:MM:SS` UTC+7 (`timefmt` helpers) shown as date-time (short `MM-DD HH:MM` on narrow) plus relative age; `title` carries the full stamp with seconds |
| Icons | folders, files, locks, nav | inline SVG or CSS `mask` (`--ico`) coloured by `currentColor`; 13-16px; icon-only controls get `aria-label` + `title`; no emoji, no icon fonts |
| Sidebar (`.v5-side`) | project / tmux session / agent pages only | 248px, `--subtle`, filter box, rows with dot + name + count, current = `--accent-soft` + 2px accent bar, collapses to a 44px rail (`pm.v5.side`), slide-in under 900px |
| Drawer (`.v5-chat`) | agent and terminal links in `main` | `min(460px,38vw)`, iframe `?embed=1`, head: dot, title, kind, Open full (new tab), close; Esc closes; full-screen under 700px |
| Chat | agent page | user message = grey bordered bubble (right, max 85%, 6px); assistant = plain text; each message shows date-time; `.chat-ask` for pending question; composer `.cbox` 6px box, focus ring, green square `.csend`, disabled = the same green at 40% opacity |
| Composer | every text box that sends: agent page, Builder, ended page, quick chat, Launch prompt, Continue popup | one macro `composer(mode)` (`templates/_composer_macros.html`), `composer.css`, `composer.js`: same box, icon bar, send key (Settings > Composer; Launch and Continue always Ctrl/Cmd+Enter), autosize, attach / paste / drop, `/` and `@` lists. Never a second hand-made message box |
| Terminal | /term | `.term-scroll` dark 6px box, two control rows, raw tmux output |

### Lists and tables

- One row = one line: `white-space:nowrap; overflow:hidden; text-overflow:ellipsis`, full text in `title`.
- One shared grid per list (`grid-template-columns` fixed widths + one `minmax(0,1fr)` text column);
  the header band uses the identical grid and hides the same columns on phone.
- Header band: `--subtle` fill, .8125rem/600, `--colh`; each sortable header is a link (`.hcell`,
  `aria-sort` on the active one) with a sort icon whose width is always reserved (no jump).
  Numeric columns right-aligned, header text flush right over the numbers.
- Show only the important columns; raw ids, paths, long commands go to the tooltip or the row's More.
- Row hover `--hover`, selected/open row `--accent-soft` (see UI-RULES "Row states"); hairline `--line2` between rows; padding 8px 12px.
- Grouped lists (Inbox) are one table with one header; Running then Needs attention stay on top.

## 5. Responsive and phone

| Breakpoint | Behaviour |
|---|---|
| >= 700px (fine pointer) | top tabs, full header labels, side gutters |
| < 700px or coarse | bottom nav (5 items) + More sheet, no side borders, 16px padding, 15px text, no bold |
| < 560px | header links icon-only (44px) |
| < 480px | clock hidden |

- Tap targets 36-44px (buttons/selects 40, nav 60, More rows 48); extend hit areas with `::after`
  instead of enlarging visuals.
- Inputs 16px (no iOS zoom). Reserve bottom padding for nav and FAB: `main` bottom padding is
  `--nav-clear + 72px`; nothing may sit under the nav.
- No horizontal scroll at 390px. Hide low-value columns first (project, cpu/mem, author, stamp);
  never wrap rows. Secondary page actions may collapse to a "+" button.
- Weights: 400 body, 500 names/active, 600 headings.

## 6. Accessibility

- Text contrast >= 4.5:1 in both themes; dots, borders and UI parts >= 3:1. Check `--muted` on
  `--subtle` and `--waiting` on `--waiting-soft` when you touch them.
- Every interactive element has `:focus-visible` (2px `--accent` outline, offset 2px; -2px inside
  rows). Never `outline:none` without a replacement.
- Sortable header: `aria-sort`/`aria-current`. Inputs have a visible label or `aria-label`.
  Icon-only controls: `aria-label` + `title`.
- Never colour alone: a state dot has `title` + `.sr-only` word; a coloured number also has text.
- HTMX-polled regions that update announce with `aria-live="polite"` only when useful; keep typing
  state with `data-keep`.

## 7. Security-aware UI conduct

- Every mutation: POST form with CSRF hidden field; the server checks Origin, session, tier,
  audit. A link-style action is a form with a button styled as a link. Never a GET mutation.
- Destructive actions (kill, stop, delete, revoke, VM power) require confirmation: confirm dialog
  or typed confirm; red styling; the label names the object.
- No inline `<script>`/`style=`/event handlers (CSP `script-src 'self'`, login included). Static
  files only, vendored HTMX, no JS build, no CDN.
- Pane/chat text is ANSI-stripped and masked on every frame; never log it, index it or send it to
  ntfy (terminal stream exception per AGENTS.md). Do not show raw `<task-notification>` or
  `<system-reminder>` text.
- Never display, log, screenshot, commit or ntfy pm's token or TOTP.
- UI errors must not echo secrets or full argv.

## 8. Process checklist for a UI change

1. Find the existing component (section 4, mock page, `src/pm/web/themes/classic/templates`); reuse it.
2. Use tokens only; no new hex, px sizes outside the scale, or font weights.
3. Check light and dark.
4. Check widths 1700, 1300 and 390.
5. Phone: WebKit iPhone via `~/.agents/skills/webkit-test`, plus Playwright Chromium.
6. Verify on the live page (never print token/TOTP; do not put them in
   screenshots or repo scripts). **Never type into a live `/a/<agent>` composer.**
7. Read the screenshots; fix; re-check. Passing tests is not enough.
8. A11y pass (section 6): focus ring, contrast, labels, state not colour-only.
9. `uv run pytest` passes; update markup-coupled tests, add tests for new behaviour.
10. `CHANGELOG.md` dated entry for every visible change.
11. New pattern or rule changed: update `docs/UI-RULES.md` and this file, after user confirmation.

## 9. Anti-patterns

- Pills for state (use the dot); status shown by colour only.
- Coloured card tops, side bars or tinted fills as decoration; per-section accent colours.
- Boxes inside boxes; cards around plain sections (use hairlines).
- Two-row list items; wrapping rows; free-text blobs, raw ids or commands in rows.
- Duplicate labels on opener and submit (use "OK" / "Cancel" for the opened form).
- Expand all / Collapse all beside Show; chip rows instead of Filter/Show selects.
- Sort chips (sort by column header only); sort icons that shift columns.
- Hard-coded colours, ad hoc px sizes, a second pill/button size.
- Bold text on phone; text under 15px body or inputs under 16px.
- Emoji or icon fonts as icons; icon-only controls without `aria-label`.
- Inline scripts or styles; JS frameworks; GET mutations.
- Sticky or floating side panels; per-page one-off styling; half-migrated pages.
- Extra read/dismiss/open buttons on rows; more than one row action per list row.
- Putting git actions (fetch/pull/push) in list rows (they live on the project page).
- Native browser popups (`alert`, `confirm`, `prompt`, `window.open`); use the in-page modal (`dialog.js`).
- Changing the design without asking the user.
