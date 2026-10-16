"""Quick chat v2 routes: options, conversations, send (create/lock/validate), settings block, nav search."""
import dataclasses
import json
import stat

import pytest
from helpers import audit_text, mk_client, post, snap_with

from pm.core.models import Tier
from pm.qchat import cfg as qchatcfg
from pm.qchat import db as qchatdb
from pm.web.routes import qchat as routes_qchat
from pm.web.routes import search as routes_search
from pm.web.routes import settings as routes_settings

SID = "11111111-2222-3333-4444-555555555555"
SECRET = "my-very-secret-question"
HDR = "Bearer sk-supersecrettokenvalue"

FAKE = """#!/bin/sh
echo "$@" > "$PWD/argv.txt"
echo '{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}}'
echo '{"type":"result","is_error":false,"session_id":"%s","total_cost_usd":0.01}'
""" % SID


def _mk(settings, tmp_path, monkeypatch, ceiling=Tier.MESSAGE, agents=()):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    b = tmp_path / "fake-claude"
    b.write_text(FAKE)
    b.chmod(b.stat().st_mode | stat.S_IXUSR)
    dev = tmp_path / "dev"
    (dev / "proj").mkdir(parents=True, exist_ok=True)
    st = dataclasses.replace(settings, agent_bins={"claude": str(b)}, dev_root=dev,
                             extra_projects=(), state_dir=tmp_path / "st")
    app, c, sess, tok, st = mk_client(st, ceiling=ceiling)
    snap_with(monkeypatch, agents)
    for r in (routes_qchat.router, routes_settings.router):
        app.include_router(r)
    return c, tok, st


@pytest.fixture
def qc(settings, tmp_path, monkeypatch):
    return _mk(settings, tmp_path, monkeypatch)


def _frames(text):
    out = []
    for b in text.split("\n\n"):
        if b.startswith("event: "):
            ev, data = b.split("\n", 1)
            out.append((ev[7:], json.loads(data[6:])))
    return out


def test_options(qc):
    c, tok, st = qc
    o = c.get("/qchat/options").json()
    assert o["profiles"] == ["none", "read", "full"] and o["folders"] == ["proj"]
    assert "sonnet" in o["models"] and o["default_profile"] == "none"
    assert all("headers" not in m and "env" not in m for m in o["mcp"])
    c.cookies.clear()
    assert c.get("/qchat/options").status_code in (401, 303, 403)


def test_send_creates_conv_and_stores_history(qc):
    c, tok, st = qc
    r = post(c, tok, "/qchat/send", text=SECRET, model="haiku")
    fr = _frames(r.text)
    assert fr[0][0] == "conv" and fr[0][1]["id"] == 1
    assert any(e == "done" for e, _ in fr)
    d = c.get("/qchat/convs/1").json()
    assert [m["role"] for m in d["messages"]] == ["user", "assistant"]
    assert d["messages"][0]["text"] == SECRET and d["messages"][1]["text"] == "Hello"
    assert d["conv"]["session"] == SID and d["conv"]["profile"] == "none"
    assert c.get("/qchat/convs").json()["convs"][0]["id"] == 1
    assert SECRET in c.get("/qchat/convs/1/export").text
    au = audit_text(st)
    assert "qchat_send" in au and SECRET not in au and '"profile":"none"' in au and '"conv":1' in au


def test_send_continues_conv_with_session(qc, tmp_path):
    c, tok, st = qc
    post(c, tok, "/qchat/send", text="one")
    post(c, tok, "/qchat/send", text="two", conv="1")
    assert len(c.get("/qchat/convs/1").json()["messages"]) == 4
    assert len(c.get("/qchat/convs").json()["convs"]) == 1


