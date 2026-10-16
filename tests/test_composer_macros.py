"""templates/_composer_macros.html: the one composer, rendered in each mode (live, builder, ended with an agent; quick chat,
Launch, Continue without one), its root contract (data-composer, data-send-key, data-draft-key, the [data-cmp-*] hooks) and
the scripts each mode loads."""
import re
from types import SimpleNamespace as NS

import pytest

from pm.core.models import Agent, AgentKind, AgentState
from pm.web.render import TEMPLATE_DIR, env

TPL = ('{% from "_composer_macros.html" import composer, composer_scripts with context %}'
       '{{ composer(mode, rec=rec, flow=flow) }}|SCRIPTS|{{ composer_scripts(mode, dock=dock) }}')
UUID = "11111111-2222-3333-4444-555555555555"


def _agent(**kw):
    base = dict(id="claude-7a", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=0, cwd="/tmp", project="alpha",
                label="dev-alpha-1-claude", state=AgentState.RUNNING)
    base.update(kw)
    return Agent(**base)


def render(mode, send_key="enter", rec=None, flow=None, dock=True, **ctx):
    out = env.from_string(TPL).render(mode=mode, rec=rec, flow=flow, dock=dock, agent=ctx.pop("agent", _agent()), csrf="tok",
                                      project=None, composer_send_key=lambda: send_key, models=[], efforts=[], **ctx)
    comp, scripts = out.split("|SCRIPTS|")
    return comp, re.findall(r'src="/static/js/([^"]+)"', scripts)


def _rec(resume_id=UUID, kind="claude"):
    return NS(resume_id=resume_id, kind=kind, project="alpha")


@pytest.mark.parametrize("mode", ["live", "builder", "ended"])
def test_root_contract(mode):
    comp, _ = render(mode, rec=_rec())
    root = re.search(r"<section[^>]*>", comp).group(0)
    assert f'data-composer="{mode}"' in root and 'data-send-key="enter"' in root and 'data-agent="claude-7a"' in root
    assert f'data-draft-key="/a/claude-7a{"/builder" if mode == "builder" else ""}"' in root
    assert ("composer--flow" if mode == "builder" else "composer--dock") in root
    for hook in ("data-cmp-form", "data-cmp-input", "data-cmp-send", "data-cmp-bar", "data-cmp-chips", "data-cmp-flash"):
        assert hook in comp, hook
    assert 'id="flash"' not in comp  # one #flash per page: the composer's line is [data-cmp-flash]
    assert 'hx-target="#flash"' not in comp and comp.count('hx-target="[data-cmp-flash]"') >= 1


def test_live_has_one_interrupt_and_the_full_bar():
    comp, scripts = render("live")
    assert 'id="msgbar"' not in comp and 'data-send="htmx"' in comp and 'data-state="running"' in comp  # no ids: the root's data
    assert 'action="/a/claude-7a/message"' in comp and "data-keys" in comp and "data-attach" in comp and "data-aside" in comp
    assert comp.count("/interrupt") == 2 and re.search(r'<form class="cstop" data-cmp-stop[^>]*hidden>', comp)  # action + hx-post
    assert 'data-stop-title="Interrupt agent (sends Ctrl-C, then Esc)"' in comp and 'class="ci-stop"' in comp
    assert 'data-dlg-open="exit-dialog"' in comp and 'id="exit-dialog"' in comp and 'class="pmdlg-head"' in comp
    assert scripts[:2] == ["composer-dock.js", "agentmode.js"]  # composer.js, composer-attach.js, slash.js: base.html
    assert "chat-send.js" in scripts and "modelpick.js" in scripts and "snippets.js" in scripts and "atfiles.js" in scripts
    assert "composer.js" not in scripts and "composer-attach.js" not in scripts and "slash.js" not in scripts


def test_builder_has_no_keys_and_flows():
    comp, scripts = render("builder", flow=None, dock=False)
    assert "data-keys" not in comp and 'name="mode" value="builder"' in comp and "composer--flow" in comp
    assert 'href="/a/claude-7a/builder"' not in comp and "composer-dock.js" not in scripts


