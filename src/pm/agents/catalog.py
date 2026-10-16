"""Models each agent CLI reports for this account (asked from the CLI itself, cached).

A catalog row is ``(id, label, family, efforts)``: the launch form shows one entry per family
(searchable) and a Variant select with the ids inside it; ``efforts`` is a comma list of the
reasoning efforts the CLI accepts for that model (claude --effort, codex model_reasoning_effort).
An agent whose listing fails shows no presets (custom id only). claude's ``--help`` only gives
aliases, so the full ids and the ``[1m]`` forms are added from CLAUDE_FULL_IDS below.
Every call is an argv list with a timeout; results are cached on disk for CACHE_TTL.
"""

from __future__ import annotations

import json
import re
import tempfile
import time

from pm.agents.launch import LaunchError, agent_binary, extra_models
from pm.core.config import Settings
from pm.core.models import AgentKind
from pm.core.proc import ProcError, run_argv
from pm.security.mask import strip_ansi

CACHE_TTL = 6 * 3600
CACHE_VERSION = 2
# Full ids Claude Code accepts next to its aliases (the CLI does not list them).
CLAUDE_FULL_IDS = {
    "fable": "claude-fable-5-1",
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-4-5-20251001",
}
_NO_1M = {"haiku"}
_VARIANT_WORDS = {"low", "medium", "high", "xhigh", "max", "fast", "thinking", "none", "extra"}
TIMEOUT = 20
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def _run(argv: list[str]) -> str:
    # stdout goes to a file, not a pipe: claude --help is cut at the 16 KiB pipe chunk otherwise
    with tempfile.TemporaryFile("w+") as out:
        r = run_argv(argv, stdout=out, timeout=TIMEOUT)
        if r.returncode != 0:
            raise OSError(f"{argv[0]} exited {r.returncode}")
        out.seek(0)
        return strip_ansi(out.read())


def parse_claude(help_text: str) -> list[tuple]:
    """Aliases quoted in the --model help ('fable', 'opus', or 'sonnet')."""
    m = re.search(r"--model <model>(.*?)(?=\n\s+-[a-z-]+[ ,]|\Z)", help_text, re.S)
    words = re.findall(r"'([a-z][a-z0-9-]*)'", m.group(1)) if m else []
    return [(w, f"{w} (latest)") for w in words]


def parse_claude_efforts(help_text: str) -> list[str]:
    m = re.search(r"--effort <level>[^(]*\(([a-z, ]+)\)", help_text)
    return [e.strip() for e in m.group(1).split(",") if e.strip()] if m else []


def claude_rows(help_text: str) -> list[tuple]:
    """alias, alias[1m], full id, full id[1m] per family; unknown aliases from --help come last."""
    eff = ",".join(parse_claude_efforts(help_text))
    seen = [a for a, _ in parse_claude(help_text)]
    fams = list(dict.fromkeys([*seen, *CLAUDE_FULL_IDS, "opusplan"]))
    rows = []
    for f in fams:
        big = f not in _NO_1M and f != "opusplan"
        rows.append((f, f"{f} (latest)", f, eff))
        if big:
            rows.append((f + "[1m]", f"{f} (latest), 1M context", f, eff))
        if f in CLAUDE_FULL_IDS:
            rows.append((CLAUDE_FULL_IDS[f], CLAUDE_FULL_IDS[f] + " (pinned)", f, eff))
            if big:
                rows.append((CLAUDE_FULL_IDS[f] + "[1m]", CLAUDE_FULL_IDS[f] + ", 1M context", f, eff))
    return rows


def parse_codex(text: str) -> list[tuple]:
    out = []
    for m in json.loads(text).get("models", []):
        if m.get("visibility") == "list" and _ID.match(m.get("slug", "")):
            lv = [x.get("effort") for x in m.get("supported_reasoning_levels") or []
                  if isinstance(x, dict) and isinstance(x.get("effort"), str)]
            out.append((m["slug"], m.get("display_name") or m["slug"], m["slug"], ",".join(lv)))
    return out


