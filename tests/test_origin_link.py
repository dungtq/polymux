"""Fork / continue origin link on the agent pages (live and ended), against real registry ids."""
import re

from helpers import make_client, make_settings
from test_agent_registry import _line, ag

from pm.agents import lineage, registry
from pm.core.models import AgentKind
from pm.infra import snapshot as services
from pm.web.routes.pages import router

REAL_REGISTRY = True


def _env(fake_tmux, tmp_path, monkeypatch, found):
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True, exist_ok=True)
    s = make_settings(fake_tmux, tmp_path)
    monkeypatch.setenv("FAKE_TMUX_OUT", _line("%140", str(dev / "alpha")))
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: list(found))
    return s, make_client(s, router, static=True, logged_in=True)


def _live_id(c, s):
    services.build_and_publish(s)
    return services.published(s).agents[0].id


def has_link(frag: str, agent_id: str) -> bool:
    """An <a> to /a/<id> opening in a new tab with rel=noopener (other attributes and order free)."""
    for m in re.finditer(r"<a\b([^>]*)>", frag):
        a = m.group(1)
        if f'href="/a/{agent_id}"' in a and 'target="_blank"' in a and 'rel="noopener"' in a:
            return True
    return False


def test_live_page_links_to_the_origin(fake_tmux, tmp_path, monkeypatch):
    found = [ag(ident="s1")]
    s, c = _env(fake_tmux, tmp_path, monkeypatch, found)
    new_id = _live_id(c, s)
    src = "claude-abc123"
    rec = registry.get(s, new_id)
    lineage.record(s, src, "fork", rec.last_pane, "claude", now=rec.first_seen - 1)
    t = c.get(f"/a/{new_id}").text
    assert "am-origin" in t
    block = t[t.index("am-origin"):][:600]
    assert has_link(block, src) and "Fork from" in block


def test_live_page_continue_label(fake_tmux, tmp_path, monkeypatch):
    found = [ag(ident="s1")]
    s, c = _env(fake_tmux, tmp_path, monkeypatch, found)
    new_id = _live_id(c, s)
    rec = registry.get(s, new_id)
    lineage.record(s, "claude-abc123", "continue", rec.last_pane, "claude", now=rec.first_seen - 1)
    t = c.get(f"/a/{new_id}").text
    assert "Continued from" in t[t.index("am-origin"):][:600]


def test_live_page_without_origin(fake_tmux, tmp_path, monkeypatch):
    s, c = _env(fake_tmux, tmp_path, monkeypatch, [ag(ident="s1")])
    r = c.get(f"/a/{_live_id(c, s)}")
    assert r.status_code == 200 and "am-origin" not in r.text


def test_ended_page_links_to_the_origin(fake_tmux, tmp_path, monkeypatch):
    found = [ag(ident="s1")]
    s, c = _env(fake_tmux, tmp_path, monkeypatch, found)
    new_id = _live_id(c, s)
    rec = registry.get(s, new_id)
    lineage.record(s, "claude-abc123", "fork", rec.last_pane, "claude", now=rec.first_seen - 1)
    found[:] = [ag(kind=AgentKind.DEVIN, ident="d1", pid=300)]  # the claude ends
    services.build_and_publish(s)
    assert registry.get(s, new_id).ended
    t = c.get(f"/a/{new_id}").text
    row = re.search(r'<[^>]*class="[^"]*\bem-row\b[^"]*"[^>]*>(?:(?!</div>).)*?em-k[^>]*>\s*Origin\s*<.*?</div>',
                    t, re.S)
    assert row and has_link(row.group(0), "claude-abc123")


def test_ended_page_without_origin(fake_tmux, tmp_path, monkeypatch):
    found = [ag(ident="s1")]
    s, c = _env(fake_tmux, tmp_path, monkeypatch, found)
    new_id = _live_id(c, s)
    found[:] = [ag(kind=AgentKind.DEVIN, ident="d1", pid=300)]
    services.build_and_publish(s)
    r = c.get(f"/a/{new_id}")
    assert r.status_code == 200 and ">Origin<" not in r.text
