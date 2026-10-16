import dataclasses
import re

import pytest
from helpers import audit_text, fake, mk_client, post, snap_with  # noqa: F401

from pm.agents import slash
from pm.core.models import Agent, AgentKind
from pm.infra import snapshot as services
from pm.web.routes import slash_output


# ------------------------------------------------------------------ catalog / policy
def test_catalog_shape_and_kinds():
    for kind in ("claude", "devin", "cursor-agent", "antigravity"):
        items = slash.catalog_for(kind)
        assert items
        for it in items:
            assert it["cmd"].startswith("/") and it["risk"] in ("info", "action", "danger")
            assert it["output"] in ("panel", "none")
            assert it["output"] == "none" or it["risk"] == "info"
    assert slash.catalog_for("codex") == [] and slash.catalog_for("nope") == []


def test_compact_alias():
    assert slash.compact_cmd("claude") == "/compact"
    assert slash.compact_cmd("devin") == "/compact"
    assert slash.compact_cmd("cursor-agent") == "/summarize"
    assert slash.compact_cmd("antigravity") is None
    for k in ("claude", "devin", "cursor-agent"):
        assert slash.lookup(k, slash.compact_cmd(k)).risk == "action"


def test_command_of_ignores_paths_and_text():
    assert slash.command_of("/Context now") == "/context"
    assert slash.command_of("hello /clear") is None
    assert slash.command_of("/home/me/x look") is None
    assert slash.command_of("/") is None
    assert slash.command_of("/ x") is None


def test_risk_and_confirm_policy():
    assert slash.risk_of("claude", "hello") is None
    assert slash.risk_of("claude", "/context") == "info"
    assert slash.risk_of("claude", "/clear") == "danger"
    assert slash.risk_of("claude", "/madeup") == "unknown"
    assert slash.risk_of("devin", "/yolo") == "danger"  # not offered, still guarded
    with pytest.raises(slash.SlashError):
        slash.check_message("claude", "/clear", False)
    assert slash.check_message("claude", "/clear", True) == "danger"
    assert slash.check_message("claude", "/madeup arg", False) == "unknown"
    assert slash.check_message("claude", "/home/x/y", False) is None
    with pytest.raises(slash.SlashError):
        slash.check_message("claude", "/compact", False, running=True)


