"""Agent specs (fixed paths per installed coding agent) and the inventory limits."""

from __future__ import annotations

from dataclasses import dataclass

MAX_SKILLS = 400
MAX_RULES = 300
MAX_SCRIPTS = 100
MAX_MCP = 200
MAX_READ = 64 * 1024
MAX_JSON_BIG = 4 * 1024 * 1024
MAX_DIR_ENTRIES = 2000
MAX_DEPTH = 3
BLOCKED = (".agents-git", ".cfg")
NO_SUMMARY = "(no summary)"
CRED_WORDS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "auth",
    "bearer",
    "credential",
)
YAML_MARKERS = {">", ">-", ">+", "|", "|-", "|+"}


@dataclass(frozen=True)
class _Spec:
    name: str
    label: str
    bins: tuple[str, ...] = ()
    bin_paths: tuple[str, ...] = ()
    ide_dirs: tuple[str, ...] = ()
    config_dir: str = ""
    rules_file: str = ""
    rules_dirs: tuple[tuple[str, str], ...] = ()
    skill_dirs: tuple[str, ...] = ()
    direct_shared: bool = False
    mcp: tuple[tuple[str, str], ...] = ()
    hook_dirs: tuple[str, ...] = ()
    hook_cfgs: tuple[str, ...] = ()
    rules_label: str = ""


SPECS: tuple[_Spec, ...] = (
    _Spec(
        "claude",
        "Claude Code",
        ("claude",),
        (),
        (),
        ".claude",
        ".claude/CLAUDE.md",
        (),
        (".claude/skills",),
        False,
        ((".claude.json", "claude"),),
        (),
        (".claude/settings.json",),
    ),
    _Spec(
        "codex",
        "Codex",
        ("codex",),
        (".codex/packages/standalone/current/bin/codex",),
        (),
        ".codex",
        ".codex/AGENTS.md",
        (),
        (".codex/skills",),
        False,
        ((".codex/config.toml", "toml"),),
        (),
        (".codex/hooks.json",),
    ),
    _Spec(
        "devin",
        "Devin",
        ("devin",),
        (),
        (),
        ".config/devin",
        ".config/devin/AGENTS.md",
        (),
        (".config/devin/skills",),
        False,
        ((".config/devin/mcp_config.json", "json"),),
        (),
        (),
    ),
    _Spec(
        "cursor",
        "Cursor",
        ("cursor-agent", "cursor", "agent"),
        (),
        (".cursor",),
        ".cursor",
        "",
        ((".cursor/rules", ".mdc"), (".cursor/rules", ".md")),
        (".cursor/skills", ".cursor/skills-cursor"),
        False,
        ((".cursor/mcp.json", "json"),),
        (".cursor/hooks",),
        (".cursor/hooks.json",),
        ".cursor/rules/*.mdc",
    ),
    _Spec(
        "cline",
        "Cline",
        ("cline",),
        (),
        (".cline",),
        ".cline",
        "",
        (("Cline/Rules", ".md"), ("Documents/Cline/Rules", ".md")),
        (),
        False,
        ((".cline/data/settings/cline_mcp_settings.json", "json"),),
        ("Cline/Hooks", "Documents/Cline/Hooks"),
        (),
        "~/Cline/Rules/*.md",
    ),
    _Spec(
        "copilot",
        "GitHub Copilot",
        ("copilot",),
        (),
        (".copilot",),
        ".copilot",
        "dev/.github/copilot-instructions.md",
        (),
        (),
        False,
        ((".copilot/mcp-config.json", "json"),),
        (".copilot/hooks",),
        (),
    ),
    _Spec(
        "windsurf",
        "Windsurf",
        ("windsurf",),
        (),
        (".codeium/windsurf",),
        ".codeium/windsurf",
        "",
        (),
        (),
        True,
        ((".codeium/windsurf/mcp_config.json", "json"),),
        (),
        (".codeium/windsurf/hooks.json",),
    ),
    _Spec(
        "antigravity",
        "Antigravity (agy)",
        ("agy",),
        (),
        (".gemini/antigravity",),
        ".gemini",
        "",
        (),
        (),
        False,
        (
            (".gemini/config/mcp_config.json", "json"),
            (".gemini/antigravity/mcp_config.json", "json"),
        ),
        (),
        (),
    ),
    _Spec(
        "gemini",
        "Gemini CLI",
        ("gemini",),
        (),
        (),
        ".gemini",
        "",
        (),
        (),
        False,
        (),
        (),
        (),
    ),
    _Spec("aider", "Aider", ("aider",), (), (), "", "", (), (), False, (), (), ()),
    _Spec(
        "opencode",
        "OpenCode",
        ("opencode",),
        (),
        (),
        ".config/opencode",
        "",
        (),
        (),
        False,
        (),
        (),
        (),
    ),
)
SPEC_BY_NAME = {s.name: s for s in SPECS}
ALL_BIN_NAMES = {b: s.name for s in SPECS for b in s.bins}
