"""Plan usage limits (5-hour, weekly, per-model weekly) for the agent page (Claude and Antigravity).

Claude source: the ``rate_limits`` object Claude Code hands to ``tools/pm-statusline.sh`` (stdin JSON),
written to ``<state_dir>/claude-limits.json``.
Antigravity source: ``agy -p /usage --output-format json``, cached in ``<state_dir>/antigravity-limits.json``
and refreshed via ``SWR`` at most once per ``AGY_FRESH_S``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pm.core import timefmt
from pm.core.cache import SWR
from pm.core.fsutil import atomic_write
from pm.core.models import AgentKind
from pm.core.proc import ProcError, run_argv

FILE = "claude-limits.json"
AGY_FILE = "antigravity-limits.json"
AGY_FRESH_S = 180
AGY_TIMEOUT_S = 5.0
STALE_S = 6 * 3600  # older than this and the numbers are not trusted
_MODEL_KEY = re.compile(r"^seven_day_([a-z0-9_]{1,24})$")
_AGY_SWR = SWR(max_entries=4)


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


def parse_agy_usage(raw: str | bytes | dict) -> dict[str, dict[str, float]]:
    """Parse ``agy -p /usage --output-format json`` into ``{bucket_id: {used_percentage, resets_at}}``."""
    try:
        obj = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return {}
    if not isinstance(obj, dict):
        return {}
    groups = ((obj.get("command") or {}).get("data") or {}).get("groups") if "command" in obj else None
    if not isinstance(groups, list):
        return {k: v for k, v in obj.items() if isinstance(k, str) and isinstance(v, dict) and "used_percentage" in v}
    out: dict[str, dict[str, float]] = {}
    for g in groups:
        for b in (g.get("buckets") if isinstance(g, dict) else None) or ():
            if not isinstance(b, dict):
                continue
            bid, rem = b.get("id"), b.get("remaining_fraction")
            if not isinstance(bid, str) or isinstance(rem, bool) or not isinstance(rem, (int, float)):
                continue
            rt = b.get("reset_time")
            try:
                ra = datetime.fromisoformat(rt).timestamp() if isinstance(rt, str) and rt else 0.0
            except ValueError:
                ra = 0.0
            out[bid] = {"used_percentage": round(max(0.0, min(1.0, 1.0 - float(rem))) * 100.0, 2), "resets_at": ra}
    return out


def _fetch_agy_limits(state_dir: Path, settings) -> bool:
    from pm.agents import launch

    try:
        bin_path = launch.agent_binary(settings, AgentKind.ANTIGRAVITY)
        r = run_argv([bin_path, "-p", "/usage", "--output-format", "json"], timeout=AGY_TIMEOUT_S, text=True)
        data = parse_agy_usage(r.stdout) if r.returncode == 0 and r.stdout else {}
        if not data:
            return False
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        atomic_write(Path(state_dir) / AGY_FILE, json.dumps(data).encode())
        return True
    except (ProcError, OSError, ValueError):
        return False


def read_agy_limits(state_dir: Path, model: str = "", now: float | None = None, settings=None) -> list[Window]:
    """Antigravity plan windows (5h, weekly, Argon/3P weekly); [] when nothing usable is stored."""
    now = time.time() if now is None else now
    path = Path(state_dir) / AGY_FILE
    if settings is not None:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = STALE_S + 1
        if age >= AGY_FRESH_S:
            _AGY_SWR.get(str(path), lambda: _fetch_agy_limits(state_dir, settings), AGY_FRESH_S, STALE_S)
    try:
        if now - path.stat().st_mtime > STALE_S:
            return []
        data = parse_agy_usage(path.read_text())
    except OSError:
        return []
    if not data:
        return []
    ml = (model or "").lower()
    is_3p = any(k in ml for k in ("claude", "gpt", "o1", "o3"))
    five_id = "3p-5h" if is_3p and "3p-5h" in data else "gemini-5h"
    week_id = ("3p-weekly" if is_3p and "3p-weekly" in data
               else "gemini-argon-wtu-weekly" if "argon" in ml and "gemini-argon-wtu-weekly" in data
               else "gemini-weekly")
    specs = (
        ("gemini-5h", "5h limit", "5-hour limit", True),
        ("gemini-weekly", "Week limit", "Weekly limit", True),
        ("gemini-argon-wtu-weekly", "Argon week limit", "Argon weekly limit", True),
        ("3p-5h", "3P 5h limit", "3P 5-hour limit", is_3p),
        ("3p-weekly", "3P week limit", "3P weekly limit", is_3p),
    )
    out: list[Window] = []
    for key, label, name, always in specs:
        tag = "5" if key == five_id else "W" if key == week_id else ""
        w = _window(label, name, data.get(key), now, tag)
        if w and (always or w.pct > 0 or tag):
            out.append(w)
    return out


def for_agent(agent, settings, model: str = "", now: float | None = None) -> list[Window]:
    """Plan usage windows for ``agent`` (Claude or Antigravity)."""
    kind = agent.kind.value
    if kind == "claude":
        return read_limits(settings.state_dir, now)
    if kind == "antigravity":
        return read_agy_limits(settings.state_dir, model=model, now=now, settings=settings)
    return []
