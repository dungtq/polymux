# Changelog

User-visible and behavioural changes, newest first. Dates are local (server time).

## 2026-10-13 (Tracker: lists in the database, items with images, `alphactl` CLI)
- New tracker (docs/TRACKER.md, phases T1 + T2). A list that is imported (`alphactl import`) keeps its items (now **issues**, numbered
  `#1`, `#2`, …) in the database; the Markdown file is written from it after every change.
- **Issues tab** replaces the List tab on a tracker list's page (`#issues`; `#list` still works): add box (Enter adds a one-liner).
- **Editor "Save as issue"**: first line = title, the rest = body, attached images go with it.
- Routes: `GET /l/<name>/issues[/<num>]`, `POST /l/<name>/issues`, `POST /l/<name>/issues/<num>/done|reopen|edit|note|delete`.

## 2026-10-13 (Preview: icons and fonts of an external mock)
- Preview: an allowed external page (`preview_frame_hosts`, e.g. `https://mock.example.test/alpha/v1/pages/...`) lost all its icons:
  its SVG sprites are now allowed by the frame CSP.
- Static JS only (`preview.js`); no restart.

## 2026-10-13 (Continue… popup: choose the prompt and where it opens)
- "Continue…" under the newest note now opens a small popup instead of starting at once. **Prompt:** "Use the default text" (as before)
  or a custom one.
- `POST /i/<id>/continue` takes optional `mode` (`predefined` | `custom`); without it it behaves as before.

## 2026-10-12 (one filter box: Home, Lists, Notices)
- One shared filter box replaces the three different ones. It is the first control under the title row and has an in-field clear (x).
- With a filter active, a list with no visible rows hides (its column header goes with it) and a page with no matches shows "No matches".

## 2026-10-10 (import)

- Import form: paste a file, drop files or use "Attach files", same as the editor.
- Top navbar Import button now opens the import form of the list you are on (`/import?list=<name>`).

## 2026-10-08 (notes)
- Notes keep their edit history; "show older" above the editor.

## 2026-10-08
- Settings: theme select (auto / light / dark) — the choice is a cookie, no reload needed.

## 2026-10-07 (evening)
- Phones: the drawer opens full width; the close button stays in view.

## 2026-10-08 (notes)
- Notes: Markdown preview toggle (Ctrl+Shift+P) ✓.

## 2026-10-07 (evening)
- History: one-line time column (`MM-DD HH:MM:SS`).

## 2026-10-07

### Fixed
- Lists with a `/` in the name opened the wrong page.
- The count badge ignored done items in collapsed sections.

### Added
- Export to CSV from the list menu.

## 2026-10-06

### Changed
- Item rows are single-line; long titles clip with an ellipsis.

### Fixed
- Login: a stale CSRF cookie after a restart no longer needs a manual reload.

### Added
- First public build: lists, items, tags, search, history, audit log. Runs as a service unit behind a proxy.
