"""Static guards for the 2026-10-11 UI fixes (CSS/JS/template text only)."""
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

S = Path(__file__).resolve().parent.parent / "src/pm"


def read(p):
    return (S / p).read_text()


def test_open_agent_highlight_covers_ntfy_but_not_history_rows():
    js = read("static/js/drawer.js")
    row = next(l for l in js.splitlines() if "var ROW =" in l)
    assert ".ntfy-row" in row and "hist-row" not in row and ".nrow" in row and ".tx-row" in row
    css = read("static/css/shell.css")
    assert "ntfy-row.v5-active" not in css and "hist-row.v5-active" not in css
    ncss = read("static/css/ntfy.css")
    assert ".ntfy-row.v5-active > summary:hover { background: var(--accent-soft); }" in ncss
    assert ncss.index(".ntfy-row[open] > summary") < ncss.index(".ntfy-row.v5-active > summary")


def test_collapsed_tmux_row_is_one_track():
    css = read("static/css/nav.css")
    assert '.pg-project .srow:not([data-open]){grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"main act";grid-template-rows:auto;grid-auto-rows:0;row-gap:0' in css


def test_row_buttons_have_no_filled_cell():
    for f in ("static/css/rows.css", "static/css/inbox.css"):
        for l in read(f).splitlines():
            if ".n-exit" in l and "{" in l and "linkbtn" not in l:
                assert "var(--hover)" not in l and "var(--bg)" not in l, l


def test_agents_detail_right_column_spans_from_top():
    html = (TEMPLATE_DIR / "agents_detail.html").read_text()
    i = html.index('<div class="ag-split">')
    assert i < html.index('{{ page_head("Agents", stats) }}') < html.index('class="ag-right"')


def test_jump_rail_inset_and_autohide():
    css = read("static/css/nav.css")
    assert "right:28px" in css and ".jump-rail.is-on,.jump-rail:focus-within{opacity:1" in css
    js = read("static/js/jumprail.js")
    assert "1500" in js and "focusin" in js
    assert "jumprail.js" in (TEMPLATE_DIR / "base.html").read_text() and "jumprail.js" in (TEMPLATE_DIR / "agent.html").read_text()


def test_row_action_lives_in_detail_panel_not_overlay():
    css = read("static/css/inbox.css")
    for l in (read("static/css/inbox.css") + read("static/css/rows.css")).splitlines():
        if ".n-exit" in l and "{" in l and "linkbtn" not in l:
            assert "position: absolute" not in l and "position:absolute" not in l, l
    assert ".nwrap:hover:not(:has(> a.v5-active))" in css
    assert "margin-right: var(--act-w)" not in css and "padding-right: calc(var(--act-w)" not in css  # no empty trailing column
    assert ".nwrap[data-open]>.n-detail{display:block}" in read("static/css/rows.css")


def test_row_grid_thresholds_have_no_action_slot():
    """exit / resume moved into the expanded detail panel: the grid needs no reserved trailing slot."""
    lists = read("static/css/rows.css")
    for old in ("724px", "864px", "964px", "1084px"):
        assert f"@container inb (min-width:{old})" not in lists, old
    for new in ("640px", "760px", "900px", "1000px", "1120px"):
        assert f"@container inb (min-width:{new})" in lists, new
    v5 = read("static/css/shell.css")
    assert "@container lst (max-width:1039px){\n    .pg-project .arow .c-start" in v5 and ".pg-project .list{container:lst/inline-size}" in v5


def test_popup_script_loaded_in_base_and_covers_popovers():
    """popup.js absorbed outclick.js: outside pointerdown + Escape close the <details> popovers, also across the drawer iframe."""
    base = (TEMPLATE_DIR / "base.html").read_text()
    assert "/static/js/popup.js" in base and "outclick.js" not in base
    assert base.index("popup.js") < base.index("{% if not embed %}<script src=\"/static/js/acct.js\"")  # not behind the embed guard
    js = read("static/js/popup.js")
    assert "pointerdown" in js and "Escape" in js and "postMessage" in js and "'outclick'" in js
    assert "innerHTML" not in js
    for c in ("details.acct", "details.tab-stats", "details.dt-pop", "details.newsess", "details[data-pop]"):
        assert c in js


def test_nodiff_keeps_user_owned_data_open():
    """A live refresh must not strip data-open from an expanded row (it collapsed, then the scroll restore was clamped)."""
    assert "a !== 'data-open'" in read("static/js/nodiff.js")


def test_chat_open_scrolls_page_column_and_aside_separately():
    css = read("static/css/shell.css")
    assert "html:has(body.chat-open){overflow:hidden}" in css
    assert "body.chat-open .v5-shell>main{height:calc(100vh - var(--top-h));min-height:0;overflow-y:auto" in css
    assert 'id="pg-rail"' in (TEMPLATE_DIR / "base.html").read_text() and "data-main-jump" in (TEMPLATE_DIR / "base.html").read_text()
    assert "pg-rail" in read("static/js/jumprail.js")


def test_drawer_binds_its_own_jump_rail_not_the_page_rail():
    assert "d.querySelector('#v5chat .jump-rail')" in read("static/js/drawer.js")


def test_show_hide_recap_buttons_on_table_headings():
    assert "macro rx_all()" in (TEMPLATE_DIR / "_macros.html").read_text() and "rx_all() }}" in (TEMPLATE_DIR / "_agentrow.html").read_text()  # Ended
    assert (TEMPLATE_DIR / "_project_live.html").read_text().count("rx_all()") == 2 and "rx_all()" in (TEMPLATE_DIR / "_inbox.html").read_text()
    js = read("static/js/rowx.js")
    assert ".rx-all" in js and "Hide recap" in js and "stopImmediatePropagation" in js


def test_nodiff_never_drops_an_event_refresh():
    """A refresh asked for while one is in flight is run once after it, not dropped (rows kept a stale state)."""
    js = read("static/js/nodiff.js")
    assert "if (busy.get(el)) { pending.set(el, true); return null; }" in js
    assert "if (pending.get(el)) { pending.delete(el); refresh(el); }" in js


def test_drawer_esc_closes_md_popup_before_the_aside():
    """One Escape listener in drawer.js; it respects defaultPrevented and closes the md popup first."""
    js = read("static/js/drawer.js")
    assert js.count("'Escape'") == 1
    i = js.index("e.key !== 'Escape' || e.defaultPrevented")
    assert js.index("mdp && !mdp.hidden", i) < js.index("close();", i)


def test_drawer_model_pill_reads_the_heading_data_model():
    """The aside model pill comes from the framed heading's data-model (agent_head), never scraped pill text."""
    js = read("static/js/drawer.js")
    assert "head.querySelector('.agent-title[data-model]')" in js and "getAttribute('data-model')" in js
    assert "getElementById('agent-pill')" not in js and ".kp-model')" not in js
    assert 'data-model="{{ m }}"' in (TEMPLATE_DIR / "_agentname.html").read_text()


def test_drawer_pill_follows_a_model_switch_in_the_frame():
    """The aside listens for pm:composer on the framed page's document (a fresh document per load: no pile-up)."""
    js = read("static/js/drawer.js")
    i = js.index("frame.addEventListener('load', function () {\n    try { var fd")
    body = js[i:js.index("function close(", i)]
    assert "doc.addEventListener('pm:composer'" in body and "pills(mm)" in body and "x.pill" in body