@pytest.mark.parametrize("send_key, label", [("enter", "Enter"), ("mod-enter", "Ctrl+Enter")])
def test_send_key_setting_reaches_root_and_titles(send_key, label):
    comp, _ = render("live", send_key=send_key)
    assert f'data-send-key="{send_key}"' in comp
    assert f'title="Send message ({label})"' in comp and f'aria-label="Send message ({label})"' in comp
    ended, _ = render("ended", send_key=send_key, rec=_rec())
    assert f"({label})" in ended  # placeholder and button titles follow the setting


def test_ended_composer_is_never_the_live_one():
    """tests/test_agent_registry.py keeps the page-level rule; this is the composer's share of it."""
    comp, scripts = render("ended", rec=_rec())
    for bad in ("/message", "/key", "/interrupt", "/stop", "msgbar", "/pane"):
        assert bad not in comp, bad
    assert 'class="composer composer--dock composer--ended"' in comp and 'data-send="form"' in comp
    form = re.search(r'<form class="cform" data-cmp-form[^>]*>', comp).group(0)
    assert 'action="/a/claude-7a/resume"' in form and 'data-transition="Resuming&hellip;"' in form and "hx-confirm=" in form
    assert 'data-label-empty="Resume" data-label-text="Resume and send"' in comp
    alt = re.search(r"<button[^>]*data-cmp-alt[^>]*>", comp).group(0)
    assert 'hx-post="/a/claude-7a/fork"' in alt and 'data-label-text="Fork and send"' in alt and "hx-confirm=" in alt
    assert 'name="text"' in comp and "required" not in re.search(r"<textarea[^>]*>", comp).group(0)  # empty = plain Resume
    assert scripts == ["composer-dock.js", "agentmode.js"]  # only what it needs (composer.js comes from base.html)


def test_ended_without_session_id_forks():
    comp, _ = render("ended", rec=_rec(resume_id=None))
    assert "/resume" not in comp and 'action="/a/claude-7a/fork"' in comp
    assert '<button type="button" class="small" disabled title="No session id recorded, cannot resume">Resume</button>' in comp
    assert "data-cmp-alt" not in comp and 'data-label-text="Fork and send"' in comp


def test_ended_page_and_builder_render_the_ended_composer():
    from pathlib import Path
    root = TEMPLATE_DIR
    assert '{{ composer("ended", rec=rec) }}' in (root / "agent_ended.html").read_text()
    b = (root / "builder.html").read_text()
    assert 'composer("ended", rec=rec, flow=true' in b and 'data-mode="{{ \'live\' if live else \'ended\' }}"' in b
    assert not (root / "_composer.html").exists()


def test_icons_in_one_place():
    src = (env.loader.get_source(env, "_composer_macros.html")[0])
    assert src.count("<svg class=\"ci-{{ kind }}\"") == 1  # cbar_icon(kind): every bar icon comes from one macro
    for kind in ("keys", "attach", "paste", "aside", "send", "stop", "caret"):
        assert f"'{kind}':" in src


def test_placeholder_can_be_given():
    """Later modes (quick chat, Launch) pass their own text; the default is the agent's."""
    out = env.from_string('{% from "_composer_macros.html" import composer with context %}{{ composer("builder", placeholder=("Ask anything", "Ask")) }}').render(
        agent=_agent(), csrf="t", composer_send_key=lambda: "enter", models=[], efforts=[])
    assert 'placeholder="Ask anything" data-ph-short="Ask"' in out


# ---- the agent-free modes: quick chat, Launch, Continue ----
FREE = '{% from "_composer_macros.html" import composer with context %}{{ composer(mode, **kw) }}'
TRAP = NS(id="TRAP", label="TRAP", state=NS(value="running"), project="TRAP")  # an agent page's own agent: never read


def free(mode, send_key="enter", **kw):
    out = env.from_string(FREE).render(mode=mode, kw=kw, agent=TRAP, csrf="tok", composer_send_key=lambda: send_key,
                                       composer_paste_lines=7, composer_accept=".png,.md")
    return out, re.search(r"<section[^>]*>", out).group(0)


