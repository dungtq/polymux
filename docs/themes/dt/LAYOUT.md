# pm v7: layout

Shell geometry, breakpoints and the layout of every page. Values were measured on the mock with Playwright
(Chromium and WebKit) on 2026-10-14. Tokens named here are defined in `DESIGN.md` §3.2.

## 1. Target screens

| Name | Viewport | What must work |
|---|---|---|
| MBP 14" | 1512 × 982 | sidebar open + aside open; sidebar rail |
| Dell 27" | 2560 × 1440 | every page; aside docked |
| Dell 34" | 3440 × 1440, half 1720, two thirds 2293 | every page; aside docked |
| Narrow desktop | 1100, 900, 899 | 900 = last docked width; 899 = overlay sidebar |
| iPhone 17 Pro Max | 440 × 956 (also 390 × 844) | phone layout, real WebKit |

No horizontal page scroll at any of these widths (checked by `scrollWidth == innerWidth`).

## 2. Shell

```
┌ top bar 40 ──────────────────────────────────────────────────────────────────┐
│ ☰ pm DEV 💬 │ [Inbox····148][claude-1c… 190][claude-44… ][+]   🔍 🔔 Launch ▯ 👤 │
├ sidebar 248 ─┬ main ─────────────────────────────────────────┬ aside 30 % ────┤
│ Inbox     4  │ page column (cap 1280)                        │ ● name  kind ⧉ ×│
│ Projects  2  │  head · tabs · controls · table               │ chat            │
│ tmux     12  │                                               │                 │
│ …            │                                               │ composer        │
│ SESSIONS     │                                               │                 │
│ filter       │                                               │                 │
│ ▾ project    │                                               │                 │
│   ● agent    │                                               │                 │
│ Audit        │                                               │                 │
│ Settings     │                                               │                 │
│ Collapse     │                                               │                 │
└──────────────┴───────────────────────────────────────────────┴─────────────────┘
```

| Part | Desktop | Under 900 px | Phone (< 700 px) |
|---|---|---|---|
| Top bar | `--top-h` 40 px. Lead block (toggle, brand, Quick chat) is `--side-w − 8px` wide so tab 1 starts over the page column | same | 48 px + safe area: ☰ (Back on a session), title + subtitle (state, "tab N of M"), search, tabs button with count |
| Tab strip | home tab fixed `--home-tab-w` 148 px; session tabs `flex: 0 1 190px`, min 110 px, 32 px high; overflow scrolls with a fade; `+` opens ⌘P | same | hidden; tabs sheet + swipe the title |
| Sidebar | `--side-w` 248 px docked; ⌘B → `--rail-w` 48 px icons (remembered) | overlay 280 px with scrim; closes on pick | drawer `min(320px, 86vw)`, full height, over the bottom nav |
| Main | fills the rest; `container-name: pg` | full width | full width; bottom padding clears the bottom nav |
| Page column | `max-width: --cap` 1280 px (README O10 for alignment) | full | full, padding 12 16 24 |
| Chat aside | docked `--aside-w` = clamp(360 px, 30 %, 50 %), drag edge 20–50 % | fixed overlay `min(440px, 100vw)` | not used: opening a chat opens a tab |
| Bottom nav | none | none | `--bnav-h` 56 px + safe area: Inbox · Projects · tmux · Alerts · More; hidden on a session tab |

Measured (mock, 2026-10-14): at 1512 the page column is 1264 px (sidebar open) and 1280 px (rail); the aside is
454 px at 1512, 768 px at 2560, 1032 px at 3440. A session tab's message column is capped at 880 px and centred.

Rules:
- **L-1** The server renders the shell in its final state: sidebar open or rail, current page, open tabs, current
  tab, aside open or closed, selected row. JS only reads the remembered sidebar flag in a blocking head script
  (static file, CSP-safe) to avoid a flash. No layout jump after load.
- **L-2** Lists drop columns by **container** width (`@container pg`), never by window width, because the
  sidebar and aside take space.
- **L-3** Nothing sits under the bottom nav or the safe areas.
- **L-4** A session's name is the last thing to truncate in its header: meta text and secondary controls go
  first (fold into ⋯), the name keeps at least its unique suffix (`claude-1c70ee` → `…1c70ee`, not `claude-…`).
- **L-5** Tabs with a shared prefix show the unique suffix when narrow (same rule as L-4).

## 3. Breakpoints

### Window (media queries)

| Query | Change |
|---|---|
| `< 700px` or `pointer: coarse` + `< 700px` | phone layout: bottom nav, 48 px top bar, two-line rows, sheets, full-screen dialogs, 15 px body, 16 px inputs |
| `pointer: coarse` (any width) | touch sizes: controls 40 px, rows 44 px, switches 44 × 26 px (iPad gets these) |
| `< 900px` | sidebar and aside become overlays with a scrim; resize edges hidden; Settings nav 180 px; project sidebar off-canvas |
| `≥ 900px` | project sidebar can collapse (⌘⇧B) |
| `< 1100px` | project sidebar 220 px (else 260 px) |
| `≥ 1560px` | project reader aside is a grid column `minmax(420px, 36%)`; below it is an overlay `min(620px, 100vw)` |
| `prefers-reduced-motion` | no slides, spinners or pulses |

The mock also has a 640 px query (keys sheet to one column); fold it into the 700 px phone query.

