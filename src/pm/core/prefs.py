"""Small per-install preferences (default launch options, notify rules) in state_dir/prefs.json.

Only the storage and validation live here; the launch form and alerts read them via
``load``. Theme is per browser (localStorage), not stored here.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from pm.core.fsutil import JsonStore, atomic_write

_LOCK = threading.Lock()
_FREE = re.compile(r"[A-Za-z0-9._:/\- \[\]]{0,80}")
NOTIFY_RULES = (
    ("finish", "Agent failed"),
    ("waiting", "Agent waiting for input"),
    ("health", "Server load, memory, swap, disk"),
    ("containers", "Unhealthy or exited containers"),
    ("hosts", "Other hosts unreachable"),
)
DEFAULTS: dict = {
    "launch": {"agent": "", "model": "", "mode": ""},
    "notify": {k: True for k, _ in NOTIFY_RULES},
    "projects": {},
}
COMPOSER_DEFAULTS: dict = {
    "paste_lines": 10,
    "exts": ["png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "md", "log", "json", "csv",
             "yaml", "yml", "toml"],
    "send_key": "enter",
}
# enter: Enter sends, Shift+Enter is a new line; mod-enter: Ctrl/Cmd+Enter sends, Enter is a new line.
SEND_KEYS = ("enter", "mod-enter")
_EXT = re.compile(r"[a-z0-9]{1,8}")
MAX_EXTS = 40
# Last launch choices per project (form field names); bypass modes are never stored.
PROJECT_KEYS = ("kind", "model_choice", "variant", "model", "effort", "mode", "extra", "branch")
_NAME = re.compile(r"[A-Za-z0-9._\-]{1,80}")
MAX_PROJECTS = 200


def _clean_exts(raw: object) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[,\s]+", raw)
    out: list[str] = []
    for e in raw if isinstance(raw, (list, tuple)) else []:
        e = str(e).strip().lower().lstrip(".")
        if _EXT.fullmatch(e) and e not in out:
            out.append(e)
        if len(out) >= MAX_EXTS:
            break
    return out


def _clean_composer(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {"paste_lines": COMPOSER_DEFAULTS["paste_lines"], "exts": list(COMPOSER_DEFAULTS["exts"]),
           "send_key": COMPOSER_DEFAULTS["send_key"]}
    n = raw.get("paste_lines")
    if isinstance(n, int) and not isinstance(n, bool):
        out["paste_lines"] = max(0, min(500, n))
    if "exts" in raw and isinstance(raw["exts"], (list, str)):
        out["exts"] = _clean_exts(raw["exts"])
    if raw.get("send_key") in SEND_KEYS:
        out["send_key"] = raw["send_key"]
    return out


def _clean(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {"launch": dict(DEFAULTS["launch"]), "notify": dict(DEFAULTS["notify"]), "projects": {},
           "composer": _clean_composer(raw.get("composer"))}
    projs = raw.get("projects") if isinstance(raw.get("projects"), dict) else {}
    for name, ch in list(projs.items())[-MAX_PROJECTS:]:
        if not (isinstance(name, str) and _NAME.fullmatch(name) and isinstance(ch, dict)):
            continue
        row = {k: ch[k].strip() for k in PROJECT_KEYS
               if isinstance(ch.get(k), str) and _FREE.fullmatch(ch[k].strip())}
        if row:
            out["projects"][name] = row
    launch = raw.get("launch") if isinstance(raw.get("launch"), dict) else {}
    for k in out["launch"]:
        v = launch.get(k, "")
        if isinstance(v, str) and _FREE.fullmatch(v.strip()):
            out["launch"][k] = v.strip()
    notify = raw.get("notify") if isinstance(raw.get("notify"), dict) else {}
    for k in out["notify"]:
        if isinstance(notify.get(k), bool):
            out["notify"][k] = notify[k]
    return out


def _store(path: Path) -> JsonStore:
    return JsonStore(path, dict, indent=1, lock=_LOCK, fsync_dir=True)


def load(path: Path) -> dict:
    return _clean(_store(path).load())


def save(path: Path, prefs: dict) -> dict:
    clean = _clean(prefs)
    with _LOCK:
        if "projects" not in prefs:  # settings form does not carry the per-project memory
            clean["projects"] = load(path)["projects"]
        if "composer" not in prefs:
            clean["composer"] = load(path)["composer"]
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _store(path).save(clean)
    return clean


def composer_prefs(settings) -> dict:
    """Composer settings {paste_lines, exts, send_key} merged over the defaults."""
    return load(settings.prefs_file)["composer"]


def save_composer_prefs(settings, paste_lines: int, exts: list[str], send_key: str | None = None) -> dict:
    """Validate and persist the composer settings (``send_key`` None keeps the stored one); returns the
    stored dict."""
    try:
        n = int(paste_lines)
    except (TypeError, ValueError):
        n = COMPOSER_DEFAULTS["paste_lines"]
    path = Path(settings.prefs_file)
    with _LOCK:
        cur = load(path)
        key = cur["composer"]["send_key"] if send_key is None else send_key
        comp = _clean_composer({"paste_lines": n, "exts": exts, "send_key": key})
        cur["composer"] = comp
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _store(path).save(_clean(cur))
    return comp


def remember(path: Path, project: str, choices: dict) -> None:
    """Store the last launch choices of ``project`` (most recent last). Best effort."""
    row = {k: str(choices.get(k) or "") for k in PROJECT_KEYS}
    with _LOCK:
        cur = load(path)
        cur["projects"].pop(project, None)
        cur["projects"][project] = row
        cur = _clean(cur)
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _store(path).save(cur)


# ---- TODO items already dispatched (no double dispatch) -> state_dir/dispatched.json
_DISPATCH_KEEP_S = 7 * 86400


def _dispatch_file(path: Path) -> Path:
    return Path(path).with_name("dispatched.json")


def _dispatch_load(path: Path) -> dict:
    try:
        raw = json.loads(_dispatch_file(path).read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def dispatched(path: Path, project: str) -> dict:
    """{item_key: {ts, agent, pane}} of items dispatched from ``project``."""
    rows = _dispatch_load(path).get(project)
    return rows if isinstance(rows, dict) else {}


def dispatched_all(path: Path) -> dict:
    """{project: {item_key: row}} of every dispatch mark."""
    return {k: v for k, v in _dispatch_load(path).items() if isinstance(v, dict)}


def mark_dispatched(path: Path, project: str, key: str, agent: str, pane: str, now: float, title: str = "") -> None:
    """Remember that item ``key`` went to ``agent`` (pane ``pane``). Best effort."""
    if not (_NAME.fullmatch(project) and re.fullmatch(r"[0-9a-f]{16}", key)):
        return
    with _LOCK:
        cur = _dispatch_load(path)
        rows = {k: v for k, v in (cur.get(project) or {}).items()
                if isinstance(v, dict) and now - float(v.get("ts") or 0) < _DISPATCH_KEEP_S}
        rows[key] = {"ts": now, "agent": agent[:80], "pane": pane[:16]}
        if title:
            rows[key]["title"] = " ".join(title.split())[:200]
        cur[project] = rows
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(_dispatch_file(path), json.dumps(cur), mode=0o600, fsync=True, fsync_dir=True)
