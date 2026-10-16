"""Claude plan usage limits (5-hour, weekly, per-model weekly) for the agent page.

Source: the `rate_limits` object Claude Code hands to a status line script (stdin JSON). Claude Code
exposes no other local source and pm never touches the OAuth token. `tools/pm-statusline.sh`
copies that object to `<state_dir>/claude-limits.json`; this module only reads that file.
Known keys: `five_hour`, `seven_day`; any `seven_day_<model>` key (opus, sonnet, ...) is shown as
a per-model weekly window when present. Each window: `used_percentage` (0-100), `resets_at` (epoch s).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pm.core import timefmt

FILE = "claude-limits.json"
STALE_S = 6 * 3600  # older than this and the numbers are not trusted
_MODEL_KEY = re.compile(r"^seven_day_([a-z0-9_]{1,24})$")


@dataclass(frozen=True)
class Window:
    label: str  # short, for the summary: "5h limit", "Week limit"
    pct: int
    reset: str  # relative: "in 3h 20m"; "" when unknown, "reset" when the window has rolled over
    level: str  # ok | warn | red
    name: str = ""  # panel label: "5-hour limit", "Weekly limit"
    when: str = ""  # summary reset text: "in 3h 20m" (<24h) or local "Mon 14:00"
    abs: str = ""  # absolute local reset time for title attributes
    tag: str = ""  # one-character summary tag: "5" (5-hour), "W" (week); "" for per-model windows


def _fmt_reset(resets_at: float, now: float) -> str:
    d = int(resets_at - now)
    if d <= 0:
        return "reset"
    if d < 3600:
        return f"in {max(1, d // 60)}m"
    if d < 86400:
        return f"in {d // 3600}h {(d % 3600) // 60:02d}m"
    return f"in {d // 86400}d {(d % 86400) // 3600}h"


def _local(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timefmt.now().tzinfo)


def _abs(resets_at: float, now: float) -> tuple[str, str]:
    """(short local text for the summary, full local text for titles)."""
    dt = _local(resets_at)
    full = dt.strftime("%a %d %b %H:%M ") + timefmt.label()
    short = dt.strftime("%H:%M") if dt.date() == _local(now).date() else dt.strftime("%a %H:%M")
    return short, full


def _level(pct: int) -> str:
    return "red" if pct >= 80 else "warn" if pct >= 60 else "ok"


def _window(label: str, name: str, raw: object, now: float, tag: str = "") -> Window | None:
    if not isinstance(raw, dict):
        return None
    p = raw.get("used_percentage")
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return None
    ra = raw.get("resets_at")
    ra = ra if isinstance(ra, (int, float)) and not isinstance(ra, bool) else 0
    if ra and ra <= now:  # window rolled over since the snapshot: old percentage no longer true
        return Window(label, 0, "reset", "ok", name, "reset", "", tag)
    pct = max(0, min(100, int(round(p))))
    if not ra:
        return Window(label, pct, "", _level(pct), name, "", "", tag)
    rel = _fmt_reset(ra, now)
    short, full = _abs(ra, now)
    return Window(label, pct, rel, _level(pct), name, rel if ra - now < 86400 else short, full, tag)


def read_limits(state_dir: Path, now: float | None = None) -> list[Window]:
    """Windows in display order (5h, weekly, per-model weekly); [] when nothing usable is stored."""
    now = time.time() if now is None else now
    path = Path(state_dir) / FILE
    try:
        if now - path.stat().st_mtime > STALE_S:
            return []
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[Window] = []
    for key, label, name, tag in (("five_hour", "5h limit", "5-hour limit", "5"), ("seven_day", "Week limit", "Weekly limit", "W")):
        w = _window(label, name, data.get(key), now, tag)
        if w:
            out.append(w)
    for key in sorted(data):
        m = _MODEL_KEY.match(key)
        if m:
            mdl = m.group(1).replace("_", " ").title()
            w = _window(f"{mdl} week limit", f"{mdl} weekly limit", data[key], now)
            if w:
                out.append(w)
    return out
