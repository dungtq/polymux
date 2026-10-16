# pm v7: design system and interaction patterns

The look and behaviour every v7 page uses. Extracted from the design mock's CSS/JS (not published) on
2026-10-14 and cleaned up: where the mock was inconsistent, this file sets the rule. Geometry and breakpoints:
`LAYOUT.md`.

## 1. Principles

1. **Flat and quiet.** Hairlines, whitespace, 6 px radius, no gradients. Shadows only on floating layers.
2. **Dense.** A desktop row is one line. Detail opens in place (row details) or at the side, never as a wall of text.
3. **Colour is meaning.** Accent blue = links, focus, selection. Green = primary action and running. Yellow =
   needs you / attention. Red = failed and destructive. Agent-kind colour = which agent. Nothing decorative.
4. **What needs a look comes first.** Status lines, Needs you groups, "worth a look" lists, folded repeats.
5. **Keyboard first, mouse friendly, phone native.** Every frequent action has a key; every key is listed.
6. **One component per job.** A second look for the same thing is a bug.
7. **The row is the link.** Click opens; extra actions live in row details or ⋯.

## 2. Naming

One class prefix per area. A new area adds a row here first. Never reuse a prefix for a second meaning.

| Prefix | Area | Note |
|---|---|---|
| `x-` | shell: top bar, tabs, sidebar, session list, aside, search dialog, keys sheet, toast, confirm, menu, phone nav and sheets, terminal tab | |
| `p-` | shared page parts: head, tabs, tools row, filter box, segments, meters, grid table, row details, attention list | |
| `gc-` | grid cell roles inside `p-g` (dot, name, end, meta, switch, act = row actions; `PARTS.md` §6.4) | mock uses `c-dot c-nm c-end c-m c-sw`; renamed because `c-` is the chat |
| `c-` | session tab: header, messages, tools, side panel (reader, canvas) | |
| `k-` | composer and token line | |
| `b-` | Builder preview | |
| `g-` | Sub-agents, Pane, Terminal tabs of the session | |
| `i-` | Inbox | |
| `pj-` | Projects (list, project sidebar, files, Git, Stack, VMs, dialogs) | |
| `bl-` | Backlog | |
| `tm-` | tmux page | mock uses `t-`, which collides with the tool families `t-read t-bash …`; renamed |
| `tool-` | tool families on chat chips (`tool-read`, `tool-bash`, …) | mock `t-*` |
| `n-` | Notifications page and bell | |
| `a-` | Agents page | |
| `sv-` | Servers page | |
| `au-` | Audit | |
| `s-` | Settings (page and popup body) | |
| `sp-` | Settings popup frame | |
| `q-` | Quick chat | |
| `l-` | Launch dialog | |

Shared atoms without prefix: `.dot .chip .bar .ib .btn .inp .sel .sw .tag .kbd .muted .mono .ell .sp`.
State classes: `.on .open .sel .cur .un .pinned .hot .danger .pri .sm .keep`. Body/html flags describe a mode
(`side-rail`, `side-open`, `aside`, `no-sess`, `no-kedge`, `data-wvmode`) and are set by the server where
possible.

Data attributes: behaviour hooks are `data-<area>-<verb>` (`data-i-open`, `data-pj-tab`), never one-letter
prefixes. `data-act` is reserved for shell verbs. Storage keys: `pm.v7.<area>.<name>`.

## 3. Tokens

v7 has its own `tokens.css` (`src/pm/web/themes/dt/static/css/tokens.css`).
It starts as a copy of the current app's `src/pm/static/css/tokens.css` (colours, `light-dark()` pairs, kind and
tool colours, `--pop-shadow`, `--scrim`, `--z-*`) and adds the v7 tokens below. It is the **only** file with
`:root` custom properties and the only file with colour literals.

### 3.1 Colour (inherited, keep the meaning)

