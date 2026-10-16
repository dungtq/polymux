"""The shared filter box on /p and /tmux: one box, rows with data-key/data-q/data-frow, server ?q= before the Show cap."""

import re

from helpers import login, pane_line
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import sorting
from pm.core.models import GitStatus, Project

HX = {"HX-Request": "true"}
CSRF = re.compile(r'name="csrf" value="[^"]+"')


def _main(c, url, **kw):
    return c.get(url, **kw).text.split("<main", 1)[1]


def _sessions(monkeypatch, names):
    """One pane per session; session 1 keeps %12 so the fixture's claude-12 belongs to it."""
    out = "".join(
        pane_line(f"${i}", n, f"@{i}", "%12" if i == 1 else f"%{100 + i}", "/tmp") for i, n in enumerate(names, 1))
    monkeypatch.setenv("FAKE_TMUX_OUT", out)


def _twelve():
    return [f"sess{i:02d}" for i in range(1, 12)] + ["zebra"]


def _rows(html):
    return re.findall(r'<div class="srowwrap"[^>]*>', html)


def test_one_filter_box_no_search_buttons(env, monkeypatch):
    s, c = env
    login(s, c)
    for url in ("/p", "/tmux", "/p?q=zzz", "/tmux?q=zzz"):
        h = _main(c, url)
        assert len(re.findall(r"<input[^>]*\bdata-filter[ >]", h)) == 1, url
        assert "Search projects" not in h and ">Search<" not in h and ">Clear<" not in h, url
        assert "psearch" not in h, url
    p = _main(c, "/p")
    assert 'data-filter-live data-filter-target="#plist"' in p and 'action="/p"' in p
    assert re.search(r'<div id="plist" hx-get="[^"]*" hx-trigger="pm:refresh" hx-swap="innerHTML" data-nodiff>', p)
    assert p.index("data-filter") < p.index('name="f" data-nav') < p.index('name="n" data-nav')
    t = _main(c, "/tmux")
    assert 'id="tmux-filter"' in t and 'data-filter-target="#tmux-list"' in t and 'action="/tmux"' in t
    assert re.search(r'<div id="tmux-list" hx-get="[^"]*" hx-trigger="pm:refresh" hx-swap="innerHTML" data-nodiff>', t)
    assert t.index("data-filter") < t.index('data-details-toggle="open"') < t.index('name="n" data-nav')


def test_project_rows_carry_filter_attrs(env):
    s, c = env
    login(s, c)
    h = _main(c, "/p")
    row = re.search(r'<div class="pitem" role="listitem" data-key="p-alpha" data-q="([^"]*)" data-frow>', h)
    assert row, "key, q, frow in that order, frow last"
    q = row.group(1)
    assert q == q.lower() and "alpha" in q and "/dev/alpha" in q
    assert 'class="pitem phd"' in h and h.count("data-frow") == 1  # the header row is not filterable


def test_project_q_is_name_type_text():
    q = sorting.project_q(Project("Alpha", "/Dev/Alpha", git=GitStatus("Feat-X", dirty=1)))
    assert "alpha" in q and "/dev/alpha" in q and "feat-x" in q


def test_projects_server_q_matches_path_and_name(env):
    s, c = env
    login(s, c)
    (s.dev_root / "beta").mkdir()
    assert 'href="/p/beta"' in _main(c, "/p") and 'href="/p/alpha"' in _main(c, "/p")
    h = _main(c, "/p?q=ALPHA")
    assert 'href="/p/alpha"' in h and 'href="/p/beta"' not in h
    assert 'href="/p/alpha"' in _main(c, "/p?q=dev/alp")  # path text is searched too
    assert "No projects found" in _main(c, "/p?q=zzz")


def test_projects_q_filters_before_show_cap(env):
    s, c = env
    login(s, c)
    for i in range(12):
        (s.dev_root / f"proj{i:02d}").mkdir()
    (s.dev_root / "zulu").mkdir()
    h = _main(c, "/p?n=10&sort=name")
    assert 'href="/p/zulu"' not in h and "showing 10 of" in h
    h = _main(c, "/p?n=10&sort=name&q=zulu")
    assert 'href="/p/zulu"' in h and "showing 1 of 1" in h


