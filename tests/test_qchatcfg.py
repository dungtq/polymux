import json
import stat
import sys

import pytest

from pm.core.config import Settings
from pm.qchat import cfg as q


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    dev = home / "dev"
    (dev / "proj").mkdir(parents=True)
    s = Settings(dev_root=dev, extra_projects=(), state_dir=tmp_path / "state")
    s.state_dir.mkdir()
    return s, home


def _claude(home, data):
    (home / ".claude.json").write_text(json.dumps(data))


def test_defaults_and_save(env):
    s, _ = env
    assert q.load(s) == q.DEFAULTS
    cfg = q.save(s, {"max_turns": 9999, "timeout_s": 1, "read_tools": "Read, Grep",
                     "mcp_enabled": {"a": False}})
    assert cfg["max_turns"] == 200 and cfg["timeout_s"] == 10
    assert cfg["read_tools"] == ["Read", "Grep"]
    assert q.load(s)["mcp_enabled"] == {"a": False}
    q.save(s, {"mcp_enabled": {"b": True}})
    assert q.load(s)["mcp_enabled"] == {"a": False, "b": True}
    assert stat.S_IMODE((s.state_dir / "qchat.json").stat().st_mode) == 0o600


def test_save_rejects(env):
    s, _ = env
    for bad in ({"nope": 1}, {"default_profile": "x"}, {"full_permission_mode": "x"},
                {"read_tools": ["bad;rm"]}, {"profiles": {"zzz": True}}, {"max_turns": "abc"}):
        with pytest.raises(ValueError):
            q.save(s, bad)
    with pytest.raises(ValueError):
        q.save(s, {"profiles": {"none": False, "read": False, "full": False}})
    assert q.save(s, {"profiles": {"none": False}})["default_profile"] == "read"  # moved off disabled
    with pytest.raises(ValueError):
        q.save(s, {"default_profile": "none"})


def test_corrupt_file_falls_back(env):
    s, _ = env
    (s.state_dir / "qchat.json").write_text("{not json")
    assert q.load(s) == q.DEFAULTS


def test_discover_mcp_secret_free(env):
    s, home = env
    _claude(home, {
        "mcpServers": {
            "web": {"type": "http", "url": "https://u:pw@h.example:8443/mcp?token=SECRETQ",
                    "headers": {"Authorization": "Bearer SECRETH"}},
            "loc": {"command": "npx", "args": ["-y", "pkg", "--api-key", "SECRETA"],
                    "env": {"K": "SECRETE"}},
        },
        "projects": {str(home / "dev/proj"): {"mcpServers": {"pj": {"command": "x"}}},
                     "/elsewhere": {"mcpServers": {"hidden": {"command": "y"}}}},
        "claudeAiMcpEverConnected": ["claude.ai Gmail"],
    })
    out = q.discover_mcp(s)
    names = {m["name"]: m for m in out}
    assert set(names) == {"web", "loc", "pj", "claude.ai Gmail"}
    assert names["pj"]["source"] == "project:proj"
    assert names["claude.ai Gmail"]["type"] == "connector"
    assert not names["claude.ai Gmail"]["testable"] and not names["claude.ai Gmail"]["enabled"]
    assert names["web"]["target"] == "https://h.example:8443/mcp"
    blob = json.dumps(out)
    for secret in ("SECRETQ", "SECRETH", "SECRETA", "SECRETE", "pw@"):
        assert secret not in blob


def test_add_remove_manual(env):
    s, _ = env
    q.add_mcp(s, name="m1", type="http", target="https://x.example/mcp",
              headers="Authorization: Bearer TOPSECRET\nX-A: b")
    out = q.discover_mcp(s)
    assert out[0]["manual"] and out[0]["source"] == "manual"
    assert "TOPSECRET" not in json.dumps(out)
    p = s.state_dir / "qchat-mcp.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert "TOPSECRET" in p.read_text()
    assert "TOPSECRET" not in (s.state_dir / "qchat.json").read_text() if (s.state_dir / "qchat.json").exists() else True
    with pytest.raises(ValueError):
        q.add_mcp(s, name="m1", type="http", target="https://y.example")
    assert q.remove_mcp(s, "m1") and not q.remove_mcp(s, "m1")
    assert q.discover_mcp(s) == []


