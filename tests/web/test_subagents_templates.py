"""Sub-agent templates: the row layout shared by #sa-live and the Sub-agents tab (_sa_macros.html), the
opened sub-agent as a mini chat (_subagent_work.html), subagents.css / subagents.js."""

import re
import time
from types import SimpleNamespace

import pm.web.routes.chat  # noqa: F401  (registers the tool_family global)
from pm.agents.subagents import Group, Step, Sub, Summary, Work
from pm.web.render import STATIC_DIR, TEMPLATE_DIR
from pm.web.render import env as jenv


def _agent():
    return SimpleNamespace(id="claude-12", kind=SimpleNamespace(value="claude"))


def _sub(now, **kw):
    base = dict(sid="agent-aaa", kind="tester", desc="Run the suite", mtime=now - 30, running=True, calls=7,
                last="Bash", last_target="uv run pytest", t0=now - 200, tokens=35200)
    base.update(kw)
    return Sub(**base)


def _classes(html):
    return re.findall(r'<(?:span|strong) class="(sa-[a-z]+|dot [a-z-]+)[^"]*"', html)


def test_live_row_left_name_title_right_tool_calls_tokens_status():
    now = time.time()
    out = jenv.get_template("_subagents_live.html").render(subs=[_sub(now)], now=now)
    assert 'class="sa-line sa-live-row"' in out
    assert _classes(out) == ["dot dot-running", "sa-kind", "sa-desc", "sa-rt", "sa-last", "sa-ln", "sa-calls",
                             "sa-tok", "sa-age"]
    assert "7 calls" in out and "35k tokens" in out and "running 3m" in out
    assert '<span class="sa-ln">Bash</span> <span class="mono">uv run pytest</span>' in out


def test_tokens_omitted_when_zero_and_waiting_is_amber():
    now = time.time()
    out = jenv.get_template("_subagents_live.html").render(subs=[_sub(now, tokens=0, waiting=True)], now=now)
    assert '<span class="sa-tok" title="tokens (latest usage)"></span>' in out and "tokens</span>" not in out
    assert '<span class="sa-age is-wait"' in out and "waiting &middot; 30s" in out
    big = jenv.get_template("_subagents_live.html").render(subs=[_sub(now, tokens=1_250_000)], now=now)
    assert "1.2M tokens" in big  # the shared `tokens` filter (chat.stats.human)


def test_tab_row_keeps_ids_and_finished_status():
    now = time.time()
    s = _sub(now, running=False, mtime=now - 180)
    summ = Summary(running=0, finished=1, total=1, groups=[Group("", "Direct", [s], 1, 0)])
    out = jenv.get_template("_subagents.html").render(agent=_agent(), subs=[s], summ=summ, now=now, ended=False)
    assert 'id="sa-i-agent-aaa"' in out and 'id="sa-d-agent-aaa"' in out and "data-keep" in out
    assert '<summary class="sa-line sa-row"><span class="dot dot-idle"' in out
    assert re.search(r'<span class="sa-age">finished <span title="[^"]+">3m ago</span></span>', out)


def _work(now, running=True):
    steps = [Step("prompt", "line one\nline two\nline three\nline four"),
             Step("text", "Running **now**.", ts=now - 150),
             Step("tool", "git status", "Bash", "t1", "ok", now - 100, now - 99.8),
             Step("tool", "src/pm/x.py", "Read", "t2", "ok", now - 99, now - 98.5),
             Step("text", "All **green**.", ts=now - 90),
             Step("tool", "uv run pytest", "Bash", "t3", "run" if running else "ok", now - 60, 0 if running else now - 5)]
    return Work(_sub(now, running=running, waiting=running), steps)


def test_work_is_a_mini_chat():
    now = time.time()
    out = jenv.get_template("_subagent_work.html").render(agent=_agent(), work=_work(now))
    assert '<p class="sa-meta"><span>7 tool calls</span><span>35k tokens</span>' in out and "running 3m" in out
    order = [out.index(x) for x in ('data-sa-prompt', 'id="sa-tg-agent-aaa-3"', "All <strong>green</strong>",
                                    'id="sa-tg-agent-aaa-5"', 'class="sa-wait"')]
    assert order == sorted(order)
    # prompt: plain text, clamped, Show all toggle + Copy
    assert '<div class="sa-ptxt">line one\nline two' in out and "data-sa-more" in out and 'data-raw="line one' in out
    # replies are Markdown; only the last one carries Copy
    assert "<p>Running <strong>now</strong>.</p>" in out
    assert out.count('class="chat-bubble role-assistant sa-reply') == 2 and out.count("sa-final") == 1
    # consecutive tools folded into one chat-style group, ids that never collide with #ci items
    assert out.count('<details id="sa-tg-') == 2 and "<details open" not in out
    assert '<span class="tchip tf-shell">Bash <b>1</b></span><span class="tchip tf-read">Read <b>1</b></span>' in out
    assert 'id="i-sa-agent-aaa-t1"' in out and not re.search(r'id="i-\d', out)
    assert "1 running" in out and "Waiting &middot; Bash &middot;" in out


def test_finished_work_has_no_waiting_line_and_short_prompt_no_toggle():
    now = time.time()
    w = _work(now, running=False)
    w.steps[0] = Step("prompt", "short")
    out = jenv.get_template("_subagent_work.html").render(agent=_agent(), work=w)
    assert "sa-wait" not in out and "data-sa-more" not in out and "took 2m" in out
    assert "<span class=\"muted\">reply</span>" in out


def test_static_files():
    css = (STATIC_DIR / "css" / "subagents.css").read_text()
    assert len(css.splitlines()) <= 500 and not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", css)
    for sel in (".sa-line", ".sa-rt", "@container (max-width:899px)", "@container (max-width:639px)",
                ".sa-ask[data-open] .sa-ptxt", "-webkit-line-clamp:3", ".sa-d[open]>.sa-row{background:var(--accent-soft)}"):
        assert sel in css, sel
    assert "max-height" not in css  # no inner scroll boxes
    for f in ("nav.css", "chat.css"):
        assert ".sa-" not in (STATIC_DIR / "css" / f).read_text(), f
    js = (STATIC_DIR / "js" / "subagents.js").read_text()
    assert len(js.splitlines()) <= 100 and "data-open" in js and "innerHTML" not in js
    tpl = (TEMPLATE_DIR / "agent.html").read_text()
    assert "/static/js/subagents.js" in tpl
