# TODO

Phases from PLAN.md section 4.

- [x] P0 Foundations (docs, lint, layout check)
- [x] P1 Core + Library: core, security, parsers + index, web shell, Library, Reader, Settings,
      service unit + proxy config files, tests
- [x] P1 live: service unit + proxy config installed, browser checks on demo.example.test (2026-10-12)
- [x] P2 Reader + Search: item pages with notes, media, list pages, full-text search page, file-watch reindex
- [x] P3 Docs + Store: docs tree/view/edit/history, row editor, git commit per save, `state/` dir
- [x] P4 Import: `pipeline.importer` + `categorize`, Import page with review
- [x] P5 Tools + Jobs: port fetch, convert, report; job runner + SSE log; `demo tool` CLI
  - [x] P5a tool ports (domain code, byte-parity tests): fetch, convert, report, export, merge
        registered in `pipeline.registry`; `demo tool <name>` / `--list` / `python -m demo.cli`
  - [x] P5b Tools/Jobs UI over the registry; the runner commits each tool's `writes` globs afterwards
  - [ ] P4a Import page over `importer.diff` and editable `state/rules.toml` (`categorize.write_defaults`)
- [x] P6 API + plugins: `demo api` (stdio), project configs, plugin sync
  - [x] P6 live (2026-10-12; one client still needs a one-time approval): runs `demo api-config --write` and `demo plugins sync --write` on a
        sample workspace, approves the server in the client (`client list` shows "Pending approval")
  - [ ] P8 later: HTTP API at `/api` behind the login token (TODO line in `demo/api/server.py`)
  - [x] P4a Import page over `importer.diff` and editable `state/rules.toml` (`categorize.write_defaults`)
- [x] P6 API + plugins: `demo api` (stdio), project configs, plugin sync
- [x] P7 Workers: start/detect/message/stop/resume for worker kinds; chat + terminal
- [x] P7 built on a branch: `demo.workers`, `demo.chat`, `/workers`, `/w/{id}`, `/term/{id}`, tests with a
      fake worker on a private socket
- [ ] P7 live: a worker started for real via the Start form (2026-10-12; stops at the first-run prompt, answered once). Still to check: other kinds (argv, prompts, `/exit`, resume,
      chat store detection) and sign off the screenshots
- [ ] P7 later: more kinds, saved presets, per-worker notes, a notice when a worker ends, terminal scrollback view
- [ ] P8 Polish: passkey, keyboard nav, saved filters, notice on job end, screen checks, HTTP API, sync server
