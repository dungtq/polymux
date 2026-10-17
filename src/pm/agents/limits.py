"""Plan usage limits (5-hour, daily, weekly, per-model weekly) for the agent page (Claude, Antigravity, Devin).

Claude source: the ``rate_limits`` object Claude Code hands to ``tools/pm-statusline.sh`` (stdin JSON),
written to ``<state_dir>/claude-limits.json`` (the CLI's own login folder) or ``claude-limits-<account>.json`` (a named
account: the script reads the account from ``CLAUDE_CONFIG_DIR``). Each account has its own plan limits.
Antigravity source: ``agy -p /usage --output-format json``, cached in ``<state_dir>/antigravity-limits.json``
and refreshed via ``SWR`` at most once per ``AGY_FRESH_S``.
Devin source: the CLI keeps quotas server-side and prints them only for the interactive ``/usage`` command.
While the agent is idle with an empty composer, pm sends ``/usage`` to the pane, waits past the
"Fetching account usage" placeholder, parses the Daily/Weekly lines and caches them in
``<state_dir>/devin-limits.json`` (refreshed via ``SWR`` at most once per ``DEVIN_FRESH_S``; a failed
attempt stamps the file too, so failures do not retrigger every poll). Captured pane text is
ANSI-stripped and masked, used in memory only — never logged, indexed or sent anywhere.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pm.agents import registry  # stored account of an agent: each Claude account has its own limits
from pm.core import timefmt
from pm.core.cache import SWR
from pm.core.fsutil import atomic_write
from pm.core.models import AgentKind, AgentState
from pm.core.proc import ProcError, run_argv

FILE = "claude-limits.json"
AGY_FILE = "antigravity-limits.json"
AGY_FRESH_S = 180
AGY_TIMEOUT_S = 5.0
DEVIN_FILE = "devin-limits.json"
DEVIN_FRESH_S = 1800  # each refresh types /usage into the agent's pane: keep it rare
DEVIN_POLL_S = 0.5
DEVIN_WAIT_S = 9.0  # Devin prints a "Fetching account usage" placeholder first, then the real quotas
DEVIN_SCAN_LINES = 120
STALE_S = 6 * 3600  # older than this and the numbers are not trusted
_MODEL_KEY = re.compile(r"^seven_day_([a-z0-9_]{1,24})$")
_AGY_SWR = SWR(max_entries=4)
_DEVIN_SWR = SWR(max_entries=4)
# Devin TUI contract: the composer shows the "Message to …" placeholder only when it is empty and the
# agent is at the prompt; a running turn shows "Guide Devin while it works". Anything else: do not type.
_DEVIN_IDLE = "message to "
_DEVIN_BUSY = "guide devin while it works"
_DEVIN_FETCHING = re.compile(r"(?i)fetching account usage")
_DEVIN_LINE = re.compile(r"(?i)^\s*(daily|weekly)\b.*?(\d{1,3})\s*%\s*used\b.*\bresets\b")
_DEVIN_REL = re.compile(r"(?i)\bresets in\s+((?:\d+\s*[dhm]\s*)+)")
_DEVIN_ABS = re.compile(r"(?i)\bresets\s+([a-z]{3}\s+\d{1,2},\s+\d{1,2}:\d{2}\s*[ap]m)(?:\s*\(utc([+-]\d{1,2})\))?")


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


def limits_file(account: str = "") -> str:
    """File the status line script writes for a Claude login account ('' = the CLI's own folder)."""
    return f"claude-limits-{account}.json" if account else FILE


def read_limits(state_dir: Path, now: float | None = None, account: str = "") -> list[Window]:
    """Windows in display order (5h, weekly, per-model weekly) of one Claude account; [] when nothing usable is stored."""
    now = time.time() if now is None else now
    path = Path(state_dir) / limits_file(account)
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


def _devin_reset_epoch(text: str, now: float) -> float:
    """``resets in 12h 57m`` or ``resets Oct 18, 3:00 PM (UTC+7)`` -> epoch; 0 when unparsed."""
    m = _DEVIN_REL.search(text)
    if m:
        d = sum(int(n) * {"d": 86400, "h": 3600, "m": 60}[u]
                for n, u in re.findall(r"(\d+)\s*([dhm])", m.group(1)))
        return now + d if d else 0.0
    m = _DEVIN_ABS.search(text)
    if m:
        tz = timefmt.now().tzinfo
        if m.group(2):  # Devin prints the zone it used, e.g. "(UTC+7)"
            from datetime import timedelta, timezone
            tz = timezone(timedelta(hours=int(m.group(2))))
        try:  # the TUI omits the year: "Oct 18, 3:00 PM" -> this year's occurrence
            md, hm_ap = m.group(1).split(",", 1)
            mon_s, day_s = md.split()
            mo = "jan feb mar apr may jun jul aug sep oct nov dec".split().index(mon_s.lower()[:3]) + 1
            hm, ap = hm_ap.strip().rsplit(None, 1)
            hh, mm = (int(x) for x in hm.split(":"))
            dt = datetime(_local(now).year, mo, int(day_s),
                          (hh % 12) + (12 if ap.lower() == "pm" else 0), mm, tzinfo=tz)
        except (ValueError, IndexError):
            return 0.0
        ts = dt.timestamp()
        return dt.replace(year=dt.year + 1).timestamp() if ts < now - 43200 else ts
    return 0.0


def parse_devin_usage(text: str, now: float | None = None) -> dict[str, dict[str, float]]:
    """``{daily|weekly: {used_percentage, resets_at}}`` from a Devin ``/usage`` screen.

    While the fetch runs, the pane shows "Fetching account usage…" above 0% placeholder bars;
    the line is removed when the real numbers land. So any Fetching line means not final.
    Older ``/usage`` blocks stay in the scroll: the last matching line per window wins."""
    now = time.time() if now is None else now
    if _DEVIN_FETCHING.search(text):
        return {}
    hits: dict[str, str] = {}
    for ln in text.splitlines():
        m = _DEVIN_LINE.match(ln)
        if m:
            hits[m.group(1).lower()] = ln
    return {key: {"used_percentage": int(_DEVIN_LINE.match(ln).group(2)),
                  "resets_at": _devin_reset_epoch(ln, now)} for key, ln in hits.items()}


def _devin_pane_text(settings, pane_id: str) -> str | None:
    from pm.security.mask import render_pane
    from pm.tmux import ctl as tmuxctl

    try:
        return render_pane(tmuxctl.capture_pane(settings, pane_id, DEVIN_SCAN_LINES))
    except tmuxctl.TmuxError:
        return None


def _fetch_devin_limits(state_dir: Path, agent, settings) -> bool:
    """SWR compute (own thread): type ``/usage`` into the agent's idle pane and store the quota lines.
    Always stamps the file so a failed attempt throttles the next retry via ``DEVIN_FRESH_S``."""
    from pm.agents import message as msg

    def mark(extra: dict) -> None:
        try:
            Path(state_dir).mkdir(parents=True, exist_ok=True)
            atomic_write(Path(state_dir) / DEVIN_FILE, json.dumps({"attempted_at": time.time()} | extra).encode())
        except OSError:
            pass

    text = _devin_pane_text(settings, agent.pane_id)
    low = (text or "").lower()
    if agent.state is not AgentState.IDLE or _DEVIN_IDLE not in low or _DEVIN_BUSY in low:
        mark({})
        return False
    try:
        msg.send_message(settings, agent, "/usage")
    except msg.MessageError:
        mark({})
        return False
    data: dict[str, dict[str, float]] = {}
    deadline = time.monotonic() + DEVIN_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(DEVIN_POLL_S)
        data = parse_devin_usage(_devin_pane_text(settings, agent.pane_id) or "")
        if data:
            break
    if not data:
        mark({})
        return False
    mark(data)
    return True


def read_devin_limits(state_dir: Path, agent=None, settings=None, now: float | None = None) -> list[Window]:
    """Devin plan windows (daily, weekly); [] when nothing usable is stored. Triggers a background
    ``/usage`` refresh when the file is older than ``DEVIN_FRESH_S`` and the agent is idle."""
    now = time.time() if now is None else now
    path = Path(state_dir) / DEVIN_FILE
    if settings is not None and agent is not None and agent.state is AgentState.IDLE:
        try:
            age = now - path.stat().st_mtime
        except OSError:
            age = STALE_S + 1
        if age >= DEVIN_FRESH_S:
            from pm.core import executors

            # SWR is the throttle/dedupe; the send+poll itself goes to the jobs pool so even a
            # cold miss never stalls the tmux pool thread this route runs on.
            _DEVIN_SWR.get(str(path), lambda: bool(
                executors.jobs.submit(_fetch_devin_limits, state_dir, agent, settings)),
                           DEVIN_FRESH_S, STALE_S)
    try:
        if now - path.stat().st_mtime > STALE_S:
            return []
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[Window] = []
    for key, label, name, tag in (("daily", "Day limit", "Daily limit", "D"),
                                  ("weekly", "Week limit", "Weekly limit", "W")):
        w = _window(label, name, data.get(key), now, tag)
        if w:
            out.append(w)
    return out


def for_agent(agent, settings, model: str = "", now: float | None = None) -> list[Window]:
    """Plan usage windows for ``agent`` (Claude, Antigravity or Devin)."""
    kind = agent.kind.value
    if kind == "claude":
        return read_limits(settings.state_dir, now, registry.account_of(settings, agent.id))
    if kind == "antigravity":
        return read_agy_limits(settings.state_dir, model=model, now=now, settings=settings)
    if kind == "devin":
        return read_devin_limits(settings.state_dir, agent=agent, settings=settings, now=now)
    return []
