from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).parent.parent / "src/pm"


def test_limit_colours_independent_of_context_level():
    css = (ROOT / "static/css/agent.css").read_text()
    # limits are coloured by their own lvl-* class, scoped under .ts-lim
    assert ".stats-sum .ts-lim .tl-p{font-weight:700;color:var(--lvl-ok)}" in css
    assert ".stats-sum .ts-lim.lvl-warn .tl-p{color:var(--lvl-warn)}" in css
    assert ".stats-sum .ts-lim.lvl-red .tl-p{color:var(--lvl-red)}" in css
    # no bare ancestor selector that the summary's context level could hit
    assert ".lvl-red .tl-p" not in css.replace(".ts-lim.lvl-red .tl-p", "")
    assert ".lvl-warn .tl-p" not in css.replace(".ts-lim.lvl-warn .tl-p", "")
    assert ".tl-l{color:var(--accent)}" in css and ".tl-x{color:var(--muted)}" in css


def test_limit_span_carries_own_level_class():
    html = (TEMPLATE_DIR / "_stats.html").read_text()
    assert 'class="ts-lim lvl-{{ w.level }}"' in html


def test_context_actions_after_meter_no_banner():
    html = (TEMPLATE_DIR / "_stats.html").read_text()
    summ = html[html.index("<summary"):html.index("</summary>")]
    assert summ.index('class="ts-bar"') < summ.index('<span class="ts-act">') < summ.index("ts-abs")
    assert "st.level == 'red'" in html
    css = (ROOT / "static/css/agent.css").read_text()
    assert ".ts-act{" in css and ".chat-suggest" not in css
