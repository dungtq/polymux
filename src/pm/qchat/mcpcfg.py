"""Quick chat MCP servers: discovery, manual add/remove, the private temp ``--mcp-config``.

Servers come from ``~/.claude.json`` (user scope and projects under the pm roots) plus manual entries
kept in ``<state_dir>/qchat-mcp.json`` (0600, which is where manual header values live). Header / env
values and URL credentials are never returned, logged or audited: listings carry only a reduced
``target``. Also the private-file helpers and the lock shared with ``cfg`` (which imports this module,
never the other way round; enabled maps are passed in by ``cfg``).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from pm.agents.inventory import read as inv
from pm.core.config import Settings
from pm.core.fsutil import atomic_write
from pm.projects.projects import discover as discover_projects

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
HEADER_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")
MAX_TARGET = 500
MAX_HEADERS = 20
TMP_MAX_AGE = 3600.0

_lock = threading.Lock()


# ---------------------------------------------------------------- files


def _home() -> Path:
    return Path.home()


def _mcp_path(settings: Settings) -> Path:
    return settings.state_dir / "qchat-mcp.json"


def _write_private(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=1, sort_keys=True), mode=0o600, fsync=True)


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


# ---------------------------------------------------------------- discovery


def _display_url(url: str) -> str:
    """scheme://host[:port]/path with no userinfo, query or fragment."""
    try:
        p = urlsplit(url)
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
    except ValueError:
        return ""
    if not p.scheme or not host:
        return ""
    return f"{p.scheme}://{host}{port}{p.path}"[:MAX_TARGET]


def _entry_url(entry: dict) -> str:
    return next((entry[k] for k in ("url", "serverUrl", "httpUrl") if isinstance(entry.get(k), str)), "")


def _target(entry: dict, typ: str) -> str:
    if typ in ("http", "sse"):
        return _display_url(_entry_url(entry))
    cmd, _cred = inv._reduce_command(entry.get("command"), entry.get("args"))
    return cmd


def _claude_json(home: Path) -> dict:
    text = inv._read(home / ".claude.json", home, inv.MAX_JSON_BIG)
    if text is None:
        return {}
    try:
        d = json.loads(text)
    except (ValueError, RecursionError):
        return {}
    return d if isinstance(d, dict) else {}


def _project_paths(settings: Settings) -> set[str]:
    return {p.path for p in discover_projects(settings)}


def _raw_servers(settings: Settings) -> dict[str, tuple[str, dict]]:
    """name -> (source, raw entry) from ~/.claude.json (user first, then pm projects). Internal only."""
    data = _claude_json(_home())
    out: dict[str, tuple[str, dict]] = {}

    def add(servers: object, source: str) -> None:
        if not isinstance(servers, dict):
            return
        for name in sorted(servers, key=str):
            e = servers[name]
            n = str(name)
            if isinstance(e, dict) and NAME_RE.fullmatch(n) and n not in out:
                out[n] = (source, e)

    add(data.get("mcpServers"), "user")
    projects = data.get("projects")
    if isinstance(projects, dict):
        roots = _project_paths(settings)
        for path in sorted(projects, key=str):
            if str(path) in roots and isinstance(projects[path], dict):
                add(projects[path].get("mcpServers"), "project:" + os.path.basename(str(path)))
    return out


def _manual(settings: Settings) -> dict[str, dict]:
    d = _read_json(_mcp_path(settings))
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, dict) and NAME_RE.fullmatch(k)}


def _connectors(home: Path) -> list[str]:
    v = _claude_json(home).get("claudeAiMcpEverConnected")
    if not isinstance(v, list):
        return []
    return [s[:80] for s in v if isinstance(s, str) and s.strip()][:50]


def _manual_entry_view(entry: dict) -> tuple[str, str]:
    typ = entry.get("type") if entry.get("type") in ("stdio", "http", "sse") else "stdio"
    return typ, _target(entry, typ)


