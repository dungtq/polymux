"""Static contract of the sidebar resize handle (strict CSP: no inline script; Playwright covers behaviour)."""
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"


def test_side_resize_wiring():
    js = (ROOT / "static/js/side.js").read_text()
    for needle in ("separator", "aria-orientation", "aria-valuenow", "pm.v5.sidew", "dblclick", "ArrowLeft", "--side-user", "setPointerCapture"):
        assert needle in js
    assert "eval(" not in js
    init = (ROOT / "static/js/side-init.js").read_text()
    assert "pm.v5.sidew" in init and "--side-user" in init
    css = (ROOT / "static/css/shell.css").read_text()
    assert "--side-w:var(--side-user,248px)" in (ROOT / "static/css/tokens.css").read_text() and ".vs-grip" in css
    assert "vs-grip" not in (TEMPLATE_DIR / "_side.html").read_text()


def test_sidebar_collapse_per_tab_width_saved():
    import pathlib
    js = pathlib.Path(__file__).parent.parent / "src/pm/static/js"
    side, init = (js / "side.js").read_text(), (js / "side-init.js").read_text()
    assert "sessionStorage" in side and "sessionStorage" in init and "pm:reset-layout" in side
    assert "'storage'" not in side and "data-layout-reset" in side and "vs-reset" not in side
    assert "pm:reset-layout" in (js / "drawer.js").read_text()


def test_drawer_terminal_header_icon_and_no_back():
    from pm.web.render import env
    drawer = env.get_template("_drawer.html").render()  # the head comes from _agentname.html agent_head(mode aside)
    js = (ROOT / "static/js/drawer.js").read_text()
    assert 'id="vc-ico"' in drawer and "polyline" in drawer
    assert "terminal, read-only" not in js and "getElementById('vc-ico').toggleAttribute('hidden', !term)" in js
    css = (ROOT / "static/css/agent.css").read_text()
    assert ".termpage.embed .term-head .term-back{display:none}" in css


def test_project_list_state_is_prepainted_and_not_flashing():
    js = Path(__file__).resolve().parents[1] / "src/pm/static"
    init, side, css = (js / "js/side-init.js").read_text(), (js / "js/side.js").read_text(), (js / "css/shell.css").read_text()
    assert "pm.v5.projopen" in init and "pj-" in init and "pj-0" in side
    assert "localStorage.getItem(PK)" not in side  # the old saved-open flag (flashed open, then closed) is gone
    assert "html.pj-0 .vs-proj{display:none}" in css and "settings-dialog" in side


def test_sidebar_filter_is_the_shared_scoped_box():
    """The sidebar box is filterbox(scope="#v5side"): filter.js filters its [data-q] rows (name-type text, not the
    row's whole textContent), side.js has no filter of its own, and the badges go through pm.badges."""
    side = (ROOT / "static/js/side.js").read_text()
    assert ".vs-filter" not in side and "textContent.toLowerCase()" not in side
    assert "pm.badges.on(badges)" in side and "pm.badges.set(b," in side
    tpl = (TEMPLATE_DIR / "_side.html").read_text()
    assert 'filterbox(scope="#v5side", cls="vs-filter", label=sidebar.filter_label)' in tpl
    assert '<input class="vs-filter"' not in tpl and tpl.count("data-fnone") == 2
    for row in ("[n.name, n.kind, n.model, n.tag]", "[h.id, h.kind, h.model]", "[r.name, r.key]"):
        assert "data-q=\"{{ " + row + " | select | join(' ') | lower }}\"" in tpl, row
    flt = (ROOT / "static/js/filter.js").read_text()
    assert "data-filter-scope" in flt and "[data-fnone]" in flt and "r.hidden = !ok" in flt
    css = (ROOT / "static/css/shell.css").read_text()
    assert ".vs-filter input[type=search]{" in css
