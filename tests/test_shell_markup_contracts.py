"""2026-10-10 backlog round (static/markup contracts; Playwright covers behaviour in tests/ui)."""
import re
from pathlib import Path

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.web.render import TEMPLATE_DIR

SRC = Path(__file__).resolve().parents[1] / "src/pm"


def test_project_right_column_is_half_and_skills_are_columns():
    css = (SRC / "static/css/doctabs.css").read_text()
    assert re.search(r"grid-template-columns:minmax\(0,1fr\) minmax\(0,1fr\)[^}]*gap:0", css)  # 50/50 like the chat aside
    assert ".pj-sk{table-layout:fixed;" in css
    tpl = (TEMPLATE_DIR / "_project_skills.html").read_text()
    assert '<table class="pj-sk"' in tpl and tpl.index('sk-n') < tpl.index('sk-s') < tpl.index('sk-d')


def test_aside_carry_is_one_shared_handler(env):
    """The chat aside survives every navigation through ONE handler in drawer.js (pagehide stash + history wrappers),
    restored by drawer-init.js; the old per-control patches are gone."""
    js = (SRC / "static/js/drawer.js").read_text()
    init = (SRC / "static/js/drawer-init.js").read_text()
    assert "'pagehide'" in js and "sessionStorage" in js and "history[fn]" in js and "pm.v5.carry" in js
    assert "pm.v5.carry" in init and "history.replaceState" in init
    for gone in ("KEEP = ", "pmKeepAside", "bl-edit, form.bl-item", "CARRY = "):
        assert gone not in js
    for f in ("lists.js", "backlog.js"):
        t = (SRC / "static/js" / f).read_text()
        assert "pmKeepAside" not in t and "(chat|term)=" not in t


def test_nav_icons_css_covers_every_section():
    css = (SRC / "static/css/icons.css").read_text()
    for href in ("/", "/p", "/tmux", "/servers", "/backlog", "/audit"):
        assert f'.tabbar a[href="{href}"]{{--ic:' in css
    for key in ("inbox", "projects", "tmux", "backlog", "audit"):
        assert f'.vs-nav .vs-row[data-p="{key}"]{{--ic:' in css


def test_topbar_has_single_account_menu(env):
    s, c = env
    login(s, c)
    h = c.get("/audit").text
    hdr = h.split('<header class="topbar">')[1].split("</header>")[0]
    assert hdr.count('id="acct-menu"') == 1 and "data-qchat-open" in hdr
    outside = hdr.split('<details class="acct"')[0]
    assert "data-theme-toggle" not in outside and 'action="/logout"' not in outside and 'href="/settings"' not in outside


def test_notes_for_agents():
    """AGENTS.md carries the hard rules; CLAUDE.md pulls it in."""
    root = SRC.parents[1]
    agents = (root / "AGENTS.md").read_text()
    assert "Hard rules" in agents and "argv list" in agents and "mutation_guard" in agents
    assert "@AGENTS.md" in (root / "CLAUDE.md").read_text()


def test_round3_static_contracts():
    """2026-10-10 round 3: skills collapsed, aside carried over filter/save, Interrupt/Exit below the composer."""
    tpl = (TEMPLATE_DIR / "_project_skills.html").read_text()
    assert 'data-sk-tab="{{ scope }}"' in tpl and '<div class="pj-skgrp"' in tpl and 'if scope != "local" %} hidden' in tpl  # Local first
    ag = (TEMPLATE_DIR / "_composer_macros.html").read_text()
    ag = ag[ag.index("{% macro composer("):]  # the root: the Exit row sits below the box
    assert ag.index('class="mrow"') > ag.index('class="cform"') and ag.index('mb-act') > ag.index('class="mrow"')
    assert "nterrupt" not in ag.split('<div class="mrow">')[1].split("</div>{% endif %}")[0]  # Interrupt: only the Send/Stop slot
    assert "pagehide" in (SRC / "static/js/drawer.js").read_text()  # 2026-10-10: one shared handler replaced the per-control hash patches
    base = (TEMPLATE_DIR / "base.html").read_text()
    q = base[base.index('id="qchat"'):base.index('<div class="qc-rmenu')]
    assert "claude -p" not in q.split('class="qc-log"')[0] and "qc-note" in q
    assert q.index('composer("qchat"') < q.index("qc-model")  # the shared composer (Send inside its box), then the model row
