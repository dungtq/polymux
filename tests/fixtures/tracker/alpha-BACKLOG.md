# alpha backlog

Edit this file directly or from `/backlog` (web/phone). Items are `- [ ] text`.

## Inbox

- [x] Desktop 1512px: the search box covers the last nav item ("Se") (found by the screen check 2026-10-12)
- [x] Doc tabs wrap to 3 rows on a phone with a lone last tab and the x stranded on row 1; tighten padding or place the x with the last row (2026-10-12)
- [x] remove the Open full button on phones (2026-10-13)
- [x] adding a new item does not update the count in the tab bar (2026-10-13)
- [x] remove the clear button, it does the same as **New** in the editor (2026-10-13)
- [x] Settings: the `theme` select forgets its value after a reload (2026-10-11) — fixed: cookie path was `/settings`
- [x] Lists page shows "0 items" for a list whose items are all done; count open and done apart, e.g. `3 open · 2 done`, and keep the order stable when two lists have the same count (2026-10-11)
- [x] long titles push the row actions off screen on narrow widths; clip the title with an ellipsis and keep the actions in view (see [layout notes](docs/LAYOUT.md#rows)) (2026-10-10)
- [ ] Open item count does not show, it is also zero (0) (2026-10-13)
- [ ] Editor placeholder says "Write a note" while the Type picker shows "task"; make the placeholder follow the picker and keep it when the picker is changed before typing anything, on desktop and on phones (2026-10-13)
- [ ] export: a list with an emoji in its name ✓ gives a broken file name (2026-10-12)
- [ ] keyboard: `j` / `k` move the selection, `Enter` opens, `Esc` closes the drawer
- [ ] import from a CSV file — map columns by header name, skip empty rows (2026-10-09)

## Next

From `docs/REVIEW-2026-10-07.md`, "Now":

- [x] Fix the phone layout of the lists page (name never clipped; list name on its own line)
- [x] Item drawer: loading + "nothing yet" states; keep the Tags expander from covering the text
- [x] History: hide/merge `attempt` followed by `ok`, one-line time column (`MM-DD HH:MM:SS`), filter by action/result, paging (50 rows)
- [x] Notices on phone: wrap titles to two lines instead of truncating
- [ ] Drop the repeated `list list` label; show the list name + count only
- [ ] Hide the `shared` badge unless a list is NOT shared; show a single lock

From `docs/REVIEW-2026-10-07.md`, "Next":

- [x] Home: states Due / New (unseen) / Open / Snoozed; collapse Snoozed; show the next due date on the row
- [x] Merge duplicate notices per list+event; per-item read/dismiss with an unread badge on the Home tab and the app icon
- [x] Item page: open scrolled to the latest note; one-line metadata; current-status line; visible Snooze/Close; inline answer buttons (y/n/1-3) when a question is pending
- [ ] Lists: search box, real filters (shared, has due items, has notes), pin favourites; inline sync results; expandable changed-item list
- [ ] Settings: list/revoke passkeys and sessions, default list options, notice rules, theme (auto/light/dark)

## Done
