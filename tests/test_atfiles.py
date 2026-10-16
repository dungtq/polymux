import json
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import builder_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services
from pm.web.routes.atfiles import allowed_dir

JS = Path(__file__).resolve().parents[1] / "src/pm/static/js/atfiles.js"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """$HOME is the test's tmp dir, so the agent cwd (tmp/dev/alpha) and tmp/elsewhere are inside home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Path.home() == tmp_path


def test_requires_login(env):
    s, c, proj, _ = env
    assert c.get("/a/claude-12/atfiles", follow_redirects=False).status_code in (303, 401)


def test_lists_cwd_dirs_first_and_hides_dotfiles(env):
    s, c, proj, _ = env
    (proj / "zdir").mkdir()
    (proj / "b.txt").write_text("x")
    (proj / ".hidden").write_text("x")
    login(s, c)
    d = c.get("/a/claude-12/atfiles").json()
    assert d["base"] == "cwd" and [i["name"] for i in d["items"]] == ["zdir", "b.txt"]
    assert [i["name"] for i in c.get("/a/claude-12/atfiles?q=.h").json()["items"]] == [".hidden"]
    assert [i["name"] for i in c.get("/a/claude-12/atfiles?q=B").json()["items"]] == ["b.txt"]


def test_other_dir_absolute_and_relative(env):
    s, c, proj, tmp = env
    (proj / "sub").mkdir()
    (proj / "sub" / "in.txt").write_text("x")
    (tmp / "elsewhere").mkdir()
    (tmp / "elsewhere" / "e.txt").write_text("x")
    login(s, c)
    assert c.get("/a/claude-12/atfiles?dir=sub/").json()["base"] == "cwd"
    o = c.get("/a/claude-12/atfiles", params={"dir": str(tmp / "elsewhere") + "/"}).json()
    assert o["base"] == "other" and [i["name"] for i in o["items"]] == ["e.txt"]
    assert c.get("/a/claude-12/atfiles?dir=../").json()["base"] == "other"
    assert c.get("/a/claude-12/atfiles?dir=nope/").json()["items"] == []
    assert c.get("/a/codex-99/atfiles").status_code == 404
    h = c.get("/a/claude-12/atfiles", params={"dir": "~/elsewhere/"}).json()
    assert h["base"] == "other" and [i["name"] for i in h["items"]] == ["e.txt"]


def _denied(c, d):
    r = c.get("/a/claude-12/atfiles", params={"dir": d})
    assert r.status_code == 200
    j = r.json()
    return j["base"] == "denied" and j["items"] == []


def test_outside_home_and_cwd_denied(env):
    s, c, proj, tmp = env
    login(s, c)
    assert _denied(c, "/etc/") and _denied(c, "/") and _denied(c, "../../../")
    assert not _denied(c, "") and not _denied(c, "~/")


def test_symlink_in_cwd_out_of_home_denied(env):
    s, c, proj, tmp = env
    (proj / "lnk").symlink_to("/etc")
    (tmp / "inhome").mkdir()
    (proj / "ok").symlink_to(tmp / "inhome")
    login(s, c)
    assert _denied(c, "lnk/") and _denied(c, str(proj / "lnk"))
    assert not _denied(c, "ok/")


def test_cwd_outside_home_allowed(env, monkeypatch):
    s, c, proj, tmp = env
    (tmp / "h").mkdir()
    monkeypatch.setenv("HOME", str(tmp / "h"))
    (proj / "sub").mkdir()
    login(s, c)
    assert c.get("/a/claude-12/atfiles").json()["base"] == "cwd"
    assert not _denied(c, "sub/") and _denied(c, "../")


@pytest.mark.parametrize("sub", [".ssh", ".gnupg", ".config/pm", ".local/state/pm", ".cfg", ".agents-git",
                                 ".password-store", ".aws", ".kube", ".config/gh", ".docker", ".mozilla",
                                 ".local/share/keyrings", ".config/rclone", ".azure", ".config/gcloud"])
def test_secret_dirs_in_home_denied(env, sub):
    s, c, proj, tmp = env
    (tmp / sub / "inner").mkdir(parents=True)
    (proj / "via").symlink_to(tmp / sub)
    login(s, c)
    assert _denied(c, f"~/{sub}/") and _denied(c, f"~/{sub}/inner/") and _denied(c, str(tmp / sub))
    assert _denied(c, "via/")


def _agent_cwd(monkeypatch, cwd):
    agent = Agent(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1", pid=4242,
                  cwd=str(cwd), state=AgentState.IDLE, state_since=1.0)
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: [agent])


def test_root_cwd_does_not_expose_system(env, monkeypatch):
    s, c, proj, tmp = env
    _agent_cwd(monkeypatch, "/")
    login(s, c)
    assert _denied(c, "") and _denied(c, "etc/") and _denied(c, "/etc/")
    assert not _denied(c, "~/")


def test_broad_cwd_grants_only_home(tmp_path, monkeypatch):
    homes = tmp_path / "homes"
    for d in ("me/proj", "other/secret"):
        (homes / d).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(homes / "me"))
    for cwd in (homes, tmp_path, Path("/")):
        assert not allowed_dir(cwd, homes / "other") and not allowed_dir(cwd, homes / "other/secret")
        assert not allowed_dir(cwd, homes) and not allowed_dir(cwd, Path("/etc"))
        assert allowed_dir(cwd, homes / "me/proj")
    assert allowed_dir(homes / "other", homes / "other/secret")  # a narrow cwd outside home still counts


def test_deny_is_per_component(env):
    s, c, proj, tmp = env
    (tmp / ".sshx").mkdir()
    (tmp / ".config" / "other").mkdir(parents=True)
    login(s, c)
    assert not _denied(c, "~/.sshx/") and not _denied(c, "~/.config/other/")


@pytest.mark.skipif(shutil.which("node") is None, reason="node missing")
def test_token_helpers_in_node():
    code = r"""
