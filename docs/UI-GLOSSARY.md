# pm UI glossary (shared vocabulary)

> **`classic` theme only.** The `dt` theme has its own rules in `docs/themes/dt/` (start with `docs/themes/dt/README.md`); this file does not apply to dt.

Use these terms in requests and in agent reports so both sides mean the same
element. Paths are under `src/pm/` (T = `templates/`, J = `static/js/`,
C = `static/css/`). "Aliases" are words the user has used; agents should accept
them but answer with the **term**. `[flux]` = name or structure is changing, check
the file before relying on it. Names were checked against the code on 2026-10-11.

## 1. Page-level layout

| Term | What it is | Where | Aliases |
|---|---|---|---|
| shell | Wrapper holding sidebar + main (+ aside) | T/base.html `.v5-shell` (`.has-side` when the sidebar shows) | page frame |
| top bar | Fixed header: brand, search, icon links, account menu | T/base.html `header.topbar` | top nav, header bar |
| sidebar | Left global/contextual list column | T/_side.html `aside#v5side` `.v5-side` | left pane, left sidebar, side nav |
| main | Page content column | T/base.html `<main>` | content, center |
| aside | Right-hand chat drawer over/next to main | T/_drawer.html `#v5chat`, J/drawer.js | chat drawer, chat panel, right pane, drawer |
| md viewer | Right-hand markdown reader | T/_drawer.html `#v5md` (`#v5md-b` body, `#v5md-full`, `#v5md-close`) | markdown pane, doc viewer |
| full mode | Chat as its own page `/a/<agent>` | T/agent.html | full page chat, full chat, agent page |
| embed | Chat page rendered inside the aside iframe | `body.embed`, `#vc-frame` | iframe chat |
| page header | `h1` + stats line at top of a list page | `.phead.inbox-head` > `.inbox-title` (`h1`, `.inbox-stats`); T/_macros.html `page_head(title, stats, short)` (actions in the call body) | title row, heading |
| tab bar (phone) | Bottom nav on narrow screens | T/base.html `.tabbar`, `.tabmore`, `#moresheet` | bottom nav, more sheet |
| tab bar | The shared in-page tab bar: one rule file, three looks. Flat (agent page, aside, Builder Chat\|Preview): text tabs, current 600 + 2px inset underline (agent kind colour `--k` in the agent head and aside, else `--accent`). Boxed group `.tabs-seg` (Servers, Agents, ntfy): grey band, current tab white, 600, `--accent` underline. Doc tabs `.tabs-line` (project docs, tmux\|Stack\|VM, Git, Skills): 2px `--accent` border under the current tab, 500 | `.tabs` > `.tab` (`role=tab` + `aria-selected`, `a[aria-current=page]`, or radio `label[for]`); C/tabs.css, J/tabs.js (`pm.tabs`: select, arrow keys / Home / End, `data-tabs-key` remembered tab, `pm:tab`) | tabs, segmented tabs |
| reload bar | Thin banner "new version, reload" | T/base.html `#reloadbar` `.reloadbar` (`.rb-t`, `.rb-b`) | update banner |
| lock bar | "Locked, unlock" banner (only if `require_unlock`) | T/base.html `#lockbar` | unlock banner |

## 2. Global chrome

