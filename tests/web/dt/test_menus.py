"""pm.web.dt.menus: the one ⋯ item builder of the Inbox, Notifications and the session tab (PARTS.md §9,
CONDUCT V7-CODE-6, V7-SEC-6), and the session tab's view model (pm.web.dt.session) built on it."""

from __future__ import annotations

from types import SimpleNamespace

import jinja2
import pytest

from pm.core.models import AgentKind, AgentState
from pm.web.dt import menus, session


def test_item_keeps_only_set_keys():
    it = menus.item("pin", "Pin", "pin", key="P", post="/a/x/pin", vals={"pinned": "1"}, done="Pinned", refresh="")
    assert it == {"id": "pin", "label": "Pin", "icon": "pin", "key": "P", "post": "/a/x/pin", "vals": {"pinned": "1"},
                  "done": "Pinned"}
    assert menus.item("det", "Details", det=True) == {"id": "det", "label": "Details", "det": True}
    assert menus.copy_id("claude-1") == {"id": "copy", "label": "Copy agent id", "icon": "copy", "copy": "claude-1"}


@pytest.mark.parametrize("kw", [{}, {"href": "/a", "event": "x"}, {"bogus": 1, "href": "/a"}])
def test_item_needs_exactly_one_action_and_known_keys(kw):
    with pytest.raises((ValueError, TypeError)):
        menus.item("x", "X", **kw)


def test_typed_item_may_say_what_is_kept():
    it = menus.item("rm", "Remove…", post="/rm", typed="x", confirm="Remove x?", undo="The files are backed up first.")
    assert it["undo"] == "The files are backed up first."


def test_disabled_item_has_no_action():
    assert menus.item("k", "Kill", disabled=True, why="protected")["why"] == "protected"
    with pytest.raises(ValueError):
        menus.item("k", "Kill", disabled=True, post="/kill")


def test_typed_confirm_names_what_is_typed():
    """V7-SEC-6: the question names the object and the user types that same name."""
    ok = menus.item("k", "Kill window s:1…", post="/kill", typed="claude-1", confirm="Kill window s:1 of claude-1?")
    assert ok["typed"] == "claude-1"
    with pytest.raises(ValueError):
        menus.item("k", "Kill window…", post="/kill", typed="claude-1", confirm="Kill tmux window s:1?")


def test_menu_flattens_and_trims_separators():
    a, b = menus.item("a", "A", href="/a"), menus.item("b", "B", href="/b")
    out = menus.menu(menus.SEP, a, None, menus.SEP, menus.SEP, [b, None], [], menus.SEP)
    assert out == [a, menus.SEP, b]
    assert menus.menu(None, menus.SEP) == []


# ---------------------------------------------------------------- session view model
AID = "claude-1c70ee"


def _agent(**kw):
    a = SimpleNamespace(id=AID, kind=AgentKind.CLAUDE, state=AgentState.WAITING, project="alpha", cwd="/x")
    a.__dict__.update(kw)
    return a


def _req(**q):
    return SimpleNamespace(query_params=q)


def test_live_session_tabs_radios_and_menu():
    s = session.live({"agent": _agent(), "request": _req(st="sub"), "subagents_running": 2, "clear_cmd": "/clear"},
                     ("claude",))
    assert (s.kind, s.state, s.st) == ("claude", "waiting", "sub")
    assert [t["key"] for t in s.tabs] == ["chat", "sub", "pane"] and s.tabs[1]["count"] == 2
    assert s.radios == [("chat", False), ("pane", False), ("sub", True)]  # classic DOM order, no term
    assert s.bld == {"href": f"/a/{AID}/builder", "on": False}
    ids = [i.get("id") for i in s.menu if not i.get("sep")]
    assert ids == ["newchat", "continue", "fork", "review", "usage", "btw", "copy", "exit", "exitkill"]
    kill = s.menu[-1]
    assert kill["typed"] == AID and AID in kill["confirm"] and kill["danger"]


def test_live_session_embed_and_unknown_tab():
    s = session.live({"agent": _agent(kind=AgentKind.CODEX, cwd="", project=None), "embed": True,
                      "request": _req(st="pane")}, ("claude",))
    assert s.st == "pane" and s.radios == [("chat", False), ("pane", True)] and s.bld is None  # same tabs (A29)
    assert [t["key"] for t in s.tabs] == ["chat", "pane"] and [t["icon"] for t in s.tabs] == ["chat", "tmux"]
    s2 = session.live({"agent": _agent(kind=AgentKind.CODEX, cwd="", project=None), "request": _req()}, ("claude",))
    assert not any("icon" in t for t in s2.tabs)  # icons only for the aside's narrow header
    assert "fork" not in [i.get("id") for i in s.menu] and "review" not in [i.get("id") for i in s.menu]
    s = session.live({"agent": _agent(), "request": _req(st="term")}, ("claude",))  # no terminal: back to chat
    assert s.st == "chat"


def test_builder_and_ended_menus():
    rec = SimpleNamespace(project="alpha", kind="claude", resume_id="u1")
    b = session.builder({"agent": _agent(), "live": False, "rec": rec, "csrf": "t"}, ("claude",))
    assert b.state == "ended" and b.tabs == [] and b.can_act and b.bld == {"href": f"/a/{AID}", "on": True}
    assert [i["id"] for i in b.menu] == ["resume", "fork", "copy"]
    e = session.ended({"agent": _agent(), "rec": SimpleNamespace(project="alpha", kind="claude", resume_id=None),
                       "csrf": "t"}, ("claude",))
    assert e.menu[0]["disabled"] and e.menu[0]["why"] == "no session id recorded"
    e = session.ended({"agent": _agent(), "rec": rec, "csrf": ""}, ("claude",))
    assert not e.can_act and [i["id"] for i in e.menu] == ["copy"]


def test_dt_session_never_raises():
    env = jinja2.Environment()
    env.globals["dt_session"] = session.dt_session
    assert env.from_string('{{ dt_session("live", []) is none }}').render() == "True"  # no agent in the context
