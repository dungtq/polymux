from __future__ import annotations

import builtins
import json
import os
import subprocess
from pathlib import Path

import pytest

from pm.agents.inventory import read as agentsinv
from pm.agents.inventory.read import (
    agent_names,
    check_path,
    clear_cache,
    display_path,
    find_agent,
    get_inventory,
    is_blocked,
)

FAKE_KEY = "sk-test-FAKEKEY1234567890abcdefXYZ"
ENV_VALUES = (
    "env-secret-value-AAA111",
    "another-secret-BBB222",
    "tavily-secret-CCC333",
)


def w(p: Path, text: str, mode: int | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if mode:
        p.chmod(mode)
    return p


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    clear_cache()
    sk = h / ".agents/skills"
    w(
        sk / "alpha/SKILL.md",
        "---\nname: alpha\ndescription: Alpha skill does things\n---\n# Alpha\n",
    )
    w(
        sk / "beta/SKILL.md",
        "---\nname: beta\ndescription: >-\n  Folded beta\n  description\n---\nbody\n",
    )
    w(sk / "gamma/SKILL.md", "---\ndescription: >-\n---\n")
    (h / ".claude/skills").mkdir(parents=True)
    (h / ".claude/skills/alpha").symlink_to(sk / "alpha")
    w(h / ".claude/skills/own/SKILL.md", "# Own skill\n\nSomething local.\n")
    w(h / "agents/AGENTS.md", "# Canonical\nline two\n")
    w(
        h / "agents/agents-index.md",
        "# Index\nSee `~/.codex/AGENTS.md` and `~/.missing/thing.md`.\n",
    )
    (h / ".codex").mkdir()
    (h / ".codex/AGENTS.md").symlink_to(h / "agents/AGENTS.md")
    w(h / ".config/devin/AGENTS.md", "# Canonical\nDRIFTED\n")
    (h / ".claude/CLAUDE.md").symlink_to(h / "nowhere/AGENTS.md")
    w(h / ".cursor/rules/one.mdc", "---\ndescription: Cursor rule one\n---\nbody\n")
    (h / ".local/bin").mkdir(parents=True)
    (h / ".local/bin/gone").symlink_to(h / "nowhere")
    (h / ".local/bin/agent").symlink_to(h / "nowhere-agent")
    w(
        h / ".local/bin/codex",
        "#!/bin/sh\n# Fake codex\necho 'codex-cli 9.9.9'\n",
        0o755,
    )
    w(
        h / ".cursor/mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "ctx": {
                        "command": "npx",
                        "args": ["-y", "ctx-mcp", "--api-key", FAKE_KEY, "--extra"],
                        "env": {"EXA_API_KEY": ENV_VALUES[0], "OTHER": ENV_VALUES[1]},
                    },
                    "remote": {
                        "url": "https://user:pw@mcp.example.com/path?token=zzz",
                        "disabled": True,
                    },
                }
            }
        ),
    )
    w(
        h / ".codex/config.toml",
        '[mcp_servers.tav]\ncommand = "/usr/bin/node"\nargs = ["server.js"]\nenabled = false\n'
        f'[mcp_servers.tav.env]\nTAVILY_API_KEY = "{ENV_VALUES[2]}"\n',
    )
    w(
        h / ".claude.json",
        json.dumps(
            {
                "userID": "unrelated-state-USERID123",
                "projects": {"/x": {"mcpServers": {"proj": {"command": "nope"}}}},
                "mcpServers": {
                    "fs": {
                        "type": "stdio",
                        "command": "/opt/bin/fs-server",
                        "args": ["/data"],
                    }
                },
            }
        ),
    )
    w(h / ".agents-git/secret.txt", "TOP-SECRET-GIT")
    w(h / ".cfg/x", "TOP-SECRET-CFG")
    return h


