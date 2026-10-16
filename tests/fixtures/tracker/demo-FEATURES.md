# Features

User-visible features by area. One line each; details in `PLAN.md`.

## Reader and search (P2)

- Item reader with notes, media and timestamp links.
- List pages and per-list item views (`/l`).
- Full-text search across all items, grouped by kind, with highlighted snippets (`/search`, header box on desktop).
- Library, list and search rows open in the right-hand drawer on a plain click.
- The index updates itself when files under `docs/` change.

## Import (P4)

- Upload or pick an export file, review the new links by target doc and category, add the checked ones to the
  link lists or the inbox in one commit; edit and preview the rules (`/import`, `/import/rules`).

## Tools and jobs (P5)

- Tools tab: every ported tool with a form (typed fields, network tick), Run starts a background job.
- Jobs list and job page with live log, Cancel, Re-run, commit link and push status; written files are committed.

## Workers (P7)

- Workers tab: start workers in their own session from a form (kind, mode, first prompt, working directory),
  with a status dot per worker (running, waiting, ended, failed).
- Worker page with the live chat, a composer, Exit, Kill, Resume and a Terminal link.
- Browser terminal attached to the worker's window, with keyboard input and quick keys for phones.
- Unsafe modes only when `allow_unsafe = true` and the "I understand" tick is set; every action is audited.
## API and plugins (P6)

- `demo api`: clients search, read, save, tag and edit the workspace and start tool jobs.
- `demo api-config` registers it per project; `demo plugins sync` installs the plugins for each client.