@pytest.mark.parametrize("mode", ["qchat", "launch", "continue"])
def test_agent_free_modes_render_without_an_agent(mode):
    out = env.from_string(FREE).render(mode=mode, kw={}, csrf="t")  # no `agent` at all
    root = re.search(r"<section[^>]*>", out).group(0)
    assert f'data-composer="{mode}"' in root and "composer--flow" in root and 'data-draft-key=""' in root
    assert "data-agent" not in root and "data-state" not in root and "data-upload" not in root
    assert "data-cmp-input" in out and "data-cmp-bar" in out and "data-cmp-flash" in out and "data-cmp-slash-card" not in out
    for bad in ("/a/", "/interrupt", "exit-dialog", "mp-pill", "data-cmp-stop", "data-snip", "required"):
        assert bad not in out, (mode, bad)
    _, root2 = free(mode)  # an agent in context (quick chat on an agent page) changes nothing
    assert "TRAP" not in free(mode)[0] and root2 == root


def test_qchat_composer():
    out, root = free("qchat", action="/qchat/send", send="stream", features=["attach", "paste", "stop"], draft_key="qchat",
                     maxlength=8000, slash=[], label="Quick chat message", placeholder=("Ask (Enter sends)", "Ask"))
    assert 'data-send="stream"' in root and 'data-send-key="enter"' in root and 'data-draft-key="qchat"' in root
    assert 'data-attach="inline"' in root and 'data-slash="[]"' in root and 'data-slash-at="word"' in root
    form = re.search(r"<form[^>]*>", out).group(0)
    assert 'action="/qchat/send"' in form and "hx-post" not in form  # qchat.js streams it itself
    assert 'maxlength="8000"' in out and 'placeholder="Ask (Enter sends)" data-ph-short="Ask"' in out
    assert 'aria-label="Quick chat message"' in out and "data-paste-text" in out and "data-paste-img" not in out
    assert 'data-stop-title="Stop the answer"' in out and 'class="ci-stop"' in out and "csplit" not in out
    assert re.search(r'<input type="file" data-cmp-file multiple hidden aria-label="File">', out)  # read here, not uploaded
    assert "data-cmp-upload" not in out and 'name="csrf" value="tok"' in out  # a POST form (tests/test_ui_contract.py)
    assert 'data-send-key="mod-enter"' in free("qchat", send_key="mod-enter")[1]  # follows the setting


def test_launch_composer_never_sends_on_enter():
    out, root = free("launch", field="prompt", send="form", features=["attach", "paste"], maxlength=8000, label="Prompt",
                     upload="/p/alpha/image", atfiles="/p/alpha/atfiles", slash=[{"cmd": "/plan"}])
    assert 'data-send-key="mod-enter"' in root and 'data-send-key="mod-enter"' in free("launch", send_key="enter")[1]
    assert 'data-upload="/p/alpha/image"' in root and 'data-paste-lines="7"' in root and 'data-atfiles="/p/alpha/atfiles"' in root
    assert "data-attach=" not in root and "data-slash=" in root and "&#34;/plan&#34;" in root
    assert "<form" not in out  # the box joins the Launch form; no Send of its own (Launch / Launch, stay)
    assert "data-cmp-send" not in out and 'type="submit"' not in out and "Ctrl+Enter launches" in out
    assert 'name="prompt"' in out and "data-paste-img" in out and 'accept=".png,.md"' in out


def test_continue_composer():
    out, root = free("continue", send="dialog", label="New prompt")
    assert 'data-send-key="mod-enter"' in root and 'data-send="dialog"' in root and "Ctrl+Enter starts" in out
    assert "data-cmp-send" not in out and '<div class="cform" data-cmp-form>' in out and "<form" not in out  # never posted
    assert 'type="file"' not in out and 'name="csrf"' not in out


def test_continue_template_on_agent_and_builder_pages():
    from pathlib import Path
    root = TEMPLATE_DIR
    for page in ("agent.html", "builder.html"):
        t = (root / page).read_text()
        assert t.index("{{ continue_template() }}") < t.index("/static/js/continuepop.js")
    out = env.from_string('{% from "_composer_macros.html" import continue_template with context %}{{ continue_template() }}').render(csrf="t")
    assert out.startswith('<template id="cmp-continue"><section class="composer composer--flow" data-composer="continue"')