def test_shapes_and_agents(home):
    inv = get_inventory(home=home, run_version=False)
    assert "claude" in agent_names(inv) and len(inv.agents) >= 8
    codex = find_agent(inv, "codex")
    assert codex and codex.installed and codex.binary == "~/.local/bin/codex"
    assert codex.config_dir == "~/.codex" and codex.version == ""
    assert codex.mcp_count == 1 and codex.mcp_config == "~/.codex/config.toml"
    assert find_agent(inv, "cursor").installed  # IDE dir only
    assert not find_agent(inv, "aider").installed or find_agent(inv, "aider").binary
    assert find_agent(inv, "nope") is None
    assert display_path(home / "a/b", home) == "~/a/b"
    assert inv.built_at > 0


def test_skills_enabled_for(home):
    inv = get_inventory(home=home, run_version=False)
    by = {(s.name, s.scope): s for s in inv.skills}
    assert by["alpha", "shared"].enabled_for == ("claude",)
    assert by["beta", "shared"].enabled_for == ()
    assert by["beta", "shared"].description == "Folded beta description"
    assert by["gamma", "shared"].description == "(no summary)"
    own = by["own", "agent"]
    assert own.owner == "claude" and own.description == "Own skill"
    assert find_agent(inv, "claude").skills_enabled == 2
    assert any("'beta'" in f.text and f.severity == "warn" for f in inv.findings)


def test_rules_status(home):
    inv = get_inventory(home=home, run_version=False)
    r = {(x.agent, x.name): x for x in inv.rules}
    assert r["", "AGENTS.md"].status == "ok" and r["", "AGENTS.md"].lines == 2
    assert (
        r["codex", "AGENTS.md"].mode == "symlink"
        and r["codex", "AGENTS.md"].status == "ok"
    )
    assert r["codex", "AGENTS.md"].target == "~/agents/AGENTS.md"
    assert (
        r["devin", "AGENTS.md"].mode == "copy"
        and r["devin", "AGENTS.md"].status == "drift"
    )
    assert r["claude", "CLAUDE.md"].status == "broken"
    assert r["cursor", "one.mdc"].summary == "Cursor rule one"
    texts = [f.text for f in inv.findings]
    assert any("drift" in t.lower() and "devin" in t for t in texts)
    assert any("Broken" in t and "CLAUDE.md" in t for t in texts)
    assert any("~/.missing/thing.md" in t for t in texts)
    assert not any("~/.codex/AGENTS.md" in t and "Stale" in t for t in texts)


def test_broken_shims(home):
    inv = get_inventory(home=home, run_version=False)
    assert any(
        f.text.startswith("Broken shim: ~/.local/bin/agent") for f in inv.findings
    )
    names = {s.name: s for s in inv.scripts}
    assert names["agent"].kind == "shim" and names["codex"].purpose == "Fake codex"


def test_mcp_safe(home):
    inv = get_inventory(home=home, run_version=False)
    m = {(x.agent, x.name): x for x in inv.mcp}
    ctx = m["cursor", "ctx"]
    assert ctx.command == "npx -y ctx-mcp --extra"
    assert ctx.env_names == ("EXA_API_KEY", "OTHER") and ctx.transport == "stdio"
    rem = m["cursor", "remote"]
    assert (
        rem.host == "mcp.example.com"
        and rem.transport == "http"
        and rem.enabled is False
    )
    tav = m["codex", "tav"]
    assert (
        tav.command == "node server.js"
        and tav.enabled is False
        and tav.env_names == ("TAVILY_API_KEY",)
    )
    assert ("claude", "fs") in m and ("claude", "proj") not in m
    assert m["claude", "fs"].command == "fs-server /data"
    assert any(
        "API key passed as a CLI argument" in f.text and "~/.cursor/mcp.json" in f.text
        for f in inv.findings
    )
    assert any("URL" in f.text and "remote" in f.text for f in inv.findings)


def test_no_secret_anywhere(home):
    inv = get_inventory(home=home, run_version=False)
    blob = repr(inv)
    for secret in (
        FAKE_KEY,
        *ENV_VALUES,
        "unrelated-state-USERID123",
        "TOP-SECRET",
        "user:pw",
        "zzz",
    ):
        assert secret not in blob


