"""Read-only inventory of the coding agents installed on this host.

Fixed paths only, no shell, size and count caps, symlink-aware. Never reads inside
~/.agents-git or ~/.cfg, never returns secret values (MCP env is names only, commands
are reduced and credential-looking arguments are dropped).

The collectors live in sibling modules (``specs``, ``fsread``, ``mcp``, ``skills``,
``rules``, ``scripts``); this module builds and caches the Inventory and re-exports the
helpers other packages use as ``read.<name>``.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pm.core.models import (
    AgentInfo,
    Finding,
    Inventory,
    McpServerInfo,
    RuleInfo,
    ScriptInfo,
    SkillInfo,
)
from pm.core.proc import ProcError, run_argv

from .fsread import _clip as _clip
from .fsread import _is_dir as _is_dir
from .fsread import _is_file as _is_file
from .fsread import _lexists as _lexists
from .fsread import _list_dir as _list_dir
from .fsread import _read as _read
from .fsread import _resolve as _resolve
from .fsread import _script_purpose as _script_purpose
from .fsread import _summary as _summary
from .fsread import _under as _under
from .fsread import _unquote as _unquote
from .fsread import check_path as check_path
from .fsread import display_path as display_path
from .fsread import is_blocked as is_blocked
from .mcp import _enabled as _enabled
from .mcp import _env_names as _env_names
from .mcp import _header_cred_literal as _header_cred_literal
from .mcp import _load_mcp_file as _load_mcp_file
from .mcp import _looks_like_token as _looks_like_token
from .mcp import _name_is_cred as _name_is_cred
from .mcp import _parse_servers as _parse_servers
from .mcp import _reduce_command as _reduce_command
from .mcp import _secret_args as _secret_args
from .mcp import _short as _short
from .mcp import _transport as _transport
from .mcp import _url_has_cred as _url_has_cred
from .rules import _collect_rules as _collect_rules
from .rules import _count_lines as _count_lines
from .rules import _digest as _digest
from .rules import _rule_for_file as _rule_for_file
from .scripts import _collect_scripts as _collect_scripts
from .scripts import _hook_texts as _hook_texts
from .scripts import _stale_index_findings as _stale_index_findings
from .skills import _collect_skills as _collect_skills
from .skills import _plugin_skill_dirs as _plugin_skill_dirs
from .skills import _scan_skill_tree_entry as _scan_skill_tree_entry
from .skills import _scan_skill_tree_flat as _scan_skill_tree_flat
from .skills import _skill_from_dir as _skill_from_dir
from .specs import ALL_BIN_NAMES as ALL_BIN_NAMES
from .specs import BLOCKED as BLOCKED
from .specs import CRED_WORDS as CRED_WORDS
from .specs import MAX_DEPTH as MAX_DEPTH
from .specs import MAX_DIR_ENTRIES as MAX_DIR_ENTRIES
from .specs import MAX_JSON_BIG as MAX_JSON_BIG
from .specs import MAX_MCP as MAX_MCP
from .specs import MAX_READ as MAX_READ
from .specs import MAX_RULES as MAX_RULES
from .specs import MAX_SCRIPTS as MAX_SCRIPTS
from .specs import MAX_SKILLS as MAX_SKILLS
from .specs import NO_SUMMARY as NO_SUMMARY
from .specs import SPEC_BY_NAME as SPEC_BY_NAME
from .specs import SPECS as SPECS
from .specs import YAML_MARKERS as YAML_MARKERS
from .specs import _Spec as _Spec

log = logging.getLogger("pm.agentsinv")

MAX_LINK = 100  # skills in one skills_link fix (mutate_skills.MAX_LINK)

CACHE_TTL = 30.0
VERSION_TTL = 600.0
VERSION_TIMEOUT = 3.0

_cache: dict[tuple[Path, bool], tuple[float, Inventory]] = {}
_cache_lock = threading.Lock()
_version_cache: dict[str, tuple[float, str]] = {}
_version_lock = threading.Lock()


def clear_cache() -> None:
    """Drop the inventory and version caches."""
    with _cache_lock:
        _cache.clear()
    with _version_lock:
        _version_cache.clear()


def agent_names(inv: Inventory) -> list[str]:
    return [a.name for a in inv.agents]


def find_agent(inv: Inventory, name: str) -> AgentInfo | None:
    for a in inv.agents:
        if a.name == name:
            return a
    return None


# ---------------------------------------------------------------- binaries and versions


def _bin_candidates(spec: _Spec, home: Path) -> list[Path]:
    dirs = [home / ".local/bin", Path("/usr/local/bin"), Path("/usr/bin")]
    out = [d / b for b in spec.bins for d in dirs]
    out += [home / rel for rel in spec.bin_paths]
    installs = home / ".local/share/mise/installs"
    for tool in _list_dir(installs, home)[:200]:
        for ver in _list_dir(tool, home)[:20]:
            out += [ver / "bin" / b for b in spec.bins]
    for b in spec.bins:
        pat = f"{b}-linux-*"
        for p in _list_dir(home / ".cline", home):
            if fnmatch.fnmatch(p.name, pat):
                out.append(p)
    return out


def _find_binary(spec: _Spec, home: Path, findings: list[Finding]) -> str:
    for c in _bin_candidates(spec, home):
        if _is_file(c) and os.access(c, os.X_OK):
            return str(c)
        if c.is_symlink() and not c.exists():
            findings.append(
                Finding("warn", f"Broken shim: {display_path(c, home)} points nowhere", "shims", spec.name)
            )
    return ""


def _probe_version(binary: str, home: Path) -> str:
    now = time.monotonic()
    with _version_lock:
        hit = _version_cache.get(binary)
        if hit and now - hit[0] < VERSION_TTL:
            return hit[1]
    env = {
        "PATH": f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
    }
    ver = ""
    try:
        r = run_argv([binary, "--version"], timeout=VERSION_TIMEOUT, env=env)
        out = (r.stdout or b"") + (r.stderr or b"") if r.returncode == 0 else b""
        first = out[:2048].decode("utf-8", "replace").strip().splitlines()
        ver = first[0].strip()[:80] if first else ""
    except (ProcError, ValueError):
        ver = ""
    with _version_lock:
        _version_cache[binary] = (time.monotonic(), ver)
    return ver


# ---------------------------------------------------------------- build


def _more(items: list[Finding], extra: int, what: str, kind: str = "") -> list[Finding]:
    return items + ([Finding("info", f"{extra} more {what}", kind)] if extra > 0 else [])


def _dedup(findings: list[Finding]) -> list[Finding]:
    out: list[Finding] = []
    for f in findings:
        if f not in out:
            out.append(f)
    return out


def _detect_binaries(
    specs: list[_Spec], home: Path, findings: list[Finding]
) -> tuple[dict[str, str], set[str]]:
    binaries: dict[str, str] = {}
    installed: set[str] = set()
    for s in specs:
        b = _find_binary(s, home, findings)
        binaries[s.name] = b
        if b or any(_is_dir(home / d) for d in s.ide_dirs):
            installed.add(s.name)
    return binaries, installed


def _probe_versions(binaries: dict[str, str], home: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    todo = {n: b for n, b in binaries.items() if b}
    if todo:
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as ex:
            futs = {n: ex.submit(_probe_version, b, home) for n, b in todo.items()}
            for n, f in futs.items():
                try:
                    versions[n] = f.result(timeout=VERSION_TIMEOUT + 2)
                except Exception:  # noqa: BLE001
                    versions[n] = ""
    return versions


def _collect_mcp(
    home: Path, specs: list[_Spec], findings: list[Finding]
) -> tuple[list[McpServerInfo], dict[str, str]]:
    """(servers sorted and capped, first existing config path per agent)."""
    mcp: list[McpServerInfo] = []
    mcp_cfg: dict[str, str] = {}
    for s in specs:
        for rel, kind in s.mcp:
            servers, exists = _load_mcp_file(home / rel, kind, s.name, home, findings)
            if exists and s.name not in mcp_cfg:
                mcp_cfg[s.name] = display_path(home / rel, home)
            mcp += servers
    mcp.sort(key=lambda m: (m.agent, m.name, m.config_path))
    return mcp[:MAX_MCP], mcp_cfg


def _agent_infos(
    home: Path,
    specs: list[_Spec],
    installed: set[str],
    binaries: dict[str, str],
    versions: dict[str, str],
    mcp: list[McpServerInfo],
    mcp_cfg: dict[str, str],
    skills: list[SkillInfo],
) -> list[AgentInfo]:
    mcp_count = {s.name: sum(1 for m in mcp if m.agent == s.name) for s in specs}
    skill_count = {
        s.name: sum(1 for k in skills if s.name in k.enabled_for or k.owner == s.name)
        for s in specs
    }
    agents: list[AgentInfo] = []
    for s in specs:
        cfg_dir = home / s.config_dir if s.config_dir else None
        skills_dir = ""
        for rel in s.skill_dirs[:1]:
            if _is_dir(home / rel):
                skills_dir = display_path(home / rel, home)
        rules_file = ""
        if s.rules_file:
            rules_file = display_path(home / s.rules_file, home)
        elif s.rules_label:
            rules_file = s.rules_label
        agents.append(
            AgentInfo(
                name=s.name,
                label=s.label,
                installed=s.name in installed,
                version=versions.get(s.name, ""),
                binary=display_path(Path(binaries[s.name]), home)
                if binaries[s.name]
                else "",
                config_dir=display_path(cfg_dir, home)
                if cfg_dir and _is_dir(cfg_dir)
                else "",
                rules_file=rules_file,
                skills_dir=skills_dir,
                mcp_config=mcp_cfg.get(s.name, ""),
                skills_enabled=skill_count[s.name],
                mcp_count=mcp_count[s.name],
            )
        )
    return agents


def _skill_findings(skills: list[SkillInfo], agents: list[AgentInfo]) -> list[Finding]:
    unl = [k for k in skills if k.scope == "shared" and not k.enabled_for]
    out = _more(
        [
            Finding(
                "warn",
                f"Skill '{k.name}' in ~/.agents/skills is not linked into any agent",
                "skills",
            )
            for k in unl[:10]
        ],
        len(unl) - 10,
        "unlinked skills",
        "skills",
    )
    shared_all = [k for k in skills if k.scope == "shared"]
    for a in agents:
        sp = SPEC_BY_NAME[a.name]
        if a.installed and sp.skill_dirs and shared_all:
            miss = [k for k in shared_all if a.name not in k.enabled_for]
            if miss and len(miss) < len(shared_all):
                fix = (
                    (("action", "skills_link"), ("agent", a.name), *(("skills", k.name) for k in miss))
                    if a.skills_dir and len(miss) <= MAX_LINK
                    else ()
                )
                out.append(
                    Finding(
                        "info",
                        f"{len(miss)} shared skills not linked into {a.label}",
                        "skills",
                        a.name,
                        fix,
                    )
                )
    return out


def _rule_findings(rules: list[RuleInfo], agents: list[AgentInfo] = ()) -> list[Finding]:
    """Drift / broken / missing rules files; a drifted single rules file gets a sync-from-canonical fix."""
    single = {a.name: a.rules_file for a in agents if a.installed and a.rules_file}
    out: list[Finding] = []
    for r in rules:
        if r.status == "drift":
            fix = (
                (("action", "rules_sync"), ("source", "canonical"), ("targets", r.agent))
                if r.agent and single.get(r.agent) == r.path
                else ()
            )
            out.append(
                Finding(
                    "warn",
                    f"Rules drift: {r.path} differs from the canonical ~/agents/AGENTS.md",
                    "rules",
                    r.agent,
                    fix,
                )
            )
        elif r.status == "broken":
            out.append(Finding("warn", f"Broken rules file or symlink: {r.path}", "rules", r.agent))
        elif r.status == "missing" and r.agent:
            out.append(Finding("info", f"Rules file missing: {r.path}", "rules", r.agent))
    return out


def _findings(
    home: Path,
    findings: list[Finding],
    skills: list[SkillInfo],
    rules: list[RuleInfo],
    agents: list[AgentInfo],
    scripts: list[ScriptInfo],
) -> list[Finding]:
    """All findings in report order, de-duplicated by (severity, text)."""
    findings = findings + _skill_findings(skills, agents) + _rule_findings(rules, agents)
    findings += _stale_index_findings(home)
    no_mcp = [a.label for a in agents if a.installed and a.mcp_count == 0]
    if no_mcp:
        findings.append(
            Finding(
                "info", "Installed agents with no MCP servers: " + ", ".join(no_mcp), "notes"
            )
        )
    for sc in scripts:
        if sc.purpose == "Broken symlink":
            findings.append(Finding("warn", f"Broken shim: {sc.path}", "shims"))
    seen: set[tuple[str, str]] = set()
    uniq: list[Finding] = []
    for f in findings:
        if (f.severity, f.text) not in seen:
            seen.add((f.severity, f.text))
            uniq.append(f)
    return uniq


def _build(home: Path, run_version: bool) -> Inventory:
    findings: list[Finding] = []
    specs = list(SPECS)
    binaries, installed = _detect_binaries(specs, home, findings)
    versions = _probe_versions(binaries, home) if run_version else {}
    skills, _ = _collect_skills(home, specs, installed)
    rules = _collect_rules(home, specs, installed)
    hook_texts = _hook_texts(home, specs)
    scripts = _collect_scripts(home, specs, hook_texts)
    mcp, mcp_cfg = _collect_mcp(home, specs, findings)
    findings = _dedup(findings)
    agents = _agent_infos(
        home, specs, installed, binaries, versions, mcp, mcp_cfg, skills
    )
    uniq = _findings(home, findings, skills, rules, agents, scripts)
    return Inventory(
        agents=tuple(agents),
        skills=tuple(skills),
        rules=tuple(rules),
        scripts=tuple(scripts),
        mcp=tuple(mcp),
        findings=tuple(uniq),
        built_at=time.time(),
    )


_refreshing: set[tuple[Path, bool]] = set()


def _refresh(key: tuple[Path, bool]) -> None:
    """Rebuild one cache entry (with version probes); runs on a background thread."""
    try:
        try:
            inv = _build(key[0], key[1])
        except Exception:  # noqa: BLE001
            log.warning("agent inventory failed", exc_info=False)
            return
        with _cache_lock:
            _cache[key] = (time.monotonic(), inv)
    finally:
        with _cache_lock:
            _refreshing.discard(key)


def _refresh_async(key: tuple[Path, bool]) -> None:
    with _cache_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)
    threading.Thread(target=_refresh, args=(key,), name="agentsinv-refresh", daemon=True).start()


def get_inventory(
    settings: object = None, *, home: Path | None = None, run_version: bool = True, background: bool = False
) -> Inventory:
    """Inventory of the agents under home (default: the real home), cached for CACHE_TTL.

    ``background=True`` (the web page) never waits for ``--version`` probes (about 1 s of
    subprocesses): a stale entry is returned at once and rebuilt on a thread; with no entry a
    quick build without versions is returned while the full one is built on a thread.
    """
    h = Path(home) if home is not None else Path.home()
    key = (h, bool(run_version))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit:
        if now - hit[0] < CACHE_TTL:
            return hit[1]
        if background:
            _refresh_async(key)
            return hit[1]
    try:
        inv = _build(h, run_version and not background)
    except Exception:  # noqa: BLE001
        log.warning("agent inventory failed", exc_info=False)
        inv = Inventory(built_at=time.time())
    if run_version and background:
        with _cache_lock:
            _cache[key] = (now - CACHE_TTL, inv)  # born stale: next read refreshes
        _refresh_async(key)
    else:
        with _cache_lock:
            _cache[key] = (time.monotonic(), inv)
    return inv


__all__ = [
    "agent_names",
    "check_path",
    "clear_cache",
    "display_path",
    "find_agent",
    "get_inventory",
    "is_blocked",
]