### Top bar (`header.topbar`)
| Term | What | Where | Aliases |
|---|---|---|---|
| brand | "pm" link home; `DEV` tag on the dev instance | `.brand`, `.devtag` | logo |
| search box | Global search, `/` or Ctrl/Cmd+K; results popover | `#gsearch` `.gsearch` (`.gs-tgl`, `.gs-in`, `#gs-list`); T/_search.html, J/search.js | global search, finder |
| launch button | New agent launcher link | `.toplink.tb-launch`, J/launchpop.js | new agent, + |
| launch prompt | The Prompt box of Launch (`/p/<name>/launch`, also in the launch popup): the composer, inside the Launch form (no form or Send of its own); Ctrl/Cmd+Enter = Launch, Enter is a newline whatever the setting; attach / paste / drop upload to `/p/<name>/image`, `@` files, `/plan` `/advisor` | `[data-composer=launch]` in `.f-prompt` (`data-upload`, `data-atfiles`, `data-slash` follows the Agent select); T/launch.html, J/launch.js, C/launch.css | prompt, task field |
| stop-all button | Stop all agents (Esc then Ctrl-C each), confirm dialog | `.tb-stop`, `#stop-dialog` | panic, stop all |
| backlog / audit links | Icon links | `.toplink` | |
| quick chat | Small `claude -p` popup; its message box is the composer (send key from Settings, draft, Send turns into Stop while the answer streams, text files and clipboard text go into the message, `/` lists the ticked skills); history dropdown, row menu and MCP / Skills pickers are popovers | `.tb-qchat`, `#qchat` `.qchat` (`.qc-*`), composer `[data-composer=qchat]` (`data-send="stream"`, `data-attach="inline"`, `data-slash-at="word"`), `.qc-hist.pop`, `.qc-rmenu.pop`, `.qc-mcppop.pop`; J/qchat.js, qchat-menu.js | qchat, quick ask |
| btw | Side question about an agent in quick chat (its recent chat as context), never sent to the agent: composer bubble icon or `/btw <question>`; header chip "<agent id> ↗", "Use in composer" under answers | `[data-aside]` in `.cbar-l`, `.qc-aside` `.qc-asl`, `.qc-use`; J/qchat-aside.js | side question, /btw (not the Aside chat drawer) |
| account menu | Dropdown: theme, settings, reload, log out | `#acct-menu` `.acct`, `.acct-pop`; T/_acct.html (`acct_groups`) | user menu, avatar menu |

### Sidebar (`#v5side`, T/_side.html, J/side.js, J/side-init.js)
Two forms:
- **section nav** (`.vs-nav`, title "Menu"): Inbox, Projects, tmux, Backlog, ntfy, Audit; sub-rows Active / Ended.
- **contextual list** (title varies: projects, agents windows, tmux + history; /tmux itself uses the section nav): `.vs-head` (`.vs-toggle` collapse button, `.vs-t` title, `.vs-all` "all" link, `.vs-fold-btn` Expand), `.vs-filter` (filter input), `.vs-list#v5list`, `.vs-foot`.

| Term | What | Where | Aliases |
|---|---|---|---|
| sidebar head | Collapse button + title + all-link | `.vs-head` | sidebar title |
| sidebar filter | The sidebar's own filter box over the list: its own query, name-type `data-q` rows only, `[data-fnone]` "No match." | `.fbox.vs-filter` (`filterbox(scope="#v5side")`, `input[data-filter-scope]`; J/filter.js) | side search |
| sidebar row | One list entry: dot, name, cols | `.vs-row` (`.on` = selected, `.vs-cold`, `.vs-sub`) | side item |
| sidebar section | tmux / History blocks with small caps heading | `.ts-sec.ts-tmux`, `.ts-histsec`, `.ts-h` | TMUX block, HISTORY block |
| unread pill | Number badge after the name; over 99 shows 99+, hidden at 0 | `.cpill.vs-ub` (and `.cpill.ntfy-unread`); T/_macros.html `count_pill()`, live updates `pm.badges` (J/core.js) | unread badge, counter |
| foot | Bottom group: Manage / Service / Account | `.vs-foot` (`.vs-foot-tg` toggle, `#vs-foot-body`) | sidebar foot, bottom of the left pane |
| collapse | Sidebar folded to a rail | `.vs-toggle`, state class on shell | fold, hide sidebar |