def test_guard_and_no_reads_under_blocked(home, monkeypatch):
    assert is_blocked(home / ".agents-git/secret.txt", home)
    assert is_blocked(home / ".cfg", home)
    assert check_path(home / ".cfg/x", home) is None
    assert check_path(home / ".agents/skills", home) is not None
    assert check_path(home.parent / "outside", home) is None
    (home / ".claude/skills/viagit").symlink_to(home / ".agents-git")
    (home / ".claude/linked.md").symlink_to(home / ".cfg/x")
    seen: list[str] = []
    real_open = builtins.open
    real_path_open = Path.open
    real_read_text = Path.read_text
    real_scandir = os.scandir

    def rec(p):
        seen.append(os.path.realpath(p))

    monkeypatch.setattr(
        builtins, "open", lambda f, *a, **k: (rec(f), real_open(f, *a, **k))[1]
    )
    monkeypatch.setattr(
        Path,
        "open",
        lambda self, *a, **k: (rec(self), real_path_open(self, *a, **k))[1],
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: (rec(self), real_read_text(self, *a, **k))[1],
    )
    monkeypatch.setattr(os, "scandir", lambda p=".": (rec(p), real_scandir(p))[1])
    get_inventory(home=home, run_version=False)
    assert seen
    bad = [s for s in seen if "/.agents-git" in s or "/.cfg" in s]
    assert bad == []


def test_rules_symlink_into_blocked_not_read(home):
    (home / ".agents-git/AGENTS.md").write_text("# secret rules\n")
    (home / ".config/devin/AGENTS.md").unlink()
    (home / ".config/devin/AGENTS.md").symlink_to(home / ".agents-git/AGENTS.md")
    inv = get_inventory(home=home, run_version=False)
    r = next(x for x in inv.rules if x.agent == "devin")
    assert r.mode == "symlink" and r.lines == 0 and "secret" not in repr(r)


def test_caps(home):
    for i in range(450):
        w(home / f".agents/skills/s{i:03d}/SKILL.md", f"# s{i}\n")
    inv = get_inventory(home=home, run_version=False)
    assert len(inv.skills) == 400
    for i in range(350):
        w(home / f"Cline/Rules/r{i:03d}.md", "# r\n")
    clear_cache()
    inv = get_inventory(home=home, run_version=False)
    assert len(inv.rules) <= 300


def test_bad_files_tolerated(home):
    w(home / ".cursor/mcp.json", "{not json")
    w(home / ".codex/config.toml", "= = bad [")
    (home / ".claude.json").write_bytes(b"\x00\x01\xff\xfe garbage")
    w(home / ".config/devin/mcp_config.json", "[1, 2]")
    inv = get_inventory(home=home, run_version=False)
    assert inv.mcp == ()
    assert any(f.text == "Could not parse ~/.cursor/mcp.json" for f in inv.findings)


