"""MCP server configs: parsing plus secret scrubbing (env names only, reduced commands)."""

from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pm.core.models import Finding, McpServerInfo

from .fsread import _lexists, _read, check_path, display_path
from .specs import CRED_WORDS, MAX_JSON_BIG, MAX_READ


def _looks_like_token(s: str) -> bool:
    if (
        re.match(r"(sk-|ghp_|gho_|github_pat_|xox[bpas]-|AKIA|eyJ|glpat-|AIza)", s)
        and len(s) >= 16
    ):
        return True
    if len(s) <= 24 or not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        return False
    classes = sum(
        [
            bool(re.search(r"[a-z]", s)),
            bool(re.search(r"[A-Z]", s)),
            bool(re.search(r"\d", s)),
        ]
    )
    return classes >= 2 and bool(re.search(r"\d", s) or re.search(r"[A-Z]", s))


def _name_is_cred(name: str) -> bool:
    n = name.lstrip("-").lower()
    return any(w in n for w in CRED_WORDS)


def _url_has_cred(s: str) -> bool:
    if re.search(r"://[^/\s@]+:[^/\s@]*@", s):
        return True
    try:
        parts = urlsplit(s)
    except ValueError:
        return False
    if not parts.scheme or not parts.netloc:
        return False
    for kv in parts.query.split("&"):
        k, _, v = kv.partition("=")
        if v and (_name_is_cred(k) or _looks_like_token(v)):
            return True
    return False


def _secret_args(args: list[str]) -> list[bool]:
    """Per-argument flag: True when the argument looks like a credential (or follows a cred flag)."""
    flags = [False] * len(args)
    for i, a in enumerate(args):
        if "://" in a and _url_has_cred(a):
            flags[i] = True
        elif a.startswith("-") and "=" not in a:
            if _name_is_cred(a) and i + 1 < len(args):
                flags[i] = True
                flags[i + 1] = True
        elif "=" in a and not a.startswith("="):
            k, _, v = a.partition("=")
            if (_name_is_cred(k) and v) or _looks_like_token(v):
                flags[i] = True
        elif _looks_like_token(a):
            flags[i] = True
    return flags


def _short(a: str) -> str:
    return a if len(a) <= 40 else a[:39] + "…"


def _reduce_command(command: object, args: object) -> tuple[str, bool]:
    """Return (binary basename + at most 3 safe args, whether a credential was found)."""
    if not isinstance(command, str) or not command.strip():
        return "", False
    argv: list[str] = []
    if isinstance(args, list):
        argv = [str(a) for a in args if isinstance(a, (str, int, float))]
    cmd = command.strip()
    if " " in cmd and not os.path.exists(cmd):
        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()
        if parts:
            cmd, argv = parts[0], parts[1:] + argv
    flags = _secret_args(argv)
    safe = [_short(a) for a, bad in zip(argv, flags) if not bad]
    return " ".join([os.path.basename(cmd), *safe[:3]]).strip(), any(flags)


def _transport(entry: dict, has_url: bool) -> str:
    t = entry.get("type", entry.get("transport"))
    if isinstance(t, str):
        t = t.lower().replace("_", "-")
        if t in ("stdio", "local"):
            return "stdio"
        if t in ("http", "streamable-http", "streamablehttp", "streamable"):
            return "http"
        if t == "sse":
            return "sse"
        if t in ("ws", "websocket"):
            return "ws"
        return "unknown"
    return "http" if has_url else "stdio"


