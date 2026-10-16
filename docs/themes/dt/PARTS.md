# pm v7: shared page parts (contract)

The parts every dt page is built from: page head, flat tabs, segments, filter box, grid table, row details, list
keys, switch, select and the ⋯ menu trigger. Written 2026-10-15 for milestone M2 phase A. Page agents code against
this file; change it only together with the parts. Look and rules: `DESIGN.md` §6.2-6.10, §8; layout: `LAYOUT.md`
§3, §4, §6.

| Part | Template (macros) | CSS | JS |
|---|---|---|---|
| Page head, controls row | `parts/_head.html` (`head`, `tools`) | `css/parts.css` | — |
| Flat tabs | `parts/_tabs.html` (`tabs`, `panel`) | `css/parts.css` | `js/parts.js` |
| Segments | `parts/_seg.html` (`seg`) | `css/parts.css` | `js/parts.js` |
| Filter box | `parts/_filter.html` (`fbox`) | `css/parts.css` | `js/filter.js` (+ `/` keys.js, Esc layers.js) |
| Grid table, row details | `parts/_table.html` (`table gh group row dot name cell end meta acts det_btn det kv dacts empty foot hints`) | `css/table.css` | `js/table.js` |
| List keys, row click | data attributes on `parts/_table.html` output | — | `js/listkeys.js` (registers in `js/keys.js`) |
| Switch, select | `parts/_switch.html`, `parts/_select.html` | `css/parts.css` | `js/parts.js` (switch) |
| ⋯ menu trigger | `parts/_menu.html` (`menu`) | `.x-ib.sm` (`css/shell.css`) | `js/parts.js` → `pm7.menu` (`js/layers.js`) |
| Meters, bar | `parts/_meter.html` (`meters`, `meter`, `bar`) | `css/parts.css` | — |
| Status line, attention list | `parts/_status.html` (`status`, `attn`) | `css/parts.css` | — |

All macros take plain values and dicts (view-model dicts from the route, or literals in the template). Optional dict
keys may be left out. Every value is escaped by Jinja; `attrs` dicts go through `xmlattr`.

## 1. Using the parts on a page

```jinja
{% extends "base.html" %}
{% import "parts/_head.html" as ph %}{% import "parts/_tabs.html" as pt %}{% import "parts/_seg.html" as ps %}
{% import "parts/_filter.html" as pf %}{% import "parts/_table.html" as tb %}{% import "parts/_menu.html" as pm %}
{% import "parts/_switch.html" as psw %}{% import "parts/_select.html" as psel %}
{% block mainclass %}{% endblock %}   {# a ported dt page: main without .x-cl #}
{% block content %} head · tabs · status line · controls row · table · foot · hints {% endblock %}
```

- **`{% block mainclass %}{% endblock %}` is required on every ported page.** `base.html` renders
  `<main class="x-main x-cl">` by default: `.x-cl` marks un-ported classic content, which gets the classic compat
  sizes (`--s3` 16px, `--row-h` 32px, `--ctl-h` 32px, ...; `tokens.css` part 4) and classic `hx-confirm` handling.
  The parts are sized for v7 tokens and look wrong inside `.x-cl`.
- `base.html` loads `css/parts.css`, `css/table.css`, `js/table.js`, `js/filter.js`, `js/parts.js` on every page and
  `js/listkeys.js` when not `embed` (after the shell files). A page adds only its own `css/<page>.css` /
  `js/<page>.js`.
- Page skeleton order (`LAYOUT.md` §4): `ph.head` → `pt.tabs` → status line → `ph.tools` (filter box first, then
  segments, switches, selects) → `tb.table` → `tb.foot` → `tb.hints`. Skip parts, never reorder: on phones the head's
  actions sit at the top right of the block right after the head (the controls row or the tabs).
- CSP: no `style=`, no inline script, no `on*=`. Dynamic values go through classes or CSSOM in a static JS file.

## 2. Page head and controls row (`parts/_head.html`)

`ph.head(title, stats=none, actions=none, id=none, cls="")`, optionally `{% call %}` with extra action markup.

| Arg | Contract |
|---|---|
| `title` | the `h1` (1.25rem/600). Hidden on phones (< 700px): the top bar shows the title |
| `stats` | the stats line `p.p-stats` (muted): a string, or a list of strings / `{"text", "cls", "title"}` joined with ` · `. `cls`: `warn` (yellow), `bad` (red), `ok` (green); the text says the state too. Every page has one |
| `actions` | list of `{"label", "icon", "href", "attrs", "pri", "danger", "title"}`: `<a class="x-btn p-act">` with `href`, else `<button type="button" class="x-btn p-act">`; `attrs` = extra attributes (`hx-post`, `hx-confirm`, `data-*`); at most one `"pri": true` (green) per page. The label is in `span.lb` |
| caller | appended inside `div.p-acts` (a form with the csrf field, a `pm.menu` trigger) |
| `id` | the head gets `id`, the stats line `id="<id>-stats"` (a target for a live swap of the counts) |

Markup: `div.p-head > h1 + p.p-stats + div.p-acts`. Phone: `h1` hidden, stats on their own line, actions become
40px icon buttons (`.lb` visually hidden, so the accessible name stays) at the top right of the next block; that
block keeps room for the shown ones (`--p-acts-w`, up to three; a `[hidden]` action takes none); when the next block
is neither `.p-tools` nor `.p-tabs` the actions get a row of their own.

`ph.tools(cls="")` — `{% call %}` only: `div.p-tools` (flex, wraps). Order inside: `pf.fbox` (flexible, grows),
`ps.seg`, switches, selects; `<span class="sp"></span>` pushes the rest right, also when the rest wraps onto a line of
its own (the element after `.sp` gets `margin-left: auto`: never a lone control at the left of a second line); a page
column of 760px or less gives the filter box the whole first line; `span.note` = muted text.

## 3. Flat tabs (`parts/_tabs.html`)

`pt.tabs(items, label, cls="")`; items `{"key", "label", "href" | "controls", "cur", "count", "bad", "icon", "title"}`.

- `div.p-tabs[role=tablist][aria-label=<label>]`; each tab `.p-tab[role=tab][id=p-tab-<key>][data-p-tab=<key>]`
  with `aria-selected` and a roving `tabindex` (selected `0`, others `-1`). No `cur`: the first tab is selected.
- `href` → `<a role=tab>`, the selected one also `aria-current="page"`: server-rendered sections (`?tab=`); Enter
  follows the link.
- `controls` → `<button role=tab type=button aria-controls=<panel id>>`: client panels. `pt.panel(id, tab_key, cur)`
  (`{% call %}`) renders `div.p-tabpanel[role=tabpanel][aria-labelledby=p-tab-<key>]`, `hidden` unless `cur`.
  Click / Enter / Space selects (aria-selected, tabindex, panels) and fires `pm7:tab` `{key}` on the tablist.
- The label is `span.t[data-t=<label>]`: its hidden 600 copy (`css/parts.css`) keeps every tab as wide as when it is
  selected, so labels never move when the selection changes.
- `count` → `span.c` after the label; `bad` → red 600.
- Keys (`js/parts.js`): ←/→ (wrapping), Home, End move focus only; handled in the capture phase and stopped, because
  classic `tabs.js` (still loaded for un-ported content) would click every tab.