### Container (`pg` = main column, `pj` = project main, `fp` = file page)

| Query | Change |
|---|---|
| `pg ≥ 1100px` | two-column page parts (`p-cols.two`) |
| `pg ≤ 1000px` | side panels (reader, canvas, sub-agent chat) float over the chat; Pane keypad behind Keys; Agents detail one column |
| `pg ≤ 1180px` / `≤ 820px` | Builder: narrower bar, then chat column `minmax(260px, 45%)` |
| `pg ≤ 760px`, `pj ≤ 760px` | `.hide-n` columns drop (Inbox: tmux, CPU, RAM; Projects: TODO, Issues, ntfy, Active, Stack); tiles 3 per row |
| `fp ≥ 900px` | Markdown outline as a 210 px column |
| `fp ≤ 520px` | `.hide-s` columns drop |

Rule **L-6**: every table defines which columns drop at 760 and 520 so the flexible text column (Last message,
Title) never shrinks below ~240 px. The mock's Inbox at 800–900 px squeezes "Last message" to a few letters while
fixed columns keep their width: that is a defect.

## 4. Page skeleton

Every page uses the same vertical order. Skip a part when the page has nothing for it; never reorder.

1. **Head** (`p-head`): h1 + stats line left, page actions right (one green). Phone: h1 in the top bar;
   actions become icon buttons beside the filter box.
2. **Page tabs** (`p-tabs`) when the page has sections (Agents, Servers, Audit, Backlog, project view).
3. **Status line / attention list** when the page has one (Servers "All OK" + worth a look, Agents findings line).
4. **Controls row** directly above the table: filter box first (flexible), then segments, then switches, then
   selects at the far right. Every list page has the filter box (the mock's Agents and Servers lack it).
5. **Table** (`p-g`) with one header; groups are bands inside it (`p-grp`). One table per list, not one box per
   group (the mock's Notifications and Audit draw a box per day: align them).
6. **Foot**: "showing X of Y · Show all N", key hints line (muted, hidden on phone and when Settings › Display ›
   Show shortcut hints is off).

## 5. Pages

| Page | Layout |
|---|---|
| Inbox | skeleton; controls: filter · All / Needs you / Running / Idle / Ended · Unread switch · Group select; table Agent · Project · tmux · Last message · Age · CPU · RAM · actions; groups Pinned · Needs you · Running · Idle |
| Session tab | 44 px header across main (state, name, model, meta, tabs, Detail/Compact, Aa, Builder, ⋯, to aside); message column cap 880 px centred; side panel docked at the right with a divider when `pg ≥ 1000`; composer pinned at the bottom of the column; Builder = chat 440 px (resizable) + preview |
| Projects list | skeleton; table pin · Project · Agents · TODO · Issues · ntfy · Active · Git · Stack |
| Project view | project sidebar 260 px (220 < 1100; collapsible ⌘⇧B; off-canvas < 900) · main (cap 1180) · reader aside (column ≥ 1560, overlay below); head with stats line; tiles (6, 3 per row ≤ 760); tabs Agents · Files · Git · Stack · VMs · TODO · Issues · Plans · Docs |
| Backlog | head · tabs Issues / TODO · controls (filter · Open / Done / All · Project select · Group select) · bulk bar (when selected) · table |
| tmux | skeleton; sessions table with windows as row details; terminal opens as a tab |
| Notifications | skeleton; controls filter · Source segments · Unread switch · period select; one table with day bands (Pinned on top) |
| Agents | head · tabs Installed / Library / MCP / Usage / Findings · findings line · controls (filter) · table; agent detail at the side (`pg ≥ 1000`), full screen on phone |
| Servers | head · tabs Overview / Services / Resources / VMs · status line + worth a look · meters · tables (Hosts + This host side by side when `pg ≥ 1100`) |
| Audit | head · tabs Activity / Client errors · controls · one table with day bands |
| Settings | own section nav 210 px (sticky) + body cap 880 px; popup 1040 × 760 px; phone: section list → section with ‹ Settings |
| Quick chat | panel 720 × 640 under its icon; conversation list 220 px left; Expand docks right of the sidebar with tabs visible; phone full screen |
| Launch | dialog 760 px; agent cards grid (5 per row, 2 on phone); footer with remembered note and the green Launch |
| Search | dialog 680 px, 72 px from the top; phone full screen with Cancel |

## 6. Phone specifics

- Rows are two lines: line 1 = dot · name · unread/pills · end value (age) · ⋯; line 2 = meta (project · message).
  56 px min. Kind edge stays.
- Every row action that matters on phone is in ⋯ (bottom sheet), including Pin (the mock hides pin on phone).
- Tabs and segments scroll sideways; the selected one is centred so neighbours show; a fade shows there is more.
- Session tab: bottom nav hidden, chat full height, sub-tabs + Builder + ⋯ on one row under the title.
- Search, Quick chat, Launch, Settings popup and big forms are full screen; confirms and menus are bottom sheets.
- Composer: rounded box (`--r-comp-phone`), token line folds to the meter, tools behind `+`.

## 7. Wide screens

At 2560 and 3440 the 1280 px page cap leaves a wide empty area right of the page column while the top-bar actions
sit at the far right; session tabs centre their 880 px column, so page and session columns do not line up. Until
the user decides (README O10), keep the mock's behaviour (page column left-aligned under the tab strip) and do not
change it per page.
