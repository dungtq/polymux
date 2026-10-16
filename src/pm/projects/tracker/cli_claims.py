"""``pmctl claim`` (agent work claims) and ``pmctl merge-check``. ``ctx`` is ``cli_cmds.Ctx`` (not imported:
cli_cmds imports this).

Each command asks tmux once for the live panes (``claims.live_panes``) to mark stale claims. ``merge-check``
exits 0 = clear, 1 = overlap (claims or work trees), 2 = error; BASE having moved on is only a warning.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from pm.core.errors import PmError
from pm.projects.tracker import claims, mergecheck


def _line(c: claims.Claim) -> str:
    state = c.status + (" stale" if c.stale else "")
    paths = f" [{', '.join(c.paths)}]" if c.paths else " [*]"
    return f"c{c.num:<4} {c.agent:<16} {state:<13} {c.branch or '-'}  {c.title}{paths}"


def _overlap_lines(found: list[claims.Claim]) -> list[str]:
    return [f"overlap: c{o.num} {o.agent} {o.branch or '-'} {o.title}"
            + (f" [{', '.join(o.paths)}]" if o.paths else " [*]") for o in found]


def _with_overlaps(ctx, c: claims.Claim, found: list[claims.Claim], head: str) -> int:
    return ctx.out({"claim": c.to_dict(), "overlaps": [o.to_dict() for o in found]},
                   "\n".join([head, *_overlap_lines(found)]))


def _add(ctx, name: str, live) -> int:
    a = ctx.args
    branch, worktree = mergecheck.where(Path.cwd())
    if a.worktree:
        worktree = str(Path(a.worktree).expanduser().resolve())
    pane = a.pane if a.pane is not None else os.environ.get("TMUX_PANE", "")
    c, found = claims.add(ctx.settings, name, a.title, agent=ctx.who(), pane=pane,
                          branch=a.branch if a.branch is not None else branch, worktree=worktree, paths=a.path,
                          issue=a.issue, audit=ctx.audit, live=live)
    return _with_overlaps(ctx, c, found, f"c{c.num}")


def claim(ctx) -> int:
    a, act = ctx.args, ctx.args.action
    name, _ = ctx.project()
    live = claims.live_panes(ctx.settings)
    kw = {"audit": ctx.audit, "live": live}
    if act == "add":
        return _add(ctx, name, live)
    if act == "list":
        cs = claims.list_claims(ctx.settings, name, closed=a.all, agent=ctx.who() if a.mine else None, live=live)
        return ctx.out([c.to_dict() for c in cs], "\n".join(_line(c) for c in cs) or "(none)")
    if act == "show":
        c = claims.show(ctx.settings, name, a.num, live=live)
        extra = [f"worktree {c.worktree or '-'}, pane {c.pane or '-'}, created {c.to_dict()['created_at']}"]
        extra += [f"issue #{c.issue}"] if c.issue is not None else []
        extra += [f"commit {c.commit}"] if c.commit else []
        extra += [f"note: {c.note}"] if c.note else []
        return ctx.out(c.to_dict(), "\n".join([_line(c), *extra]))
    if act == "update":
        c = claims.update(ctx.settings, name, a.num, paths=a.path, title=a.title, **kw)
        return ctx.out(c.to_dict(), f"c{c.num} updated: {_line(c)}")
    if act == "done":
        c, found = claims.done(ctx.settings, name, a.num, actor=ctx.who(), commit=a.commit, note=a.note or "", **kw)
        return _with_overlaps(ctx, c, found, f"c{c.num} done" + (f" in {c.commit}" if c.commit else ""))
    if act == "drop":
        c = claims.drop(ctx.settings, name, a.num, note=a.note or "", **kw)
        return ctx.out(c.to_dict(), f"c{c.num} dropped")
    gone = claims.gc(ctx.settings, name, **kw)
    return ctx.out({"dropped": [c.to_dict() for c in gone]},
                   f"{len(gone)} stale claim(s) dropped" + ("" if live is not None else " (tmux unknown)"))


def _report_text(rep: mergecheck.Report) -> str:
    lines = [f"merge-check: {rep.branch or 'HEAD'} -> {rep.base}: {len(rep.files)} file(s)"]
    if rep.base_moved:
        lines.append(f"warning: {rep.base} has {rep.behind} commit(s) not in HEAD: rebase/merge {rep.base} first")
    for c in rep.claims:
        hit = claims.touches(c, rep.files)
        lines.append(f"claim c{c.num} {c.agent} {c.branch or '-'} {c.title}: {', '.join(hit[:10])}"
                     + (" ..." if len(hit) > 10 else ""))
    for w in rep.worktrees:
        lines.append(f"worktree {w['path']} ({w['branch'] or '-'}): {', '.join(w['files'][:10])}"
                     + (" ..." if len(w["files"]) > 10 else ""))
    lines.append("overlap found" if rep.exit_code else "clear")
    return "\n".join(lines)


def merge_check(ctx) -> int:
    """Exit 0 / 1 / 2 (see the module doc); errors go to stderr with exit 2, not pmctl's usual 1."""
    try:
        name, _ = ctx.project()
        rep = mergecheck.check(ctx.settings, name, Path.cwd(), agent=ctx.who(), base=ctx.args.base,
                               live=claims.live_panes(ctx.settings))
    except (PmError, OSError, sqlite3.Error) as e:
        print(f"pmctl: {getattr(e, 'msg', None) or e}", file=sys.stderr)
        return mergecheck.ERROR
    ctx.out(rep.to_dict(), _report_text(rep))
    return rep.exit_code