## 3. Home page (`/`, "Inbox")  T/inbox.html + T/_inbox.html
| Term | What | Where | Aliases |
|---|---|---|---|
| inbox | Whole home page (code name) | T/inbox.html, C/inbox.css, J/inbox.js | home, dashboard, active sessions |
| filter bar | Row under the title holding the filter box and Show select (no button); a `data-filter-live` form also sends the query to the server | `.filterbar`; forms `#inbox-filter` (Inbox), Projects `form.filterbar.pctl` (`#plist` target), `#tmux-filter` (tmux, `#tmux-list` target); project page: a bare `.filterbar` above the live block (client-only) | |
| filter box | The one list filter: input plus in-field clear x (x only with text); one query per page for all tables; name-type text only, never message text | `.fbox` (`input[data-filter]`, `.fbox-x`); T/_filterbox.html `filterbox()`, J/filter.js (`pm.filter`, `html[data-pmq]`, `pm:filter`) | filter, search field, clear x |
| agent list | Table of live agents | `.nlist` > `.nwrap` > `.nrow` | agents table, active list |
| agent row | One live agent line | `.nrow.row-<state>` (`.unread`); cells `.n-dot .n-main .n-proj .n-msg .n-tmux .n-age .n-res` | session row |
| column header | Sortable header cell with sort icon | `.nhead`, `.hcell` | sort header |
| idle section | Collapsible "Idle (n)" list | `#sec-idle` `.sec-idle`, `.nlist-idle` | idle fold |
| git section | "Git" per-project status rows | `#sec-git` `.inbox-git`, `.ig-row` (`.ig-name`, `.ig-badges`, `.ig-last`) | git rows |
| ended sessions | Folded list of finished agents | `#ended-sessions` `.ended-fold`, `.ended-head`; T/_macros.html | ended list, history |
| ended row | One finished session, with `resume` button | rows in `.ended-fold` | |
| ntfy panel | Notifications list under the page | `#ntfy-all` (page `/ntfy`) `.ntfy-panel`; T/_ntfy.html, J/ntfy.js, C/ntfy.css | notifications, ntfy box |
| ntfy row | One notification | `.ntfy-row` (`.ntfy-title .ntfy-agent .ntfy-ago`) | notification row |
| ntfy tools | Expand toggle (and, in the Inbox/system panel head, the filter box) beside Mark read | `.ntfy-tools`, `.ntfy-ctl`, `.ntfy-toggle` | |
| ntfy tabs | Period tab bar on /ntfy (boxed group, kept by `pm.tabs` as `pm.ntfy.tab`) | `.tabs.ntfy-tabs.tabs-seg` > `.tab.ntfy-tab`; J/ended.js on `pm:tab` | period filter |
| ntfy footer | Show select + "Showing X of N" line under the list | `.ntfy-foot` | |

Other list pages share this skeleton: Agents `/agents` (T/agents.html, `.pg-agents`),
Projects `/p` (T/projects.html, `.pg-projects`; `.psearch` is gone: the Agents library and Skills forms use the filter box), tmux `/tmux` (T/tmux.html, `.pg-tmux`),
Backlog, ntfy `/ntfy` (T/ntfy.html), Audit, Servers.

## 4. Agents and chat
### Agents page and split page
| Term | What | Where | Aliases |
|---|---|---|---|
| agents page | `/agents` list of installed agent setups | T/agents.html | |
| split page | `/agents/<name>`: list left, detail right | T/agents_detail.html `.ag-split` (`.ag-left`, `.ag-right`, `.ag-hd`, `.ag-close`) | agents detail, two-column agents |
| detail pane | Right half of the split page | `.ag-right` | right side |

### Aside (chat drawer) `#v5chat`  T/_drawer.html, J/drawer.js, J/drawer-init.js, C/shell.css
| Term | What | Where | Aliases |
|---|---|---|---|
| aside head | Top strip of the aside | `.vc-head` | drawer header |
| agent-id | The id like `claude-7cd08a`, always the heading | `#vc-title` `.vc-title` | agent name, title |
| kind icon | Agent kind glyph | `#vc-ico`, `#vc-kind` | |
| state dot | Status dot | `#vc-dot` `.dot` | status dot |
| model pill | Model name chip | `#vc-model` `.vc-pill.vc-model` | model badge |
| aside tools | Right-hand icon group | `#vc-tools` (`#vc-full` open full mode, `#vc-close`) | |
| aside tabs | Chat / Sub-agents / Pane tabs, mirrored from the framed page's radios | `#vc-tabs` `.vc-tabs` > `button.tab.vc-tab` (J/drawer.js, marked by `pm.tabs`) | tabs |
| zoom control | A- / value / A+ text size | `#vc-zoom` `.zoomctl` (`.vc-zb`, `.vc-zv`) | text size, zoom |
| jump buttons | Scroll-to-top / bottom arrows | `.jump-rail` > `.vc-jump` (`#vc-top`, `#vc-bottom`; `data-page-jump`) | top/bottom buttons |
| resize handle | Drag edge to widen | `#vc-resize` `.vc-resize` | splitter |
| chat frame | iframe holding the chat | `#vc-frame` `.vc-frame` | |
| md viewer parts | head `.mdpane-h`, title `.mdpane-t`, body `.mdpane-b` | `#v5md` | |

