"""The dt Launch dialog (PRD §6.12): the view model (pm.web.dt.launch), _launch_form.html rendered from a
launchctx.form_model-shaped context, the dialog shell and its openers in base.html / shell/_top.html, and the pure
helpers of js/launch-prompt.js (node)."""

from __future__ import annotations

import html as htmlmod
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

from pm.core.models import AgentState
from pm.web import themes
from pm.web.dt import launch

from .kit import LAUNCH_DATA as DATA
from .kit import launch_ctx as ctx
from .kit import launch_env as _env
from .kit import render_launch_form as render_form

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = THEME / "static" / "js"

# ---------------------------------------------------------------- view model
def test_view_model_from_the_form_context():
    v = launch.build(ctx())
    assert (v.project, v.base, v.path, v.projects) == ("alpha", "/p/alpha", "/dev/alpha", ("alpha", "beta"))
    assert [(c.kind, c.label, c.cur) for c in v.cards] == [("claude", "Claude Code", False), ("codex", "Codex", True),
                                                           ("devin", "Devin", False)]
    assert v.kind == "codex"  # the remembered kind is preselected
    assert v.running == (launch.Running("claude-aaa111", "claude", "waiting", "w1"),)
    assert v.remembered == "codex · gpt-5 · auto · high"
    assert v.todo_free == 1 and v.todo_sel is None and v.prompt == ""


def test_unknown_remembered_kind_falls_back_to_the_first():
    v = launch.build(ctx(launch_data={**DATA, "remembered": {"kind": "nope"}}))
    assert v.kind == "claude" and [c.cur for c in v.cards] == [True, False, False]


@pytest.mark.parametrize("rem, want", [
    ({}, ""), ({"kind": "claude"}, "claude · default model · default mode"),
    ({"kind": "claude", "model_choice": "custom", "model": "x-1", "branch": "f/a"},
     "claude · x-1 · default mode · branch f/a"),
    ({"kind": "claude", "model_choice": "opus", "variant": "opus[1m]", "mode": "plan"}, "claude · opus[1m] · plan"),
])
def test_remembered_line(rem, want):
    assert launch.remembered_line(rem) == want


def test_selected_todo_must_be_free():
    rem = {**DATA["remembered"], "prompt": "Do: Write tests"}
    v = launch.build(ctx(todo_selected=0, launch_data={**DATA, "remembered": rem}))
    assert v.todo_sel == 0 and v.prompt == "Do: Write tests"
    assert launch.build(ctx(todo_selected=1)).todo_sel is None  # in progress: not preselected


def test_broken_context_gives_the_empty_view(monkeypatch):
    def boom(c):
        raise TypeError("boom")
    monkeypatch.setattr(launch, "build", boom)
    assert _env().from_string("{{ dt_launch().project == '' and dt_launch().cards == () }}").render() == "True"


# ---------------------------------------------------------------- template
def test_form_posts_to_the_project_and_carries_its_data():
    h = render_form()
    form = re.search(r"<form [^>]*data-l-form[^>]*>", h).group(0)
    assert 'action="/p/alpha/launch"' in form and 'data-l-base="/p/alpha"' in form
    data = json.loads(htmlmod.unescape(re.search(r'data-l-data="([^"]*)"', form).group(1)))
    assert data["worktree"]["branches"] == ["main", "feat/x"] and data["templates"][0]["name"] == "review"
    assert '<input type="hidden" name="csrf" value="tok">' in h
    # the project picker in the title, the current project selected
    assert re.search(r'<option value="alpha" selected>alpha</option>', h)
    # agent cards: real radios named kind, kind colour by data-kind, the remembered one checked
    radios = re.findall(r'<label class="l-ag" data-kind="([^"]+)"><input type="radio" name="kind" value="\1"( checked)?',
                        h)
    assert radios == [("claude", ""), ("codex", " checked"), ("devin", "")]
    assert "logged in" not in h  # the core has no login state: none is shown


