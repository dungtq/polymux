"""Where an agent's chat worked: the absolute .md paths it named and the folders it worked in, read from the stored
chat items (newest first) for the markdown reader's fallbacks (``pm.core.fsutil.resolve_md``'s ``seen`` and
``dirs``). The items are read lazily, once, and shared by both."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator

from pm.chat import items as chat_items
from pm.core.fsutil import md_mentions
from pm.core.models import ChatItem

SEEN_PAGES = 2  # chat items read for a relative ref no folder has: the newest 2 x 1000

_ARG = r"(\"[^\"]+\"|'[^']+'|[^\s;&|()<>]+)"
_CMD_DIR = re.compile(
    r"(?:^\s*|[;&|(]\s*|\bthen\s+|\bdo\s+)cd\s+(?:--\s+)?" + _ARG
    + r"|\bgit\b[^;&|]*?\s-C\s*" + _ARG
    + r"|--directory(?:=|\s+)" + _ARG)
_ABS_PATH = re.compile(r"(?<![\w.~/:$-])((?:~|\$HOME|\$\{HOME\})?/[\w.@+~/-]+)")


def cmd_dirs(line: str) -> list[str]:
    """Absolute folders a shell line works in (``cd``, ``git -C``, ``--directory``), ``~``/``$HOME`` expanded,
    in order, each once; relative ones are skipped."""
    out = []
    for m in _CMD_DIR.finditer(line or ""):
        d = _abs_dir(next(g for g in m.groups() if g).strip("\"'"))
        if d:
            out.append(d)
    return list(dict.fromkeys(out))


def file_dirs(text: str, *, any_file: bool = False) -> list[str]:
    """Folders of the absolute paths in ``text``: the .md files (``md_mentions``); with ``any_file`` (a tool
    line) every absolute path, its folder when it looks like a file (``x.py``), else itself."""
    paths = _ABS_PATH.findall(text or "") if any_file else md_mentions(text)
    out = []
    for p in paths:
        p = p.rstrip("/") if ".." not in p.split("/") else ""
        p = p.rstrip(".") if p.endswith(".") else p  # "see /a/b." in prose
        d = _abs_dir(os.path.dirname(p) if not any_file or "." in os.path.basename(p) else p) if p else None
        if d:
            out.append(d)
    return list(dict.fromkeys(out))


def _abs_dir(d: str) -> str | None:
    for home in ("$HOME", "${HOME}"):
        if d == home or d.startswith(home + "/"):
            d = "~" + d[len(home):]
    d = os.path.expanduser(d) if d.startswith("~") else d
    if not d.startswith("/") or "\0" in d or ".." in d.split("/"):
        return None
    return os.path.normpath(d)


class ChatPaths:
    """The .md paths (``mentions``) and folders (``dirs``) of one agent's chat, newest item first. Both are
    generators over the same cached pages: the DB is read only when one is iterated, at most ``SEEN_PAGES``."""

    def __init__(self, settings, agent_id: str) -> None:
        self.settings, self.agent_id = settings, agent_id
        self._pages: list[list[ChatItem]] = []
        self._done = False

    def _items(self) -> Iterator[ChatItem]:
        i = 0
        while i < len(self._pages) or self._load():
            yield from reversed(self._pages[i])
            i += 1

    def _load(self) -> bool:
        if self._done or len(self._pages) >= SEEN_PAGES:
            return False
        before = self._pages[-1][0].seq if self._pages else None
        page = chat_items.list_items(self.settings, self.agent_id, before_seq=before, limit=chat_items.MAX_LIMIT)
        self._done = len(page) < chat_items.MAX_LIMIT
        if page:
            self._pages.append(page)
        return bool(page)

    def mentions(self) -> Iterator[str]:
        """Absolute .md paths the items name (a tool's target line, a message's text), each once."""
        return _unique(m for it in self._items() if it.kind != "think"  # thinking is not a mention
                       for m in md_mentions(it.line if it.kind == "tool" else it.text))

    def dirs(self) -> Iterator[str]:
        """Folders the chat worked in: a tool line's ``cd``/``git -C``/``--directory`` and its paths' folders,
        then a message's .md files' folders; each once."""
        def per_item(it: ChatItem) -> list[str]:
            if it.kind == "tool":
                return cmd_dirs(it.line) + file_dirs(it.line, any_file=True)
            return [] if it.kind == "think" else file_dirs(it.text)
        return _unique(d for it in self._items() for d in per_item(it))


def _unique(it) -> Iterator[str]:
    seen: set[str] = set()
    for x in it:
        if x not in seen:
            seen.add(x)
            yield x
