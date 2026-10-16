"""MCP config mutation: move a credential passed as a CLI argument of an MCP server into its ``env``.

The fix for the "API key passed as a CLI argument" finding. JSON configs only (the Claude ``~/.claude.json``
and the ``mcpServers``/``servers`` files of the other agents); TOML is refused. The credential value is never
put in an op, an error message or the plan: ops carry the flag and env names only. The write keeps the
file's mode, backs the old file up (0600) and re-checks the file just before the replace, since Claude
rewrites ``~/.claude.json`` while it runs.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

from .mcp import _name_is_cred, _reduce_command, _secret_args
from .mutate_base import BACKUP_DISP, MutError, _Built, _from_display, _guard, _lstat, _read_file, _Step, _str
from .specs import MAX_JSON_BIG, SPECS

NAME_MAX = 80
_ENV_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,79}")


def _config(params: dict, home: Path) -> tuple[Path, str]:
    """(path, kind) of a JSON MCP config named by its display path; only the fixed spec paths are allowed."""
    disp = _str(params, "config")
    known = {"~/" + rel: kind for s in SPECS for rel, kind in s.mcp}
    kind = known.get(disp)
    if kind is None:
        raise MutError("Not an MCP config file pm knows.")
    if kind not in ("json", "claude"):
        raise MutError("Only JSON MCP configs can be changed here; edit this file by hand.")
    return _from_display(disp, home), kind


def _env_name(prefix: str, key: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", key.lstrip("-")).strip("_").upper()
    pre = re.sub(r"[^A-Za-z0-9]+", "_", prefix).strip("_").upper()
    name = f"{pre}_{base}" if pre and not base.startswith(pre) else base
    if name[:1].isdigit():
        name = "MCP_" + name
    if not _ENV_RE.fullmatch(name):
        raise MutError("No usable env variable name for this argument; edit the file by hand.")
    return name


def _split(server: str, args: list[str]) -> tuple[list[str], list[tuple[str, str, str]]]:
    """(args without credentials, [(env name, value, flag)]); refuses forms it cannot move safely."""
    flags = _secret_args(args)
    keep: list[str] = []
    moved: list[tuple[str, str, str]] = []
    i = 0
    while i < len(args):
        a = args[i]
        if not flags[i]:
            keep.append(a)
            i += 1
        elif a.startswith("-") and "=" not in a and _name_is_cred(a) and i + 1 < len(args):
            moved.append((_env_name(server, a), args[i + 1], a))
            i += 2
        elif "=" in a and "://" not in a and not a.startswith("=") and _name_is_cred(a.partition("=")[0]):
            k, _, v = a.partition("=")
            moved.append((_env_name(server if k.startswith("-") else "", k), v, k))
            i += 1
        else:
            raise MutError("This credential argument cannot be moved automatically; edit the file by hand.")
    return keep, moved


def _entry(data: dict, kind: str, server: str) -> dict:
    keys = ("mcpServers",) if kind == "claude" else ("mcpServers", "servers")
    for k in keys:
        servers = data.get(k)
        if isinstance(servers, dict) and isinstance(servers.get(server), dict):
            return servers[server]
    raise MutError("That MCP server is not in the file any more.")


def _move(entry: dict, server: str) -> list[tuple[str, str]]:
    """Rewrite ``entry`` in place; returns (env name, flag) per moved credential (never the value)."""
    args = entry.get("args")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise MutError("The server's arguments are not a list of strings; edit the file by hand.")
    if _reduce_command(entry.get("command"), [])[1]:
        raise MutError("The credential is inside the command itself; edit the file by hand.")
    keep, moved = _split(server, args)
    if not moved:
        raise MutError("No credential argument to move.")
    env = entry.get("env", {})
    if not isinstance(env, dict):
        raise MutError("The server's env is not an object; edit the file by hand.")
    names = [n for n, _, _ in moved]
    if len(set(names)) != len(names):
        raise MutError("Two arguments map to the same env variable; edit the file by hand.")
    for name, value, _ in moved:
        if name in env and env[name] != value:
            raise MutError(f"env {name} already holds another value; edit the file by hand.")
        env[name] = value
    entry["args"] = keep
    entry["env"] = env
    return [(n, f) for n, _, f in moved]


def _dump(data: dict, text: str) -> bytes:
    """JSON in the file's own layout: the same indent (none for a one-line file), trailing newline kept."""
    m = re.match(r"\{[ \t]*\r?\n([ \t]+)\S", text)
    seps = None if m or re.search(r'":\s', text) else (",", ":")
    out = json.dumps(data, indent=m.group(1) if m else None, separators=seps, ensure_ascii=False)
    return (out + ("\n" if text.endswith("\n") else "")).encode("utf-8")


def _mcp_env(params: dict, home: Path) -> _Built:
    path, kind = _config(params, home)
    server = _str(params, "server")
    if len(server) > NAME_MAX:
        raise MutError("Invalid server name.")
    root = path.parent
    _guard(path, root, home)
    st = _lstat(path)
    if st is None or not stat.S_ISREG(st.st_mode):
        raise MutError("The config is not a regular file.")
    try:
        text = _read_file(path, home, MAX_JSON_BIG).decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise MutError("The config could not be parsed.") from None
    if not isinstance(data, dict):
        raise MutError("The config could not be parsed.")
    moved = _move(_entry(data, kind, server), server)
    detail = (f"server '{server}': move {', '.join(f for _, f in moved)} to env {', '.join(n for n, _ in moved)}"
              f" (the server must read it); backup -> {BACKUP_DISP}")
    data_out = _dump(data, text)
    return _Built(steps=[_Step("write", path, root, detail, data=data_out, mode=stat.S_IMODE(st.st_mode), backup=True)])