| Token | Meaning |
|---|---|
| `--page --bg --subtle --head` | page, boxes, control fill / header bands |
| `--fg --muted --colh` | text, secondary text, column headers |
| `--line --line2 --ctl-line` | borders, row hairlines, control borders |
| `--hover`, `--accent-soft` | row hover, selected/open row (the only two row fills) |
| `--accent`, `--on-accent` | links, focus, selection, unread; text on accent |
| `--btn-green`, `--st-run`, `--ok-fg` | primary button, running dot, OK text |
| `--st-attn`, `--waiting`, `--waiting-soft` | needs you / attention dot, text, band |
| `--st-err`, `--failed`, `--failed-soft` | failed dot, danger text, error band |
| `--lvl-ok --lvl-warn --lvl-red` (+ `-soft`) | meter levels |
| `--k-claude --k-codex --k-devin --k-cursor --k-antigravity --k-other` | agent kinds (`--k` set per element from `data-kind`) |
| `--tool-shell --tool-read --tool-edit --tool-web --tool-agent --tool-mcp` | tool groups (`--tc` per chip) |
| `--pane-bg --pane-fg` | terminal and pane |
| `--scrim`, `--backdrop` | the one scrim colour (dark mode safe) |

User-set colours (Settings › Display, Chat colours) override **these tokens** on `<html>` from a server-rendered
class or CSSOM: `--dot-run/wait/fail/idle/end`, `--badge-un`, `--badge-need`, `--src-you`, `--src-ntfy`,
`--c-msg-bg`, the `--k-*`, `--tool-*`, `--bub-user-*` values. A page never reads raw colours.

### 3.2 New v7 tokens (add to v7 `tokens.css`; replace the mock's raw values)

| Group | Tokens (value) |
|---|---|
| Shell | `--top-h` 40px (phone 48px + safe-top) · `--side-w` 248px · `--rail-w` 48px · `--side-over-w` 280px · `--aside-w` `clamp(360px, var(--aside-pct, 30vw), 50vw)` · `--aside-over-w` `min(440px, 100vw)` · `--bnav-h` 56px + safe-bottom · `--cap` 1280px · `--home-tab-w` 148px · `--tab-w` 190px · `--tab-min` 110px |
| Controls | `--ctl-h` 30px (touch 40px) · `--ctl-h-sm` 26px (touch: `.x-btn.sm` min `--ctl-h`, A20) · `--ib` 32px (touch 40px) · `--ib-sm` 26px · `--seg-h` 28px (touch 40px, A20) · `--sw-w/--sw-h` 34/20px (touch 44/26px) · `--chip-h` 18px · `--tag-h` 24px |
| Rows | `--row-h` 36px (phone two-line 56px) · `--row-head-h` 32px · `--row-group-h` 30px · `--row-side-h` 28px (touch 44px) · `--row-menu-h` 32px (touch 48px) |
| Radius | `--r-xs` 4px (kbd) · `--r` 6px (controls, boxes, bubbles) · `--r-pop` 8px (menus, composer) · `--r-dlg` 10px (dialogs, search, quick chat) · `--r-sheet` 14px (phone sheet top) · `--r-comp-phone` 20px · `--r-pill` 999px |
| Type | `--fs-xs` .6875rem · `--fs-s` .75rem · `--fs-ui` .8125rem · `--fs-m` .875rem · `--fs-body` 14px (phone 15px) · `--fs-l` 1rem · `--fs-xl` 1.125rem · `--fs-h1` 1.25rem · `--font-ui`, `--font-chat`, `--font-mono` (user-set) |
| Shadow | `--shadow-menu` (menus, popovers) · `--shadow-dlg` (dialogs, search, sheets) · `--shadow-side` (floating side panels). Built from black alpha in both themes, never from `--fg` |
| Layers | `--z-local` 1–9 (inside a component) · `--z-sticky` · `--z-side-over` · `--z-aside-over` · `--z-panel` (quick chat) · `--z-pop` (menus) · `--z-sheet` · `--z-toast`. Modal dialogs use the browser top layer (`showModal`), above all |
| Motion | `--t-fast` 120ms · `--t-slide` 180ms · `--ease` `cubic-bezier(.2,.8,.2,1)`; all off under `prefers-reduced-motion` |
| Spacing | 4 / 8 / 12 / 16 / 24 / 32 (`--s1`…`--s6`); page padding 16 24 32 (phone 12 16 24) |