def test_read_profile_folder_validation_and_lock(qc):
    c, tok, st = qc
    assert post(c, tok, "/qchat/send", text="x", profile="read").status_code == 400  # no folder
    assert post(c, tok, "/qchat/send", text="x", profile="read", folder="../etc").status_code == 400
    assert post(c, tok, "/qchat/send", text="x", profile="bogus").status_code == 400
    assert post(c, tok, "/qchat/send", text="x", profile="read", folder="proj", mcp="nope").status_code == 400
    r = post(c, tok, "/qchat/send", text="x", profile="read", folder="proj")
    assert r.status_code == 200 and _frames(r.text)[0][1]["id"] == 1
    assert c.get("/qchat/convs/1").json()["conv"]["folder"] == "proj"
    assert post(c, tok, "/qchat/send", text="y", profile="read", folder="other", conv="1").status_code == 400
    assert post(c, tok, "/qchat/send", text="y", profile="read", folder="proj", conv="1").status_code == 200
    assert post(c, tok, "/qchat/send", text="y", conv="99").status_code == 400


def test_full_needs_no_unlock_and_disabled_profile_refused(settings, tmp_path, monkeypatch):
    c, tok, st = _mk(settings, tmp_path, monkeypatch)
    assert post(c, tok, "/qchat/send", text="x", profile="full", folder="proj").status_code == 200
    qchatcfg.save(st, {"profiles": {"full": False}})
    assert post(c, tok, "/qchat/send", text="x", profile="full", folder="proj").status_code == 400
    assert "full" not in c.get("/qchat/options").json()["profiles"]


def test_guards(qc, settings, tmp_path, monkeypatch):
    c, tok, st = qc
    assert post(c, tok, "/qchat/send", csrf=False, text="hi").status_code == 403
    assert post(c, tok, "/qchat/send", origin="https://evil.example", text="hi").status_code == 403
    assert post(c, tok, "/qchat/convs/1/delete", csrf=False).status_code == 403
    assert post(c, tok, "/qchat/delete-all", origin="https://evil.example").status_code == 403
    assert post(c, tok, "/settings/qchat", csrf=False, part="history").status_code == 403
    (tmp_path / "t2").mkdir()
    c2, tok2, _ = _mk(settings, tmp_path / "t2", monkeypatch, ceiling=Tier.SAFE)
    assert post(c2, tok2, "/qchat/send", text="hi").status_code == 403
    assert post(c2, tok2, "/qchat/delete-all").status_code == 403


def test_rename_delete_delete_all(qc):
    c, tok, st = qc
    post(c, tok, "/qchat/send", text="a")
    post(c, tok, "/qchat/send", text="b")
    assert post(c, tok, "/qchat/convs/1/rename", title="  New   name ").json()["title"] == "New name"
    assert post(c, tok, "/qchat/convs/1/rename", title=" ").status_code == 400
    assert post(c, tok, "/qchat/convs/9/rename", title="x").status_code == 404
    assert post(c, tok, "/qchat/convs/1/delete").status_code == 200
    assert c.get("/qchat/convs/1").status_code == 404
    assert post(c, tok, "/qchat/delete-all").json()["deleted"] == 1
    assert c.get("/qchat/convs").json()["convs"] == []
    assert c.get("/qchat/convs/abc").status_code == 404
    assert "qchat_delete_all" in audit_text(st)