- Phones: 44px, the strip scrolls edge to edge; the selected tab is centred once the layout has settled (fonts
  loaded), again after a swap or a resize while the user has not scrolled the strip, and when a tab is chosen. An item
  cut at the start edge keeps at least half of itself in view (else it is scrolled out, or brought in when the strip is
  at its end; failing both it is transparent, `.p-cut`, still focusable, until the user scrolls or focuses it), so a
  label is never reduced to its count.
  The "more" fades show only while the strip overflows, at an edge whose first / last item is not wholly in view
  (`.ovf` / `.beg` / `.end`, `js/parts.js`).

## 4. Segments (`parts/_seg.html`)

`ps.seg(items, label="Show", target=none, swap="outerHTML", push=true, cls="")`; items
`{"key", "label", "href", "cur", "count", "warn", "bad", "title"}`. Segments **filter, never sort**.

- `nav.p-seg[aria-label]` of `a[href][data-p-seg=<key>]`; the current one `aria-current="true"`; `count` →
  `span.c` (`warn` yellow 600, `bad` red 600).
- `target` set: each link also has `hx-get=<href> hx-target=<target> hx-swap=<swap>` (+ `hx-push-url="true"` when
  `push`); `js/parts.js` moves `aria-current` to the clicked link at once; `js/filter.js` sends the filter box's
  current `q` with that request. Without `target`: plain links (full page load).
- Phones: the strip scrolls; the current segment is centred and the fade shows only while it overflows (as the tabs,
  §3). Touch: `--seg-h` 40px (README A20).