def parse_cursor(text: str) -> list[tuple]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^(\S+) - (.+)$", line.strip())
        if m and _ID.match(m.group(1)):
            out.append((m.group(1), m.group(2).strip()))
    return out


def cursor_base(model_id: str) -> str:
    """claude-opus-5-5-high-fast -> claude-opus-5-5 (effort / fast / thinking words stripped)."""
    parts = model_id.split("-")
    while len(parts) > 1 and parts[-1] in _VARIANT_WORDS:
        parts.pop()
    return "-".join(parts)


def group_cursor(rows: list[tuple]) -> list[tuple]:
    return [(i, label, cursor_base(i), "") for i, label in rows]


def parse_devin(text: str) -> list[tuple[str, str, str]]:
    """(id, label, family); a family's aliases (e.g. 'opus') come first."""
    out, fam = [], ""
    for line in text.splitlines():
        if line and not line[0].isspace():
            m = re.match(r"^(.+?) \(", line)
            fam = m.group(1) if m else ""
            continue
        t = line.strip()
        if t.startswith("aliases:"):
            out += [(a.strip(), f"{a.strip()} (alias)", fam) for a in t[8:].split(",")
                    if _ID.match(a.strip())]
            continue
        m = re.match(r"^(\S+)\s{2,}(.+?)(?:\s+\[.*)?$", line.strip())
        if m and _ID.match(m.group(1)) and m.group(1) == m.group(1).lower():
            out.append((m.group(1), m.group(2).strip(), fam))  # UPPER_CASE ids are internal enums
    return out


_SOURCES = {
    AgentKind.CLAUDE: (["--help"], claude_rows),
    AgentKind.CODEX: (["debug", "models"], parse_codex),
    AgentKind.CURSOR: (["models"], lambda t: group_cursor(parse_cursor(t))),
    AgentKind.DEVIN: (["models", "list"], parse_devin),
}

# programs whose TUI takes `/model <id>` (codex's /model has no argument)
SWITCHABLE = (AgentKind.CLAUDE, AgentKind.CURSOR, AgentKind.DEVIN)


def _norm(m) -> tuple:
    return (tuple(m) + ("", "", ""))[:4] if len(m) >= 2 else (m[0], m[0], "", "")


def fetch(settings: Settings, kind: AgentKind) -> list[tuple]:
    args, parse = _SOURCES[kind]
    return [_norm(m) for m in parse(_run([agent_binary(settings, kind), *args]))]


def models_for(settings: Settings, kind: AgentKind, now: float | None = None) -> list[tuple]:
    now = time.time() if now is None else now
    path = settings.state_dir / "agent-models.json"
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(kind.value)
    if isinstance(hit, dict) and hit.get("v") == CACHE_VERSION and now - hit.get("at", 0) < CACHE_TTL:
        return [_norm(x) for x in hit["models"]]
    try:
        models = fetch(settings, kind)
    except (OSError, ProcError, ValueError, LaunchError):
        old = hit.get("models") if isinstance(hit, dict) else None
        return [_norm(x) for x in old or []]
    cache[kind.value] = {"at": now, "v": CACHE_VERSION, "models": models}
    try:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache))
    except OSError:
        pass
    return models


def families(models: list[tuple]) -> list[dict]:
    """Rows grouped by family (CLI order): [{name, variants: [[id, label]], efforts: [..]}]."""
    out: dict[str, dict] = {}
    for mid, label, fam, eff in models:
        f = out.setdefault(fam or mid, {"name": fam or mid, "variants": [], "efforts": []})
        f["variants"].append([mid, label])
        for e in eff.split(","):
            if e and e not in f["efforts"]:
                f["efforts"].append(e)
    return list(out.values())


def kind_models(settings: Settings, kind: AgentKind) -> list[tuple]:
    rows = models_for(settings, kind)
    have = {r[0] for r in rows}
    return rows + [(m, m, "configured", "") for m in extra_models(settings, kind) if m not in have]


def all_models(settings: Settings, kinds: list[str]) -> dict[str, list[tuple]]:
    return {k: kind_models(settings, AgentKind(k)) for k in kinds if AgentKind(k) in _SOURCES}
