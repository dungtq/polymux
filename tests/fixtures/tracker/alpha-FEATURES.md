# alpha - features

What alpha can do now. `CHANGELOG.md` says what changed and when; this file says what exists.
Entry format and upkeep: `docs/FEATURES-GUIDE.md`, completeness pass: `docs/FEATURES-AUDIT.md`.

Last audited: 2026-10-11
Summary: 26 features in 12 areas.

Guarantees that hold on every page (stated once, not repeated per feature): every change is a POST with CSRF, Origin check and an audit line; secrets are masked on every page.

## Navigation

- **Header** — one-line header with the list switcher, search box and the Settings link; collapses to a menu under 700px.
- **Keyboard** — `j` / `k` move the selection, `Enter` opens, `Esc` closes the drawer.
- **Search box** — searches titles, tags and notes across all lists (`/search`), grouped by list.

## Home

- **Due today** — items due today or overdue, newest first, with a one-line reason.
- **Snoozed** — snoozed items collapse into one row with a count; a click expands them.

## Lists

- **Lists page** — every list with open / done counts, the last change and a share badge (`/lists`).
- **List page** — items in file order with tags and due dates; column-header sort (`/l/<name>`).
- **Filters** — shared, has due items, has notes; the filter is kept in the URL.
- **Pinned lists** — pin favourites to the top of the Lists page.

## Editor

- **Composer** — Markdown editor with preview (Ctrl+Shift+P), image paste and drop.
- **Save as issue** — first line = title, the rest = body; images go with it.

## Projects / Sync

- **Sync status** — per list: last sync time, ahead / behind counts, an inline Sync button.
- **`alphactl` CLI (scripting interface to the tracker)** — read and change issues, tasks, changelog and features from a shell; JSON output with `--json`.

## Servers / Hosts

- **Host list** — known hosts with a reachability dot and the last check time (`/hosts`).

## Settings

- **Theme** — auto / light / dark, stored in a cookie.
- **Passkeys** — list, add and revoke passkeys; list and end sessions.
- **Defaults (new list options)** — default sort, default tags and the default share mode.

## Notices

- **Notice feed** — the latest notices with an unread dot and "mark all read" (`/notices`).

## Security

- **Login** — token or passkey; optional unlock step for risky actions (`require_unlock`).
- **Audit log** — one JSON line per change; the History page filters by action and result.

## Phones / Preview

- **Phone layout** — single-column pages, a full-width drawer and quick action buttons.
- **Preview aside** — a sandboxed live preview of a list's static page that reloads on every file change.

## Background

- **Backups** — a daily copy of the database, 7 kept (`alphactl backup`).
- **Deploy** — `deploy/alpha.service`, `deploy/alpha.example.test.conf` (proxy) and an install script.

## Tools

- **Import** — CSV import with a column map and a dry run.
- **Export** — CSV and Markdown export from the list menu.
