"""POST /a/{id}/message stores into the outbox (no tmux); /a/{id}/outbox/{oid}/{op} acts on a row."""

import pytest
from helpers import audit_text, fake, mk_client, post, snap_with  # noqa: F401  (fake is a fixture)

from pm.agents import deliver
from pm.chat import outbox
from pm.core.models import AgentState
from pm.web.routes import outbox as routes_outbox

SECRET = "super-secret-needle-123"


@pytest.fixture
def kicks(monkeypatch):
    seen = []
    monkeypatch.setattr(deliver, "kick", seen.append)
    return seen


@pytest.fixture
def env(fake, monkeypatch, kicks):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    app.include_router(routes_outbox.router)
    monkeypatch.setattr(deliver, "_D", deliver.Deliverer(st, lambda a: a.state is AgentState.IDLE, lambda _aid: agent))
    return st, calls, c, tok, app


def test_message_is_stored_not_typed(env, kicks):
    st, calls, c, tok, app = env
    r = post(c, tok, "/a/claude-1/message", text=SECRET)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    rows = outbox.pending(st, "claude-1")
    assert len(rows) == 1 and rows[0].state == "saved" and rows[0].text == SECRET
    assert f'id="ob-{rows[0].id}"' in r.text
    assert calls == []  # no tmux in the request
    assert kicks == ["claude-1"]
    log = audit_text(st)
    assert SECRET not in log and "sha256" not in log
    assert f'"outbox":{rows[0].id}' in log and f'"len":{len(SECRET)}' in log
    ev = app.state.hub.replay(0)[-1]
    assert ev.type == "chat_outbox" and ev.data == {"agent_id": "claude-1", "id": rows[0].id, "state": "saved"}


def test_message_refusals(env, kicks):
    st, calls, c, tok, app = env
    assert post(c, tok, "/a/claude-1/message", text="  ").status_code == 400
    assert post(c, tok, "/a/claude-1/message", text="bad\x07bell").status_code == 400
    assert post(c, tok, "/a/nope-9/message", text="hi").status_code == 404
    assert post(c, tok, "/a/claude-1/message", csrf=False, text="hi").status_code == 403
    assert outbox.pending(st, "claude-1") == [] and kicks == [] and calls == []


def test_slash_command_keeps_direct_path(env, kicks):
    st, calls, c, tok, app = env
    r = post(c, tok, "/a/claude-1/message", text="/clear", confirm="1")
    assert r.status_code == 200 and "flash ok" in r.text
    assert calls and outbox.pending(st, "claude-1") == [] and kicks == []


def _row(st, text="hi", agent="claude-1"):
    return outbox.add(st, agent, text)


def test_cancel(env):
    st, calls, c, tok, app = env
    m = _row(st, SECRET)
    r = post(c, tok, f"/a/claude-1/outbox/{m.id}/cancel")
    assert r.status_code == 200 and 'data-state="cancelled"' in r.text
    assert outbox.get(st, m.id).state == "cancelled"
    assert post(c, tok, f"/a/claude-1/outbox/{m.id}/cancel").status_code == 200  # same state: no-op
    log = audit_text(st)
    assert SECRET not in log and '"op":"cancel"' in log and '"state":"cancelled"' in log
    assert calls == []


def test_edit_returns_text_and_cancels(env):
    st, calls, c, tok, app = env
    m = _row(st, "draft text")
    outbox.set_state(st, m.id, "wait")
    r = post(c, tok, f"/a/claude-1/outbox/{m.id}/edit")
    assert r.status_code == 200 and r.json() == {"text": "draft text"}
    assert outbox.get(st, m.id).state == "cancelled"
    assert "draft text" not in audit_text(st)


def test_retry_failed(env, kicks):
    st, calls, c, tok, app = env
    m = _row(st)
    outbox.set_state(st, m.id, "wait")
    assert post(c, tok, f"/a/claude-1/outbox/{m.id}/retry").status_code == 409  # wait is not failed
    outbox.set_state(st, m.id, "sending")
    outbox.set_state(st, m.id, "failed", error="pane gone")
    r = post(c, tok, f"/a/claude-1/outbox/{m.id}/retry")
    assert r.status_code == 200 and 'data-state="saved"' in r.text
    assert kicks == ["claude-1"] and calls == []
    assert '"result":"denied"' in audit_text(st)


def test_send_now_types_into_queue(env):
    st, calls, c, tok, app = env
    m = _row(st, "now text")
    outbox.set_state(st, m.id, "wait")
    r = post(c, tok, f"/a/claude-1/outbox/{m.id}/now")
    assert r.status_code == 200
    got = outbox.get(st, m.id)
    assert got.state == "queued"  # the agent is not idle: Claude's own queue
    assert any("send-keys" in a for a in calls)
    assert post(c, tok, f"/a/claude-1/outbox/{m.id}/now").status_code == 409
    assert "now text" not in audit_text(st)


def test_send_now_without_deliverer_is_409(env, monkeypatch):
    st, calls, c, tok, app = env
    monkeypatch.setattr(deliver, "_D", None)
    m = _row(st)
    assert post(c, tok, f"/a/claude-1/outbox/{m.id}/now").status_code == 409
    assert outbox.get(st, m.id).state == "saved" and calls == []
    assert '"reason":"deliverer not running"' in audit_text(st)


def test_send_now_failure_marks_failed(env, monkeypatch):
    st, calls, c, tok, app = env
    from pm.agents import message as msg

    def boom(settings, agent, text, **kw):
        raise msg.MessageError("send failed")
    monkeypatch.setattr(msg, "send_message", boom)
    m = _row(st)
    r = post(c, tok, f"/a/claude-1/outbox/{m.id}/now")
    assert r.status_code == 200 and 'data-state="failed"' in r.text
    assert outbox.get(st, m.id).tries == 1


def test_wrong_agent_bad_op_bad_id(env):
    st, calls, c, tok, app = env
    m = _row(st, agent="claude-2")
    assert post(c, tok, f"/a/claude-1/outbox/{m.id}/cancel").status_code == 404
    assert outbox.get(st, m.id).state == "saved"
    assert post(c, tok, f"/a/claude-2/outbox/{m.id}/bogus").status_code == 404
    assert post(c, tok, "/a/claude-1/outbox/abc/cancel").status_code == 404
    assert post(c, tok, "/a/claude-1/outbox/999/cancel").status_code == 404
    assert post(c, tok, f"/a/claude-2/outbox/{m.id}/cancel", csrf=False).status_code == 403
    assert post(c, tok, f"/a/claude-2/outbox/{m.id}/cancel", origin="https://evil.example").status_code == 403
    assert outbox.get(st, m.id).state == "saved"


def test_done_row_cannot_be_cancelled(env):
    st, calls, c, tok, app = env
    m = _row(st)
    outbox.set_state(st, m.id, "sending")
    outbox.set_state(st, m.id, "done", item=3)
    assert post(c, tok, f"/a/claude-1/outbox/{m.id}/cancel").status_code == 409
    assert post(c, tok, f"/a/claude-1/outbox/{m.id}/edit").status_code == 409