def test_version_not_spawned_when_disabled(home, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("spawned")

    monkeypatch.setattr(subprocess, "run", boom)
    inv = get_inventory(home=home, run_version=False)
    assert find_agent(inv, "codex").version == ""


def test_version_probe_argv_and_timeout(home, monkeypatch):
    calls = []

    def fake(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(
            argv, 0, b"codex-cli 9.9.9\nsecond line\n", b""
        )

    monkeypatch.setattr(subprocess, "run", fake)
    inv = get_inventory(home=home, run_version=True)
    assert find_agent(inv, "codex").version == "codex-cli 9.9.9"
    assert calls
    for argv, kw in calls:
        assert isinstance(argv, list) and argv[1:] == ["--version"]
        assert kw.get("shell") is not True and kw.get("timeout") == 3.0
        assert set(kw["env"]) <= {"PATH", "HOME", "LANG", "NO_COLOR"}
    n = len(calls)
    agentsinv._cache.clear()
    get_inventory(home=home, run_version=True)
    assert len(calls) == n  # version cache (10 min)


def test_version_real_script(home):
    inv = get_inventory(home=home, run_version=True)
    assert find_agent(inv, "codex").version == "codex-cli 9.9.9"


def test_cache(home):
    a = get_inventory(home=home, run_version=False)
    assert get_inventory(home=home, run_version=False) is a
    clear_cache()
    assert get_inventory(home=home, run_version=False) is not a


def test_cache_is_keyed_by_run_version(tmp_path):
    """A cheap run_version=False read (mutation backend) must not serve a later versioned read."""
    from pm.agents.inventory import read as agentsinv

    agentsinv.clear_cache()
    a = agentsinv.get_inventory(home=tmp_path, run_version=False)
    b = agentsinv.get_inventory(home=tmp_path, run_version=True)
    c = agentsinv.get_inventory(home=tmp_path, run_version=True)
    assert a is not b and b is c
    agentsinv.clear_cache()


def test_background_read_does_not_probe_versions(home, monkeypatch):
    """The web page never waits on `--version`: quick build first, full build on a thread."""
    import time as _t

    from pm.agents.inventory import read as agentsinv

    agentsinv.clear_cache()
    calls = []

    def fake(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, b"codex-cli 9.9.9\n", b"")

    monkeypatch.setattr(subprocess, "run", fake)
    inv = get_inventory(home=home, background=True)
    assert not calls and find_agent(inv, "codex").version in ("", None)
    for _ in range(100):
        if calls and not agentsinv._refreshing:
            break
        _t.sleep(0.05)
    agentsinv._cache[(home, True)] = (_t.monotonic() - 1e6, agentsinv._cache[(home, True)][1])
    assert get_inventory(home=home, background=True) is not None
    for _ in range(100):
        if not agentsinv._refreshing:
            break
        _t.sleep(0.05)
    assert find_agent(get_inventory(home=home, background=True), "codex").version == "codex-cli 9.9.9"


def test_findings_kind_agent_fix(home):
    inv = get_inventory(home=home, run_version=False)
    by = {f.text: f for f in inv.findings}
    key = next(f for f in inv.findings if "API key passed as a CLI argument" in f.text)
    assert (key.kind, key.agent) == ("security", "cursor")
    assert key.fix == (("action", "mcp_env"), ("config", "~/.cursor/mcp.json"), ("server", "ctx"))
    url = next(f for f in inv.findings if "URL" in f.text)
    assert url.kind == "security" and url.fix == ()
    stale = by["Stale entry in ~/agents/agents-index.md: ~/.missing/thing.md does not exist"]
    assert stale.kind == "index" and stale.fix == (("action", "index_prune"), ("lines", "~/.missing/thing.md"))
    drift = next(f for f in inv.findings if f.text.startswith("Rules drift"))
    assert drift.kind == "rules" and drift.agent == "devin"
    assert next(f for f in inv.findings if f.text.startswith("Broken shim")).kind == "shims"
    assert next(f for f in inv.findings if "'beta'" in f.text).kind == "skills"
    assert all(f.kind in ("security", "rules", "skills", "index", "shims", "notes") for f in inv.findings)
    blob = repr(inv.findings)
    assert FAKE_KEY not in blob and not any(v in blob for v in ENV_VALUES)


def test_findings_fix_for_installed_agents(home):
    """A drifted single rules file of an installed agent gets a rules_sync fix; missing shared skills a skills_link."""
    from pm.agents.inventory.read import _rule_findings, _skill_findings
    from pm.core.models import AgentInfo, RuleInfo, SkillInfo

    ags = [AgentInfo("codex", "Codex", True, rules_file="~/.codex/AGENTS.md", skills_dir="~/.codex/skills")]
    rules = [RuleInfo("AGENTS.md", "codex", "~/.codex/AGENTS.md", "", "copy", "", 2, "drift"),
             RuleInfo("x.md", "codex", "~/.codex/rules/x.md", "", "copy", "", 2, "drift")]
    f1, f2 = _rule_findings(rules, ags)
    assert f1.fix == (("action", "rules_sync"), ("source", "canonical"), ("targets", "codex"))
    assert f2.fix == ()
    skills = [SkillInfo(n, "", f"~/.agents/skills/{n}", "shared", "", e)
              for n, e in (("a", ("codex",)), ("b", ()), ("c", ()))]
    f = next(x for x in _skill_findings(skills, ags) if x.agent == "codex")
    assert f.kind == "skills" and f.fix == (("action", "skills_link"), ("agent", "codex"), ("skills", "b"), ("skills", "c"))
