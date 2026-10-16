"""Desktop and phone rows: click the agent id for a recap, click the rest of the row for the full chat (ntfy panel + agent tables)."""

from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parent.parent / "src" / "pm"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_row_markup_has_recap_box_for_owner():
    t = (TEMPLATE_DIR / "_ntfy.html").read_text()
    assert 'class="ntfy-recap n-recap"' in t and 'data-recap="/a/{{ r.owner.agent_id|urlencode }}/recap-row"' in t
    assert t.index("ntfy-recap") < t.index("ntfy-msg")  # recap above the message
    assert 'class="ntfy-agent" role="button" aria-expanded="false" href="/a/' in t  # button role; href stays as no-JS fallback


def test_js_contract_is_universal():
    j = _read("static/js/ntfy.js")
    assert "matchMedia" not in j and "pointer: coarse" not in j  # no touch-only gate any more
    assert "a.ntfy-agent" in j and "stopImmediatePropagation" in j and "aria-expanded" in j and "ag.click()" in j
    assert "window.pm.loadRecap" in j and "alert(" not in j and "fetch(" not in j.replace("pm.fetch(", "")
    assert "pm.loadRecap = function" in _read("static/js/core.js")


def test_agent_tables_id_is_the_recap_toggle():
    n = (TEMPLATE_DIR / "_agentname.html").read_text()
    assert 'data-rx role="button" tabindex="0" aria-expanded="false"' in n
    for f in ("_inbox.html", "_project_live.html"):
        assert "agent_row(a, " in (TEMPLATE_DIR / f).read_text()  # the shared row (_agentrow.html)
    m = (TEMPLATE_DIR / "_agentrow.html").read_text()
    assert "agent_name(a, rx=True)" in m and "agent_name(r, model=model, rx=True)" in m  # live and ended rows
    r = _read("static/js/rowx.js")
    assert ".aname[data-rx]" in r and "window.pm.loadRecap" in r and "hasAttribute('data-rx')" in r


def test_recap_hidden_unless_opened():
    c = _read("static/css/ntfy.css")
    assert ".ntfy-recap { display: none;" in c and ".ntfy-row[data-recap-open] .ntfy-recap { display: block; }" in c
