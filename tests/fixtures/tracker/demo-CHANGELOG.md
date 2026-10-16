# Changelog

## 2026-10-13 - List pages load fast

- `/l/{source}` (and the Library's source/category filter) took ~10 s on a large data set: the query used an
  `EXISTS` over `found_in` that rescanned the whole source for every item. It is now a join plus
  one index lookup; `/l/sample` query time drops from ~10 s to ~10 ms.

## 2026-10-12 - P6 API server and plugins

- `demo api`: stdio API server with `search`, `read_item`, `list_categories`, `save_item`, `set_state`,
  `write_doc` and `job_log`. It runs in the client's process tree, reads the index db and writes through
  `demo.store.state` (lock, sha check, commit of the touched paths). A stale write gets a
  `conflict` error with `current_sha`.
- Every API write is audited with actor `api:<DEMO_CLIENT_ID or unknown>`.
- `demo api-config [--root DIR] [--write]`: merges a `demo` entry into the client config files; other
  entries are kept.

## 2026-10-12 - Import (P4)

- Upload or pick an export file, review the new links by target doc and category, add the checked ones in one
  commit (`/import`).
- Rules editor with a live preview (`/import/rules`).

## 2026-10-12 - Tools ported (domain code + CLI)

- `fetch`, `convert`, `report`, `export`, `merge` are ported with byte-parity tests.
- `demo tool <name>` / `demo tool --list`.

## 2026-10-12 - P5 Jobs runner and Tools/Jobs pages

- Tools tab: every tool with a form (typed fields, network tick); Run starts a background job.
- Jobs list and job page with a live log, Cancel, Re-run, commit link and push status.

## 2026-10-12 - P7 Workers

- Workers tab: start a worker in its own session from a form (kind, mode, first prompt, working directory),
  with a status dot per worker (running, waiting, ended, failed).
- Worker page with the live chat, a composer, Exit, Kill, Resume and a Terminal link.

## 2026-10-12 - P2 Reader + Search

- Item reader with notes, media and timestamp links.
- Full-text search across all items, grouped by kind, with highlighted snippets (`/search`).

## 2026-10-12 - P3 Docs manager + Store

- Docs tree, view, edit and history; a git commit per save.

## 2026-10-12 - P1 Core + Library

- Core, security, parsers and index, web shell, Library, Reader and Settings.
- Service unit and proxy config files; tests.
