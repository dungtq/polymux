"""Cursor agent statistics: Cursor stores no token counts, so the context percent is read from the
pane's status line (``Grok 4.6 High Fast · 11.9%``). Turns come from ``prompt_history.json`` next to
the matched chat's ``store.db`` (a JSON list of strings; only its length is used)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .common import Stats
from .cursor import chat_dir

_FOOT_RE = re.compile(r"^\s*(?P<model>[A-Za-z][^\n·|]{1,60}?)\s+·\s+(?P<pct>\d{1,3}(?:\.\d+)?)\s*%(?:\s.*)?$")
_AMBIGUOUS_S = 120.0  # another chat of the same cwd written this close to the newest: cannot tell which
_MAX_HISTORY = 1 << 20


def _clean(line: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", line).replace("\xa0", " ").rstrip()


def parse_footer(pane_text: str) -> tuple[str, float] | None:
    """(model, percent) from the lowest status line of the form ``<model> · <pct>%``, else None."""
    for raw in reversed((pane_text or "").splitlines()[-40:]):
        m = _FOOT_RE.match(_clean(raw))
        if m:
            pct = float(m.group("pct"))
            if pct <= 100:
                return m.group("model").strip(), pct
    return None


def turns_from_history(cwd: str, home: Path) -> int:
    """Prompt count of the newest chat of ``cwd``; 0 when missing or when the match is ambiguous."""
    if not cwd:
        return 0
    base = chat_dir(cwd, home)
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    times: list[tuple[float, str]] = []
    for n in names:
        try:
            times.append((os.stat(base / n / "store.db").st_mtime, n))
        except OSError:
            continue
    if not times:
        return 0
    times.sort(reverse=True)
    if len(times) > 1 and times[0][0] - times[1][0] < _AMBIGUOUS_S:
        return 0
    hist = base / times[0][1] / "prompt_history.json"
    try:
        if hist.stat().st_size > _MAX_HISTORY:
            return 0
        data = json.loads(hist.read_bytes())
    except (OSError, ValueError):
        return 0
    return len(data) if isinstance(data, list) else 0


def session_stats(agent, pane_text: str, home: Path | None = None) -> Stats | None:
    got = parse_footer(pane_text)
    if got is None:
        return None
    model, pct = got
    turns = 0
    try:
        turns = turns_from_history(agent.cwd, home or Path.home())
    except Exception:  # noqa: BLE001 - turns are optional
        turns = 0
    return Stats(turns=turns, model=model[:80], pct_hint=round(pct), source="cursor-agent", exact=False)
