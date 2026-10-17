"""``pmctl change`` (CHANGELOG.md) and ``pmctl feature`` (FEATURES.md), ``pmctl render --post-commit`` and
the doc files' part of ``pmctl import``. ``ctx`` is ``cli_cmds.Ctx`` (not imported: cli_cmds imports this).

A change made from a linked worktree on another branch than the default one is that branch's draft
(``drafts``); its render is written into the worktree's copy at once (the pre-commit hook stages it).
"""

from __future__ import annotations

from pathlib import Path

from pm.core.errors import Invalid, NotFound
from pm.projects import backlog
from pm.projects.tracker import changelog, drafts, featurelist, gitglue, mdgen, resolve, schema, views

DOCS = {"changelog": changelog, "features": featurelist}


def _here(ctx) -> tuple[drafts.View, Path, Path]:
    """(view of the cwd's work tree, that tree's root, the project's main root). A cwd outside the project
    counts as its main tree."""
    name, main_root = ctx.project()
    root = main_root
    try:
        if resolve.project_for(ctx.settings, Path.cwd())[0] == name:
            root = gitglue.toplevel(Path.cwd())
    except (NotFound, Invalid):
        pass
    return drafts.view_for(ctx.settings, name, root), root, main_root


def _also_here(ctx, file: str, root: Path, main_root: Path) -> None:
    """After a change from a linked worktree: its copy shows the branch's view too."""
    if root.resolve() != main_root.resolve():
        gitglue.render_into(ctx.settings, ctx.project()[0], root, files=(file,))


def _where(here: drafts.View) -> str:
    return "" if here.main else f" (draft of {here.branch}: on main once merged)"


# ------------------------------------------------------------------ pmctl change
def _change_line(e: dict) -> str:
    st = "" if e["state"] == "landed" else f"  [{e['state']}{' ' + e['branch'] if e['branch'] else ''}]"
    return f"#{e['num']:<4} {e['title']}{st}"


def change(ctx) -> int:
    a, act = ctx.args, ctx.args.action
    name, _ = ctx.project()
    if act == "list":
        es = changelog.entries(ctx.settings, name)
        es = es if a.all else [e for e in es if e["state"] != "abandoned"]
        return ctx.out(es, "\n".join(_change_line(e) for e in es) or "(none)")
    if act == "show":
        e = changelog.entries(ctx.settings, name, a.num)[0]
        meta = f"{e['state']}" + (f", branch {e['branch']}" if e["branch"] else "") + (
            f", commit {e['sha'][:12]}" if e["sha"] else "") + f", by {e['created_by'] or '?'}"
        return ctx.out(e, f"#{e['num']} {e['heading']}\n({meta})\n{e['body']}".rstrip("\n"))
    here, root, main_root = _here(ctx)
    kw = {"here": here, "actor": ctx.who(), "audit": ctx.audit}
    if act == "add":
        body = ctx.read(a.body_file)
        e = changelog.add(ctx.settings, name, a.title, body or "", day=a.date, **kw)
        text = f"#{e['num']} added: {e['title']}{_where(here)}"
    elif act == "edit":
        body = ctx.read(a.body_file)
        e = changelog.edit(ctx.settings, name, a.num, title=a.title, body=body, **kw)
        text = f"#{e['num']} updated: {e['title']}"
    else:
        changelog.remove(ctx.settings, name, a.num, **kw)
        e, text = {"ok": True, "num": a.num}, f"#{a.num} deleted"
    _also_here(ctx, "changelog", root, main_root)
    return ctx.out(e, text)


# ------------------------------------------------------------------ pmctl feature
def _feature_text(ctx) -> str:
    a = ctx.args
    text = a.text if a.text is not None else ctx.read(a.text_file)
    if text is None:
        raise Invalid("feature text missing (--text or --text-file)")
    return text


def feature(ctx) -> int:
    a, act = ctx.args, ctx.args.action
    name, _ = ctx.project()
    here, root, main_root = _here(ctx)
    if act == "list":
        fs = featurelist.features(ctx.settings, name, area=a.area, here=here)
        lines, area = [], None
        for f in fs:
            if f["area"] != area:
                lines.append(f"## {f['area']}")
                area = f["area"]
            lines.append(f"- {f['name']}")
        return ctx.out(fs, "\n".join(lines) or "(none)")
    if act == "show":
        f = featurelist.features(ctx.settings, name, area=a.area, name=a.name, here=here)[0]
        return ctx.out(f, f"## {f['area']}\n- **{f['name']}** — {f['text']}\n{f['extra']}".rstrip("\n"))
    kw = {"actor": ctx.who(), "audit": ctx.audit}
    if act == "audited":
        line = featurelist.set_audited(ctx.settings, name, a.date, **kw)
        _also_here(ctx, "features", root, main_root)
        return ctx.out({"line": line}, line)
    if act == "set":
        f = featurelist.set_feature(ctx.settings, name, a.area, a.name, _feature_text(ctx), here=here, **kw)
        out, text = f, f"{f['area']} / {f['name']}: set{_where(here)}"
    else:
        featurelist.rm_feature(ctx.settings, name, a.area, a.name, here=here, **kw)
        out, text = {"ok": True}, f"{a.area} / {a.name}: removed{_where(here)}"
    _also_here(ctx, "features", root, main_root)
    return ctx.out(out, text)


# ------------------------------------------------------------------ hooks, import
def post_commit(ctx) -> int:
    """The post-commit / post-merge hook: stamp this branch's drafts with HEAD; in the main tree on the default
    branch land the drafts HEAD has merged (the merge driver only previews them). Quiet unless it stamped."""
    try:
        name, main_root = resolve.project_for(ctx.settings, Path.cwd())
    except NotFound:
        return 0
    if not views.is_tracker(ctx.settings, name):
        return 0
    root = gitglue.toplevel(Path.cwd())
    if root.resolve() == main_root.resolve() and drafts.view_for(ctx.settings, name, root).main:
        for f in mdgen.tracked_files(ctx.settings, name, DOCS):
            gitglue.sync(ctx.settings, name, f)
        return 0
    n = drafts.stamp(ctx.settings, name, root, audit=ctx.audit)
    if n or ctx.json:
        ctx.out({"stamped": n}, f"pmctl: {n} CHANGELOG/FEATURES draft(s) stamped with this commit")
    return 0


def import_docs(ctx, root: Path, files: list[str], replace: bool, explicit: bool) -> list[dict]:
    """Import CHANGELOG.md / FEATURES.md from ``root`` (a missing file is skipped unless named)."""
    name, _ = ctx.project()
    out = []
    for file in files:
        path = mdgen.file_path(root, file)
        if not path.exists():
            if explicit:
                raise NotFound(f"{schema.file_name(file)} not found in {root}")
            continue
        text = backlog.read(path, schema.DOC_MAX_BYTES * 4).decode("utf-8", "replace")
        out.append(DOCS[file].import_text(ctx.settings, name, text, replace=replace, actor=ctx.who(),
                                          audit=ctx.audit))
    return out