def test_settings_block_save_and_mcp_never_shows_headers(qc):
    c, tok, st = qc
    r = c.get("/settings/qchat")
    assert r.status_code == 200 and "Quick chat" in r.text and "Delete all history" in r.text
    r = post(c, tok, "/settings/qchat", part="tools", profile_read="1", default_profile="read",
             default_folder="", read_tools="Read, Grep", full_permission_mode="acceptEdits",
             max_turns="10", timeout_s="120")
    assert "saved" in r.text and "profile_full" in r.text
    cfg = qchatcfg.load(st)
    assert cfg["profiles"] == {"none": True, "read": True, "full": False} and cfg["max_turns"] == 10
    assert cfg["read_tools"] == ["Read", "Grep"]
    r = post(c, tok, "/settings/qchat", part="tools", max_turns="0")
    assert "max_turns" in r.text and qchatcfg.load(st)["max_turns"] == 10
    r = post(c, tok, "/settings/qchat/mcp/add", name="tav", type="http", target="https://mcp.example.com/mcp",
             headers="Authorization: " + HDR)
    assert "Added MCP server tav" in r.text and HDR not in r.text and "sk-supersecret" not in r.text
    assert "sk-supersecret" not in c.get("/settings/qchat").text
    assert "sk-supersecret" not in json.dumps(c.get("/qchat/options").json())
    assert "sk-supersecret" not in audit_text(st)
    assert post(c, tok, "/settings/qchat/mcp/toggle", name="tav", on="0").status_code == 204
    assert qchatcfg.load(st)["mcp_enabled"]["tav"] is False
    assert [m["name"] for m in c.get("/qchat/options").json()["mcp"]] == []
    assert post(c, tok, "/settings/qchat/mcp/toggle", name="ghost", on="1").status_code == 404
    r = post(c, tok, "/settings/qchat/mcp/remove", name="tav")
    assert "Removed tav" in r.text
    r = post(c, tok, "/settings/qchat", part="history", keep_convs="50", keep_days="3")
    assert qchatcfg.load(st)["keep_convs"] == 50 and qchatcfg.load(st)["nav_search"] is False


def test_settings_skills_and_history_delete(qc):
    c, tok, st = qc
    sk = st.state_dir.parent / "home" / ".claude" / "skills" / "demo"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: demo\ndescription: A demo <b>skill</b>.\n---\nBody <script>x</script>\n")
    assert 'value="demo"' in c.get("/settings/qchat").text or "demo" in c.get("/settings/qchat").text
    v = c.get("/settings/qchat/skills/demo")
    assert "Body" in v.text and "<script>" not in v.text
    assert c.get("/settings/qchat/skills/none-such").status_code == 404
    assert post(c, tok, "/settings/qchat/skills/toggle", name="demo", on="0").status_code == 204
    assert qchatcfg.load(st)["skills_enabled"]["demo"] is False
    post(c, tok, "/qchat/send", text="a")
    r = post(c, tok, "/settings/qchat/history/delete-all")
    assert "Deleted 1 conversation" in r.text and qchatdb.stats(st)["convs"] == 0


def test_settings_panel_has_placeholder_and_nav(qc):
    c, tok, st = qc
    assert 'hx-get="/settings/qchat"' in c.get("/settings/panel").text


def test_nav_search_hits_respect_setting(qc):
    c, tok, st = qc
    post(c, tok, "/qchat/send", text="find the zebra please")
    hits = routes_search.qchat_hits(st, "zebra")
    assert hits and hits[0].href == "/?qchat=1" and "zebra" in hits[0].sub
    qchatcfg.save(st, {"nav_search": False})
    assert routes_search.qchat_hits(st, "zebra") == []


def test_prepare_send_raises_invalid_with_field(qc):
    from pm.core.errors import Invalid
    from pm.qchat import send as qsend
    c, tok, st = qc
    names = lambda s: ["proj"]  # noqa: E731
    cases = [(qsend.SendReq(text=" "), "text"), (qsend.SendReq(text="x", model="gpt"), "model"),
             (qsend.SendReq(text="x", profile="bogus"), "profile"), (qsend.SendReq(text="x", conv="7"), "conv"),
             (qsend.SendReq(text="x", profile="read"), "folder"),
             (qsend.SendReq(text="x", profile="read", folder="proj", mcp=["nope"]), "mcp"),
             (qsend.SendReq(text="x", skills=["nope"]), "skills")]
    for req, field in cases:
        with pytest.raises(qsend.SendInvalid) as ei:
            qsend.prepare_send(st, req, folder_names=names)
        assert isinstance(ei.value, Invalid) and ei.value.status == 400 and ei.value.field == field
    assert qchatdb.list_convs(st) == []  # nothing stored on a refusal
    p = qsend.prepare_send(st, qsend.SendReq(text="hi", profile="read", folder="proj"), folder_names=names)
    assert p.folder == "proj" and p.conv["msgs"] == 1 and p.mcp == [] and p.skills == []


