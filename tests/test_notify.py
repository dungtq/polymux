import httpx
import pytest

from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState
from pm.notify import ntfy as notify


@pytest.fixture(autouse=True)
def _reset():
    notify.reset()


def mk(**kw):
    return Settings(**kw)


def agent(**kw):
    base = dict(id="claude-12", kind=AgentKind.CLAUDE, pane_id="%12", session_id="$1",
                pid=1, cwd="/x", project="pm")
    base.update(kw)
    return Agent(**base)


def recorder(status=200):
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(status)

    return seen, httpx.MockTransport(h)


def test_publish_headers_and_url():
    seen, tr = recorder()
    s = mk()
    assert notify.publish(s, "t", "claude-12: waiting", click=notify.agent_link(s, "claude-12"),
                          tags=("pm", "waiting"), priority="high", transport=tr)
    r = seen[0]
    assert str(r.url) == "https://ntfy.example.test/pm"  # never the agent topic
    assert r.headers["click"] == "https://pm.example.test/a/claude-12"
    assert r.headers["tags"] == "pm,waiting" and r.headers["priority"] == "high"
    assert "actions" not in r.headers
    assert r.content == b"claude-12: waiting"


def test_custom_url_and_failure_never_raises(caplog):
    s = mk(ntfy_url="https://x.example/t")

    def boom(req):
        raise httpx.ConnectError("secret-detail-xyz")

    assert notify.publish(s, "t", "m", transport=httpx.MockTransport(boom)) is False
    assert "secret-detail-xyz" not in caplog.text and "ConnectError" in caplog.text
    _, tr = recorder(500)
    assert notify.publish(s, "t", "m", transport=tr) is False


@pytest.mark.parametrize("msg", ["", "x" * 500, "a\nb", "café"])
def test_bad_message_refused(msg):
    seen, tr = recorder()
    assert notify.publish(mk(), "t", msg, transport=tr) is False
    assert not seen


def test_bad_agent_id():
    with pytest.raises(ValueError):
        notify.agent_link(mk(), "../x")


def test_state_change_only_for_interesting_transitions():
    seen, tr = recorder()
    s = mk()
    a = agent()
    R, W, I, F, U = (AgentState.RUNNING, AgentState.WAITING, AgentState.IDLE,
                     AgentState.FAILED, AgentState.UNKNOWN)
    assert not notify.notify_state_change(s, a, U, I, now=0, transport=tr)
    assert not notify.notify_state_change(s, a, I, R, now=0, transport=tr)
    assert not notify.notify_state_change(s, a, W, W, now=0, transport=tr)
    assert not seen
    assert not notify.notify_state_change(s, a, R, I, now=0, transport=tr)  # idle: no push
    assert not seen
    assert notify.notify_state_change(s, a, R, F, now=100, transport=tr)
    assert seen[-1].headers["priority"] == "high"
    assert seen[0].headers["click"].endswith("/a/claude-12")


def test_debounce_and_dedupe():
    seen, tr = recorder()
    s = mk()
    a = agent()
    W, R = AgentState.WAITING, AgentState.RUNNING
    assert notify.notify_state_change(s, a, R, W, now=0, transport=tr)
    assert not notify.notify_state_change(s, a, R, W, now=5, transport=tr)  # debounce
    assert not notify.notify_state_change(s, a, R, W, now=60, transport=tr)  # dedupe
    assert notify.notify_state_change(s, a, R, W, now=700, transport=tr)
    other = agent(id="codex-3")
    assert notify.notify_state_change(s, other, R, W, now=701, transport=tr)
    assert len(seen) == 3


def test_no_secret_in_body_or_headers():
    seen, tr = recorder()
    a = agent(project="pm\nSECRET token=abc", cwd="/home/me/.secret")
    notify.notify_state_change(mk(), a, AgentState.RUNNING, AgentState.WAITING, now=0,
                               transport=tr)
    r = seen[0]
    blob = r.content.decode() + str(dict(r.headers))
    assert "\n" not in r.content.decode() and "=" not in r.content.decode()
    assert ".secret" not in blob
    assert set(k.lower() for k in r.headers) <= {"title", "tags", "click", "priority", "host",
        "accept", "accept-encoding", "connection", "user-agent", "content-length"}


def test_state_change_failure_swallowed():
    def boom(req):
        raise RuntimeError("x")

    assert notify.notify_state_change(mk(), agent(), AgentState.RUNNING, AgentState.FAILED,
                                      now=0, transport=httpx.MockTransport(boom)) is False


def test_ssl_context_verifies_but_not_strict():
    import ssl

    from pm.notify.ntfy import _ssl_context

    ctx = _ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT
