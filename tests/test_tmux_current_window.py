"""tmux window rows are never starred or highlighted (user decision 2026-10-09: highlight only the link you are in);
the page still refreshes itself through /tmux/<sid>/live."""

import pytest
from helpers import make_client, make_settings

from pm.infra import snapshot as services
from pm.tmux.ctl import SEP


def _line(sid, sname, wid, widx, wname, pid, act):
    return SEP.join([sid, sname, "0", wid, widx, wname, act, pid, "4242", "fish", "/tmp", "1", "t"]) + "\n"


def _out(active1, active2="@21"):
    """Session $1 with windows @1 @2 @3; session $2 with @20 @21 (its own current window)."""
    a = lambda cur, wid: "1" if cur == wid else "0"  # noqa: E731
    return (_line("$1", "work", "@1", "1", "one", "%1", a(active1, "@1"))
            + _line("$1", "work", "@2", "2", "two", "%2", a(active1, "@2"))
            + _line("$1", "work", "@3", "3", "three", "%3", a(active1, "@3"))
            + _line("$2", "other", "@20", "1", "o1", "%20", a(active2, "@20"))
            + _line("$2", "other", "@21", "2", "o2", "%21", a(active2, "@21")))


@pytest.fixture
def env(fake_tmux, tmp_path, monkeypatch):
    s = make_settings(fake_tmux, tmp_path)
    (tmp_path / "dev").mkdir()
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: [])
    c = make_client(s, logged_in=True)

    def switch(active1, active2="@21"):
        monkeypatch.setenv("FAKE_TMUX_OUT", _out(active1, active2))
        services.build_snapshot(s, force=True)  # what the poll loop does every few seconds

    return c, switch


def _unmarked(html):
    return "tx-act" not in html and "tx-star" not in html and "ts-cur" not in html


def test_no_star_or_highlight(env):
    c, switch = env
    for act in ("@2", "@3", None):
        switch(act)
        for url in ("/tmux/s1/live", "/tmux/s2/live", "/tmux", "/"):
            assert _unmarked(c.get(url).text), (act, url)
    r = c.get("/tmux/s1", follow_redirects=False)  # the per-session page now redirects to the list
    assert r.status_code == 303 and r.headers["location"] == "/tmux?open=s1"


def test_live_rows_know_pms_own_session(env, monkeypatch):
    """``own_sid`` in the /tmux/<sid>/live context (themes disable the kill of pm's own session there)."""
    from pm.web.routes import tmuxpages

    c, switch = env
    seen = []

    class Spy:
        def render(self, **ctx):
            seen.append(ctx.get("own_sid", "missing"))
            return "<tr></tr>"

    monkeypatch.setattr(tmuxpages, "get_template", lambda name: Spy())
    switch("@1")
    monkeypatch.setenv("TMUX_PANE", "%20")
    assert c.get("/tmux/s1/live").status_code == 200
    monkeypatch.delenv("TMUX_PANE")
    assert c.get("/tmux/s1/live").status_code == 200
    assert seen == ["$2", None]