def test_projects_fragment_on_hx_request(env):
    s, c = env
    login(s, c)
    r = c.get("/p?q=alpha", headers=HX)
    assert r.status_code == 200 and "<html" not in r.text and "<h1>" not in r.text
    assert 'data-key="p-alpha"' in r.text and CSRF.search(r.text) and "showing 1 of 1" in r.text
    assert 'data-key="p-' not in c.get("/p?q=zzz", headers=HX).text
    assert "<html" in c.get("/p", headers={**HX, "HX-Boosted": "true"}).text  # boosted nav = full page


def test_tmux_rows_carry_filter_attrs(env, monkeypatch):
    s, c = env
    login(s, c)
    _sessions(monkeypatch, ["Work", "other"])
    h = _main(c, "/tmux")
    rows = _rows(h)
    assert len(rows) == 2 and 'class="hrow srowhead"' in h
    mine = next(r for r in rows if 'data-key="t-$1"' in r)
    m = re.search(r'data-key="t-\$1" data-q="([^"]*)" data-frow>$', mine)
    assert m, mine
    q = m.group(1)
    assert q == q.lower() and "work" in q and "main" in q and "claude-12" in q  # session, window, agent
    assert "pane" not in q and "/tmp" not in q
    assert all(r.endswith(" data-frow>") for r in rows)


def test_tmux_q_filters_before_show_cap(env, monkeypatch):
    s, c = env
    login(s, c)
    _sessions(monkeypatch, _twelve())
    h = _main(c, "/tmux?n=10&sort=name")
    assert len(_rows(h)) == 10 and "zebra" not in h and "showing 10 of 12" in h
    h = _main(c, "/tmux?n=10&sort=name&q=zebra")
    assert len(_rows(h)) == 1 and "zebra" in h and "showing 1 of 1" in h
    h = _main(c, "/tmux?n=10&sort=name&q=SESS1")  # case-insensitive; sess10, sess11
    assert len(_rows(h)) == 2 and "showing 2 of 2" in h
    assert "No tmux sessions" in _main(c, "/tmux?q=nomatch")
    assert "claude-12" in _main(c, "/tmux?q=claude-12") and len(_rows(_main(c, "/tmux?q=claude-12"))) == 1  # agent text


def test_tmux_show_form_and_sort_links_keep_q(env, monkeypatch):
    s, c = env
    login(s, c)
    _sessions(monkeypatch, _twelve())
    h = _main(c, "/tmux?q=sess&sort=name&n=10")
    bar = h[h.index('id="tmux-filter"'):h.index("</form>", h.index('id="tmux-filter"'))]
    assert 'name="q"' in bar and 'value="sess"' in bar and 'name="sort" value="name"' in bar


def test_tmux_open_pin_with_q(env, monkeypatch):
    s, c = env
    login(s, c)
    _sessions(monkeypatch, _twelve())
    h = _main(c, "/tmux?n=10&sort=name&q=sess&open=s2")  # sess02 matches: shown, open
    assert 'id="sess-s2"' in h and "data-focus" in h
    h = _main(c, "/tmux?n=10&sort=name&q=zebra&open=s3")  # pinned beside the match even though q hides it
    assert 'id="sess-s12"' in h and 'id="sess-s3"' in h and "data-focus" in h
    h = _main(c, "/tmux?n=1&sort=-name&open=s2")  # capped to one row, the opened one is still there
    assert 'id="sess-s2"' in h and '<details class="sessrow" open data-focus>' in h


def test_tmux_fragment_on_hx_request(env, monkeypatch):
    s, c = env
    object.__setattr__(s, "unprotected_sessions", ("zebra",))  # only unprotected sessions get a kill form
    login(s, c)
    _sessions(monkeypatch, _twelve())
    r = c.get("/tmux?q=zebra", headers=HX)
    assert r.status_code == 200 and "<html" not in r.text and "<h1>" not in r.text
    assert len(_rows(r.text)) == 1 and 'data-key="t-$12"' in r.text and "showing 1 of 1" in r.text
    assert CSRF.search(r.text)  # the kill forms need csrf
    assert 'id="kill-dialog"' not in r.text and 'id="tmux-filter"' not in r.text
    r = c.get("/tmux?q=zebra&open=s12", headers=HX)
    assert "open data-focus" in r.text
    assert "<html" in c.get("/tmux", headers={**HX, "HX-Boosted": "true"}).text
