"""The one agent-name macro: "<id> [model pill]", the kind only when the model is unknown."""
from __future__ import annotations

import dataclasses

from test_lists import _a

from pm.core.models import AgentState
from pm.web.render import env as _env


def _r(src: str, **kw) -> str:
    return _env.from_string('{% from "_agentname.html" import agent_name %}' + src).render(**kw)


def test_id_and_model_pill_no_kind_pill():
    out = _r('{{ agent_name(a, model="sonnet-5-5") }}', a=_a(1, AgentState.IDLE))
    assert '<strong class="aname"' in out and "claude-1" in out
    assert out.count("kpill") == 1 and 'kp-model' in out and ">sonnet-5-5<" in out and "kp-claude" not in out


def test_kind_stands_in_only_without_model_and_label_never_shown():
    a = dataclasses.replace(_a(1, AgentState.IDLE), label="build")
    out = _r("{{ agent_name(a, model='') }}", a=a)
    assert "build" not in out and 'class="kpill kp-claude"' in out and "kp-model" not in out


def test_dict_link_and_pill_only():
    d = {"id": "codex-abc", "kind": "codex"}
    out = _r("{{ agent_name(d, link='/a/codex-abc', dot=True, model='gpt-5') }}", d=d)
    assert '<a class="aname-l" href="/a/codex-abc"' in out and ">gpt-5<" in out and "dot-" not in out  # no state: no dot
    assert "aname" not in _r("{{ agent_name(d, show_id=False) }}", d=d) and "kp-codex" in _r("{{ agent_name(d, show_id=False) }}", d=d)


def _h(src: str, **kw) -> str:
    return _env.from_string('{% from "_agentname.html" import agent_head %}' + src).render(**kw)


def test_agent_head_live_h1_one_model_pill():
    a = _a(1, AgentState.RUNNING)
    out = _h('{{ agent_head(a, "live", "opus-5-5") }}', a=a)
    assert out.startswith('<h1 class="agent-title" title="claude-1 opus-5-5" data-model="opus-5-5">') and out.endswith("</h1>")
    assert 'class="at-ico"><svg class="wico"' in out and '<span class="at-base">claude-1</span>' in out
    assert '<span class="kpill kp-model" id="agent-pill" data-kind="claude" title="running" aria-label="opus-5-5, running">opus-5-5</span>' in out
    assert out.count("kpill") == 1 and "at-ended" not in out and "at-pill" not in out
    assert ">claude<" in _h('{{ agent_head(a, "live") }}', a=a)  # no pill given, no transcript: the kind


def test_agent_head_ended_and_builder():
    rec = {"id": "codex-abc", "kind": "codex", "transcript": ""}
    out = _h('{{ agent_head(r, "ended") }}', r=rec)
    assert out.startswith('<h1 class="agent-title" title="codex-abc codex (ended)" data-model="codex">')
    assert 'id="agent-pill"' not in out and '<span class="kpill at-ended" title="ended">Ended</span></h1>' in out
    assert 'class="kpill kp-model" data-kind="codex"' in out
    b = _h('{{ agent_head(a, "builder", "sonnet-5-5") }}', a=_a(2, AgentState.IDLE))
    assert b.startswith('<span class="agent-title at-in"') and "<h1" not in b and "wico" not in b
    assert '<strong class="at-base">claude-2</strong>' in b and 'id="agent-pill"' in b and ">sonnet-5-5<" in b


def test_agent_head_aside_is_the_empty_drawer_head():
    out = _h('{{ agent_head(none, "aside") }}')
    for i in ('id="vc-dot"', 'id="vc-ico" hidden', 'id="vc-title"', 'id="vc-model" hidden', 'id="vc-kind"'):
        assert i in out, i
    assert 'class="kpill kp-model vc-model"' in out and "polyline" in out and "<h1" not in out