@pytest.mark.parametrize("kw", [
    dict(name="bad name", type="http", target="https://x"),
    dict(name="a", type="ftp", target="x"),
    dict(name="a", type="http", target="https://u:p@x.example"),
    dict(name="a", type="http", target="https://x.example/?token=abcdef123456"),
    dict(name="a", type="http", target="file:///etc/passwd"),
    dict(name="a", type="http", target="https://x.example", headers="nocolon"),
    dict(name="a", type="stdio", target="cmd --api-key abc"),
    dict(name="a", type="stdio", target="cmd", headers="A: b"),
    dict(name="a", type="stdio", target=""),
])
def test_add_rejects(env, kw):
    with pytest.raises(ValueError):
        q.add_mcp(env[0], **kw)


def test_config_file(env):
    s, home = env
    _claude(home, {"mcpServers": {"web": {"type": "http", "url": "https://h.example/m",
                                          "headers": {"Authorization": "Bearer SECRETH"}},
                                  "off": {"command": "x"}}})
    q.save(s, {"mcp_enabled": {"off": False}})
    assert q.mcp_config_file(s, ["off", "ghost"]) is None
    p = q.mcp_config_file(s, ["web", "off", "web"])
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert list(json.loads(p.read_text())["mcpServers"]) == ["web"]
    assert "SECRETH" in p.read_text()


def test_test_mcp_stdio(env, tmp_path):
    s, _ = env
    script = tmp_path / "srv.py"
    script.write_text(
        "import sys,json\n"
        "l=json.loads(sys.stdin.readline())\n"
        "print(json.dumps({'jsonrpc':'2.0','id':l['id'],'result':{'serverInfo':{'name':'fake'}}}),flush=True)\n"
    )
    q.add_mcp(s, name="fake", type="stdio", target=f"{sys.executable} {script}")
    ok, msg = q.test_mcp(s, "fake")
    assert ok and "fake" in msg
    q.add_mcp(s, name="dead", type="stdio", target="/nonexistent/bin")
    ok, msg = q.test_mcp(s, "dead")
    assert not ok
    assert q.test_mcp(s, "ghost")[0] is False


def test_test_mcp_http_no_leak(env):
    s, _ = env
    q.add_mcp(s, name="h", type="http", target="http://127.0.0.1:9/mcp", headers="X-K: SECRETVAL")
    ok, msg = q.test_mcp(s, "h")
    assert not ok and "SECRETVAL" not in msg and "127.0.0.1" not in msg


def _skill(root, name, desc, body):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n")


def test_skills(env):
    s, home = env
    _skill(home / ".claude/skills", "alpha", "Does alpha", "ALPHA BODY")
    _skill(home / "dev/proj/.claude/skills", "beta", "Does beta", "BETA BODY")
    _skill(home / ".claude/plugins/cache/mk/pl/1/skills", "gamma", "Does gamma", "GAMMA")
    base = {x["name"]: x for x in q.discover_skills(s)}
    assert set(base) == {"alpha", "gamma"}
    assert base["alpha"]["source"] == "user" and base["gamma"]["source"] == "plugin"
    assert "alpha" in base["alpha"]["description"].lower()
    withp = {x["name"]: x for x in q.discover_skills(s, "proj")}
    assert withp["beta"]["source"] == "project"
    assert q.discover_skills(s, "../etc") == q.discover_skills(s)
    assert q.skill_text(s, "alpha") == "ALPHA BODY"
    assert q.skill_text(s, "beta") is None and q.skill_text(s, "beta", "proj") == "BETA BODY"
    q.save(s, {"skills_enabled": {"gamma": False}})
    assert {x["name"]: x["enabled"] for x in q.discover_skills(s)}["gamma"] is False


def test_skill_prompt(env):
    s, home = env
    _skill(home / ".claude/skills", "alpha", "Does alpha", "ALPHA BODY")
    _skill(home / ".claude/skills", "big", "Huge", "x" * 100000)
    add, user = q.skill_prompt(s, ["alpha", "ghost"], "", "/alpha do it")
    assert "alpha: Does alpha" in add and "ghost" not in add
    assert "ALPHA BODY" in user and user.endswith("do it") and "---" not in user
    add, user = q.skill_prompt(s, [], "", "hello /alpha")
    assert (add, user) == ("", "hello /alpha")
    assert q.skill_prompt(s, [], "", "/unknown x")[1] == "/unknown x"
    assert len(q.skill_prompt(s, [], "", "/big")[1]) < 21000
    q.save(s, {"skills_enabled": {"alpha": False}})
    assert q.skill_prompt(s, ["alpha"], "", "/alpha") == ("", "/alpha")