### Full mode (`/a/<agent>`, T/agent.html, J/chat.js, C/chat.css, C/agent.css)
| Term | What | Where | Aliases |
|---|---|---|---|
| agent header | Title row: icon, agent-id, pill, state, actions | `.agent-head` (`.agent-title`, `.at-ico .at-base .at-pill`, `#agent-pill`, `#agent-state`, `.ah-tools`, `.agent-actions`) | heading, top of the chat |
| meta line | host/dir/main line under the title | `.agent-meta`, `.am-host`, `.am-main`, `.am-row2` | |
| fork button | Plain Fork button (one form, new tmux window) | | fork |
| tabs | Chat / Sub-agents / Pane / Terminal; sticky; radio tabs (switch in CSS alone) | `.tabs` > `label.tab.tab-l-chat\|sub\|pane\|term` (`role=tab`), `.tab-radio`, `.tabpanel-*` | tab bar (sticky chat nav) |
| sub-agents tab | Work done by the session's sub-agents | `#agent-subs`, `.tabpanel-sub`, T/_subagents.html, `.sa-bar` Refresh | subagents |
| chat log | Scrolling conversation | `#chatlog`, `#chat`, `.chat-bubble.role-*`, `.chat-text`; T/_chat.html, _chatmsg.html | transcript, messages |
| turn box / working | "working..." indicator, steps | `.turn-box`, `.chat-working`, `.steps` | spinner |
| ask card | Question with options from the agent | `.chat-ask`, `.chat-opts`, `.chat-opt` | prompt card |
| suggest bar | Suggested next action | `.chat-suggest`, `.chat-quick` | quick replies |
| pane tab | Raw tmux pane text | `.tabpanel-pane`, T/_pane.html, J/pane.js | pane view |
| terminal tab | Live terminal stream (P7) | `.tabpanel-term`, `.term-frame`, J/term.js | terminal |
| composer | The one message box: agent page, Builder, ended page, quick chat, Launch prompt, Continue popup. Text area, icon bar, green square Send (Launch and Continue: their own buttons) | T/_composer_macros.html `composer(mode)`; root `section.composer[data-composer=live\|builder\|ended\|qchat\|launch\|continue]` (`.composer--dock` fixed bar, `.composer--flow` in the page, `.composer--ended`; no ids); hooks `[data-cmp-form]` `[data-cmp-input]` `[data-cmp-send]` `[data-cmp-bar]` `[data-cmp-chips]` `[data-cmp-hint]` `[data-cmp-flash]`; `.cbox`, `.cicon`, `.csend`; C/composer.css, J/composer.js (`pm.composer.init/insert/running`; + composer-dock.js, composer-attach.js, chat-send.js) | message box, input box, msgbar |
| send key | Enter sends (Shift+Enter newline; touch: Enter is a newline) or Ctrl/Cmd+Enter sends; Settings > Composer (agent pages, quick chat). Launch and Continue: always Ctrl/Cmd+Enter | root `data-send-key="enter\|mod-enter"`; Send title "Send message (Enter)" / "(Ctrl+Enter)" | send with |
| Stop | The Send button while the agent runs and the box is empty: interrupts it (Ctrl-C, then Esc; in-page confirm). Typing turns it back into Send. The only Interrupt besides the Pane tab's tools | `.csend.is-stop` + the hidden `form[data-cmp-stop]` (POST /a/<id>/interrupt) | interrupt button |
| ended composer | The composer of an ended session: empty box = Resume / Fork; with text = Resume and send (the session resumes with it as its opening message, the page switches to live, same id) / Fork and send | `[data-composer=ended]`, `[data-cmp-alt]` (Fork); POST /a/<id>/resume or /fork with `text` | resume box |
| key pad | Special-key buttons (Esc, Enter, arrows) | `.keypad`, `.keybtn`, `.keygroup` | keys |
| slash card | `/` command picker above the composer (Launch: below it; quick chat: its skills, also mid-text) | `.slash-list` picker (root `data-slash`), `.slash-card-wrap[data-cmp-slash-card]` command output; T/_slash_card.html, J/slash.js | slash menu |
| continue popup | "Continue in a new chat" under a reply: default message or a new prompt (the composer, Ctrl/Cmd+Enter = Start; four canned chips), new window or split pane | `pmConfirm` with `.cpop`; composer `[data-composer=continue]` cloned from `<template id="cmp-continue">`; J/continuepop.js, C/chat-continue.css | continue dialog |
| snippets menu | Canned text menu on the Send caret (+ Save as issue) | `.csend-more[data-snip]` -> `.snipmenu.pop` (pm.popover); J/snippets.js, issue-save.js | snippets |
| model pill | One pill "<model> <effort>" that opens a menu (current model, Effort, More models) above the composer | `.modelbar.mpick`, `.mp-pill`, `.mp-menu.pop` (pm.popover), `.mp-row`; J/modelpick.js | model select, model bar, effort select |
| token status | Context/usage line above the composer | `#chat-stats` `details.tokbar` / `.cstat` (loads `/a/<id>/stats`) | token row, stats row, usage line |
| reload notice | "pm changed, restart" row (pm project only) | `.pmreload[data-cmp-reload]` (`.pmr-t`, `.pmr-act`, `.pmr-refresh`, `.pmr-restart`); J/pmreload.js, reload.js | restart notice |
| ended page | Finished-session page | T/agent_ended.html (`.ended-meta`; Resume / Fork in the sticky `.agent-head`, the ended composer at the bottom) | |

Composer stack, top to bottom: command output card, notice line (`[data-cmp-flash]`), send hint, reload notice, token status +
Builder / New Chat / Fork, composer box (text, icon bar: Keys, Attach, Paste, btw; Send/Stop + snippets caret), model pill + Exit.
The ended composer has only the notice line and the box (Resume / Fork). Page-level notices (`#flash`) sit just above a docked composer.

## 5. Project page (`/p/<name>`)  T/project.html, J/projview.js, C/rows.css, C/projects.css
| Term | What | Where | Aliases |
|---|---|---|---|
| project header | Name, badges (stack, git) | top of `.pg-project` | |
| project body | Split: main left, docs panel right | `.pj-split` (`.pj-main`, `.pj-right` `#pj-right`, `#pj-body`) | |
| docs panel | Right panel with tabs | `.pj-right`; tabs `.pj-tabbar` > `.tabs.pj-tabs.tabs-line` > `.tab.pj-tab` (`data-pj` browse, dirty, readme, changelog); `.pj-close`, `.pj-showdocs` reopen | docs, doc pane, right panel |
| docs tabs | Browse / Git / Plans / TODO / Backlog / README / CHANGELOG | `.tab.pj-tab`; Git has sub-tabs `.tab.pj-sub` Git status (`.pj-n` count) / History / Config (`pm.pjgit.sub`); Skills has Local / Global | |
| doc body | Rendered markdown | T/_project_doc.html `.pj-doc.md`, `.pj-meta`, `.pj-note` | |
| files browser | Read-only file tree | T/_project_files.html, `.pj-files` | Browse |
| git status | Dirty-file view | tab `dirty`, T/_dirty.html | dirty |
| git remotes panel | Remotes / push remote (`push_remote`) state in the git card [flux] | project.html git block (`no <push_remote> remote` warning) | remotes |
| tmux section | Sessions of this project | `#proj-live` (T/_project_live.html) | live, sessions |
| tmux session row | One session, expandable | `.lrow.srow` (`.srow-tg` chevron, `.srow-act`) | session row |
| window row | A window/pane inside an expanded session (table `.tx-w`; `/tmux/<s>` redirects to `/tmux?open=<s>`, no separate session page; the window name links to the terminal, the agent name to the chat; the expanded row only carries Kill session) | rows under `.srow`; T/_tmux_session_rows.html | tmux window |
| backlog card | Inbox list of the project's backlog | `#backlog-card` `.bl-card`, `.bl-add`, `.bl-list` | backlog |
| todo card | TODO.md items | `#todo-card` `.td-card` | todo |
| plans card | Plans | `#plans` `.pg-plans` | |
| skills card | Project skills | `#skills` `.pj-skills` | |
| commits list | Recent commits | `.commits` (`.chash`, `.csubj`, `.cauth`) | git log |

## 6. Settings (`/settings`)  T/settings.html, T/_settings_panel.html, C/settings.css
Each section = `h2` + `.card.set-sec` (`.set-grid` for form grids; `.set-row`, `.set-main`, `.set-more`, `.set-quiet`, `.set-notice`, `.set-chip`, `.set-seg`).
Sections in order: Theme, Fonts, Unlock, Passkey login, Passkeys, Sessions, Default launch options, Notify rules, Composer. The lower group is the live-reloaded `#settings-panel`. Aliases: settings cards, settings blocks.

