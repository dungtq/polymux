"""Project-page TODO box domain: ``<project>/TODO.md`` path resolution, checklist toggle and the
summary shown on the project page. HTTP routes live in web.routes.todo."""

from __future__ import annotations

import re
from pathlib import Path

from pm.core.errors import PmError
from pm.core.fsutil import safe_join
from pm.projects import backlog as rb
from pm.projects.backlog import Bad, token_of
from pm.projects.projects import todo_items

FILE = "TODO.md"
VISIBLE = 8
_MARK = re.compile(r"^(\s*[-*]\s+\[)([ xX])(\]\s+.*)$")


def resolve_path(project_path: str) -> Path:
    try:
        return safe_join(project_path, FILE)
    except PmError:
        raise Bad(400, "TODO.md is a symlink or escapes the project; refused") from None


def toggle_line(text: str, idx: int, expect: str) -> str:
    """Flip the item identified by line index AND exact text, against the current content.
    If it moved, its only exact match is used; gone/changed/ambiguous is a 409."""
    items = {it["line"]: it["text"] for it in todo_items(text)}
    try:
        idx = rb.locate(items, idx, expect)
    except Bad:
        raise Bad(409, "That TODO item was changed or removed in TODO.md; nothing was saved. "
                       "Reload.") from None
    lines = text.split("\n")
    m = _MARK.match(lines[idx])
    if not m:
        raise Bad(400, "line is not a checklist item")
    lines[idx] = f"{m.group(1)}{' ' if m.group(2) in 'xX' else 'x'}{m.group(3)}"
    return "\n".join(lines)


def _group(items: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for it in items:
        if not groups or groups[-1]["heading"] != it["heading"]:
            groups.append({"heading": it["heading"], "items": []})
        groups[-1]["items"].append(it)
    return groups


def _label(h: str) -> str:
    return re.sub(r"[`*_]", "", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)).strip()


def todo_summary(settings, name: str) -> dict:
    """For the project page: open items grouped by heading (first VISIBLE, then the rest)."""
    out = {"exists": False, "blocked": False, "token": "", "open_n": 0, "done_n": 0,
           "total": 0, "shown": [], "rest": []}
    path = rb.registry(settings).get(name)
    if path is None:
        return out
    try:
        data = rb.read(resolve_path(path))
    except Bad:
        out["blocked"] = True
        return out
    except OSError:
        return out
    text = data.decode("utf-8", "replace")
    items = todo_items(text)
    opens = [dict(it, html=rb.inline(it["text"]), label=_label(it["heading"]))
             for it in items if not it["done"]]
    for o in opens:
        o["heading"] = o["label"]
    out.update(exists=bool(items), token=token_of(data), open_n=len(opens),
               done_n=len(items) - len(opens), total=len(items),
               shown=_group(opens[:VISIBLE]), rest=_group(opens[VISIBLE:]))
    return out
