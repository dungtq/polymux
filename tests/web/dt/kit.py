"""Shared helpers of the dt theme tests (tests/web/dt): a tiny DOM (``Tree``), a flat element list (``Doc``), plain
Jinja over the dt templates (``ENV``), tmux sessions for the view models, and base.html rendered with a stub
``dt_shell`` (``render_base``), the Launch form. Test modules import these from here, never from each other."""

from __future__ import annotations

import dataclasses
from html.parser import HTMLParser
from types import SimpleNamespace

import jinja2

from pm.core.models import AgentState, TmuxPane, TmuxSession, TmuxWindow
from pm.web import themes
from pm.web.dt import launch
from pm.web.dt import shell as dtshell

from .conftest import agent

TPL = themes.HERE / "dt" / "templates"
VOID = {"input", "br", "img", "hr", "meta", "link", "use", "source", "col", "area", "wbr"}


# ---------------------------------------------------------------- a tiny DOM for structure checks
class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: Node | None):
        self.tag, self.attrs, self.parent, self.kids, self.text = tag, attrs, parent, [], ""

    @property
    def cls(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def all(self):
        for k in self.kids:
            yield k
            yield from k.all()

    def find(self, pred) -> list[Node]:
        return [n for n in self.all() if pred(n)]

    def by_cls(self, c: str) -> list[Node]:
        return self.find(lambda n: c in n.cls)

    def one(self, c: str) -> Node:
        found = self.by_cls(c)
        assert len(found) == 1, (c, len(found))
        return found[0]

    def get_text(self) -> str:
        return self.text + "".join(k.get_text() for k in self.kids)


class Tree(HTMLParser):
    def __init__(self, src: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.feed(src)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, dict(attrs), self.cur)
        self.cur.kids.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(Node(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        assert n is not self.root, f"stray </{tag}>"
        self.cur = n.parent

    def handle_data(self, data):
        self.cur.text += data


ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True, undefined=jinja2.StrictUndefined)


class Doc(HTMLParser):
    """Flat element list: (tag, attrs dict); enough for presence / attribute / order checks."""

    def __init__(self, src: str):
        super().__init__(convert_charrefs=True)
        self.els: list[tuple[str, dict]] = []
        self.feed(src)

    def handle_starttag(self, tag, attrs):
        self.els.append((tag, {k: (v if v is not None else "") for k, v in attrs}))

    def find(self, tag=None, **want) -> list[dict]:
        out = []
        for t, a in self.els:
            if tag and t != tag:
                continue
            ok = True
            for k, v in want.items():
                k = k.replace("_", "-").rstrip("-")
                if k == "class":
                    ok = ok and set(v.split()) <= set(a.get("class", "").split())
                elif v is True:
                    ok = ok and k in a
                else:
                    ok = ok and a.get(k) == v
            if ok:
                out.append(a)
        return out

    def one(self, tag=None, **want) -> dict:
        found = self.find(tag, **want)
        assert len(found) == 1, (tag, want, len(found))
        return found[0]


# ---------------------------------------------------------------- tmux sessions
def pane(pid, wid, sid, cmd="zsh", cwd="/dev/alpha", active=True):
    return TmuxPane(pid, wid, sid, 1, cmd, cwd, active=active)


W1 = TmuxWindow("@1", "$1", 0, "agent", active=True, panes=(pane("%1", "@1", "$1", "claude"),))
W2 = TmuxWindow("@2", "$1", 1, "logs", panes=(pane("%2", "@2", "$1"), pane("%3", "@2", "$1", cwd="/tmp", active=False)))
WORK = TmuxSession("$1", "work", attached=2, protected=False, windows=(W1, W2))


def one(sid, name, n):
    return TmuxSession(sid, name, protected=True,
                       windows=(TmuxWindow(f"@{n}", sid, 0, "sh", True, (pane(f"%{n}", f"@{n}", sid),)),))


# ---------------------------------------------------------------- base.html with a stub dt_shell
def stub_shell(cur_agent: bool) -> dtshell.Shell:
    counts = {"inbox": 3, "projects": 2, "tmux": 5}
    main, foot = dtshell.nav_items()
    nav = [dtshell.NavRow(k, h, lab, ic, counts.get(k), k == "inbox", ch, k == "inbox" and not cur_agent)
           for k, h, lab, ic, ch in main]
    foot = [dtshell.NavRow(k, h, lab, ic, None, False, ch, False) for k, h, lab, ic, ch in foot]
    rows = [dtshell.SessRow("claude-1c70ee", "claude", "running", "/a/claude-1c70ee", "claude-1c70ee", 1, True,
                            cur_agent, age="1h"),
            dtshell.SessRow("codex-44aa01", "codex", "waiting", "/a/codex-44aa01", "codex-44aa01", None, False, False,
                            "fix-ci")]
    tabs = [dtshell.Tab("agent", "claude-1c70ee", "claude-1c70ee", "/a/claude-1c70ee", "running", "claude", cur_agent),
            dtshell.Tab("term", "$3", "work", "/term/s3", "", "", False)]
    home = dtshell.Tab("home", "", "Inbox", "/", "", "", not cur_agent, "inbox")
    return dtshell.Shell(nav, foot, [dtshell.SessGroup("pm", "/p/pm", "", rows)], tabs, home, 7, 1, 1, 2)


def render_base(cur_agent: bool = False, embed: bool = False, chat: bool = False) -> str:
    env = jinja2.Environment(loader=jinja2.ChoiceLoader([
        jinja2.DictLoader({"page.html": '{% extends "base.html" %}{% block title %}Inbox{% endblock %}'
                                        '{% block content %}<h1>Inbox</h1>{% endblock %}'}),
        jinja2.FileSystemLoader(TPL)]), autoescape=True, undefined=jinja2.StrictUndefined)
    sh = dtshell.EMPTY if embed else dataclasses.replace(stub_shell(cur_agent), chat=chat)
    env.globals.update(dt_shell=lambda: sh)
    return env.get_template("page.html").render(
        csrf="tok", embed=embed, dev_banner=False, require_unlock=False, locked=False, provisioned=True,
        ui_theme="dt", ui_themes=[("classic", "Classic"), ("dt", "dt (keyboard)")])


# ---------------------------------------------------------------- the Launch form (_launch_form.html)
LAUNCH_DATA = {
    "remembered": {"kind": "codex", "model_choice": "gpt-5", "mode": "auto", "effort": "high", "branch": ""},
    "templates": [{"name": "review", "text": "Review the changes."}, {"name": "fix tests", "text": "Fix it."}],
    "worktree": {"base": "/dev/alpha-wt", "branches": ["main", "feat/x"], "taken": {"main": "/dev/alpha"}},
    "slash": {}, "modes": {"claude": {"modes": [{"key": "plan", "help": "Plans.", "bypass": False}], "extra": None,
                                      "efforts": ["low", "high"]}},
    "models": {"claude": [{"name": "opus", "variants": [["opus", "opus"]], "efforts": ["high"]}]},
}


def launch_ctx(**over):
    c = {"project": SimpleNamespace(name="alpha", path="/dev/alpha"), "base": "/p/alpha", "projects": ["alpha", "beta"],
         "running": [agent("claude-aaa111", "alpha", state=AgentState.WAITING, window="w1")],
         "kinds": ["claude", "codex", "devin"], "launch_data": LAUNCH_DATA, "bypass_allowed": True,
         "todo": [{"index": 0, "text": "Write tests", "prompt": "Do: Write tests", "busy": ""},
                  {"index": 1, "text": "Fix CI", "prompt": "Do: Fix CI", "busy": "claude-bbb222"}],
         "todo_selected": None}
    c.update(over)
    return c


def launch_env():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True, undefined=jinja2.StrictUndefined)
    env.globals["dt_launch"] = launch.dt_launch
    return env


def render_launch_form(**over) -> str:
    return launch_env().get_template("_launch_form.html").render(csrf="tok", **launch_ctx(**over))