## 7. Lists, rows, tables
| Term | What | Where | Aliases |
|---|---|---|---|
| row | Single-line list entry (UI-RULES: one line) | `.nrow` (home), `.vs-row` (sidebar), `.lrow` (project), `.ntfy-row`, `.ig-row` | line, item |
| row kinds | agent row, ended row, tmux session row, window row, ntfy row, git row, history row (`.ts-hist`) | see sections above | |
| column header | Sort control, icon shows state | `.hcell` (`.on` active), T/_sortbar.html | sort header |
| list container | Generic list | `.list[role=list]`, `.nlist` | |
| card | Boxed section | `.card` | panel, box |
| show selector | "Show N" limit control | T/_limitbar.html `showsel()` / `limitbar()`: `.showbar`, `.limitcount` (ntfy: `sel="ntfy-n"`, browser-side) | limit, page size |
| pager | newer / page N / older under Audit and Client errors | T/_limitbar.html `pager()`: `.aud-pager`, `.aud-chip` | pagination |
| filter select | Filter dropdown (also an agent's Skills Enabled / Disabled, `name="show"`) | T/_sortbar.html `filtersel(opts, cur, name)` | |
| fold / disclosure | Collapsible section | `<details>`, `.fold-tog`, `.idle-fold`, `.ft-open/.ft-close` | collapse |
| empty text | Muted placeholder | T/_macros.html `empty(text, cls)`; `.vs-empty`, `.chat-empty`, `.ts-none` | empty state |

## 8. Controls
| Term | What | Where | Aliases |
|---|---|---|---|
| button | Default `button` / `.button`; `.small`, `.primary`, `.danger` variants | C/chrome.css | |
| link button | Text/icon button without border | `.linkbtn` | icon button |
| row button | `resume`, `exit`, kill etc. at row end (outline only, no fill) | `.srow-act`, row forms `.inline` | action button |
| pill | Small rounded label: model pill `.vc-pill`/`.at-pill`, unread pill `.vs-ub`/`.ntfy-unread` | | chip, badge |
| badge | State label | `.badge` (`.ok .warn .bad`, `.st-<state>`) | status badge |
| state dot | Colored dot + its word for screen readers | `.dot` `.dot-<state>`, `.n-dot`; T/_macros.html `dot(kind, label, cls)` (Servers: `cls="sdot"`) | status dot |
| notice line | One-line ok / warn / err message in a page | T/_macros.html `notice(kind, text, cls)` (`.notice-<kind>` + the place's look) | message |
| post button | One-button POST form (CSRF, in-page confirm) | T/_macros.html `post_btn(csrf, url, label, fields, confirm, cls, target, form_cls)` | action form |
| dialog | In-page modal (never native popups); a bottom-aligned sheet under 700px | `dialog.pmdlg` (`.pmdlg-head` title + optional close x `.pmdlg-x`, `.pmdlg-body`, `.pmdlg-foot` [Cancel][Primary]; sizes `.pmdlg-sm/-wide/-full`, `.pmdlg-frame`); J/dialog.js `pmDialog` (static or built), `pmConfirm`/`pmPrompt`, `data-dlg-open`/`data-dlg-close`, typed confirm `data-dlg-typed`, `hx-confirm`, `data-confirm`; T/_popup.html `dialog()`, `dlg_foot()`; C/popup.css | modal, confirm, popup |
| flash | Result message after an action | `#flash` `.flash`, inline `.flash-inline` | toast, notice |
| popover | Floating card anchored to a control (not modal): bell list, stats, VM More, New session | `.pop`; J/popup.js `pm.popover` (one layer stack: Esc closes the top one, outside click closes, flip/clamp); `<details data-pop>` | dropdown, tooltip |
| menu | A popover whose items are `role=menuitem` and move with Up/Down/Home/End: account menu `.acct-pop`, More menu `#moresheet`, quick chat row menu `.qc-rmenu` | `.pop[role=menu]`, rows `.pop-item` (account menu rows also `.acct-it`); J/popup.js | dropdown |
| sheet | A popover shown as a bottom sheet under 700px (`data-sheet`): More, account menu, bell | `.pop.is-sheet`; C/popup.css | bottom sheet, drawer |
| tooltip | `title` / `.tip` hover text | | |
| icon | Inline SVG | `svg.hi`, `.wico` | |

## 9. States
| Term | Meaning | Where / token | Not to be confused with |
|---|---|---|---|
| hover | Pointer over a row: grey | `--hover` | highlight |
| highlight | The row of the agent open in the aside, matched by agent id; clears on close; also called selected / open row | `--accent-soft`; `.on` on `.vs-row`; JS in J/drawer.js | hover |
| selected | Current nav/tab item | `.on` (top nav), `aria-selected=true` / `aria-current=page` / `:checked` radio (tabs) | |
| unread | Row with new messages | `.nrow.unread`, `.vs-ub` | |
| collapsed / expanded | Sidebar rail, session row, fold | `aria-expanded`, `.vs-fold`, `.srow-tg` | |
| sticky | Tabs/nav stay pinned while the chat scrolls | `.tabs` in full mode, `.vc-head` | fixed |
| auto-hide | Bits that hide until needed (docs panel, reload notice, jump rail, empty token row) | `[hidden]`, `.tokbar:empty` | |
| working | Agent running | `.working`, `.chat-working` | |
| dev instance | Test instance, `DEV` tag | `.devtag` | live page |

## 10. How to refer to things in a request
Good:
- "In the aside, the model pill overlaps the agent-id at narrow width."
- "On the home page, the ended row `resume` button has a grey fill; outline only."
- "Full mode: make the tabs sticky and keep the jump buttons right of the chat log."
- "Composer: put the token status above the reload notice." (current order is reload notice, then token status)
- "Sidebar foot: collapsed by default on phone."
- "Project page docs panel: tab bar must not scroll, wrap the tabs."
- "tmux session row on the project page: click the name toggles the windows."
- "Settings, Notify rules section: align the labels in the grid."
- "Highlight (not hover) should clear when the aside closes."

Weak (avoid): "the bar at the top" (top bar or tab bar?), "the panel on the right" (aside, md viewer
or docs panel?), "the sidebar" when the aside is meant, "the menu" without saying account menu.

Always give: page (URL), the **term**, and the state (light/dark, width, hover/selected).

## 11. Layout sketches
### Home page (`/`)
```
+-----------------------------------------------------------------------+
| pm  [search box........]   launch stop backlog audit qchat [acct v]  |  top bar
+-----------+-----------------------------------------------------------+
| Menu    < | Inbox   n agents . stats             (page hdr) |
|-----------| [filter box: Filter...  x]   [show 25]                   |
| Active    | col hdrs: dot  agent   project  last msg  tmux  age  res  |
|  Ended    | (o) agent row ........................................  |
| Projects  | (o) agent row ........................................  |
| tmux      | v Idle (3)                                                |
| Backlog   |    agent row ...                                          |
| Audit     | Git  n projects                                           |
|-----------|    git row ...                                            |
| foot:     | v Ended sessions                                          |
| Manage    |    ended row ............................ [resume]       |
| Service   | Notifications (ntfy panel)                                |
| Account   |    ntfy row ...                                           |
+-----------+-----------------------------------------------------------+
```
### Agent full page (`/a/<agent>`, full mode)
```
+-----------------------------------------------------------------------+
| top bar                                                               |
+---------+-------------------------------------------------------------+
| sidebar | [ico] agent-id [model pill] [state]       [fork] [actions]  |  agent header
| (agents | host . dir . branch                        (meta line)      |
|  list,  | Chat | Sub-agents | Pane | Terminal       (tabs, sticky)    |
|  highl- |-------------------------------------------------------[^]--|
|  ight)  |  chat log: bubbles, ask card, working...              [v]   |  jump buttons
|         |                                                             |
|         |-------------------------------------------------------------|
|         | pm changed - [Refresh] [Restart]      (reload notice)       |
|         | ctx 42% . 12k tokens . limit          (token status)        |
|         | [model pill]   [keys]                                      |
|         | [ composer text area ............ ] [attach] [send]        |
+---------+-------------------------------------------------------------+
```
### Aside (chat drawer) over a list page
```
+-----------+--------------------------+--------------------------------+
| sidebar   | main (list page)         |<> resize                       |
|           |  row                     | [ico] agent-id [pill] [dot]    |  aside head
|           |  row  <- highlight       |       [A- 100% A+] [full] [x]  |  zoom, tools
|           |  row                     | Chat | Sub-agents | Pane       |  aside tabs
|           |                          |+------------------------------+|
|           |                          || #vc-frame: embed chat log     ||
|           |                          || composer ...                 ||
|           |                          |+------------------------------+|
+-----------+--------------------------+--------------------------------+
 md viewer (#v5md) opens in the same right column: head [title][full][x], body.
```

## 12. Keeping this file true
When you rename or add a UI part, update the row here in the same change. Names in `[flux]`
may move. Rules for look and behaviour stay in `UI-RULES.md` and `UI-CONDUCT.md`.
