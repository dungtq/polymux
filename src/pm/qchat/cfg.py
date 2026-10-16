"""Quick chat settings (``<state_dir>/qchat.json``, no restart needed) and the MCP server test.

MCP discovery / manual entries live in ``mcpcfg``, skills in ``skills``; their public names are
re-exported here with the saved enabled maps filled in, so callers keep using ``cfg``. Header / env
values and URL credentials are never returned, logged or audited; test messages are scrubbed. Reuses
the secret-stripping helpers of ``pm.agents.inventory.read``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from pm.agents.inventory import read as inv
from pm.core.config import Settings
from pm.qchat import mcpcfg
from pm.qchat import skills as qskills
from pm.qchat.mcpcfg import (  # noqa: F401 - re-exported
    HEADER_RE,
    MAX_HEADERS,
    MAX_TARGET,
    NAME_RE,
    TMP_MAX_AGE,
    _entry_for,
    _entry_url,
    _home,
    _lock,
    _read_json,
    _write_private,
    add_mcp,
)
from pm.qchat.skills import MAX_SKILL_BODY, SKILL_RE, skill_text  # noqa: F401 - re-exported

DEFAULTS: dict = {
    "profiles": {"none": True, "read": True, "full": True},
    "default_profile": "none",
    "default_folder": "",
    "read_tools": ["Read", "Grep", "Glob", "WebFetch", "WebSearch"],
    "full_permission_mode": "bypassPermissions",
    "max_turns": 25,
    "timeout_s": 600,
    "mcp_enabled": {},
    "skills_enabled": {},
    "skills_in_none": True,
    "keep_convs": 500,
    "keep_days": 0,
    "nav_search": True,
}

PROFILES = ("none", "read", "full")
PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:*().\- ]{0,79}$")
PROBE_TIMEOUT = 8.0


# ---------------------------------------------------------------- files


def _cfg_path(settings: Settings) -> Path:
    return settings.state_dir / "qchat.json"


# ---------------------------------------------------------------- settings


def _clean_bool_map(v: object, what: str) -> dict[str, bool]:
    if not isinstance(v, dict):
        raise ValueError(f"{what}: expected an object")
    out: dict[str, bool] = {}
    for k, b in v.items():
        if not isinstance(k, str) or not (1 <= len(k) <= 120) or not isinstance(b, bool):
            raise ValueError(f"{what}: invalid entry")
        out[k] = b
    return out


def _int(v: object, lo: int, hi: int, what: str) -> int:
    if isinstance(v, bool):
        raise ValueError(f"{what}: invalid number")
    try:
        n = int(v)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise ValueError(f"{what}: invalid number") from None
    return max(lo, min(hi, n))


def _bool(v: object, what: str) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.lower() in ("1", "true", "on", "yes"):
        return True
    if isinstance(v, str) and v.lower() in ("0", "false", "off", "no", ""):
        return False
    raise ValueError(f"{what}: invalid value")


def _validate(key: str, v: object, cur: dict) -> object:
    if key == "profiles":
        if not isinstance(v, dict):
            raise ValueError("profiles: expected an object")
        out = dict(cur["profiles"])
        for k, b in v.items():
            if k not in PROFILES:
                raise ValueError("profiles: unknown profile")
            out[k] = _bool(b, "profiles")
        return out
    if key == "default_profile":
        if v not in PROFILES:
            raise ValueError("default_profile: unknown profile")
        return v
    if key == "default_folder":
        if not isinstance(v, str) or (v and not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}", v)):
            raise ValueError("default_folder: invalid project name")
        return v
    if key == "read_tools":
        if isinstance(v, str):
            v = [t.strip() for t in re.split(r"[,\n]", v) if t.strip()]
        if not isinstance(v, list) or len(v) > 40:
            raise ValueError("read_tools: expected a list of tool names")
        out_t: list[str] = []
        for t in v:
            if not isinstance(t, str) or not TOOL_RE.fullmatch(t.strip()):
                raise ValueError("read_tools: invalid tool name")
            if t.strip() not in out_t:
                out_t.append(t.strip())
        return out_t
    if key == "full_permission_mode":
        if v not in PERMISSION_MODES:
            raise ValueError("full_permission_mode: unknown mode")
        return v
    if key == "max_turns":
        return _int(v, 1, 200, key)
    if key == "timeout_s":
        return _int(v, 10, 3600, key)
    if key == "keep_convs":
        return _int(v, 0, 100000, key)
    if key == "keep_days":
        return _int(v, 0, 3650, key)
    if key in ("mcp_enabled", "skills_enabled"):
        out_m = dict(cur[key])
        out_m.update(_clean_bool_map(v, key))
        return out_m
    if key in ("skills_in_none", "nav_search"):
        return _bool(v, key)
    raise ValueError(f"unknown setting: {key}")


def load(settings: Settings) -> dict:
    """Saved settings over DEFAULTS; invalid saved values fall back to the default."""
    cur = json.loads(json.dumps(DEFAULTS))
    saved = _read_json(_cfg_path(settings))
    for k in DEFAULTS:
        if k in saved:
            try:
                cur[k] = _validate(k, saved[k], cur)
            except ValueError:
                pass
    return cur


def save(settings: Settings, patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("invalid settings")
    with _lock:
        cur = load(settings)
        new = dict(cur)
        for k, v in patch.items():
            new[k] = _validate(k, v, new)
        if not new["profiles"].get(new["default_profile"]):
            enabled = [p for p in PROFILES if new["profiles"].get(p)]
            if not enabled:
                raise ValueError("at least one profile must stay enabled")
            if "default_profile" in patch:
                raise ValueError("default_profile: that profile is disabled")
            new["default_profile"] = enabled[0]
        if not any(new["profiles"].values()):
            raise ValueError("at least one profile must stay enabled")
        _write_private(_cfg_path(settings), new)
        return new


# ---------------------------------------------------------------- MCP


def discover_mcp(settings: Settings) -> list[dict]:
    return mcpcfg.discover_mcp(settings, load(settings)["mcp_enabled"])


def remove_mcp(settings: Settings, name: str) -> bool:
    with _lock:
        if not mcpcfg.drop_manual(settings, name):
            return False
        en = load(settings)["mcp_enabled"]
        if name in en:
            cur = _read_json(_cfg_path(settings))
            m = dict(en)
            del m[name]
            cur["mcp_enabled"] = m
            _write_private(_cfg_path(settings), cur)
        return True


def mcp_config_file(settings: Settings, names: list[str]) -> Path | None:
    """Private 0600 temp --mcp-config JSON for ticked, enabled, known servers; None when empty."""
    return mcpcfg.mcp_config_file(settings, names, load(settings)["mcp_enabled"])


def _scrub(text: str, entry: dict) -> str:
    """Remove every secret value of the entry from a message, then clip."""
    secrets: list[str] = []
    for key in ("headers", "http_headers", "env"):
        m = entry.get(key)
        if isinstance(m, dict):
            secrets += [str(v) for v in m.values() if isinstance(v, (str, int)) and len(str(v)) >= 4]
    secrets += [a for a in map(str, entry.get("args") or []) if len(a) >= 8]
    url = _entry_url(entry)
    if url:
        secrets.append(url)
    for s in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(s, "***")
    return inv._clip(text, 200)


def _probe_stdio(entry: dict) -> tuple[bool, str]:
    cmd = entry.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return False, "no command"
    argv = [cmd.strip(), *[str(a) for a in (entry.get("args") or []) if isinstance(a, (str, int, float))]]
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    e = entry.get("env")
    if isinstance(e, dict):
        env.update({str(k): str(v) for k, v in e.items() if isinstance(v, (str, int, float))})
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "pm-qchat-test", "version": "1"}}}) + "\n"
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, cwd=str(_home()),
                                start_new_session=True)
    except (OSError, ValueError) as exc:
        return False, f"cannot start: {type(exc).__name__}"
    try:
        try:
            out, _ = proc.communicate(init.encode(), timeout=PROBE_TIMEOUT)
        except subprocess.TimeoutExpired:
            # many servers keep running: read what arrived so far
            proc.kill()
            out, _ = proc.communicate()
        for line in out.decode("utf-8", "replace").splitlines():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == 1:
                if "result" in msg:
                    info = (msg["result"] or {}).get("serverInfo") or {}
                    nm = info.get("name") if isinstance(info, dict) else None
                    return True, "ok" + (f" ({inv._clip(str(nm), 40)})" if nm else "")
                return False, "server refused initialize"
        return False, "no initialize reply"
    finally:
        try:
            proc.kill()
        except OSError:
            pass


def _probe_http(entry: dict, typ: str) -> tuple[bool, str]:
    url = _entry_url(entry)
    if not url or urlsplit(url).scheme not in ("http", "https"):
        return False, "no http(s) URL"
    hdr = {"Accept": "application/json, text/event-stream", "User-Agent": "pm-qchat-test"}
    h = entry.get("headers") if isinstance(entry.get("headers"), dict) else {}
    hdr.update({str(k): str(v) for k, v in h.items() if HEADER_RE.fullmatch(str(k)) and "\n" not in str(v)})
    body = None
    if typ != "sse":
        hdr["Content-Type"] = "application/json"
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "pm-qchat-test", "version": "1"}}}).encode()
    req = urllib.request.Request(url, data=body, headers=hdr, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as r:
            return True, f"ok (HTTP {r.status})"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"reachable, but auth failed (HTTP {exc.code})"
        if exc.code < 500 and exc.code != 404:
            return True, f"reachable (HTTP {exc.code})"
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, f"unreachable ({type(reason).__name__})"


def test_mcp(settings: Settings, name: str) -> tuple[bool, str]:
    entry = _entry_for(settings, name)
    if entry is None:
        return False, "unknown or not testable"
    typ = entry.get("type") if entry.get("type") in ("stdio", "http", "sse") else inv._transport(
        entry, bool(_entry_url(entry)))
    try:
        if typ == "stdio":
            ok, msg = _probe_stdio(entry)
        elif typ in ("http", "sse"):
            ok, msg = _probe_http(entry, typ)
        else:
            return False, "unsupported transport"
    except Exception as exc:  # noqa: BLE001 - never leak details
        return False, f"test failed ({type(exc).__name__})"
    return ok, _scrub(msg, entry)


test_mcp.__test__ = False  # type: ignore[attr-defined]  # not a pytest test

# ---------------------------------------------------------------- skills


def discover_skills(settings: Settings, folder: str = "") -> list[dict]:
    return qskills.discover_skills(settings, folder, load(settings)["skills_enabled"])


def skill_prompt(settings: Settings, names: list[str], folder: str, typed: str) -> tuple[str, str]:
    """(system-prompt addendum listing ticked skills, user text with a leading /name expanded)."""
    return qskills.skill_prompt(settings, names, folder, typed, load(settings)["skills_enabled"])