- A **view toggle** (not a filter, e.g. the session header's Detail | Compact) uses the same look without the macro:
  `<span class="p-seg" role="group" aria-label=...>` of `button[type=button][aria-pressed]`; `css/parts.css` styles
  `>button` like `>a`, `aria-pressed="true"` like `aria-current`. The page's JS flips `aria-pressed`.

## 5. Filter box (`parts/_filter.html`)

`pf.fbox(name, value="", placeholder="Filter", label="Filter", rows=none, get=none, target=none,
swap="outerHTML", push=true, delay=200, cls="")`. One per page for the page's lists; it matches name-type text only
(ids, names, project, branch, window, title), never message bodies.

Markup: `div.p-fbox[role=search] > svg + input[type=search][name=q][data-p-filter=<name>] + button.p-fclr[data-p-fclr]`.
`name` must be unique on the page (the box is found again by it after a swap). The × shows only while the field has
text (CSS `:placeholder-shown`; the input always has a placeholder).

| Mode | Set | Behaviour (`js/filter.js`) |
|---|---|---|
| Client | `rows="<selector>"` → `data-p-filter-rows` | every `[data-p-q]` inside the container is `hidden` unless its text contains every typed word (case-folded); a `.p-gg` group with no row left hides; `[data-p-fnone]` (`tb.empty(..., fnone=true)`) shows when nothing matches; re-applied after every htmx swap; `pm7:filter` `{name, q, shown, total}` fires on the input. Rows carry their text with `tb.row(..., q="...")` |
| Server | `get="<url>"`, `target="<selector>"` | `hx-get=<get> hx-trigger="input changed delay:<delay>ms, search" hx-target hx-swap hx-sync="this:replace"` (+ `hx-replace-url="true"` when `push`). On that request the page URL's other query parameters (segment, sort, group) are kept and `q` comes from the box. GET requests from links/controls inside `.p-seg`, `.p-gh`, `.p-foot` or `.p-tools` get the box's current `q` set (or removed when empty) |

Both modes: when a swap replaces the focused box, the new one gets the typed text, the caret, its own scroll and
the page scroll (`#main`) back. Best practice: keep the box outside the swapped target.
Keys: `/` focuses it (`keys.js`), Esc clears it, a second Esc leaves the field (`layers.js`, `DESIGN.md` §7). The
filter has no keydown listener of its own.

## 6. Grid table (`parts/_table.html`, `css/table.css`, `js/table.js`)

### 6.1 Frame and columns

`{% call tb.table(key, label, cls="", list=none, keys=none, labels=none, id=none) %} ... {% endcall %}`

- `div.p-g[role=table][aria-label=<label>][data-p-table=<key>]`. `key` = the state key (open details and folds are
  remembered per key, in memory, across swaps); unique on the page.
- `cls` = the page's table class, which sets the ONE grid template (never an inline style):

  ```css
  .i-list{--g-cols:var(--dot) minmax(9rem,1fr) fit-content(9rem) fit-content(9rem) minmax(0,2.2fr) auto auto auto auto;
    --g-cols-n:var(--dot) minmax(9rem,1fr) fit-content(9rem) minmax(0,2fr) auto auto;   /* without .hide-n */
    --g-cols-s:var(--dot) minmax(8rem,1fr) fit-content(7rem) auto auto}                 /* without .hide-n, .hide-s */
  ```

  One track per cell, in cell order. `--g-cols-n` / `--g-cols-s` default to the wider template, so a table without
  `.hide-*` cells sets `--g-cols` only. The table is the grid and header, groups and rows are **subgrids**, so
  `auto`, `max-content` and `fit-content()` tracks size to the widest cell of the whole table and still line up;
  use them instead of fixed widths. The flexible text column gets `minmax(0, Nfr)` and never drops below ~240px
  (`LAYOUT.md` L-6): drop columns at 760 / 520 instead.
- `.hide-n` cells (and header cells) drop when the page column (`@container pg`) or the project main (`pj`) is
  ≤ 760px; `.hide-s` ones at ≤ 520px (`pg`, `pj`, `fp`). The template switches to `--g-cols-n` / `--g-cols-s` at the
  same widths.
- `list` → `data-list=<name>`: the table is a keyboard list (§7). `keys` = `{"enter"|"o"|"x"|"e"|"p": action}` →
  `data-key-<k>`; `labels` = `{k: "text"}` → `data-key-<k>-label` (the `?` sheet text).

### 6.2 Header

`tb.gh(cols, sort=none, sort_url=none, target=none)`; cols `{"label", "key", "href", "cls", "num", "sr", "title"}`.

- `div.p-gh[role=row]` of `span[role=columnheader]`, one per column (also the dot and actions columns: give them
  `"sr"`, a screen-reader label).
- Sortable when the column has `"href"`, or `"key"` and `sort_url` is given: `a.p-sort[href=<href or
  sort_url(key)>]` + `i.ar` (the arrow slot, always reserved, so a sorted label never moves). The server decides
  the direction (`sort_url` returns the reversed order for the current column: "click again reverses").
- `sort` = the current order: `"key"` ascending, `"-key"` descending → `aria-sort` on that columnheader only.
- `target` → the sort links also load with htmx (`hx-get`, `hx-target`, `hx-swap="outerHTML"`, `hx-push-url`).
- `"num"` → `.num` (right-aligned, tabular figures; the arrow sits before the label); `"cls"` carries
  `hide-n` / `hide-s`.

### 6.3 Groups (bands)

`{% call tb.group(key, label, count=none, open=true, note="", icon=none, cls="", actions=none) %} rows {% endcall %}`

`div.p-gg[role=rowgroup][data-p-grp=<key>]` (`.fold` when closed) > band `div.p-grp[role=row]` > `span.p-grpc[role=cell]`
> `button.p-fold[aria-expanded]` (chevron, optional icon, `span.t` label, `span.c` count, `span.note`), then the
rows. Enter / Space / click fold it (`js/table.js`; `pm7:fold` `{key, open}`). `cls="warn"` / `"bad"` colours the
label and icon (yellow / red), never the band. One table with bands per list (MR-27), never one box per group.

`actions` (the caller is the rows, so band buttons come as data): a list of `{"label", "icon", "title", "attrs",
"hidden"}` → a second band cell `span.p-grpa[role=cell]` at the band's right end (shown also while folded) with one
`button.x-btn.sm[type=button]` per item; `attrs` = `data-*` / `hx-*`, `hidden` = starts hidden (the page's JS shows
it). Example: the Notifications day band's "Mark read" (`{"attrs": {"data-n-dayread": key}, "hidden": not un}`).
Never position a button over the band from page CSS.

### 6.4 Rows and cells

`{% call tb.row(id, href=none, cls="", kind=none, cur=false, open=false, sel=false, q=none, title=none, attrs=none) %}
cells {% endcall %}`

- `div.p-gr[role=row][data-row-id=<id>][tabindex]`; `id` = the real id (agent id, project name, tmux `$id`, issue
  number; never an index).
- `href` → `data-href` (the row's link: ↵ / click in a list). Give `tb.name` the same `href` with `row_link=true`
  so the name is a real link and the row opens in a new tab / window on ⌘ / Ctrl / Shift click or a middle click
  (`div` rows cannot; see `tb.name` below and §7 "Mouse"). `kind` → `data-kind` (3px kind edge; `html.no-kedge`
  turns it off). `cur` → `tabindex="0"` (the list's Tab stop; others `-1`). `open` → `.open` (details shown;
  render the `tb.det` open too). `sel` → `.sel` (the row shown in the aside). `q` → `data-p-q` (lower-cased text for
  the client filter). `cls`: `un` = unread (name 600), page classes. `attrs` = extra `data-*` attributes (`xmlattr`;
  e.g. the Notifications rows' `data-n-abs`, `data-n-click`, `data-n-pri`, read by the bell).
- Row fills: hover `--hover`, `.open` / `.sel` `--accent-soft`, nothing else. Focus ring: 2px accent outline inset
  (no layout shift, MR-25). Desktop rows: 36px min, one line, cells truncate with an ellipsis.
- **Every direct child of a row is a cell with `role="cell"`**, in the order of the grid tracks. Cell macros:

| Macro | Markup | Role in the layout |
|---|---|---|
| `tb.dot(state, title=none)` | `span.gc-dot > i.dot.<state>[title][aria-hidden] + span.vh` | state dot; the word is in `.vh` (never colour alone) |
| `{% call tb.name(text, href=none, sfx=false, row_link=false) %} chips {% endcall %}` | `span.gc-nm > (a|span).nm` + caller | the name (500; `.un` row 600); `sfx` truncates from the left (`…1c70ee`, L-4); chips after it. `row_link=true` (with the row's `href`): `a.nm[data-row-link][tabindex=-1]`, looks like the name (no link colour or underline), not a Tab stop (the row is) |
| `{% call tb.cell(cls="", num=false) %}...{% endcall %}` | `span[role=cell]` | any column; `cls` may hold `hide-n hide-s muted mono warn bad ok` |
| `tb.end(text, since=none)` | `span.gc-end` (+ `data-since` for `ticker.js`) | the end value (age), right-aligned |
| `{% call tb.meta() %}...{% endcall %}` | `span.gc-m` | phone line 2 only (project · message); hidden on desktop |
| `{% call tb.acts() %}...{% endcall %}` | `span.gc-act` | row actions: one visible action + `pm.menu`; on hover devices non-`.keep` buttons show on row hover / focus / `.open`; on phones only `.keep` (the ⋯) |
| `<span class="gc-sw" role="cell">{{ psw.switch(...) }}</span>` | | a switch column (stays on phones in the end slot) |

- Phones (< 700px): no header; the table runs edge to edge; **two-line rows**, 56px min: line 1 = `.gc-dot` ·
  `.gc-nm` · `.gc-end` (or `.gc-sw`) · `.gc-act`, line 2 = `.gc-m`. Every other cell is hidden, so put what matters
  on phones into `.gc-m` and the ⋯ menu. A row whose `.gc-m` is empty is one line, centred, `--hit` (44px) min.
- A table without a header draws no rule above its first line (the table's border is its top edge).

### 6.5 Row details

- `tb.det_btn(table, id, open=false, label=none)`: `button.x-ib.sm.p-detb[data-p-det-btn][aria-expanded]
  [aria-controls=p-det-<table>-<id>]` (chevron) for `tb.acts`.
- `{% call tb.det(table, id, open=false, src=none, label=none) %} content {% endcall %}`, right after its row:
  `div.p-det[role=row][id=p-det-<table>-<id>][data-p-det-for=<id>]` (`hidden` unless `open`) > `div.p-detc[role=cell]`.
  With `src` (and no call body) the content is loaded with `htmx.ajax('GET', src)` into `.p-detc` on first open, and
  again when a swap brought a fresh empty one.
- Inside: `tb.kv(pairs)` → `dl.p-kv` (pairs `(key, value)` or `(key, value, cls)`), `{% call tb.dacts() %}` →
  `div.p-dacts` (buttons at the end; `span.sp` pushes the rest right).
- `js/table.js`: the chevron, `X` / `→` / `←` (listkeys) and the menu item `{"det": true}` toggle; opening one row
  closes the table's other open row; `pm7:det` `{id, open}` fires on the row.

### 6.6 Empty line, foot, hints

- `tb.empty(text="", fnone=false)` (or `{% call %}` with a link): `div.p-empty[role=row] > span[role=cell]`, inside
  the table. `fnone=true`: the client filter's "no match" line (`data-p-fnone`, starts hidden). Say what is
  missing and how to add it (`DESIGN.md` §6.21).
- `tb.foot(shown, total, more_href=none, target=none, noun="", text=none, newer=none, older=none,
  older_label="Older", newer_label="Newer", notes=(), push=true)`: `p.p-foot` under the table, rendered only when it
  has something to say. "Showing X of Y <noun> · Show all Y" when `shown < total` (`a.p-more`); `text` replaces the
  "Showing" phrase (a range: "Showing 51–100 of 1,234+"); `newer` / `older` = paging links (`a.p-more`, labels
  `newer_label` / `older_label`, e.g. "Load older (800 more)"); `notes` = muted sentences after them; items are
  separated by "·". `target` = htmx swap of that element (`outerHTML`; `push` = `hx-push-url` on the paging links,
  Show all always pushes); without it the links are plain (a full page load). Audit (Activity, Client errors) pages
  with it; never hand-written `p.p-foot` paging.
- `tb.hints(items)`: `p.p-keys[aria-hidden]`, items `(keys, text)` with space-separated key specs (`("J K",
  "move")`, `("Enter", "aside")`); each key is `kbd[data-k=<spec>]`, filled from the key table by `keys.js` (also
  after swaps). Hidden on phones and with `html.no-khints` (Settings › Display › Show shortcut hints off).

### 6.7 State across swaps (`js/table.js`)

The server renders the first state (`L-1`). After the user toggles a fold or a row's details, that choice is kept
in memory per `data-p-table` key and applied again after every htmx swap (also a swap of the whole table). Nothing
is stored in the browser; a page that wants folds remembered across reloads keeps them on the server and renders
`open`.

## 7. List keys (`js/listkeys.js` + `pm7.keys.register`)

A keyboard list = any element with `data-list=<name>` (normally `tb.table(..., list=...)`) whose rows are
`[data-row-id]` inside it. While a list is on the page these keys work (no text field focused, no layer open) and
show in the `?` sheet under "This list":

| Key | Action | Needs |
|---|---|---|
| J / K | focus the next / previous visible row (roving tabindex; the first J starts at the list's Tab stop) | — |
| ↵ | the row's `enter` action (the row itself must have focus; on a button inside it Enter is the button's) | default: `data-href` |
| O | `o` action | declared |
| X | `x` action (default: toggle the row's details) | a `.p-det` or declared |
| → / ← | open / close the focused row's details | a `.p-det` |
| E | `e` action (read / unread) | declared |
| P | `p` action (pin) | declared |

Mouse: a click on a row (not on a link, button, input, switch or ⋯ inside it, not in its details) runs `enter`; a
double-click runs `o` when declared. The row's own link (`a[data-row-link]`, `tb.name(..., row_link=true)`) counts as
the row: a plain click on it runs `enter` (an event such as `event:i:open` too, not only `href`; the link is not
followed when the action ran, nor on a double-click's second click) and moves focus to the row. ⌘ / Ctrl / Shift
click and a middle click (`auxclick`) on it are left to the browser: the row's `href` opens in a new tab or window.
Tables with row hrefs use it: the agent rows (live `/a/<id>`, Ended), Projects, Agents › Installed, Agents › Usage
top sessions.

**Actions.** For key `k` in `enter o x e p` the first that exists wins:

1. a page callback: `pm7.list.on("<list name>", {enter(row, id, e), o, x, e, p})` (from the page's own JS; return
   `false` = nothing done). `pm7.list.on` returns an `off()` function.
2. `data-key-<k>` on the row, then on the list, with one of:
   - `menu:<item id>` — run that item of the row's ⋯ menu (`[data-menu]` in the row, §9)
   - `event:<name>` — dispatch `CustomEvent(name)` on the row (bubbles), `detail {id, list, key}`
   - `href` — go to the row's `data-href` (same-origin path only)
   - `det` — toggle the row's details
   - anything else — a CSS selector; the first match inside the row is clicked (an htmx button, a pin toggle)
3. the default (`enter` = `href`, `x` = `det`), else nothing.

`data-key-<k>-label` on the list = the sheet text for that key ("Open in the chat aside").

**Focus.** One row per list is the Tab stop (`tabindex="0"`, the server's `cur` row, else the first); listkeys
keeps it on the last focused row and, after an htmx swap, gives focus back to the same row id if focus was lost.

**Key table API (`js/keys.js`).** Page keys never add a keydown listener; they register in the one table:

```js
const off = pm7.keys.register(
  { id: 'list', group: 'This list', active: () => bool },          // scope: keys fire / show only while active()
  [{ id, label /* text or () => text */, keys: ['J'], run(e) /* return false = nothing to do */,
     active /* optional () => bool */ }]);
```

Specs as in the table: `'J'`, `'Enter'`, `'ArrowRight'`, `'mod+K'`, `'G I'`. Single keys fire only when no text
field has focus; a shell key always wins a clash (page rows are checked after the shell's); scoped rows are never
search commands. `pm7.keys.label(spec)` renders a label (`Enter` → `↵`, arrows → `← → ↑ ↓`, `mod` → ⌘ / Ctrl);
`pm7.keys.fill(root)` fills `kbd[data-k]` and adds ` (<key>)` once to the title of `[data-k-title=<spec>]`.

## 8. Switch and select

`psw.switch(name, on=false, label="", count=none, hide_label=false, disabled=false, why=none, attrs=none, cls="")`

- `label.p-swl > button.sw[type=button][role=switch][aria-checked][data-p-sw=<name>] + input[type=hidden][name=<name>]
  [value="1"|"0"] + span(label) + span.c(count)`. The label element names the switch (`hide_label`: the text is
  screen-reader only). 34×20 (touch 44×26, with an invisible `--hit` 44px target), on = green.
- Touch hit area (README A20, `css/parts.css`): `button.sw` and any `.p-hit` control (a small icon button such as a
  tag's ×) get a `::before` of at least `--hit` around their centre; a colour input pads to `--hit` and draws its
  border on the swatch; a plain link (`a[href]` without a class) in a `p` or `.p-empty` pads vertically. Neighbours
  need `--hit` spacing for the target to be whole (Settings tags: row gap `--hit − --tag-h` on touch).
- Click / Space / Enter: `js/parts.js` flips `aria-checked` and the hidden value, then fires a bubbling `change`
  from the hidden input (capture phase, so an `hx-*` on the switch already sees the new state). Use it in a form
  with `hx-trigger="change"`, or listen for `change`.
- `disabled`: .55 opacity, `why` becomes the label's title (say why).

`psel.select(name, options, value=none, label="", disabled=false, attrs=none, cls="")`

- `select.sel[name][aria-label]`, 30px (touch 40px), native arrow; options `(value, text)` or
  `{"value", "text", "disabled"}`; the one equal to `value` is selected. Controls rows have no visible label: put it
  in the option text ("Group: state"). `attrs` for `hx-get` / `hx-trigger="change"` / `data-*`.

## 9. ⋯ menu trigger (`parts/_menu.html`)

`pm.menu(items, label="Actions", keep=true, cls="")` emits **only** the trigger:
`button.x-ib.sm.p-mbtn.keep[type=button][aria-haspopup=menu][aria-expanded=false][aria-label=<label>][title]
[data-menu='<items as JSON>']`. A click opens `pm7.menu` (`layers.js`: popover on desktop, bottom sheet on phones,
one Esc, outside click, focus back to the trigger). `keep` = also shown on phones and without hover.

Items (JSON, in order): `{"sep": true}` or

| Key | Meaning |
|---|---|
| `id` | item id (`data-key-<k>="menu:<id>"` and `pm7.menus.run(trigger, id)` run it) |
| `label`, `icon` | text, sprite icon id |
| `note`, `key` | small note; key spec shown as `kbd` (`"E"`) |
| `danger` | red item |
| `disabled`, `why` | greyed out; `why` shown as the note |
| one action: `href` | same-origin path: a link |
| `click` | selector inside the trigger's row (`[data-row-id]`) or `[data-menu-scope]`: clicked |
| `det` | `true`: toggle the row's details |
| `copy` | text copied to the clipboard; toast "Copied" |
| `event` | `CustomEvent(<event>)` on the trigger (bubbles), `detail {id: row id, item: item id}` |
| `post` (+ `vals`) | POST with the CSRF token and `Accept: application/json` (`pm7.fetch`); a JSON `{ok: false, notice}` counts as failed, a JSON `redirect` (same-origin path) is followed, the success toast is the server's `notice`, else `done`. `confirm` = question (plain confirm), `typed` = exact name (typed confirm, `DESIGN.md` §6.12), `undo` = the typed
confirm's last sentence instead of "This cannot be undone." (say what is kept: "The files are backed up to … first."),
`ok` = button text, `done` = toast after the server said OK, `refresh` = selector that gets the htmx event `pm7:refresh` afterwards; an error toasts `pm7.why(r)`: the server's message (JSON `detail` / `error` / `msg`, or a short text body; masked, one line) with the status, else "Failed (status)" |

Prefer `click` on a real row control or a `post` with confirm over JS; destructive items say what they destroy in
`confirm` / `typed` (`CONDUCT.md` V7-SEC-6).

**Building the items: `pm.web.dt.menus`** (one builder for every dt menu, V7-CODE-6). View models (Inbox, the
Notifications rows, the session tab) build their lists in Python and the template only passes them to `pm.menu`:
`menus.item(id, label, icon="", key=, note=, danger=, disabled=, why=, <one action + its options>)`,
`menus.copy_id(agent_id)`, `menus.gated(id, label, icon, why=, need=, need_why=, <action>)` (disabled with a
reason when `why` is set or `need` is false: control off, protected, wrong state), `menus.SEP`, `menus.menu(*items)` (flattens lists, drops `None`, no leading / trailing /
double separator). `item` refuses an item without exactly one action (a disabled one has none), an unknown key, and a
`typed` value that the `confirm` question does not name (the label names the object, the user types that name).

## 10. JS globals and events

| Global | From | API |
|---|---|---|
| `pm7.table` | `table.js` | `det(row, open?) -> bool`, `fold(button|group, open?) -> bool`, `restore(root?)` |
| `pm7.filter` | `filter.js` | `apply(input)`, `clear(input)`, `q(name?) -> text`, `match(text, q) -> bool` |
| `pm7.list` | `listkeys.js` | `on(name, handlers) -> off()`, `act(row, key, e) -> bool`, `row() -> focused row` |
| `pm7.menus` | `parts.js` | `open(trigger)`, `items(trigger) -> pm7.menu items`, `run(trigger, id) -> bool`, `exec(opener, item) -> bool` (run one item object, e.g. a page button carrying it as `data-*` JSON: Settings) |
| `pm7.reply` | `core.js` | `reply(response) -> Promise<{ok, notice, redirect, json}>`: a POST's outcome; a JSON `{ok: false}` is a failure even with status 200 (the core's `/settings/*` refusals); `pm7.why` also reads JSON `notice` |
| `pm7.keys` | `keys.js` | `register(scope, rows) -> off()`, `label(spec)`, `fill(root)` (+ the shell's `table`, `commands`, `sheet`) |
| `pm7.el`, `pm7.add` | `core.js` | the one DOM builder: `el(spec, attrs \| cls, ...kids) -> element` (spec `"tag.cls.cls"`; attrs object: `null`/`false` skipped, `true` = `""`, `cls` adds classes, `on` = `{event: fn}`; a string = the class; kids: nodes, text as text nodes, arrays), `add(node, kids)`. No HTML strings and no page-local `el` / `h` copies (`pm7.pjops.h` is this one) |
| `pm7.confirm` | `layers.js` | `confirm({title, body, ok, cancel, danger, typed, undo, opener}) -> Promise<bool>`: plain (reversible) or typed (`typed` = the exact name; OK stays disabled until it matches). The typed hint reads "Type <name> to confirm. This cannot be undone."; `undo` replaces the last sentence when something is kept (Agents › Remove: "The files are backed up to ~/… first."). Toasts: `pm7.toast(text, key, {error, ms})`; a `key` (the shortcut's label) makes it a shortcut toast, shaped by `html[data-ktoast]` (`SHELL.md` §4) |
| `pm7.attach` | `attach.js` | `upload(base, file) -> Promise<path>`, `chip(box, name, onRemove) -> chip`, `done(chip, path)`, `paths(root)`, `uploading(root)`, `wire(zone, onFiles)` (paste + drop of files into a box) (§15 "Attachments") |
| `pm7.backlog` | `backlog.js`, `backlog-add.js`, `backlog-reader.js` | `add.open(...)`, `add.task(...)` (Add issue / Add task dialogs: one per line, Add another, pasted images), `reader(p, n, opener, {edit})` (`edit: true` opens the issue's Edit form), events `bl:edit` / `bl:tedit` (row ⋯ Edit…, key R) |
| `pm7.peek` | `peek.js` | `open(project, {opener})`, `close()`, `isOpen()`, `name()`: the project quick look, `dialog#x-peek` (`shell/_peek.html`, `css/peek.css`) filled from `GET /dt/peek/{project}?path=` (`project/_peek_list.html`); `[data-peek="<project>"]` opens it; file links follow the `peek_open` pref (new browser tab / this tab) |
| `pm7.md` | `mdview.js` | `open(agentId, path)`, `current()`: the markdown reader dialog `dialog#x-md` (`shell/_mdview.html`) over the shell, from the aside's `.md` links (`GET /a/{id}/md`) |

Events (all bubble): `pm7:det` `{id, open}` on a row · `pm7:fold` `{key, open}` on a group · `pm7:tab` `{key}` on
a tablist · `pm7:filter` `{name, q, shown, total}` on a client filter input · `change` from a switch's hidden input ·
menu / key `event:` names as declared by the page · `pm7:refresh` (htmx trigger) after a menu `post` with `refresh`.

## 11. Example (Inbox rows, shortened)

```jinja
{{ ph.head("Inbox", stats=[n ~ " agents", {"text": need ~ " need you", "cls": "warn"}],
           actions=[{"label": "Mark all read", "icon": "check", "attrs": {"hx-post": "/inbox/read-all"}}]) }}
{% call ph.tools() %}
  {{ pf.fbox("inbox", value=q, placeholder="Filter agent, project, tmux", label="Filter agents",
             get="/inbox/rows", target="#i-list") }}
  {{ ps.seg(views, target="#i-list") }}
  {{ psw.switch("unread", on=unread_only, label="Unread", count=unread) }}
  {{ psel.select("grp", [("state", "Group: state"), ("project", "Group: project")], value=grp, label="Group by",
                 attrs={"hx-get": "/inbox/rows", "hx-target": "#i-list", "hx-trigger": "change"}) }}
{% endcall %}
{% call tb.table("inbox", "Agents", cls="i-list", list="inbox", id="i-list",
                 keys={"enter": "event:i:aside", "o": "menu:tab", "e": "menu:read", "p": "[data-i-pin]"},
                 labels={"enter": "Open in the chat aside"}) %}
  {{ tb.gh([{"sr": "State"}, {"label": "Agent", "key": "id"}, {"label": "Project", "key": "project"},
            {"label": "Age", "key": "age", "num": true}, {"sr": "Actions"}], sort=sort, sort_url=sort_url,
           target="#i-list") }}
  {% for g in groups %}{% call tb.group(g.key, g.label, count=g.rows|length, open=g.open, cls=g.tone) %}
    {% for a in g.rows %}
      {% call tb.row(a.id, href="/a/" ~ a.id, kind=a.kind, cls="un" if a.unread else "", sel=a.id == aside_id) %}
        {{ tb.dot(a.state) }}
        {% call tb.name(a.id, href="/a/" ~ a.id, sfx=true, row_link=true) %}{% if a.unread %}<span class="chip">{{ a.unread }}</span>{% endif %}{% endcall %}
        {% call tb.cell("muted") %}{{ a.project }}{% endcall %}
        {{ tb.end(a.age, since=a.since) }}
        {% call tb.meta() %}{{ a.project }} · {{ a.last }}{% endcall %}
        {% call tb.acts() %}{{ tb.det_btn("inbox", a.id) }}{{ pm.menu(a.menu, label="Actions for " ~ a.id) }}{% endcall %}
      {% endcall %}
      {{ tb.det("inbox", a.id, src="/inbox/" ~ a.id ~ "/details") }}
    {% endfor %}
  {% endcall %}{% endfor %}
  {{ tb.empty("No agent matches.", fnone=true) }}
{% endcall %}
{{ tb.foot(shown, total, more_href=all_url, target="#i-list", noun="agents") }}
{{ tb.hints([("J K", "move"), ("Enter", "aside"), ("O", "tab"), ("X", "details"), ("E", "read"), ("P", "pin")]) }}
```

```css
/* css/inbox.css */
.i-list{--g-cols:var(--dot) minmax(9rem,1fr) fit-content(10rem) auto auto;
  --g-cols-s:var(--dot) minmax(8rem,1fr) auto auto}
```

## 12. Rules for page agents

- Reuse these parts; a page-local copy of a head, tab strip, filter, sort header, menu or Esc handler is a bug
  (`CONDUCT.md` V7-UI-1, V7-CODE-6). Missing something: extend the part (and this file) instead.
- Page CSS sets only the table's `--g-cols*` and page-specific cell looks (`i-`, `n-`, ... prefixes); never restyle
  a part.
- Page keys go through `pm7.keys.register` (or `data-key-*` on a list); never `document.addEventListener('keydown')`.
- Rows are addressed by real ids (`data-row-id`), never by index (`CONDUCT.md` V7-CODE-4).
- Toasts that report a result come after the server answered (`post` items do this; V7-CODE-5).

## 13. Timed polls (V7-CODE-7 exceptions)

dt pages take live data from the one stream (`js/live.js`: `pm:<type>` events, `pm7:resync` after a reopen) and the
shared ticker (`data-since`); the Inbox (`#inbox`) and the Notifications list (`#n-panel`) have no timed trigger. The
only timed polls left are classic content kept by README A5, until the chat items are ported:

| Element | Poll | Why it stays |
|---|---|---|
| `#chat` (agent page, Builder) | `every 2s` (core `_chat.html` keeps the same trigger on its swap) | the live strip ("working… N tool calls", a tool waiting with its pane tail, the Needs you card) is read from the pane; the stream has no event for a pane change, only for state changes |
| `#sa-live` (agent page, Builder, Claude) | `every 4s` | running sub-agents' progress (transcript files); no stream event |
| `#sa-body[data-sa-poll]` (Sub-agents tab) | classic `nodiff.js` timer, 3 s while something runs, 15 s idle, only while the tab shows | same as `#sa-live` |
| Agents › one agent, login output (classic `_agents_auth.html`, README A22) | `every 2s`, only while a login runs (the fragment drops the trigger when it ends) | the CLI's login output (device code, URL) is read from its pane; no stream event. Classic markup inside `.x-cl.pg-agents`, so it is not a dt template |
| `#sv-panel` (Servers, README A21) | `every 10s` (+ `pm7:refresh`, `res-changed` / `vms-changed from:body`); dt `js/servers.js` cancels it while `document.hidden` and refreshes once when the tab shows again | host metrics, containers and processes change without any stream event (`EVENT_TYPES` is closed, the panel is collected per request) |
| `/healthz` wait (`js/reload.js`, the "pm is restarting…" bar in the `.x-lock` look) | one `setTimeout` chain (700 ms to 1.5 s apart, each call aborted after 4 s), never `setInterval`; it starts only after this tab's own Reload pm (POST `/service/reload` OK) or a 502 / 503 / 504 answer to this tab's own `pm7.fetch` or htmx request, and stops when pm answers or after 90 s | a restarting pm has no stream to report its return; after a Reload pm the page reloads itself, after a 5xx it only offers "Reload now" |

`tests/web/dt/test_theme_static.py` fails on any other `every Ns` in a dt template.

## 14. Project tabs (the project view's tab panels)

The project view (`templates/project.html`, PRD §6.4, LAYOUT §5) is a frame: project sidebar · main (head, links row,
tiles, flat tabs) · reader aside. Each tab is a panel the frame renders; other areas fill it with a partial.

**Panel.** The frame renders every tab (Agents · tmux · Files · Git · Stack · VMs · TODO · Issues · Plans · Docs, minus
those hidden in the project's settings; tmux follows the setting `live`) as

```html
<section class="pj-tab" id="pj-t-<key>" role="tabpanel" aria-labelledby="p-tab-<key>" data-pj-tab="<key>"
         data-pj-base="/p/<project>" hidden>  <!-- no hidden on the first tab -->
  {% include "project/_tab_<key>.html" ignore missing %}   <!-- or a placeholder line when missing -->
</section>
```

- The partial renders the panel's **content only** (never its own `section.pj-tab`). It may load its own CSS / JS with
  `<link>` / `<script defer>` tags (once-guarded JS: several partials may load the same file).
- Partials of other areas: `project/_tab_git.html`, `_tab_stack.html`, `_tab_vms.html` (Git / Stack / VMs),
  `project/_tab_todo.html`, `_tab_issues.html` (Backlog component scoped to the project, README O7),
  `project/_tab_tmux.html` (the project's tmux sessions: the tmux page's rows through the macro `sess_row` of
  `_tmux_session_rows.html`, `ProjectView.sessions` from `tmux.sess_row(..., refresh="#pj-agents", project=...)`;
  `#pj-tmux` has no request of its own: the Agents tab's live fragment carries it out of band, `hx-swap-oob`, only
  when the tab is shown; Close all = typed project name, `POST /p/<name>/tmux/close-all`). The frame's own:
  `project/_agents.html` (Agents; also the live fragment `_project_live.html` of `GET /p/<name>/live`; Ended is a band
  of the same table: `ar.ended_row(..., live_cols=true)` keeps the live columns), `_files.html`, `_plans.html`,
  `_docs.html`.
- Files and Docs content: the dt overrides of the core's fragments. `_project_files.html` (view model `dt_files()`,
  `pm.web.dt.files`): tree `nav.pj-ft` (hidden on phones) · breadcrumb `nav.pj-fcr` · a directory as one grid
  table (Name · Git · Size · Modified · Created) · a file view still in classic markup inside its own `.x-cl` box.
  `_project_doc.html`: a document (`article.pj-doc` in `.x-cl`) or a notice as the shared empty-state box
  (`tb.empty` in a table). The Docs tab opens README, else the first document that exists (`ProjectView.docs`:
  `html` rendered, or `lazy` = `js/project.js` loads the choices in order until one is a document). Docs › Skills
  (`[data-pj-list]`, setting `skills`) is a list, never the first document: the dt `_project_skills.html` (Local |
  Global view toggle `[data-pjk-scope]`, one grid table per scope; a row fires `pj:skill` and opens the SKILL.md in
  the reader).
- Classic hash links (the core's redirects of `/p/<n>/files?path=`, `/view/<key>`, `/skill/...`, `/issues`,
  `/issues/<n>`, and Save as issue's "Open it"): `js/project.js` maps `#browse[=<path>]` → Files at that path,
  `#view=<key>` → Docs at that choice (`dirty` → Git), `#skill=<scope>/<src>/<name>` → Docs › Skills + the reader,
  `#issues` / `#backlog` → Issues, `#issue=N` → Issues + `pm7.backlog.reader(project, N)` (Issues hidden: `/backlog`);
  then the hash becomes `#tab=...`. `#settings` / `#remotes` open those panels (`js/pjsettings.js`).
- Context: the core route's project view model (`project`, `backlog`, `todo`, `plans`, `ps`, `csrf`, ...) and `v` =
  `dt_project()` (`pm.web.dt.project.ProjectView`: `name`, `base`, `tabs`, `cur_tab`, ...).
- First tab: `?tab=<key>` (server), else Agents. `js/project.js` keeps the current one in the URL hash `#tab=<key>`
  (Files adds `&path=<path>`) and selects it again from the hash on load and on `hashchange`.

**Showing.** `js/project.js` shows one panel at a time (through the flat tabs, `parts.js`) and fires
`pm7:pj-tab` `{key}` on `document` whenever a panel becomes visible: a tab click, a tile (`[data-pj-goto=<key>]`), the
hash, `pm7.pj.tab(key)`, and once for the first panel after load. Partials load their data lazily on that event (or
when their section is already visible at first paint) and again on `pm:refresh` / `pm7:refresh` while shown; no timer.
After a Git ▾ action the frame sends `pm7:refresh` to `#pj-t-git` (htmx trigger) and reloads its stats line and tiles.

**Reader aside.** `pm7.pj.reader.open({title, src | url, href, cl})` shows a page in a frame (`src`, e.g. a plan with
`embed=1`) or a fragment (`url`, loaded with htmx into `.x-cl`; `cl: false` for a dt fragment: no classic box;
`href: ''` hides "Open as a page") at the right: a grid column `minmax(--reader-min,
--reader-w)` at ≥ 1560px, an overlay (`--side-float-max`) below; `href` = the "Open as a page" link; Esc / × close it
(`pm7.pj.reader.close()`, `isOpen()`). Git diffs, Plans, skills, project settings and git remotes use it; one reader
per page.

**Project settings and remotes (`js/pjsettings.js`).** The head's Settings button (`[data-pj-settings]`) and Git ▾
Remotes… (`pm7.pjs.open('settings' | 'remotes')`) load the dt overrides of the core's fragments
`_project_settings.html` (`GET /p/<n>/settings/panel`: Show components, default branch, VM names, the remotes panel,
Workspace links) and `_git_remotes.html` (`GET /p/<n>/git/remotes`) into the reader with `cl: false`. Their forms
(`form[data-pjs-form]`) post with `pm7.fetch` to the core's routes; the answer is the same panel with a flash
`[data-pjs-flash=ok|bad]` that replaces it and decides the toast (a refused form keeps what was typed). A saved form
that changed a checkbox reloads the page with `#settings`. Removing a remote (`[data-pjr-rm]`) asks for the typed remote
name. Under the head, the links row (`.pj-links`, `ProjectView.links`): linked projects and plugin `ext_groups`.

**Frame API (`js/project.js`).** `pm7.pj = {tab(key), refresh(), reader, nav, hash: {get, set}}` (hash: the URL hash
as URLSearchParams; set = replaceState): `refresh()` reloads the head's stats line (`#pj-head-stats`) and the tiles
(`.pj-sum`) from a fresh render of the page; a tab partial calls it after an action that changes them (Git: stage,
commit, pull, ...). Tab partials check `pm7.pj && pm7.pj.refresh` before the call.

**Other hooks.** `pm7.pj.nav.toggle()` (⌘⇧B), the Agents tab box `#pj-agents[data-i-rows]` (the Inbox row hooks of
`js/inbox.js` work in any `[data-i-rows]` box), `data-pj-launch="<project>"` on any link opens the Launch dialog
(`pm7.launch.open({project})`) with the link as the fallback.

## 15. M3 parts (Launch, Quick chat, Backlog component, pin, step list)

Short contracts for the parts M3 added; the file named first owns the markup, the JS file the behaviour.

**Launch dialog** (`shell/_launch.html` + `_launch_form.html`, `css/launch.css`, `js/launch.js`, `js/launch-prompt.js`).
`dialog#l-dlg.l-dlg` (width `--launch-w`, README A10; full screen on phones) in `base.html` (not in embed). Its body is
`GET /launch/form?project=&todo=` (the theme's `_launch_form.html`; `form[data-l-form][data-l-base][data-l-data=<JSON
launch_data>]`). Openers: any `[data-l-open]` (`data-l-project`, `data-l-todo`; the top-bar Launch, hidden < 700px),
`pm7.launch.open({project, todo})` (project head `[data-pj-launch]`, Backlog Dispatch), key N and the `>` command
"Launch an agent" (the key table's row `launch`). Launch posts `<base>/launch` (`open=1`: the new agent opens as a
tab); Launch in background toasts after the server's OK. `data-l-data` is also read by the New project dialog
(`tests/web/dt/test_projects.py` fails on a rename).

**Attachments** (`js/attach.js`, look `.chip.att` in `css/parts.css`). The one upload + chip helper, used by the Launch
prompt box (upload at once, the chip is `.up` until the server answers) and the Add issue dialog (a chip per picked
file, uploaded on Add). `pm7.attach.upload(base, file)` POSTs `<base>/image` (field `image`, CSRF by `pm7.fetch`) and
resolves to the `data-image-path` of the answer (parsed, never inserted), or rejects with `Error("<name>: <reason>")`.
`pm7.attach.chip(box, name, onRemove)` appends `span.chip.att[data-att]` (name + × "Remove <name>"; `.up` uploading,
`.bad` failed) and `done(chip, path)` stores the path on it; `paths(root)` / `uploading(root)` read the chips. `pm7.attach.wire(zone, onFiles)`
hands files pasted or dropped into `zone` to `onFiles` (the Launch prompt box, the Add issue dialog). A new
upload or file chip anywhere uses these; no second copy, no second chip look.

**Quick chat panel** (`shell/_qchat.html`, `css/qchat.css`, `js/qchat.js`, `js/qchat-menus.js`, `js/qstream.js`).
`section#q-panel.q-pop[role=dialog][aria-modal=false]` (`--qchat-w` × `--qchat-h`, list `--qchat-list-w`; layer
`--z-panel`, no scrim; `.dock` = over the page right of the sidebar; phones full screen). Openers `[data-q-open]` (top-bar
icon; the phone drawer row `.x-sqc`), key Q and the `>` command "Quick chat" (the key table's row `qchat`; `qchat.js`
points its `run` at `toggle`). API `pm7.qchat = {open(id?), close(), toggle(), isOpen()}`. Data only through `/qchat/*`.
⋯ of the open conversation: Rename, Copy as Markdown, Delete (typed: the conversation's title), Delete all conversations
(typed `delete`; `POST /qchat/delete-all`).

**Issues / TODO component** (`backlog/_bar.html`, `_issues.html`, `_todo.html`, `_dlg_issue.html`, `_dlg_task.html`,
`css/backlog.css`, `js/backlog.js`, `js/backlog-reader.js`). One component for the Backlog page and the project tabs (README O7):
`div.bl-c#bl-issues|#bl-todo[data-bl-scope=page|project][data-bl-tab][data-bl-project][data-bl-projects=<tracker
projects JSON>][hx-get=<its URL>]`, a grid table (§6) inside; `GET /backlog?...` with `HX-Target: bl-issues|bl-todo`
returns the component alone. The project tabs render a lazy placeholder (`[data-bl-lazy=<url>]`) loaded on
`pm7:pj-tab`. Dialogs (outside the swapped box, once per page): `#bl-add` (Add issue: images, then `POST /p/{n}/issues`),
`#bl-addt` (Add task: `POST /p/{n}/todo/add`; 409 = not a tracker project → the pmctl line to copy), `#bl-rd` (reader:
`js/backlog-reader.js`, loaded right after `backlog.js` wherever `#bl-rd` is; a fixed head `.bl-rdf` in the dialog
frame holds #N, the project and Close, focused on open, above the body that scrolls, loads or fails, which has no Close
of its own). Reader actions: Mark done / Reopen,
Dispatch, Copy pmctl command, Open project, Edit (form `#bl-ed[data-bl-editform]` in the body's place, values = the
title as the list shows it and the raw Markdown from the view model's `title` / `body`; the stored ` (YYYY-MM-DD)` suffix
rides in `data-bl-sfx` (`title_sfx`) and is added back on save; `POST .../edit {title, body}`), Delete
(typed confirm `#N`; `POST .../delete`, then the reader closes); a refusal stays in the form's or the reader's alert
line (`[data-bl-rderr]`), toasts only after the server's OK. API `pm7.backlog = {refresh(id?), reader(project, num,
opener)}` (`reader` is set by `backlog-reader.js`); `pm7.backlog.lib` (`post`, `json`, `copy`, `dispatch`, `issueUrl`,
`refreshAll`) is for `backlog-reader.js` only: one post helper, no copy.

**Pin button** (Projects list, `_projects_list.html`, `js/projects.js`). `button.x-ib.sm.pj-pin[data-pj-pin]
[aria-pressed]` in the row's first cell, label "Pin <name>" / "Unpin <name>". A click (not the row click) runs the
row's ⋯ item `pin` (`pm7.menus.run(menu, 'pin')`): the same `POST /p/<name>/pin` (pinned=0|1), toast and `#pj`
refresh as the menu and the P key. No second code path.

**NDJSON step list** (New project, `projects/_new.html`, `js/newproject.js`). `ol.pj-nsteps[data-pj-steps]
[aria-live=polite]`: one `li.st-<state>` per step of `GET /p/new/plan` (`{id, text, kind}`), updated line by line from
the `POST /p/new/run` NDJSON answer (`{id, ok, text, state: done | skipped | failed | not_run}`, then `{end, ok,
project, url, done, failed, not_run}`); `[data-pj-sum]` then says what was done, failed and not run (nothing is
undone). The answer is read with `getReader()` from `pm7.fetch`; the toast "Created <name>" only after the end line
says ok.

## 16. Meters, status line, attention list (`parts/_meter.html`, `parts/_status.html`; DESIGN §6.7, README A24)

`pmt.meters(items, label="Summary", cls="")` → `div.p-sum[role=group][aria-label]`, an auto-fill grid of `--meter-w`
cells (two columns on phones; `cls="one"`: one full-width cell, e.g. a pool meter). `pmt.meter(m)` renders one cell,
`pmt.bar(pct, level="", label="", inline=false)` the bar alone.

| Key of `m` | Contract |
|---|---|
| `label` | the muted line on top |
| `value`, `small` | the big value (`--fs-xl` / 600, tabular figures) and a muted unit or total after it ("/ 36") |
| `pct` | 0..100 (clamped): a `progress.bar` (`--bar-h`, no inline style); none = no bar |
| `level` | `ok` · `warn` · `crit` (also `red`, `bad`) · `na`: bar `--lvl-ok` / `--lvl-warn` / `--lvl-red`; warn / crit add a `.vh` word and colour the value; `na` = muted value, no bar |
| `sub` | the muted line under the bar |
| `href` | the cell is `a.p-m` (the tab or page with the detail; hover `--hover`) |
| `key`, `title` | `data-p-m=<key>`; tooltip |

`pmt.bar(..., inline=true)` is a short share bar inside a table cell (`--bar-in-w`, accent colour), e.g. a row's RAM
share; give it a `label` or it is `aria-hidden`. A page never draws its own bar or meter cell (V7-UI-1).

**Status line and attention list** (`parts/_status.html`, `css/parts.css`; LAYOUT §4 step 3). `pst.status(dot, word,
note="", cls="")` → `p.p-status[role=status]`: a state dot (`running` · `waiting` · `failed` · `idle`, aria-hidden), the
word (600; `cls` `ok` · `warn` · `bad` colours it, the word itself says the state) and a muted note (what the verdict
is based on). `pst.attn(items, label="Worth a look")` → `div.p-attn[role=list]` of rows, nothing for an empty list; an
item `{"tone": warn | bad | muted, "icon", "text", "go", "href"}` is `a.p-attn-i` with `href` (the tab or anchor that
shows it; `go` = that target's name at the right), else a plain row; the tone colours only the icon. Chips that label a
fact: `.chip.warn` / `.bad` / `.ok` (`css/parts.css`, DESIGN §6.14), never a page copy.

## 17. Section heading and check row (`css/parts.css`)

**Section heading** `h2.p-h2`: a block inside a page or tab (Agents › Usage and MCP, Servers › Overview and VMs, the
project's Git / Stack / VMs tabs built by `js/pjops.js`). `--fs-m` / 600, `--s5` above (none when it is the first
child of its box), `--s2` below. Children: `span.c` a muted count or note (tabular figures), `span.note` muted text
that ellipsizes, `span.sp` a spacer before a control (a `.p-seg`: the heading then wraps and centres). Pages never
restyle it.

**Check row** `label.p-chk > input[type=checkbox] + text` (or `+ span` for text that wraps): an option in a dialog or
form (Agents › Sync rules targets, New project options). The whole label is the target (`--row-side-h`, `--hit` on
touch), the box is `--icon-sm` with `accent-color: var(--accent)` and a focus ring; `.off` = not available here
(dimmed, the input `disabled`). A table's select box is not this (`.pj-ck`).
