"""Static contracts of the shared tab bar (#163): one rule file (css/tabs.css) with three looks (flat, .tabs-seg boxed
group, .tabs-line doc tabs), underline colours only as tokens, and every tablist in the templates made of role=tab +
aria-selected tabs with the .tab class."""
import re
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

PM = Path(__file__).resolve().parents[1] / "src" / "pm"
CSS = PM / "static" / "css"
TPL = TEMPLATE_DIR


def _rules(text: str):
    """(selector list, body) pairs of a flat CSS file, @media wrappers included in the walk."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return [(s.strip(), b) for s, b in re.findall(r"([^{}]+)\{([^{}]*)\}", text)]


def test_tabs_css_is_linked_right_after_chrome_css():
    base = (TPL / "base.html").read_text()
    sheets = re.findall(r'<link rel="stylesheet" href="/static/css/([\w-]+)\.css">', base)
    assert sheets[sheets.index("chrome") + 1] == "tabs"


LOOK_SELS = {".tabs", ".tab", ".tab:hover", ".tab:focus-visible", ".tabs.tabs-seg", ".tabs-seg .tab",
             ".tabs-seg .tab[aria-current=page]", ".tabs-seg .tab[aria-selected=true]", ".tabs.tabs-line", ".tabs-line .tab",
             ".tabs-line .tab[aria-selected=true]"}  # the variant looks; page sheets may only add layout on longer selectors
LOOK = re.compile(r"(^|;)\s*(color|background|border|box-shadow|font|outline|display|padding)\b")


def test_one_tabs_and_tab_definition():
    owners = {}
    for f in sorted(CSS.glob("*.css")):
        for sel, body in _rules(f.read_text()):
            if not LOOK.search(body):  # e.g. shell.css's shared overscroll-behavior list
                continue
            for s in (x.strip() for x in sel.split(",")):
                if s in LOOK_SELS or re.search(r":checked\s*~.*(\.tab-l-|\[for=)", s):
                    owners.setdefault(s, set()).add(f.name)
    assert owners and all(v == {"tabs.css"} for v in owners.values()), owners


def test_tab_underline_only_in_tabs_css():
    bad = [f.name for f in CSS.glob("*.css") if f.name != "tabs.css" and re.search(r"inset\s+0\s+-2px", f.read_text())]
    assert bad == []
    s = (CSS / "tabs.css").read_text()
    assert "inset 0 -2px 0 var(--tab-line,var(--accent))" in s and "--tab-line:var(--k,var(--tab-on))" in s
    assert "border-bottom-color:var(--tab-line,var(--accent))" in s  # .tabs-line
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", re.sub(r"/\*.*?\*/", "", s, flags=re.S))  # tokens only


def test_variants_per_page():
    """Servers, Agents, ntfy = boxed group; project docs, tmux|Stack|VM, Git, Skills = doc tabs; agent page and Builder = flat."""
    seg = {"_servers.html": "srv-tabs", "_agents_macros.html": "ag-tabs", "_ntfy.html": "ntfy-tabs"}
    line = {"project.html": "pj-tabs", "_project_live.html": "data-ptabs", "_project_git.html": "pj-subtabs",
            "_project_skills.html": "pj-sktabs"}
    for name, hook in seg.items():
        assert re.search(r'class="tabs ' + hook + r' tabs-seg"', (TPL / name).read_text()), name
    for name, hook in line.items():
        t = (TPL / name).read_text()
        bar = next(m.group(0) for m in re.finditer(r"<div[^>]*role=\"tablist\"[^>]*>", t))
        assert "tabs-line" in bar and hook in bar, (name, bar)
    for name in ("agent.html", "builder.html"):
        bar = next(m.group(0) for m in re.finditer(r"<div[^>]*role=\"tablist\"[^>]*>", (TPL / name).read_text()))
        assert "tabs-seg" not in bar and "tabs-line" not in bar, name


def test_orange_literal_only_in_tokens_css():
    hits = [str(f.relative_to(PM)) for f in list(CSS.glob("*.css")) + list((PM / "static/js").glob("*.js")) + list(TPL.glob("*.html"))
            if f.name != "tokens.css" and "fd8c73" in f.read_text().lower()]
    assert hits == []
    assert re.search(r"--tab-on:\s*#fd8c73", (CSS / "tokens.css").read_text())


def _tablists(text: str):
    """Each role=tablist element's inner markup up to its closing </div> (the tab bars hold no nested divs)."""
    for m in re.finditer(r'<div[^>]*role="tablist"[^>]*>', text):
        end = text.index("</div>", m.end())
        yield m.group(0), text[m.end():end]


def test_every_tablist_has_tab_role_aria_selected_and_tab_class():
    seen = 0
    for f in sorted(TPL.glob("*.html")):
        for head, inner in _tablists(f.read_text()):
            if 'id="vc-tabs"' in head:  # the chat aside: built by drawer.js (checked below)
                continue
            seen += 1
            assert re.search(r'class="[^"]*\btabs\b', head), (f.name, head)
            tabs = re.findall(r"<(?:a|button|label)\b[^>]*>", inner)
            assert tabs, f.name
            for t in tabs:
                assert 'role="tab"' in t and "aria-selected=" in t, (f.name, t)
                assert re.search(r'class="(?:tab|tab [^"]*)"', t), (f.name, t)
    assert seen >= 7  # agent, builder, project docs, tmux|Stack|VM, git, skills, ntfy
    drawer = (PM / "static/js/drawer.js").read_text()
    assert "b.className = 'tab vc-tab'" in drawer and "setAttribute('role', 'tab')" in drawer and "'aria-selected', 'false'" in drawer


def test_nav_tab_bars_use_aria_current_links():
    for name in ("_servers.html", "_agents_macros.html"):
        t = (TPL / name).read_text()
        assert re.search(r'<nav class="tabs [\w -]+"', t) and 'class="tab ' in t and 'aria-current="page"' in t, name


def test_keyed_tab_bars_persist_through_pm_tabs():
    assert 'data-tabs-key="pm.ntfy.tab"' in (TPL / "_ntfy.html").read_text()
    assert 'data-tabs-key="pm.ptab"' in (TPL / "_project_live.html").read_text()
    assert 'data-tabs-key="pm.pjgit.sub"' in (TPL / "_project_git.html").read_text()
    for name in ("ended.js", "projtabs.js", "projview.js"):  # the old private copies of the select/persist code are gone
        js = (PM / "static/js" / name).read_text()
        assert "localStorage.setItem('pm.ntfy.tab'" not in js and "TABKEY" not in js and "SKEY" not in js, name
        assert "pm.tabs." in js, name