| Components (approved 2026-10-15) | `--dot` 8px · `--dot-open` 5px · `--dot-un` 6px · `--icon` 18px · `--icon-sm` 16px · `--icon-xs` 12px · `--badge-h` 16px · `--menu-min-w` 200px · `--acct-w` 240px · `--dlg-w` 440px (confirm) · `--pal-w` 680px · `--pal-top` 72px · `--pal-in-h` 48px · `--keys-w` 720px · `--sheet-max-h` 78dvh · `--grab-w` 36px · `--side-drawer-w` `min(320px, 86vw)` · `--fs-in` 16px on touch/phone (else `--fs-ui`) |
| Layout (approved 2026-10-15) | `--pj-cap` 1180px (project main) · `--reader-min` 420px · `--reader-w` 36% (project reader column ≥ 1560px) · `--z-scrim` 30 (under `--z-side-over`) |
| Session (added 2026-10-15, agent decision A8 — confirm) | `--chat-cap` 880px (session message column and its composer, `LAYOUT.md` §5) · `--bld-chat-w` 440px (Builder chat column until dragged, `PRD.md` §6.3) · `--side-float-max` 600px (floating side panel: reader, canvas, §6.19) |
| Shell layers (added 2026-10-15, agent decision A10 — confirm) | `--launch-w` 760px (Launch dialog, `LAYOUT.md` §5) · `--qchat-w` 720px · `--qchat-h` 640px (Quick chat panel) · `--qchat-list-w` 220px (its conversation list) · `--sp-w` 1040px × `--sp-h` 760px (Settings popup, `LAYOUT.md` §5; agent decision A27 — confirm) |
| Page sizes (added 2026-10-15, agent decision A19 — confirm) | `--field-w` 16rem (select / filter cap, attachment chip cap, the project stats line's basis) · `--field-w-sm` 12.5rem (worktree branch input, Quick chat chips) · `--pj-side-w` 260px · `--pj-side-w-sm` 220px < 1100px (project sidebar column, `LAYOUT.md` §5) · `--empty-w` 22.5rem (a centred empty-state note) · `--prompt-min-h` 6rem · `--prompt-max-h` 40vh (Launch prompt box) |
| Touch (added 2026-10-15, agent decision A20 — confirm) | `--hit` 44px: the touch target of a control drawn smaller (TODO tick, issue select box, New project check rows; M4: switches, `.p-hit` icon buttons such as a tag's ×, colour inputs, plain links in running text — one rule in `css/parts.css`) and the min height of a one-line phone row; interactive chips (link / button chips, a chip holding a button) are at least `--ctl-h` on `pointer: coarse` |
| Meters (added 2026-10-15, agent decision A24 — confirm) | `--meter-w` 170px (auto-fill meter cell, §6.7) · `--bar-h` 4px (the meter bar) · `--bar-in-w` 56px (a share bar inside a table cell) |
| User looks (added 2026-10-15, agent decision A25 — confirm) | `--fs-k` 1 (Interface size scale, set on `<html>` by `js/head.js`; `--fs-body` = 14px / 15px × `--fs-k`) · `--bub-asst-tint` `color-mix(--k or --accent 7 %, --page)` (Chat colours › Background tint) |
| User colours (defaults, approved 2026-10-15) | `--dot-run` `--st-run` · `--dot-wait` `--st-attn` · `--dot-fail` `--st-err` · `--dot-idle` / `--dot-end` `--muted` · `--badge-need` `--waiting` · `--badge-un` `--accent` |

Rules: a size that is not in this table is not allowed; add the token first (needs a user yes, `CONDUCT.md`
V7-UI-5). `pointer:coarse` switches the touch values, not only the 700 px breakpoint (an iPad gets touch sizes).

## 4. Colour semantics

| Colour | Used for | Never for |
|---|---|---|
| Accent | links, focus ring, selected tab underline (non-session), unread, mark in search | decoration, headings |
| Green | the one primary button per page/dialog, running dot, switch on, tick | secondary actions |
| Yellow | Needs you (dot, band, card frame), warnings, ctx ≥ 60 % | info |
| Red | failed dot, error text, destructive buttons, Stop, denied | emphasis |
| Agent kind `--k` | session tab top edge (2 px), chat tab underline, row left edge (3 px inset), model pill, working word, kind chip border | state |
| Tool `--tc` | tool chip border or fill (Settings › Chat colours › Pill style) | state |

State is a dot (`.dot`, 8 px) with `title` and a screen-reader word. Chips with colour (`.chip.warn/.bad/.ok`)
may **label** a fact (P4, "key in args", "denied") next to text; they never replace the state dot.

## 5. Type and density

- Body 14 px desktop, 15 px phone; inputs 16 px on phone (no iOS zoom).
- Weights: 400 text, 500 names and buttons, 600 headings, column headers, selected tab, unread names. **700 is
  not used** (mock uses it in five places; replace with 600).
- Phone: names 500, headings 600, nothing bolder (same as desktop rule; no extra bold).
- Mono via `--font-mono` only (the mock repeats the stack 15 times).
- Numbers right-aligned with tabular figures.

## 6. Components (reuse; contract per component)

Each component has one CSS home and, if interactive, one JS home (`CONDUCT.md` V7-CODE-6).

### 6.1 Shell (`x-`)
Top bar, sidebar (nav, session list, foot), tab strip, chat aside, phone bottom nav, tabs sheet, sidebar drawer.
Geometry in `LAYOUT.md` §2. Contracts:
- Sidebar nav row: icon 18 px, label, count (`.n`, `.hot` = needs-you colour), `kbd` hint on hover (Settings ›
  Display › Show shortcut hints). Current page = `--accent-soft` + 600.
- Session row: open-in-tab dot (5 px accent), state dot, id, working word or age. Current = `--accent-soft`.
- Tab: state dot, name, × (hover or current), kind top edge 2 px on the current tab; home tab fixed width,
  never closes; overflow scrolls with a fade.
- Aside head: dot, name, kind chip, Open as tab, Close. Resize edge = 1 px hairline, 2–3 px accent on hover,
  focus and drag; arrow keys move 1 %, double-click resets.

### 6.2 Page head (`p-head`)
`h1` (1.25rem/600), stats line (muted) on the same line or under, page actions right. At most one green button.
Phone: `h1` hidden (title is in the top bar), actions become 40 px icon buttons beside the filter box.
Every page has a stats line, including the project view.

### 6.3 Flat tabs (`p-tabs`, `c-tabs`)
36 px (phone 44 px, scrolls, selected centred), muted text, selected `--fg` 600 + 2 px underline inset
(`--accent`; in a session tab `--k`). Counts `.c`, a bad count in red. `role=tablist`/`tab`, `aria-selected`,
arrow keys, roving tabindex. One CSS rule for both variants (the mock defines the underline twice).

### 6.4 Segments (`p-seg`)
Narrowing choices with counts (All · Running · …). 28 px (touch 34 px), `--subtle` band, selected
`--accent-soft` 600. Phone: scrolls, selected centred. Segments **filter**; they never sort.

### 6.5 Filter box (`p-fbox`)
One per page for the page's lists (plus the sidebar's own box, which filters only the sidebar). Icon, input
30 px (phone 40 px full width, 16 px text), in-field clear × only with text. `/` focuses it; Esc clears, a second
Esc leaves the field. Matches name-type text only (ids, names, project, branch, window, title), never message
bodies. Typing never loses the caret or scroll on a list swap. One implementation (the mock has three).

### 6.6 Switch, select, number, chips (forms)
Switch 34×20 (touch 44×26), on = `--btn-green`, `role=switch`, `aria-checked`. Select and text 30 px, label
left / control right in Settings rows (`s-row`), label above in dialogs. Number with unit text after it.
Typed-confirm input is mono. Disabled = .55 opacity + `title` saying why.

### 6.7 Meters (`p-sum`, `p-m`, `.bar`)
Auto-fill grid of 170 px cells (`--meter-w`): label, value 1.125rem/600, 4 px bar (`--bar-h`) in `--lvl-*`. Phone: two
columns. Part: `parts/_meter.html` (`meters`, `meter`, `bar`), `PARTS.md` §16.

### 6.8 Grid table (`p-g`, `p-gh`, `p-gr`)
The table for every list. One `grid-template-columns` per table (a class, not an inline style), header band
32 px `--subtle`, `.8125rem/600 --colh`; sortable headers are buttons with `aria-sort` and a reserved arrow
slot; click again reverses. Rows 36 px min, hairline `--line2`, hover `--hover`, open/selected `--accent-soft`;
the name column is the flexible one. Columns drop by **container** width (`.hide-n` ≤ 760 px, `.hide-s` ≤ 520 px).
Phone: header hidden; **two-line rows** (dot · name · end value · ⋯ on line 1, meta on line 2), 56 px min.
Groups are foldable bands (`p-grp`, 30 px, Enter/Space, `aria-expanded`). Rows are focusable (`tabindex=0`
or a real link) and open with Enter. Long lists: first N rows, then "Show all N" with "showing X of Y".

### 6.9 Row details (`p-det`)
In-place panel under the row: `--subtle`, indent 34 px, key/value grid (`p-kv`), actions at the end. Chevron
rotates; `→`/`X` open, `←` closes. One open at a time per table.

### 6.10 Row actions and ⋯ menu (`x-menu`)
One visible action per row (hover on desktop, always on touch) + ⋯ for the rest. ⋯ menu: min 200 px, items
32 px (touch 48 px), icon + label, `.danger` red, disabled with a note, separators. Phone: bottom sheet with
scrim. Built on the one layer system (§7).

### 6.11 Dialogs (`x-dlg`)
Native `<dialog>` + `showModal()`: title row (optional ×), body, foot [Cancel][Primary] right. 440 px (confirm),
680–760 px (forms), 1040 px (Settings popup). Phone: bottom sheet (12 px margins) or full screen for big forms.
First field or the safe button has focus; focus returns to the opener. Esc / backdrop / Cancel close.

### 6.12 Confirm and typed confirm
Plain confirm for reversible actions. **Typed** (exact name, mono input, OK disabled until it matches, Enter
submits) for anything that cannot be undone. Destructive OK is a filled red button in the dialog; elsewhere
destructive controls are red text that fill red on hover. The label names the object ("Delete branch
feat/x").

### 6.13 Toast (`x-toast`)
One live region (`role=status`), inverted colours, bottom centre (phone: above the bottom nav), 1.4 s. Optional
key chip (Settings › Display › Shortcut helper). Results only after the server answered (`CONDUCT.md` V7-CODE-5).

### 6.14 Chips, tags, badges, dots
`.chip` 18 px outlined pill (`.k` kind border, `.warn`, `.bad`, `.ok`, `.lock`); `.tag` 24 px; count badges
15–16 px (`--failed` for alerts, `--badge-un` for unread, `--badge-need` for needs you); tab unread 6 px dot;
`.dot` 8 px (`.end` = 1.5 px ring for ended).

### 6.15 Search dialog (`x-pal`)
680 px, 72 px from the top, input 48 px, rows 36 px, `mark` in accent 600, footer hints (the ↵ word follows the selected
row: open in a tab, switch to the tab, go to the page, open in Quick chat, run). Scopes: everything,
sessions, commands (`>`). ARIA combobox + listbox, `aria-activedescendant`. Phone: full screen.

### 6.16 Keys sheet (`x-keys`)
720 px, two columns of `dl`, labels from one key table (§8) rendered with ⌘ on Apple and Ctrl elsewhere.

### 6.17 Chat (`c-`)
Message frame 6 px; your bubble right, max 85 % (phone 92 %), outbox dot; agent messages one background
(Settings), optional 3 px kind edge; Working notes with tool chips (`tool-*`); live strip; recap fold; turn
divider. **Needs you** card: 2 px `--waiting` frame, `--waiting-soft` band with label, question 1rem/600, answer
rows 36 px (phone 44 px). It never looks like a normal message.

### 6.18 Composer (`k-`)
One component everywhere a message is sent (tab, aside, Builder, Quick chat, btw). 8 px box (phone 20 px),
accent focus ring, textarea + row; split send (green) with caret menu; red Stop replaces Send while running and
empty; token line 24 px with a 48 px inline meter, detail opens above; attachment chips; `/` and `@` pickers above
the box (300 px, keyboard driven); drop cover over the chat column.

### 6.19 Side panel (`c-side`)
Reader, canvas, sub-agent mini chat. Docked beside the chat with a 1 px divider (accent on hover/drag) when the
container is ≥ 1000 px; otherwise a floating panel or popup with `--shadow-side`; phone full screen. 40 px head.
This is a v7 rule change: side panels may float (the current pm forbids it).

### 6.20 Builder preview (`b-`), terminal (`x-term`, `g-`)
Builder: bar with 28 px controls, scaled sandboxed iframe, Fit %. Terminal: dark `--pane-bg` box, head with window
‹ ›, Aa, History, Live; foot that says keystrokes are never sent.

### 6.21 Empty states and errors
One muted line saying what is missing and how to add it (with the action as a link or button). Errors say what
happened and what to do; no stack traces, argv or secrets.

### 6.22 Working words (`.wv`)
A running agent shows a changing word with the spinning glyph in the agent colour, seeded per agent id.
Settings › Display › Running status: playful | plain ("Working…") | off (dot only). One helper renders it
everywhere (chat strip, notes, header, Inbox rows, sidebar, tabs sheet).

## 7. Layers, focus and Esc

One layer system for the whole app (v7 `layers.js`; in the current app this is `popup.js` + `dialog.js`):

| Layer | Opened by | Closes on |
|---|---|---|
| Modal dialog (top layer) | confirm, forms, search, keys sheet, Settings popup, Launch | Esc, backdrop, Cancel |
| Menu / popover | ⋯, bell, model picker, `/` `@` pickers | Esc, outside click, choosing |
| Phone sheet | ⋯ on phone, tabs sheet | Esc, scrim, swipe down |
| Panel (Quick chat) | Q, its icon | Esc, icon |
| Overlay sidebar / aside (under 900 px) | ☰, ⌘B, ⌘L | Esc, scrim |
| Toast | results | timer |

- Esc closes **only the top layer**. Order: dialog → menu/sheet → Quick chat → overlay sidebar → filter text →
  leave text field → aside.
- One stack, one Esc listener, one outside-click listener. Pages register layers; they never add their own.
- Focus moves into a layer when it opens and back to the opener when it closes. Dialogs trap focus.
- A confirm opened from a layer (Launch, Settings) stacks on top and closes alone.

## 8. Keyboard map

⌘ on macOS/iPadOS, Ctrl on Linux/Windows. Single keys fire only when no text field has focus. Letter and
bracket keys match `KeyboardEvent.code` (layout independent); `?`, `/`, `>` and Esc match `KeyboardEvent.key`
(README O6).

| Action | Key | In composer | Note |
|---|---|---|---|
| Sidebar open / rail | ⌘B | yes | |
| Chat aside + its composer (again: close); on a session tab with the aside closed: that tab's composer (again: leave it, README A9) | ⌘L | yes | takes over "focus address bar" (decision 3) |
| Move tab into the aside | ⌘⇧L | yes | |
| Project sidebar collapse / show | ⌘⇧B | yes | Projects page only |
| Search everything | ⌘⇧P, ⌘K | yes | Firefox keeps ⌘⇧P, so ⌘K stays |
| Commands only | `>` in search | — | |
| Go to session | ⌘P | yes | takes over Print |
| Previous / next tab | ⇧[ ⇧] | ⌘⌥[ ⌘⌥] | not ⌘⇧[ ]: browser tab switch |
| Close / reopen tab | ⌥W / ⌥⇧T | no | home tab never closes |
| Go to page | G then I P T N B A S | no | 1.2 s window; hints in the sidebar |
| Settings popup (toggle; on the Settings page: its search) | ⌘, | yes | |
| Quick chat · Launch | Q · N | no | |
| Filter this list · shortcuts | / · ? | no | |
| List keys (Inbox, Notifications) | J K move, ↵ aside, O tab, X / → details, E read, P pin | no | |
| Close top layer / leave composer | Esc | yes | §7 order |

Reserved for the browser, never bound: ⌘1–9, ⌘T, ⌘N, ⌘W, ⌘⇧T, ⌘⇧[ ]. A new key needs the browser-conflict check
(`CONDUCT.md` V7-UI-7).

## 9. Accessibility

- Contrast AA in both themes (text 4.5:1, UI parts 3:1); check user-set colours against the background and warn
  in Settings when they fail.
- `:focus-visible` 2 px accent outline (offset 2 px; −2 px inside rows) on everything interactive.
- Roles: tablist/tab, listbox/option, combobox, switch, menu/menuitem, dialog; `aria-sort`, `aria-selected`,
  `aria-current`, `aria-expanded`.
- Live regions: toast (`polite`), unread counts only when useful.
- `prefers-reduced-motion`: no spinners rotating, no slides, working words static.

## 10. What changes from the current pm's rules (do not apply the old rule in v7)

| Current pm rule (`docs/UI-RULES.md`, `UI-CONDUCT.md`) | v7 rule |
|---|---|
| Top tabs + contextual sidebar + drawer | one sidebar + session tabs + aside |
| Rows never wrap, also on phone | phone rows are two lines (§6.8) |
| Side panels static, never sticky or floating | side panels may dock or float (§6.19) |
| Orange underline on the current top tab | no orange; accent or agent kind |
| Kind colour only as a fallback | kind colour edges, tab top, model pill (switchable) |
| Show `<select>` + "showing X of Y" | first N rows + "Show all N" + "showing X of Y" |
| At most one row action | one visible row action + ⋯ |
| Controls 32 px | controls 30 px (touch 40 px) |
| `--tab-h` 60 px phone nav, 5 items with More sheet | 56 px bottom nav, More = sidebar drawer |
| Separate search and palette | one search dialog |
| Bottom-sheet confirms with 12 px margins | same (kept) |