const src=require('fs').readFileSync(process.argv[1],'utf8');
globalThis.window={};globalThis.document=undefined;
eval(src);
const a=window.pmAtFiles;
console.log(JSON.stringify([a.tokenAt('hi @sr',6),a.tokenAt('a@b',3),a.tokenAt('@',1),a.split('x/y/z'),a.split('z')]));
"""
    out = subprocess.run(["node", "-e", code, str(JS)], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [{"start": 3, "text": "sr"}, None, {"start": 0, "text": ""},
                                      {"dir": "x/y/", "q": "z"}, {"dir": "", "q": "z"}]


def test_project_route_lists_project_dir(env):
    s, c, proj, tmp = env
    (proj / "sub").mkdir()
    (proj / "a.md").write_text("x")
    (tmp / "elsewhere").mkdir()
    assert c.get("/p/alpha/atfiles", follow_redirects=False).status_code in (303, 401)
    login(s, c)
    d = c.get("/p/alpha/atfiles").json()
    assert d["base"] == "cwd" and d["dir"] == str(proj) and [i["name"] for i in d["items"]] == ["sub", "a.md"]
    assert c.get("/p/alpha/atfiles?q=A").json()["items"] == [{"name": "a.md", "dir": False}]
    assert c.get("/p/alpha/atfiles", params={"dir": "~/"}).json()["base"] == "other"
    assert _denied_p(c, "/etc/") and _denied_p(c, "~/.ssh/")
    assert c.get("/p/nope/atfiles").status_code == 404


def _denied_p(c, d):
    j = c.get("/p/alpha/atfiles", params={"dir": d}).json()
    return j["base"] == "denied" and j["items"] == []


def test_launch_prompt_has_at_picker(env):
    s, c, proj, _ = env
    login(s, c)
    for url in ("/p/alpha/launch", "/p/alpha/launch?embed=1"):
        t = c.get(url).text
        assert 'data-atfiles="/p/alpha/atfiles"' in t and '/static/js/atfiles.js' in t
        assert t.index('data-composer="launch"') < t.index('data-atfiles="/p/alpha/atfiles"') < t.index('name="prompt"')  # on the root
    src = JS.read_text()
    assert "textarea[data-atfiles]" not in src and "root.getAttribute('data-atfiles')" in src