def discover_mcp(settings: Settings, enabled: dict[str, bool]) -> list[dict]:
    out: list[dict] = []
    taken: set[str] = set()
    for name, (source, e) in _raw_servers(settings).items():
        typ = inv._transport(e, bool(_entry_url(e)))
        if typ not in ("stdio", "http", "sse"):
            continue
        taken.add(name)
        out.append({"name": name, "source": source, "type": typ, "target": _target(e, typ),
                    "enabled": enabled.get(name, True) and inv._enabled(e) is not False,
                    "manual": False, "testable": True})
    for name, e in sorted(_manual(settings).items()):
        if name in taken:
            continue
        taken.add(name)
        typ, target = _manual_entry_view(e)
        out.append({"name": name, "source": "manual", "type": typ, "target": target,
                    "enabled": enabled.get(name, True), "manual": True, "testable": True})
    for c in _connectors(_home()):
        if c not in taken:
            out.append({"name": c, "source": "claude.ai", "type": "connector", "target": "",
                        "enabled": False, "manual": False, "testable": False})
    return out


# ---------------------------------------------------------------- manual entries


def _parse_headers(headers: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (headers or "").splitlines():
        if not line.strip():
            continue
        k, sep, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if not sep or not HEADER_RE.fullmatch(k) or not v or "\0" in v or len(v) > 2000:
            raise ValueError("headers: use one 'Name: value' per line")
        out[k] = v
    if len(out) > MAX_HEADERS:
        raise ValueError("headers: too many")
    return out


def add_mcp(settings: Settings, *, name: str, type: str, target: str, headers: str = "") -> None:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError("name: letters, digits, . _ - (max 64)")
    if type not in ("stdio", "http", "sse"):
        raise ValueError("type must be stdio, http or sse")
    target = (target or "").strip() if isinstance(target, str) else ""
    if not target or len(target) > MAX_TARGET or "\0" in target:
        raise ValueError("target: required (max 500 characters)")
    entry: dict = {"type": type}
    if type == "stdio":
        if headers.strip():
            raise ValueError("headers apply to http and sse only")
        try:
            argv = shlex.split(target)
        except ValueError:
            raise ValueError("target: unbalanced quotes") from None
        if not argv:
            raise ValueError("target: command required")
        if inv._reduce_command(argv[0], argv[1:])[1]:
            raise ValueError("target: put credentials in the environment, not in the command line")
        entry["command"], entry["args"] = argv[0], argv[1:]
    else:
        try:
            p = urlsplit(target)
            p.port  # noqa: B018 - validates the port
        except ValueError:
            raise ValueError("target: invalid URL") from None
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("target: an http(s) URL is required")
        if inv._url_has_cred(target) or p.username or p.password:
            raise ValueError("target: no credentials in the URL; use headers")
        entry["url"] = target
        h = _parse_headers(headers)
        if h:
            entry["headers"] = h
    with _lock:
        if name in _raw_servers(settings) or name in _manual(settings):
            raise ValueError("a server with that name already exists")
        data = _manual(settings)
        data[name] = entry
        _write_private(_mcp_path(settings), data)


def drop_manual(settings: Settings, name: str) -> bool:
    """Remove a manual entry from qchat-mcp.json; the caller holds ``_lock``."""
    data = _manual(settings)
    if name not in data:
        return False
    del data[name]
    _write_private(_mcp_path(settings), data)
    return True


def _entry_for(settings: Settings, name: str) -> dict | None:
    raw = _raw_servers(settings)
    if name in raw:
        return raw[name][1]
    return _manual(settings).get(name)


# ---------------------------------------------------------------- temp --mcp-config


def _tmp_dir(settings: Settings) -> Path:
    d = settings.state_dir / "qchat-mcp-tmp"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def mcp_config_file(settings: Settings, names: list[str], enabled: dict[str, bool]) -> Path | None:
    """Private 0600 temp --mcp-config JSON for ticked, enabled, known servers; None when empty."""
    servers: dict[str, dict] = {}
    for n in names or []:
        if not isinstance(n, str) or n in servers or not enabled.get(n, True):
            continue
        e = _entry_for(settings, n)
        if e is None or inv._enabled(e) is False:
            continue
        servers[n] = e
    if not servers:
        return None
    d = _tmp_dir(settings)
    now = time.time()
    for old in d.glob("mcp-*.json"):
        try:
            if now - old.stat().st_mtime > TMP_MAX_AGE:
                old.unlink()
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix="mcp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": servers}, f)
    os.chmod(tmp, 0o600)
    return Path(tmp)