def test_running_line_todo_tab_and_foot():
    h = render_form()
    assert "1 agent already running in alpha." in h
    assert re.search(r'<span data-kind="claude"><a class="chip k" href="/a/claude-aaa111"', h)  # never a[data-kind]
    assert 'data-l-todo="0" data-l-tprompt="Do: Write tests"' in h
    assert 'href="/a/claude-bbb222" data-l-agent="claude-bbb222"' in h and 'data-l-todo="1"' not in h
    assert re.search(r'id="p-tab-l-todo"[^>]*>.*?<span class="c">1</span>', h, re.S)  # free items only
    assert "Remembered for alpha: codex · gpt-5 · auto · high" in h
    assert re.search(r'<button type="submit" class="x-btn pri" name="open" value="1" data-l-go="tab">', h)
    assert re.search(r'<button type="submit" class="x-btn" name="open" value="0" data-l-go="bg">', h)
    assert h.count(' pri"') + h.count(' pri ') == 1  # one green button
    assert 'name="confirm_bypass" value="1"' in h


def test_no_running_line_without_agents_and_prefill():
    rem = {**DATA["remembered"], "prompt": "Do: Write tests"}
    h = render_form(running=[], todo_selected=0, launch_data={**DATA, "remembered": rem})
    assert "already running" not in h
    assert re.search(r"<textarea [^>]*>Do: Write tests</textarea>", h)
    assert 'data-l-todosel="0"' in h and re.search(r"<p class=\"l-help\" data-l-todonote>", h)


def test_form_ids_are_unique():
    ids = re.findall(r'\sid="([^"]+)"', render_form())
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------- shell
def _base(embed=False) -> str:
    from pm.web.dt import shell as dtshell
    env = jinja2.Environment(loader=jinja2.ChoiceLoader([
        jinja2.DictLoader({"page.html": '{% extends "base.html" %}{% block content %}x{% endblock %}'}),
        jinja2.FileSystemLoader(TPL)]), autoescape=True)
    main, foot = dtshell.nav_items()
    sh = dtshell.Shell([dtshell.NavRow(k, h, lab, ic, None, False, ch, k == "inbox") for k, h, lab, ic, ch in main],
                       [dtshell.NavRow(k, h, lab, ic, None, False, ch, False) for k, h, lab, ic, ch in foot], [], [],
                       dtshell.Tab("home", "", "Inbox", "/", "", "", True, "inbox"), 1, 0, 0, 0)
    env.globals.update(dt_shell=lambda: dtshell.EMPTY if embed else sh)
    return env.get_template("page.html").render(csrf="tok", embed=embed, dev_banner=False, require_unlock=False,
                                                project=SimpleNamespace(name="alpha"))


def test_base_has_the_dialog_and_the_openers():
    h = _base()
    assert re.search(r'<dialog id="l-dlg" class="l-dlg" aria-labelledby="l-dlg-t">', h)
    launch_btn = re.search(r'<a class="x-btn pri x-launch"[^>]*>', h).group(0)
    assert 'data-l-open' in launch_btn and 'data-l-project="alpha"' in launch_btn
    assert 'href="/launch?project=alpha"' in launch_btn  # the no-JS fallback
    for s in ("launch-prompt.js", "launch.js"):
        assert f'src="/static/t/dt/js/{s}"' in h
    assert 'href="/static/t/dt/css/launch.css"' in h


def test_embed_has_no_launch():
    h = _base(embed=True)
    assert "l-dlg" not in h and "launch.js" not in h and "launch.css" not in h


# ---------------------------------------------------------------- js (node)
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_prompt_token_helpers():
    code = r"""
globalThis.window = { pm7: {} }; globalThis.document = {};
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const P = window.pm7.launch.prompt;
console.log(JSON.stringify([P.tokenAt('see @src/ma', 11), P.tokenAt('mail a@b', 8), P.tokenAt('@', 1),
  P.tokenAt('x @a b', 6), P.split('src/pm/x'), P.split('abc')]));
"""
    out = subprocess.run(["node", "-e", code, str(JS / "launch-prompt.js")], capture_output=True, text=True,
                         check=True)
    assert json.loads(out.stdout) == [{"start": 4, "text": "src/ma"}, None, {"start": 0, "text": ""}, None,
                                      {"dir": "src/pm/", "q": "x"}, {"dir": "", "q": "abc"}]


def test_launch_js_points_the_n_key_at_the_dialog():
    js = (JS / "launch.js").read_text()
    assert "t.id === 'launch'" in js and "row.run" in js  # the shell's N row (keys.js) opens the dialog
    assert "document.addEventListener('keydown'" not in js and "d.addEventListener('keydown'" not in js
    assert "'HX-Request': 'true'" in js  # the server answers HX-Redirect instead of a 303 to the agent page
