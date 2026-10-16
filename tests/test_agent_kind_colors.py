"""Per-kind identity colours: tokens, data-kind hooks, no stray hex."""
import re
from pathlib import Path

from pm.core.models import AgentKind
from pm.notify.owner import Owner
from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src/pm"
KINDS = [k.value for k in AgentKind]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text()


def test_tokens_cover_every_kind_with_light_and_dark():
    tokens = _read("static/css/tokens.css")
    css = _read("static/css/chrome.css")
    for k in KINDS:
        assert re.search(rf"--k-{k}:light-dark\(#[0-9a-f]{{6}},#[0-9a-f]{{6}}\)", tokens), k
        assert f"[data-kind={k}]" in css or k == "cursor-agent" and "[data-kind=cursor-agent]" in css
    assert ":where([data-kind]){--k:var(--k-other)}" in css  # unknown kinds -> other


def test_no_kind_hex_outside_token_definitions():
    for rel in ("static/css/shell.css", "static/css/agent.css", "static/css/chat.css", "static/css/side-tmux.css", "static/css/lists.css"):
        for hexv in ("#c96442", "#e0866a", "#7c5cdb", "#a58af0", "#0e8f8f", "#3cc0c0", "#d1388f", "#f06ab2"):
            assert hexv not in _read(rel), (rel, hexv)


def test_templates_carry_data_kind():
    need = {
        "_macros.html": ['data-kind="{{ kind }}"'],
        "_agentrow.html": ['data-kind="{{ a.kind.value }}"', 'data-kind="{{ r.kind }}"'],  # Inbox + project rows
        "_history.html": ['data-kind="{{ h.kind }}"'],
        "_side.html": ['data-kind="{{ n.kind }}"', 'data-kind="{{ h.kind }}"'],
        "_tmux_session_rows.html": ['data-kind="{{ a.kind.value }}"'],
        "_ci_macros.html": ['data-kind="{{ agent.kind.value }}"'],
        "agent.html": ['data-kind="{{ agent.kind.value }}"'],
        "agent_ended.html": ['data-kind="{{ rec.kind }}"'],
        "_ntfy.html": ['data-kind="{{ r.owner.kind }}"'],
        "launch.html": ['data-kind="{{ k }}"'],
    }
    for rel, needles in need.items():
        txt = (TEMPLATE_DIR / rel).read_text()
        for n in needles:
            assert n in txt, (rel, n)


def test_state_dot_rules_untouched_by_kind():
    css = _read("static/css/side-tmux.css")
    assert "ts-kp-running .kpill" not in css  # the kind pill follows kind, not state
    assert ".dot-running{background:var(--st-run)}" in _read("static/css/rows.css")


def test_drawer_head_follows_row_kind():
    js = _read("static/js/drawer.js")
    assert "vc-head" in js and "data-kind" in js
    assert "vc-head[data-kind]" in _read("static/css/shell.css")


def test_ntfy_owner_kind_from_label():
    mk = lambda label: Owner(agent_id="x", label=label, pane="", live=True)
    assert mk("dev-pm-3-claude").kind == "claude"
    assert mk("dev-pm-codex-2").kind == "codex"
    assert mk("dev-web-cursor-agent").kind == "cursor-agent"
    assert mk("dev-antigravity").kind == "antigravity"
    assert mk("whatever").kind == "other"


def test_model_pill_follows_kind_and_agent_id_not_bold():
    css = _read("static/css/chrome.css")
    assert "[data-kind] .kpill.kp-model{--pill:var(--k)}" in css
    assert ".nrow .n-main strong.aname" in css and "font-weight:400!important" in css


def test_tmux_window_text_inherits_cell_size():
    assert "font-size: inherit" in next(l for l in _read("static/css/lists.css").splitlines() if l.startswith(".wname {"))


def test_ntfy_title_uses_cell_font_size():
    line = next(l for l in _read("static/css/ntfy.css").splitlines() if l.startswith(".ntfy-t {"))
    assert "font-size: var(--fs-s)" in line


def test_agid_and_agtitle_macros_derive_kind_from_id():
    from pm.web.render import env as env

    t = env.from_string('{% from "_macros.html" import agid, agtitle %}{{ agid(a) }}|{{ agtitle(b) }}')
    out = t.render(a="cursor-agent-1a2b", b="codex-3fa91c - Refactor <x>")
    assert '<span class="agid" data-kind="cursor-agent">cursor-agent-1a2b</span>' in out
    assert '<span class="agid" data-kind="codex">codex-3fa91c</span> - Refactor &lt;x&gt;' in out
    assert env.from_string('{% from "_macros.html" import agid %}{{ agid("pm-1") }}').render() == "pm-1"


def test_other_pages_carry_kind_hooks():
    assert "agtitle(r.title)" in (TEMPLATE_DIR / "_ntfy.html").read_text()
    assert "agid(e.get('target'" in (TEMPLATE_DIR / "_audit_list.html").read_text()
    assert 'data-kind="{{ a.kind.value }}">{{ agent_name(a) }}' in (TEMPLATE_DIR / "launch.html").read_text()
    css = _read("static/css/shell.css")
    assert ".agid[data-kind]" in css
    # agent heading pill = the one model pill: kind colour comes from chrome.css [data-kind] .kpill.kp-model
    assert 'class="kpill kp-model"' in (TEMPLATE_DIR / "_agentname.html").read_text() and "h1.agent-title{--pill" not in css