# ------------------------------------------------------------------ message route
def test_danger_needs_server_side_confirm(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/claude-1/message", text="/clear")
    assert r.status_code == 400 and not calls
    r = post(c, tok, "/a/claude-1/message", text="/clear", confirm="1")
    assert r.status_code == 200 and calls
    log = audit_text(st)
    assert '"slash": "danger"' in log or "danger" in log
    assert "/clear" in log  # command name is audited (catalog names only), arguments never
    calls.clear()
    assert post(c, tok, "/a/claude-1/message", text="/yolo-ish").status_code == 200
    assert post(c, tok, "/a/claude-1/message", text="/home/x/f.py look").status_code == 200


def test_args_not_audited(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    post(c, tok, "/a/claude-1/message", text="/compact keepSECRETword", confirm="1")
    assert "keepSECRETword" not in audit_text(st)


# ------------------------------------------------------------------ output card
def test_new_lines_multiset_diff():
    before = ["a", "b", "b", "c"]
    after = ["a", "b", "b", "c", "> /context", "ctx 10%", "b"]
    assert slash_output.new_lines(before, after) == ["> /context", "ctx 10%", "b"]


def test_bound_limits():
    out = slash_output._bound(["x" * 1000] * 100)
    assert len(out) <= slash_output.MAX_OUT_LINES and sum(map(len, out)) <= slash_output.MAX_OUT_CHARS


def test_slash_output_route(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    app.include_router(slash_output.router)
    monkeypatch.setattr(slash_output, "WINDOW_S", 0.3)
    monkeypatch.setattr(slash_output, "POLL_S", 0.05)
    state = {"n": 0}

    def pane_text(settings, pane, lines=200):
        state["n"] += 1
        return "old\n" if state["n"] == 1 else "old\nCONTEXT 12% <script>\n"

    monkeypatch.setattr(services, "pane_text", pane_text)
    r = post(c, tok, "/a/claude-1/message", text="/context")
    assert r.status_code == 200
    m = re.search(r'since=([A-Za-z0-9_-]+)', r.text)
    assert m and 'hx-target="[data-cmp-slash-card]"' in r.text  # the asking composer's card (composer.js keeps it there)
    r2 = c.get(f"/a/claude-1/slash-output?since={m.group(1)}")
    assert r2.status_code == 200
    assert "CONTEXT 12%" in r2.text and "&lt;script&gt;" in r2.text and "old" not in r2.text
    assert "data-slash-dismiss" in r2.text
    # the token is single use and bound to the agent
    assert c.get(f"/a/claude-1/slash-output?since={m.group(1)}").status_code == 404
    assert c.get("/a/claude-1/slash-output?since=bad").status_code == 404
    assert "CONTEXT" not in audit_text(st)


def test_slash_output_requires_login(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    app.include_router(slash_output.router)
    c.cookies.clear()
    assert c.get("/a/claude-1/slash-output?since=abcdefghijkl").status_code in (303, 401)


# ------------------------------------------------------------------ token panel Compact button
from helpers import chat_env as env  # noqa: E402,F401  (fixture)
from helpers import login  # noqa: E402,F401

from pm.chat.sources.common import Stats  # noqa: E402


@pytest.mark.parametrize("ctx,kind,want", [
    (180_000, AgentKind.CLAUDE, "/compact"),
    (130_000, AgentKind.CLAUDE, "/compact"),
    (50_000, AgentKind.CLAUDE, None),
    (180_000, AgentKind.CURSOR, "/summarize"),
    (180_000, AgentKind.ANTIGRAVITY, None),
])
def test_stats_compact_button(env, monkeypatch, ctx, kind, want):  # noqa: F811
    s, c = env
    login(s, c)
    monkeypatch.setattr("pm.chat.stats.stats_for",
                        lambda agent, settings=None: Stats(input=1, output=1, ctx=ctx, source=kind.value))
    ag = services.agents_mod.detect_agents(None)[0]
    monkeypatch.setattr(services.agents_mod, "detect_agents",
                        lambda panes: [dataclasses.replace(ag, kind=kind)])
    r = c.get("/a/claude-12/stats")
    assert r.status_code == 200
    has = 'class="stats-act"' in r.text
    assert has == bool(want)
    if want:
        assert f'name="text" value="{want}"' in r.text and 'name="confirm" value="1"' in r.text
    if kind is AgentKind.ANTIGRAVITY:
        assert "no compact command" in r.text


# ------------------------------------------------------------------ /plan and /advisor (chat + launch)
def test_advisor_in_claude_catalog_only():
    assert slash.lookup("claude", "/advisor").risk == "action"
    for k in ("devin", "cursor-agent", "antigravity"):
        assert slash.lookup(k, "/advisor") is None
        assert slash.lookup(k, "/plan")


def test_launch_catalog_is_plan_and_advisor():
    cat = slash.launch_catalog()
    assert [c["cmd"] for c in cat["claude"]] == ["/plan", "/advisor"]
    assert [c["cmd"] for c in cat["devin"]] == ["/plan"]
    assert cat["codex"] == []


def test_apply_prompt_commands():
    from pm.agents.launch import apply_prompt_commands as ap
    C, D = AgentKind.CLAUDE, AgentKind.DEVIN
    assert ap(C, "/plan fix the bug", "auto") == ("fix the bug", "plan")
    assert ap(C, "/PLAN", None) == ("", "plan")
    assert ap(C, "/plan\nline two", "manual") == ("line two", "plan")
    assert ap(AgentKind.CURSOR, "/plan x", None) == ("x", "plan")
    assert ap(D, "/plan x", "auto") == ("/plan x", "auto")      # no plan mode flag: stays a message
    assert ap(C, "/advisor opus", "auto") == ("/advisor opus", "auto")
    assert ap(C, "/planet x", "auto") == ("/planet x", "auto")
    assert ap(C, "please /plan", "auto") == ("please /plan", "auto")
