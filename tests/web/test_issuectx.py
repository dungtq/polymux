"""web.issuectx: item numbers from forms, the audit callable handed to tracker mutators, Send-to-agent links."""

from types import SimpleNamespace

import pytest

from pm.core.errors import Invalid
from pm.core.models import AgentState
from pm.web import issuectx


@pytest.mark.parametrize("value,num", [("1", 1), (7, 7), (" 12 ", 12)])
def test_num_of(value, num):
    assert issuectx.num_of(value) == num


@pytest.mark.parametrize("value", ["", "x", "0", "-3", "1.5", None])
def test_num_of_refuses(value):
    with pytest.raises(Invalid):
        issuectx.num_of(value)


def test_audit_ok_writes_route_line_with_kept_keys(monkeypatch):
    calls = []
    monkeypatch.setattr(issuectx, "audit_result", lambda *a: calls.append(a))
    req = object()
    write = issuectx.audit_ok(req, "issue_add", "issues", {"project": "trk"})
    write("tracker_issue", {"project": "trk", "file": "backlog", "num": 4, "images": 2, "title_len": 9})
    assert calls == [(req, "issue_add", "issues", "ok", {"project": "trk", "num": 4, "images": 2})]
    calls.clear()
    issuectx.audit_ok(req, "todo_toggle", "todo", {"project": "p"}, keep=("num", "done"))("x", {"num": 1, "done": 0})
    assert calls == [(req, "todo_toggle", "todo", "ok", {"project": "p", "num": 1, "done": 0})]


def test_audit_ok_propagates_errors(monkeypatch):
    def boom(*a):
        raise RuntimeError("audit down")
    monkeypatch.setattr(issuectx, "audit_result", boom)
    with pytest.raises(RuntimeError):
        issuectx.audit_ok(object(), "a", "issues", {})("x", {"num": 1})


def test_send_links_only_the_projects_agents():
    agents = [SimpleNamespace(id="claude-1", label="", state=AgentState.IDLE, project="trk"),
              SimpleNamespace(id="codex-2", label="fixer", state=AgentState.RUNNING, project="trk"),
              SimpleNamespace(id="claude-3", label="", state=AgentState.IDLE, project="other")]
    links = issuectx.send_links(agents, "trk", 5, "a & b")
    assert [(x["id"], x["label"], x["state"]) for x in links] == [("claude-1", "claude-1", "idle"),
                                                                   ("codex-2", "fixer", "running")]
    assert links[0]["href"] == ("/a/claude-1#draft=Fix%20issue%20%235%3A%20a%20%26%20b%0A%0A"
                                "Details%3A%20pmctl%20issue%20show%205")


def test_issue_key_is_a_dispatch_mark_key():
    k = issuectx.issue_key(7)
    assert len(k) == 16 and all(c in "0123456789abcdef" for c in k)
    assert k == issuectx.issue_key("7") != issuectx.issue_key(8)


def test_agents_on_live_starting_and_gone(tmp_path):
    from pm.core import prefs
    st = SimpleNamespace(prefs_file=tmp_path / "prefs.json")
    rows = [{"project": "trk", "num": n} for n in (1, 2, 3, 4)] + [{"project": "other", "num": 1}]
    assert issuectx.agents_on(st, [], rows) == {}  # no marks at all
    now = 10_000.0
    prefs.mark_dispatched(st.prefs_file, "trk", issuectx.issue_key(1), "claude-12", "%12", now - 900)
    prefs.mark_dispatched(st.prefs_file, "trk", issuectx.issue_key(2), "claude-77", "%77", now - 30)
    prefs.mark_dispatched(st.prefs_file, "trk", issuectx.issue_key(3), "claude-88", "%88", now - 900)
    live = [SimpleNamespace(id="claude-12", pane_id="%12", state=AgentState.RUNNING)]
    got = issuectx.agents_on(st, live, rows, now=now)
    assert got == {("trk", 1): {"id": "claude-12", "state": "running"},  # alive in the snapshot
                   ("trk", 2): {"id": "claude-77", "state": "starting"}}  # just launched, not seen yet
    # #3: dispatched long ago, agent gone; #4 and other#1: never dispatched
