"""The ``pmctl`` commands (argparse namespace in, text or JSON out). See ``cli.py`` for the grammar."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pm.core import db, timefmt
from pm.core.config import Settings, load_settings
from pm.core.errors import Invalid, NotFound
from pm.projects.tracker import (
    cli_claims,
    cli_docs,
    cli_memory,
    gitglue,
    issues,
    mdgen,
    mdimport,
    resolve,
    schema,
    store,
    views,
)

MAX_INPUT = store.MAX_BODY + 1


class AuditFailed(Exception):
    """The audit line could not be written; the change was rolled back."""


@dataclass
class Ctx:
    settings: Settings
    args: object
    actor: str = ""
    _project: tuple[str, Path] | None = None

    @property
    def json(self) -> bool:
        return bool(getattr(self.args, "json", False))

    def project(self) -> tuple[str, Path]:
        if self._project is None:
            self._project = resolve.project_for(self.settings, name=getattr(self.args, "project", None))
        return self._project

    def who(self) -> str:
        if not self.actor:
            self.actor = resolve.actor(self.settings)
        return self.actor

    def audit(self, action: str, detail: dict) -> None:
        from pm.core.models import AuditEntry
        from pm.security.audit import AuditLog  # local: pm.security's package init loads the web stack

        target = f"{detail.get('project', '')}/{detail.get('file', '')}"
        if "num" in detail:
            target += f"#{detail['num']}"
        clean = {k: v for k, v in detail.items() if isinstance(v, str | int) and not isinstance(v, bool)}
        try:
            AuditLog(self.settings.audit_file).write(AuditEntry(
                ts=time.time(), client=f"pmctl:{self.who()}"[:80], action=action, target=target[:200],
                result="ok", detail=clean))
        except Exception as e:  # AuditError: fail closed (store rolls the change back)
            raise AuditFailed() from e

    def out(self, data, text: str) -> int:
        return out(self, data, text)

    def read(self, spec: str | None) -> str | None:
        return _read_input(spec)


def out(ctx: Ctx, data, text: str) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=1) if ctx.json else text)
    return 0


def _read_input(spec: str | None) -> str | None:
    if spec is None:
        return None
    if spec == "-":
        data = sys.stdin.read(MAX_INPUT)
    else:
        with open(spec, encoding="utf-8", errors="replace") as f:
            data = f.read(MAX_INPUT)
    if len(data) >= MAX_INPUT:
        raise Invalid(f"input too long (max {store.MAX_BODY // 1024} KB)")
    return data


def _mark(it: store.Item) -> str:
    return "[x]" if it.done else "[ ]"


def _extra(it: store.Item) -> str:
    bits = [f"{it.n_img} img" if it.n_img else "", f"{it.n_notes} notes" if it.n_notes else "",
            "body" if it.body_md.strip() else ""]
    bits = [b for b in bits if b]
    return f"  ({', '.join(bits)})" if bits else ""


def _list(ctx: Ctx, file: str) -> int:
    name, _ = ctx.project()
    status = None if ctx.args.status == "all" else ctx.args.status
    items = store.list_items(ctx.settings, name, file, status)
    lines = [f"#{it.num:<4} {_mark(it)} {'  ' * (it.indent // 2)}{it.title}{_extra(it)}" for it in items]
    return out(ctx, [it.to_dict() for it in items], "\n".join(lines) or "(none)")


def _show(ctx: Ctx) -> int:
    name, _ = ctx.project()
    d = issues.detail(ctx.settings, name, ctx.args.num)
    it: store.Item = d["item"]
    data = {**it.to_dict(), "images": [{k: im[k] for k in ("name", "path", "label")} for im in d["images"]],
            "notes": [{k: n[k] for k in ("id", "author", "ts", "body_md")} for n in d["notes"]]}
    lines = [f"#{it.num} {_mark(it)} {it.title}",
             f"created {timefmt.fmt_full(it.created_at)} by {it.created_by or '?'}"
             + (f"; closed {timefmt.fmt_full(it.closed_at)} by {it.closed_by or '?'}" if it.done else "")
             + (f"; commit {it.commit}" if it.commit else "")]
    if it.labels:
        lines.append("labels: " + ", ".join(it.labels))
    if d["images"]:
        lines += ["images:", *(f"  {im['path']}" for im in d["images"])]
    if it.body_md.strip():
        lines += ["", it.body_md]
    for n in d["notes"]:
        lines += ["", f"--- note by {n['author']} at {timefmt.fmt_full(n['ts'])}", n["body_md"]]
    return out(ctx, data, "\n".join(lines))


def _issue_add(ctx: Ctx) -> int:
    name, _ = ctx.project()
    a = ctx.args
    body = _read_input(a.body_file) or ""
    if a.image:
        it = issues.create_issue(ctx.settings, name, a.title, body, [Path(p).resolve() for p in a.image],
                                 actor=ctx.who(), audit=ctx.audit, any_path=True, labels=a.label,
                                 section=a.section)
    else:
        it = store.add_item(ctx.settings, name, "backlog", a.title, body=body, section=a.section,
                            actor=ctx.who(), audit=ctx.audit, labels=a.label)
    return out(ctx, it.to_dict(), f"#{it.num} added: {it.title}")


def _issue_note(ctx: Ctx) -> int:
    name, _ = ctx.project()
    text = ctx.args.text if ctx.args.text is not None else _read_input(ctx.args.body_file)
    if text is None:
        raise Invalid("note text missing (argument or --body-file)")
    n = issues.add_note(ctx.settings, name, ctx.args.num, text, actor=ctx.who(), audit=ctx.audit)
    return out(ctx, n, f"note added to #{ctx.args.num}")


def _set_done(ctx: Ctx, file: str, done: bool, commit: str | None = None) -> int:
    name, _ = ctx.project()
    it = store.set_done(ctx.settings, name, file, ctx.args.num, done, actor=ctx.who(), commit=commit,
                        audit=ctx.audit)
    return out(ctx, it.to_dict(), f"#{it.num} {'done' if done else 'reopened'}: {it.title}")


def _issue_edit(ctx: Ctx) -> int:
    name, _ = ctx.project()
    body = _read_input(ctx.args.body_file)
    if ctx.args.title is None and body is None:
        raise Invalid("nothing to change (--title and/or --body-file)")
    it = store.edit_item(ctx.settings, name, "backlog", ctx.args.num, title=ctx.args.title, body=body,
                         actor=ctx.who(), audit=ctx.audit)
    return out(ctx, it.to_dict(), f"#{it.num} updated: {it.title}")


def _issue_image(ctx: Ctx) -> int:
    name, _ = ctx.project()
    im = issues.attach_image(ctx.settings, name, ctx.args.num, Path(ctx.args.file).resolve(), actor=ctx.who(),
                             audit=ctx.audit, any_path=True)
    path = str(issues.image_path(ctx.settings, im["name"]))
    return out(ctx, {**im, "path": path}, f"image attached to #{ctx.args.num}: {path}")


def _issue_rm(ctx: Ctx) -> int:
    name, _ = ctx.project()
    store.delete_item(ctx.settings, name, "backlog", ctx.args.num, actor=ctx.who(), audit=ctx.audit)
    return out(ctx, {"ok": True, "num": ctx.args.num}, f"#{ctx.args.num} deleted")


def _issue_move(ctx: Ctx) -> int:
    name, _ = ctx.project()
    dst, _ = resolve.project_for(ctx.settings, name=ctx.args.to)
    it = store.move_item(ctx.settings, name, dst, ctx.args.num, actor=ctx.who(), audit=ctx.audit)
    return out(ctx, it.to_dict(), f"#{ctx.args.num} moved to {dst} as #{it.num}: {it.title}")


def issue(ctx: Ctx) -> int:
    a = ctx.args.action
    if a == "add":
        return _issue_add(ctx)
    if a == "list":
        return _list(ctx, "backlog")
    if a == "show":
        return _show(ctx)
    if a == "note":
        return _issue_note(ctx)
    if a in ("done", "reopen"):
        return _set_done(ctx, "backlog", a == "done", getattr(ctx.args, "commit", None))
    if a == "edit":
        return _issue_edit(ctx)
    if a == "image":
        return _issue_image(ctx)
    if a == "move":
        return _issue_move(ctx)
    return _issue_rm(ctx)


def todo(ctx: Ctx) -> int:
    a = ctx.args.action
    if a == "list":
        return _list(ctx, "todo")
    if a in ("done", "reopen"):
        return _set_done(ctx, "todo", a == "done")
    name, _ = ctx.project()
    it = store.add_item(ctx.settings, name, "todo", ctx.args.text, section=ctx.args.section, actor=ctx.who(),
                        audit=ctx.audit)
    return out(ctx, it.to_dict(), f"#{it.num} added: {it.title}")


def render(ctx: Ctx) -> int:
    """A hand edit of the MAIN tree's file is taken into pm.db first (``mdimport.absorb``); a worktree's
    copy never is (branches carry stale copies)."""
    a = ctx.args
    if a.merge:  # merge driver; a file pm does not track gets git's own 3-way merge
        base, result, theirs, path = a.merge
        try:
            name, _ = resolve.project_for(ctx.settings, Path.cwd())
            ok = gitglue.merge(ctx.settings, name, base, result, theirs, path, actor=ctx.who(), audit=ctx.audit)
        except NotFound:
            ok = False
        return 0 if ok or gitglue.merge_plain(base, result, theirs) else 1
    if a.post_commit:
        return cli_docs.post_commit(ctx)
    if a.stage:  # pre-commit hook: a repo that is not a tracker project commits as usual
        try:
            name, main_root = resolve.project_for(ctx.settings, Path.cwd())
        except NotFound:
            return 0
        if not views.is_tracker(ctx.settings, name):
            return 0
        root = gitglue.toplevel(Path.cwd())
        if root.resolve() != main_root.resolve():  # a branch's hand additions / ticks (never removals)
            gitglue.take_worktree(ctx.settings, name, root, actor=ctx.who(), audit=ctx.audit)
        for f in mdgen.tracked_files(ctx.settings, name, schema.ALL_FILES):
            gitglue.sync(ctx.settings, name, f)
        changed = gitglue.stage(ctx.settings, name, root)
        return out(ctx, {"changed": changed}, "\n".join(f"{n}: rendered + staged" for n in changed) or "up to date")
    name, root = ctx.project()
    if not views.is_tracker(ctx.settings, name):
        raise Invalid(f"{name} is not in the tracker (run `pmctl import` first)")
    files = mdgen.tracked_files(ctx.settings, name, schema.ALL_FILES)
    if a.check:
        for f in files:
            gitglue.absorb_locked(ctx.settings, name, f)
        stale = gitglue.stale(ctx.settings, name, root)
        out(ctx, {"stale": stale}, "\n".join(str(root / n) for n in stale) or "up to date")
        if stale:
            print("pmctl: these files differ from pm.db: `pmctl render` rewrites them", file=sys.stderr)
        return 1 if stale else 0
    ok = {}
    for f in files:
        ok[schema.ALL_FILES[f]] = gitglue.sync(ctx.settings, name, f)
    if not all(ok.values()):
        raise Invalid("could not write " + ", ".join(n for n, v in ok.items() if not v))
    return out(ctx, {"written": list(ok)}, "\n".join(f"{root / n}: up to date" for n in ok))


def do_import(ctx: Ctx) -> int:
    """``--file`` picks files (default: all four; a missing TODO / CHANGELOG / FEATURES file is skipped)."""
    name, root = ctx.project()
    chosen = ctx.args.file or list(schema.ALL_FILES)
    items = tuple(f for f in chosen if f in schema.FILES)
    res = mdimport.import_project(ctx.settings, name, root, replace=ctx.args.replace, actor=ctx.who(),
                                  audit=ctx.audit, files=items) if items else []
    res += cli_docs.import_docs(ctx, root, [f for f in chosen if f in schema.DOC_FILES], ctx.args.replace,
                                bool(ctx.args.file))
    if not res:
        raise Invalid("nothing to import")
    lines = [f"{schema.ALL_FILES[r['file']]}: {r['items'] if 'items' in r else r['entries']}"
             f" {'items' if 'items' in r else 'entries'}, {r['lines']} other lines"
             + (f", {r['dropped']} old {'items' if 'items' in r else 'entries'} dropped" if r["dropped"] else "")
             for r in res]
    return out(ctx, res, "\n".join(lines))


def hooks(ctx: Ctx) -> int:
    if getattr(ctx.args, "project", None):
        _, root = ctx.project()
    else:
        root = gitglue.toplevel(Path.cwd())
    msgs = gitglue.install_hooks(root, force=ctx.args.force)
    return out(ctx, {"root": str(root), "done": msgs}, "\n".join(msgs))


def do_backup(ctx: Ctx) -> int:
    """Online copy of pm.db now (``pm.core.db.backup``); an existing copy of today is kept unless ``--force``."""
    if ctx.settings.db_backup_keep <= 0:
        raise Invalid("pm.db backups are off (db_backup_keep = 0)")
    path = db.backup(ctx.settings, force=ctx.args.force)
    if path is None and not ctx.settings.db_file.exists():
        raise NotFound(f"no pm.db at {ctx.settings.db_file}")
    if path is None:
        day = timefmt.today().isoformat()
        return out(ctx, {"path": None, "skipped": True},
                   f"{ctx.settings.state_dir / 'backups' / f'pm-{day}.db'}: already there (--force overwrites)")
    return out(ctx, {"path": str(path)}, str(path))


COMMANDS = {"issue": issue, "backlog": issue, "todo": todo, "render": render, "import": do_import, "hooks": hooks,
            "change": cli_docs.change, "feature": cli_docs.feature,
            "claim": cli_claims.claim, "merge-check": cli_claims.merge_check, "memory": cli_memory.memory_cmd,
            "backup": do_backup}


def dispatch(args) -> int:
    cfg = getattr(args, "config", None)
    ctx = Ctx(load_settings(Path(cfg).expanduser() if cfg else None), args)
    timefmt.configure(ctx.settings)  # dates and stamps in the display offset, not the host's timezone
    return COMMANDS[args.cmd](ctx)