def _env_names(entry: dict) -> tuple[str, ...]:
    names: set[str] = set()
    env = entry.get("env")
    if isinstance(env, dict):
        names.update(str(k) for k in env)
    elif isinstance(env, list):
        names.update(str(k).split("=", 1)[0] for k in env if isinstance(k, str))
    ev = entry.get("env_vars")
    if isinstance(ev, list):
        names.update(str(k) for k in ev if isinstance(k, str))
    b = entry.get("bearer_token_env_var")
    if isinstance(b, str):
        names.add(b)
    return tuple(sorted(n for n in names if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", n)))


def _enabled(entry: dict) -> bool | None:
    d = entry.get("disabled")
    if isinstance(d, bool):
        return not d
    e = entry.get("enabled")
    if isinstance(e, bool):
        return e
    return True


def _header_cred_literal(entry: dict) -> bool:
    for key in ("headers", "http_headers"):
        h = entry.get(key)
        if isinstance(h, dict):
            for k, v in h.items():
                if (
                    isinstance(v, str)
                    and _name_is_cred(str(k))
                    and v
                    and "${" not in v
                    and "env:" not in v
                ):
                    return True
    return False


def _parse_servers(
    servers: object, agent: str, cfg: str, findings: list[Finding], kind: str = ""
) -> list[McpServerInfo]:
    """Servers of one config; ``kind`` (json | claude | toml) decides whether a key-in-args finding gets a fix."""
    out: list[McpServerInfo] = []
    if not isinstance(servers, dict):
        return out
    for name in sorted(servers, key=str):
        entry = servers[name]
        if not isinstance(entry, dict):
            continue
        url = next(
            (
                entry[k]
                for k in ("url", "serverUrl", "httpUrl")
                if isinstance(entry.get(k), str)
            ),
            "",
        )
        host = ""
        if url:
            try:
                host = urlsplit(url).hostname or ""
            except ValueError:
                host = ""
            if _url_has_cred(url):
                findings.append(
                    Finding(
                        "warn",
                        f"Credential embedded in the URL of MCP server '{name}' in {cfg}",
                        "security",
                        agent,
                    )
                )
        command, cred = _reduce_command(entry.get("command"), entry.get("args"))
        if cred:
            fix = (
                (("action", "mcp_env"), ("config", cfg), ("server", str(name)))
                if kind in ("json", "claude") and len(str(name)) <= 80
                else ()
            )
            findings.append(
                Finding(
                    "warn",
                    f"API key passed as a CLI argument for MCP server '{name}' in {cfg}"
                    " (visible in ps); move it to an env var",
                    "security",
                    agent,
                    fix,
                )
            )
        if _header_cred_literal(entry):
            findings.append(
                Finding(
                    "warn",
                    f"Literal credential in the headers of MCP server '{name}' in {cfg}",
                    "security",
                    agent,
                )
            )
        out.append(
            McpServerInfo(
                name=str(name)[:80],
                agent=agent,
                transport=_transport(entry, bool(url)),
                command=command,
                host=host,
                env_names=_env_names(entry),
                config_path=cfg,
                enabled=_enabled(entry),
            )
        )
    return out


def _load_mcp_file(
    path: Path, kind: str, agent: str, home: Path, findings: list[Finding]
) -> tuple[list[McpServerInfo], bool]:
    """Parse one MCP config file. Returns (servers, file_exists)."""
    if not _lexists(path):
        return [], False
    cfg = display_path(path, home)
    text = _read(path, home, MAX_JSON_BIG if kind == "claude" else MAX_READ)
    if text is None:
        if check_path(path, home) is not None:
            findings.append(Finding("info", f"Could not read {cfg}", "notes", agent))
        return [], True
    try:
        if kind == "toml":
            data = tomllib.loads(text)
            return _parse_servers(data.get("mcp_servers"), agent, cfg, findings, kind), True
        data = json.loads(text)
    except (ValueError, tomllib.TOMLDecodeError, RecursionError):
        findings.append(Finding("info", f"Could not parse {cfg}", "notes", agent))
        return [], True
    if not isinstance(data, dict):
        return [], True
    if kind == "claude":
        servers = data.get("mcpServers")
        del data
        return _parse_servers(servers, agent, cfg, findings, kind), True
    out = _parse_servers(data.get("mcpServers"), agent, cfg, findings, kind)
    out += _parse_servers(data.get("servers"), agent, cfg, findings, kind)
    return out, True
