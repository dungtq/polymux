"""``pmctl memory``: the agents' shared memory (``memory.py``) and its sync with Claude Code's memory dirs
(``memsync.py``). ``ctx`` is ``cli_cmds.Ctx`` (not imported: cli_cmds imports this).

``add`` / ``set`` / ``rm`` sync the project right after the change (Claude's dirs and ``<memory_dir>/<project>.md``;
a global memory re-renders every project's summary). A failed sync after a change is a warning on stderr: the
change itself is in pm.db.
"""

from __future__ import annotations

import sys

from pm.core.errors import Invalid
from pm.projects.tracker import memory, memsync
from pm.projects.tracker.memory import Memory

SHOW_HINT = "show one: pmctl memory show NAME"
CONTEXT_DESC = 300


def _line(m: Memory, cap: int | None = None) -> str:
    d = m.description if cap is None or len(m.description) <= cap else m.description[:cap - 1] + "…"
    return f"- {m.name} ({m.type}{', global' if m.scope == 'global' else ''}): {' '.join(d.split())}"


def _body(ctx) -> str | None:
    a = ctx.args
    if a.text is not None:
        return a.text
    return ctx.read(a.body_file)


def _project(ctx) -> str:
    """The project's name; '' for ``--global`` (no project needed)."""
    return memory.GLOBAL if getattr(ctx.args, "is_global", False) else ctx.project()[0]


def _after_change(ctx, m: Memory) -> None:
    """Best-effort sync after a change; failures are a warning (the change is committed)."""
    try:
        if m.scope == "global":
            memsync.refresh_summaries(ctx.settings)
            return
        name, root = ctx.project()
        rep = memsync.sync_project(ctx.settings, name, root, actor=ctx.who(), audit=ctx.audit)
        for d in rep["dirs"]:
            for note in d["notes"]:
                print(f"pmctl: memory sync: {note}", file=sys.stderr)
    except Exception as e:  # incl. AuditFailed of the sync's own audit line: the change itself is committed
        print(f"pmctl: warning: memory saved, sync failed: {e.__class__.__name__}: {e}", file=sys.stderr)


def _add(ctx) -> int:
    a = ctx.args
    project = _project(ctx)
    m = memory.add(ctx.settings, project, a.name, type=a.type, description=a.description, body=_body(ctx) or "",
                   title=a.title or "", is_global=a.is_global, actor=ctx.who(), audit=ctx.audit)
    _after_change(ctx, m)
    return ctx.out(m.to_dict(), f"memory {m.name} added" + (" (global)" if m.scope == "global" else ""))


def _set(ctx) -> int:
    """Replace the given fields; a memory that does not exist yet is added (needs --type and --description)."""
    a = ctx.args
    project = _project(ctx)
    body = _body(ctx)
    if memory.find(ctx.settings, project, memory.clean_name(a.name)) is None:
        if a.type is None or a.description is None:
            raise Invalid(f"no memory {a.name!r} yet: --type and --description are needed to add it")
        m = memory.add(ctx.settings, project, a.name, type=a.type, description=a.description, body=body or "",
                       title=a.title or "", is_global=a.is_global, actor=ctx.who(), audit=ctx.audit)
        verb = "added"
    else:
        m = memory.update(ctx.settings, project, a.name, type=a.type, description=a.description, body=body,
                          title=a.title, is_global=a.is_global, actor=ctx.who(), audit=ctx.audit)
        verb = "updated"
    _after_change(ctx, m)
    return ctx.out(m.to_dict(), f"memory {m.name} {verb}" + (" (global)" if m.scope == "global" else ""))


def _show(ctx) -> int:
    a = ctx.args
    project = memory.GLOBAL if a.is_global else ctx.project()[0]
    m = memory.get(ctx.settings, project, a.name, is_global=a.is_global)
    d = m.to_dict()
    lines = [_line(m)] + ([f"title: {m.title}"] if m.title else [])
    lines.append(f"created {d['created_at']} by {m.created_by or '?'}, updated {d['updated_at']} by"
                 f" {m.updated_by or '?'}" + (f"; file {m.source}" if m.source else ""))
    lines += ["", m.body_md] if m.body_md else []
    return ctx.out(d, "\n".join(lines))


def _rm(ctx) -> int:
    m = memory.remove(ctx.settings, _project(ctx), ctx.args.name, is_global=ctx.args.is_global, audit=ctx.audit)
    _after_change(ctx, m)
    return ctx.out({"ok": True, "name": m.name, "scope": m.scope}, f"memory {m.name} removed")


def _context(ctx) -> int:
    """The short index an agent reads at session start."""
    name, _ = ctx.project()
    mems = memory.list_memories(ctx.settings, name, include_global=True)
    head = f"pm memory for {name}: {len(mems)} memor{'y' if len(mems) == 1 else 'ies'}"
    lines = [head, *(_line(m, CONTEXT_DESC) for m in mems),
             SHOW_HINT if mems else "add one: pmctl memory add NAME --type T --description D --text BODY"]
    data = {"project": name, "memories": [{"name": m.name, "type": m.type, "scope": m.scope,
                                           "description": m.description} for m in mems], "show": SHOW_HINT}
    return ctx.out(data, "\n".join(lines))


def _sync_text(rep: dict) -> list[str]:
    out = []
    for d in rep["dirs"]:
        out.append(f"{d['dir']}: {d['imported']} imported, {d['updated']} updated, {d['written']} written,"
                   f" {d['removed']} removed" + (f", {len(d['conflicts'])} conflict(s)" if d["conflicts"] else ""))
        out += [f"  note: {n}" for n in d["notes"]]
    out.append(f"summary: {rep['summary'] or '-'}")
    return [f"{rep['project']}:", *("  " + ln for ln in out)]


def _sync(ctx) -> int:
    if ctx.args.all:
        reps = memsync.sync_all(ctx.settings, actor=ctx.who(), audit=ctx.audit)
        return ctx.out(reps, "\n".join(ln for r in reps for ln in _sync_text(r)) or "(no project has memories)")
    name, root = ctx.project()
    rep = memsync.sync_project(ctx.settings, name, root, actor=ctx.who(), audit=ctx.audit)
    return ctx.out(rep, "\n".join(_sync_text(rep)))


def memory_cmd(ctx) -> int:
    a, act = ctx.args, ctx.args.action
    if act == "add":
        return _add(ctx)
    if act == "set":
        return _set(ctx)
    if act == "show":
        return _show(ctx)
    if act == "rm":
        return _rm(ctx)
    if act == "context":
        return _context(ctx)
    if act == "sync":
        return _sync(ctx)
    name, _ = ctx.project()
    if act == "search":
        found = memory.search(ctx.settings, name, a.query, limit=a.limit)
        return ctx.out([m.to_dict() for m in found], "\n".join(_line(m) for m in found) or "(none)")
    mems = memory.list_memories(ctx.settings, name, type=a.type, include_global=a.all)
    return ctx.out([m.to_dict() for m in mems], "\n".join(_line(m) for m in mems) or "(none)")
