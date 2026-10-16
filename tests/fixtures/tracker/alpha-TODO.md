# alpha TODO

Decisions are final (PLAN.md section 9). Start with `README.md` and `docs/LAYOUT.md` (the 2026-10-06 review is in `docs/REVIEW.md`).

## P0 scaffold
- [x] project layout, `uv` env, lint config
- [x] config file + loader with defaults and validation
- [x] login page, session cookie, CSRF on every form
- [x] audit log (one JSON line per change)
- [x] health route and service unit
- [x] test fixtures (temp dirs, fake clock)

## P1 read-only lists
- [x] lists page with counts
- [x] list page: items, tags, due dates
- [x] item drawer with notes
- [x] search box over titles
- [ ] saved searches

## P2 safe editing
- [x] add / edit / close an item (POST + CSRF + audit)
- [ ] undo the last close from the notice bar

## P3 sharing
- [x] share a list read-only by link
- [ ] share with edit rights (needs per-list roles)

## P4 import & export
- [x] CSV export
- [ ] CSV import with a column map (2026-10-09)

## P5 mobile
- [x] phone layout for lists and the drawer

## P6 deploy
- [x] service unit with restart policy
- [x] proxy vhost (TLS, SSE-friendly)
- [x] backups of the database (daily, 7 kept)

User steps still open (not code): enrol a passkey via `/settings/passkeys`, and set the notice topic in `alpha.toml`.

## P7 read-only terminal (decisions 12-16)
- [x] spike findings (`docs/P7-spike.md`)
- [x] `term.py` / `routes_term.py` / ticket, read-only attach, limits, audit
- [x] terminal viewer page (vendored), WebSocket upgrade, per-response CSP

## Later
- [ ] P7 write mode (not planned), P8 multi-user / schedules / quotas

## Import improvements (from [the import review](docs/IMPORT-REVIEW.md))
- [x] keep the file's own order
- [x] report skipped rows with their line number
- [ ] detect the separator (`,` / `;` / tab)
- [ ] dry run: show what would change before writing

## Shortcuts (`docs/SHORTCUTS.md`)
- [x] `/` focuses the search box
- [ ] `?` opens the shortcut sheet (out of scope for now)

## v2 UI in the app (`docs/UI-ROADMAP.md`)
- [x] header with the list switcher
- [ ] **dark** theme for the drawer
- [ ] per-list colour (needs a design pass per `docs/UI-COLOURS.md`)

## Notes history (2026-10-09)
- [x] notes keep their edit history
- [x] a "show older" link above the editor
