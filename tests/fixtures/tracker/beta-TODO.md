# beta TODO

Plan: `PLAN.md`. Mark `[x]` when done.

## Phase 0: Skeleton
- [x] `uv init`, deps: fastapi, uvicorn, jinja2, markdown-it-py, pygments, watchfiles, python-multipart; dev: pytest, httpx
- [x] `beta.toml` + `config.py`: `[[roots]]`, `[skip]` (dirs/globs/per-root/gitignore), `[include]`, `[mask]`, `[server]`
- [x] Config hot reload (SIGHUP / file change); validate roots exist and are absolute
- [x] `safe.py`: resolve, root check, deny rules, symlink escape
- [x] Tests for `safe.py` (traversal, symlink, `.env`, `.git`)

- [x] Confirm the port is free (`ss -ltn`)

## Phase 0b: Auth (token / code, no users)
- [x] Login with a shared token; cookie session, 12 h idle timeout
- [x] Rate limit on failed logins

## Phase 1: File browser
- [x] Tree view with lazy folders
- [x] Markdown view (tables, task lists, code highlight)
- [ ] Image preview for `.png` / `.jpg` / `.svg` (svg sanitised)

## Phase 2: Search (v1: filenames + markdown)
- [x] Filename search with fuzzy match
- [x] Markdown full-text search, masked text only
- [ ] Search scope toggle: files / markdown / code

## Decisions (resolved)
- [x] Auth: simple shared token / code, no users (accounts later, optional)
- [x] Search v1: filenames + markdown only
- [x] Secrets always masked

## Open questions
- [ ] Which extra roots to enable first (`/srv/notes`, `/srv/shared`)?
- [ ] Default skip for large trees (`node_modules`, `vendor`, `third_party`)?
- [ ] Token only, or token plus a one-time code later?

## Phase 3: Docker without rebuilds
- [x] Image = deps only; `src/` mounted `:ro` at /app/src (PYTHONPATH), no code baked in
- [x] `compose.yaml` + `compose.dev.yaml` (uvicorn --reload) + `compose.prod.yaml` (restart, no reload); BETA_SRC / BETA_TOML vars
- [ ] Staging host: copy files, adjust mounts/roots, run per docs/docker.md (not touched)

## Background work (proposed)

Plan: `docs/PLAN-background.md` (awaiting decisions in its section 9; related: `docs/queue/`).

- [ ] Observed, not touched: with the refresher off and no requests the process still uses ~45% of a core (index sync suspected: not trivial)

## Control moved to alpha (2026-10-06)

- [ ] beta stays read-only. Write **control** (option B, edit
      items, start jobs, git actions) is planned in the alpha project's `PLAN.md`.
      beta keeps only read-only views (activity feed, masked snapshots).
- [ ] `git init` beta (`.gitignore`: `.venv/`, caches, no tokens), add remote
      `origin` = `git@git.example.test:demo/beta.git`, first commit + push (decision 2026-10-06).

## Notices (docs/notices.md)

### Phase N0: decisions
- [x] Base URL `http://notices`, retention 168h, topics `jobs` (configurable), failure rule, `beta_seen` cookie

### Phase N1: adapter and config
- [x] `[notices]` in `config.py` (enabled, base_url, topics, timeout, ttl, window, max_messages), `/settings` rows
- [x] `src/beta/sources/notices.py`: `Notice`, `parse_lines`, `fetch` (urllib, timeout, byte/line caps)

### Phase N2: home panel
- [ ] Home panel with the latest 10 notices, unread dot, "mark all read"
- [ ] `/notices` page with paging and a topic filter (2026-10-07)

## Follow-ups (2026-10-07)
- [ ] Remote roots over SSH (read-only, cached; no agent on the remote host)
- [ ] Per-root mask rules (decided: not trivial)