def test_cfg_reexports_mcp_and_skills(qc):
    from pm.qchat import mcpcfg
    from pm.qchat import skills as qskills
    assert qchatcfg.add_mcp is mcpcfg.add_mcp and qchatcfg.skill_text is qskills.skill_text
    assert qchatcfg._lock is mcpcfg._lock  # save() and add_mcp() share one lock


TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


def _aside_agent(tmp_path):
    from pm.core.models import Agent, AgentKind
    p = tmp_path / "t" / "22222222-2222-3333-4444-555555555555.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "user", "uuid": "u1", "timestamp": "2026-10-09T10:00:01.000Z",
             "message": {"role": "user", "content": "refactor the parser " + TOKEN}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-09T10:00:02.000Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Parser split in two."}]}}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return Agent(id="claude-abc123", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=0, cwd="/nowhere",
                 transcript=str(p), label="dev-pm-1-claude")


def test_aside_send_context_title_and_stored_agent(settings, tmp_path, monkeypatch):
    from pm.qchat import run as qchat
    from pm.tmux import ctl as tmuxctl
    tmux_calls = []
    monkeypatch.setattr(tmuxctl, "run", lambda *a, **k: tmux_calls.append(a) or "")
    seen = []
    real = qchat.stream

    def spy(*a, **kw):
        seen.append(kw.get("context", ""))
        return real(*a, **kw)
    monkeypatch.setattr(qchat, "stream", spy)
    c, tok, st = _mk(settings, tmp_path, monkeypatch, agents=[_aside_agent(tmp_path)])
    r = post(c, tok, "/qchat/send", text=SECRET, agent="claude-abc123")
    assert r.status_code == 200 and _frames(r.text)[0][1]["title"] == "btw: dev-pm-1-claude"
    assert "User: refactor the parser" in seen[0] and "Parser split in two." in seen[0] and TOKEN not in seen[0]
    conv = c.get("/qchat/convs/1").json()["conv"]
    assert conv["agent"] == "claude-abc123" and conv["title"] == "btw: dev-pm-1-claude"
    r = post(c, tok, "/qchat/send", text="and then?", conv="1")  # turn 2: no agent field, stored one used
    assert r.status_code == 200 and "Parser split in two." in seen[1]
    assert len(c.get("/qchat/convs").json()["convs"]) == 1
    au = audit_text(st)
    assert '"agent":"claude-abc123"' in au and '"ctx":' in au and SECRET not in au and "Parser" not in au
    assert TOKEN not in au and tmux_calls == []  # the agent's pane is never touched
    post(c, tok, "/qchat/send", text="plain")
    assert c.get("/qchat/convs/2").json()["conv"]["agent"] == "" and seen[2] == ""


def test_aside_always_runs_without_tools(settings, tmp_path, monkeypatch):
    # its context is another agent's chat (untrusted): no tools, MCP or skills, whatever the form asks for
    from pm.qchat import run as qchat
    kws = []
    real = qchat.stream

    def spy(*a, **kw):
        kws.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(qchat, "stream", spy)
    c, tok, st = _mk(settings, tmp_path, monkeypatch, agents=[_aside_agent(tmp_path)])
    r = post(c, tok, "/qchat/send", text="x", agent="claude-abc123", profile="read", folder="proj")
    assert r.status_code == 200
    r = post(c, tok, "/qchat/send", text="y", conv="1", profile="full", folder="proj")
    assert r.status_code == 200
    assert [(k["profile"], k["folder"], k["mcp"], k["skills"]) for k in kws] == [("none", "", [], [])] * 2
    assert c.get("/qchat/convs/1").json()["conv"]["profile"] == "none"


def test_aside_unknown_or_invalid_agent_is_denied(qc):
    c, tok, st = qc
    for bad in ("claude-nope99", "../etc", "X" * 200):
        r = post(c, tok, "/qchat/send", text="x", agent=bad)
        assert r.status_code == 400
    assert qchatdb.list_convs(st) == []
    au = audit_text(st)
    assert '"reason":"agent"' in au and '"result":"denied"' in au.replace(" ", "")
