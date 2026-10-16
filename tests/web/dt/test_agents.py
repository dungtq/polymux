"""dt Agents page (pm.web.dt.agents, agents_library, agents_usage; templates agents.html, agents_detail.html,
_agents_*.html, agents/*.html; js/agents.js, css/agents.css): the view models over a synthetic inventory, the
partials rendered with plain Jinja on the real dt templates, and the whole pages through the core routes (real dt
theme, a tmp home)."""

from __future__ import annotations

import json
import re
import stat
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import jinja2
import pytest

from pm.agents import registry
from pm.agents.limits import Window
from pm.agents.usage import Aggregate, Counts
from pm.core.models import AgentInfo, Finding, Inventory, McpServerInfo, RuleInfo, ScriptInfo, SkillInfo
from pm.web import themes
from pm.web.dt import DtTheme, agents, agents_base, agents_library, agents_usage

from .conftest import make_env

THEME = themes.HERE / "dt"
TPL = THEME / "templates"
JS = (THEME / "static" / "js" / "agents.js").read_text(encoding="utf-8")
CSS = (THEME / "static" / "css" / "agents.css").read_text(encoding="utf-8")

CLAUDE = AgentInfo("claude", "Claude Code", True, "2.1.296", "~/.local/bin/claude", "~/.claude", "~/.claude/CLAUDE.md",
                   "~/.claude/skills", "", 2, 0)
CODEX = AgentInfo("codex", "Codex", True, "0.162.1", "~/.local/bin/codex", "~/.codex", "~/.codex/AGENTS.md",
                  "~/.codex/skills", "", 0, 0)
CURSOR = AgentInfo("cursor", "Cursor", True, "1", "~/.local/bin/cursor-agent", "~/.cursor", "", "~/.cursor/skills",
                   "~/.cursor/mcp.json", 0, 2)
AIDER = AgentInfo("aider", "Aider")
MCP_FIX = (("action", "mcp_env"), ("config", "~/.cursor/mcp.json"), ("server", "context7"))
SYNC_FIX = (("action", "rules_sync"), ("source", "canonical"), ("targets", "codex"))
LINK_FIX = (("action", "skills_link"), ("agent", "codex"), ("skills", "ntfy"), ("skills", "cfg"))
INV = Inventory(
    agents=(CLAUDE, CODEX, CURSOR, AIDER),
    skills=(SkillInfo("ntfy", "Publish to ntfy.", "~/.agents/skills/ntfy", "shared", "", ("claude",)),
            SkillInfo("cfg", "Home configs.", "~/.agents/skills/cfg", "shared", "", ("claude", "cursor")),
            SkillInfo("short", "Tiny.", "~/.claude/skills/short", "agent", "claude", ("claude",))),
    rules=(RuleInfo("AGENTS.md", "", "~/agents/AGENTS.md", "Agent rules"),
           RuleInfo("CLAUDE.md", "claude", "~/.claude/CLAUDE.md", "Agent rules", status="drift"),
           RuleInfo("AGENTS.md", "codex", "~/.codex/AGENTS.md", "Old copy", status="drift"),
           RuleInfo("cfg.md", "", "~/agents/rules/cfg.md", "cfg rule"),
           RuleInfo("cfg.mdc", "cursor", "~/.cursor/rules/cfg.mdc", "cfg rule")),
    scripts=(ScriptInfo("tmux-agents", "~/.agents/hooks/tmux-agents", "hook", "Spawn agents", ("claude", "codex")),),
    mcp=(McpServerInfo("context7", "cursor", "stdio", "npx -y @upstash/context7-mcp", "", (), "~/.cursor/mcp.json"),
         McpServerInfo("exa", "cursor", "stdio", "npx -y exa-mcp-server", "", ("EXA_API_KEY",), "~/.cursor/mcp.json")),
    findings=(Finding("warn", "API key passed as a CLI argument for MCP server 'context7' in ~/.cursor/mcp.json"
                      " (visible in ps); move it to an env var", "security", "cursor", MCP_FIX),
              Finding("warn", "Rules drift: ~/.codex/AGENTS.md differs from the canonical ~/agents/AGENTS.md", "rules",
                      "codex", SYNC_FIX),
              Finding("warn", "Rules drift: ~/.claude/CLAUDE.md differs from the canonical ~/agents/AGENTS.md",
                      "rules", "claude", SYNC_FIX[:2] + (("targets", "claude"),)),
              Finding("info", "2 shared skills not linked into Codex", "skills", "codex", LINK_FIX),
              Finding("info", "Installed agents with no MCP servers: Claude Code, Codex", "notes")))


def ctx(**kw) -> dict:
    """The core's /agents context (installed section) for INV."""
    base = {"inv": INV, "view": "installed", "section": "installed", "stats": "3 installed · 3 skills · 5 rules",
            "agents": list(INV.agents), "cols": {}, "csrf": "tok"}
    return {**base, **kw}


def lib_ctx(**kw) -> dict:
    return ctx(**{"view": "library", "section": "library", "q": "", "typ": "", "a": "", "n": "20", "sort": "", **kw})


def plans_of(attrs: dict | None) -> dict:
    return json.loads(attrs["data-a-plans"]) if attrs else {}


def fields(p: dict) -> list:
    return [tuple(f) for f in p["fields"]]


# ---------------------------------------------------------------- frame, Installed
def test_frame_tabs_counts_and_findings_line():
    f = agents.frame(ctx())
    assert [(t["key"], t["count"], t["cur"]) for t in f.tabs] == [
        ("installed", 3, True), ("library", 6, False), ("mcp", 2, False), ("usage", None, False),
        ("findings", 4, False)]
    assert f.tabs[-1]["bad"] and f.tabs[-1]["href"] == "/agents?t=findings"
    assert f.stats == ["3 installed", "3 skills", "5 rules"]
    assert f.actions[0]["attrs"] == {"data-a-rescan": ""} and not f.actions[0].get("pri")
    assert f.find.total == 4 and f.find.warn and f.find.parts == ["1 security", "2 rules", "1 skills not linked"]
    assert agents.frame(ctx(view="findings")).find is None  # the line is the Installed section's
    assert agents.frame(ctx(view="mcp", agent=CODEX)).view == "installed"  # the detail page sits under Installed


def test_installed_rows_counts_and_not_installed_band():
    ins = agents.installed(ctx())
    assert [r.name for r in ins.rows] == ["claude", "codex", "cursor"] and [r.name for r in ins.off] == ["aider"]
    claude, codex = ins.rows[0], ins.rows[1]
    assert (claude.skills, claude.rules, claude.scripts, claude.findings, claude.warn) == (2, 1, 1, 1, True)
    assert (codex.rules, codex.findings, codex.kind, codex.href) == (1, 2, "codex", "/agents/codex")
    assert ins.off[0].meta == "not installed" and ins.off[0].kind == "other"
    assert not ins.narrow and [c.get("label") or c.get("sr") for c in ins.cols][:4] == [
        "State", "Agent", "Version", "Login"]  # without the core's sort dicts: plain labels, no links
    assert not any(c.get("href") for c in ins.cols)
    narrow = agents.installed(ctx(agent=CODEX, icols={}))
    assert narrow.narrow and [r.sel for r in narrow.rows] == [False, True, False]


def test_installed_header_follows_the_cores_sort_dicts():
    cols = {"c-ag": {"label": "Agent", "href": "/agents?t=installed&sort=-agent", "active": True,
                     "aria": "ascending"},
            "c-sk": {"label": "Skills", "href": "/agents?t=installed&sort=skills", "active": False, "aria": "none"}}
    ins = agents.installed(ctx(cols=cols))
    assert ins.sort == "agent"
    assert {c.get("key"): c.get("href") for c in ins.cols if c.get("key")} == {
        "agent": "/agents?t=installed&sort=-agent", "skills": "/agents?t=installed&sort=skills"}


# ---------------------------------------------------------------- Library
def test_library_groups_one_row_per_item():
    rows = {(g.type, g.name): g for g in agents_library.items(INV)}
    top = rows[("rule", "AGENTS.md")]
    assert top.canonical and sorted(top.agents) == ["claude", "codex"] and sorted(top.drift) == ["claude", "codex"]
    assert sorted(rows[("rule", "cfg")].agents) == ["cursor"] and rows[("rule", "cfg")].canonical  # .md + .mdc
    assert rows[("skill", "short")].agents == ["claude"] and rows[("script", "tmux-agents")].agents == ["claude",
                                                                                                         "codex"]
    assert len(rows) == 6


def test_library_view_filters_counts_sort_and_foot():
    lb = agents_library.library(lib_ctx())
    assert [s["key"] for s in lb.segs] == ["all", "skill", "rule", "script"]
    assert [s["count"] for s in lb.segs] == [6, 3, 2, 1] and lb.segs[0]["cur"]
    assert lb.segs[1]["href"] == "/agents?t=library&type=skill"
    by = {r.id: r for r in lb.rows}
    assert by["rule:AGENTS.md"].copies == "2 differ" and by["rule:AGENTS.md"].differ == 2
    assert by["skill:cfg"].copies == "same" and by["skill:ntfy"].copies == "one"
    assert [c.label for c in by["skill:cfg"].chips] == ["Claude", "Cursor"]
    lb = agents_library.library(lib_ctx(q="cfg", a="cursor", n="20"))
    assert [r.id for r in lb.rows] == ["rule:cfg", "skill:cfg"] and lb.a_url == "/agents?t=library&q=cfg"
    lb = agents_library.library(lib_ctx(n="20", sort="-agents"))
    assert lb.rows[0].id in ("rule:AGENTS.md", "skill:cfg", "script:tmux-agents") and lb.sort == "-agents"
    assert agents_library.library(lib_ctx(q="zzz")).empty == "Nothing matches."
    many = Inventory(agents=(CLAUDE,), skills=tuple(SkillInfo(f"s{i:02d}", path=f"~/.agents/skills/s{i:02d}")
                                                   for i in range(25)))
    lb = agents_library.library(lib_ctx(inv=many))
    assert (lb.shown, lb.total) == (20, 25) and lb.more_url == "/agents?t=library&n=all"


def test_library_menu_edit_sync_link_remove():
    by = {r.id: r for r in agents_library.library(lib_ctx()).rows}
    m = {i["id"]: i for i in by["rule:AGENTS.md"].menu if "id" in i}
    assert m["view"]["disabled"] and m["edit"]["href"] == "/agents/claude/edit?kind=rule&name=AGENTS.md"
    assert m["sync"]["event"] == "a:plan" and m["rm"]["disabled"]  # a top rules file is never removed here
    p = plans_of(by["rule:AGENTS.md"].attrs)
    assert fields(p["sync"]) == [("action", "rules_sync"), ("source", "canonical"), ("targets", "claude"),
                                 ("targets", "codex")]
    sk = plans_of(by["skill:ntfy"].attrs)
    assert fields(sk["rm"]) == [("action", "skill_delete"), ("name", "ntfy"), ("confirm", "ntfy")]
    assert sk["rm"]["typed"] == "ntfy" and "backed up" in sk["rm"]["done"] and "trash" not in json.dumps(sk)
    assert fields(sk["link:codex"]) == [("action", "skill_toggle"), ("agent", "codex"), ("skill", "ntfy"),
                                        ("enable", "1")]
    assert "link:cursor" in sk and "link:claude" not in sk
    m = {i["id"]: i for i in by["skill:short"].menu if "id" in i}
    assert m["edit"]["disabled"] and m["rm"]["disabled"]  # an agent's own skill: not in a folder pm edits


# ---------------------------------------------------------------- MCP
def test_mcp_rows_key_in_args_fix_and_read_only_coverage():
    cov_rows = [{"name": "context7", "has": [False, False, True]}, {"name": "exa", "has": [False, False, True]}]
    mc = agents_library.mcp(ctx(view="mcp", servers=list(INV.mcp), cols={}, no_mcp=[CLAUDE, CODEX],
                                cov_agents=[CLAUDE, CODEX, CURSOR], cov_rows=cov_rows,
                                labels={a.name: a.label for a in INV.agents}))
    by = {r.id: r for r in mc.rows}
    assert by["context7"].warn and not by["exa"].warn and by["exa"].env == "EXA_API_KEY"
    assert fields(plans_of(by["context7"].attrs)["env:0"]) == list(MCP_FIX)
    assert [c.label for c in mc.cov_agents] == ["Claude", "Codex", "Cursor"] and mc.no_mcp == ["Claude Code", "Codex"]
    assert mc.cov_rows[0] == ("context7", [False, False, True])


# ---------------------------------------------------------------- Usage, Findings
def _agg() -> Aggregate:
    days = [("2026-10-13", Counts(input=600, output=200, replies=20)), ("2026-10-12", Counts(input=150, replies=5)),
            ("2026-10-11", Counts(input=50, replies=1))]
    total = Counts(input=800, output=200, cache_read=4000, replies=26, tools=9)
    return Aggregate(days=days, models=[("claude-opus-5-5", Counts(input=795, output=200)),
                                        ("claude-haiku-4-5", Counts(input=5))],
                     top=[("pm", "/dev/pm", Counts(input=500), 7200)], total=total, sessions=3,
                     top_ids=["0b7e4c1a-0000-4000-8000-000000000001"])


def test_usage_totals_bars_models_limits_top():
    lim = [Window("5h limit", 82, "in 2h", "warn", "5-hour limit", abs="Thu 21:00"),
           Window("Week limit", 95, "in 3d", "red", "Weekly limit")]
    u = agents_usage.usage(ctx(view="usage", agg=_agg(), r="7", limits=lim,
                               ranges=(("7", "7 days"), ("30", "30 days"), ("all", "All"))))
    assert [s["key"] for s in u.segs if s["cur"]] == ["7"] and u.segs[2]["href"] == "/agents?t=usage&r=all"
    assert [m["value"] for m in u.totals] == ["3", "26", "9", "1.0k", "4.0k"]
    assert [(b.label, b.h, b.pct) for b in u.bars] == [("10-11", 1, 5), ("10-12", 4, 15), ("10-13", 20, 80)]
    assert [(m.name, m.share) for m in u.models] == [("claude-opus-5-5", "100%"), ("claude-haiku-4-5", "<1%")]
    assert [(m["level"], m["pct"], m["sub"]) for m in u.limits] == [("warn", 82, "resets in 2h"),
                                                                     ("crit", 95, "resets in 3d")]
    t = u.top[0]
    assert t.href == "/a/" + registry.mint_id("claude", "0b7e4c1a-0000-4000-8000-000000000001") and t.span == "2h 00m"
    empty = agents_usage.usage(ctx(view="usage", agg=Aggregate([], [], [], Counts(), 0), r="30", limits=[],
                                   ranges=()))
    assert empty.empty and not empty.limits


def test_findings_groups_fix_plans_and_fix_all():
    from pm.web import agentslib

    fd = agents_usage.findings(ctx(view="findings", **agentslib.findings_ctx(INV)))
    assert [(g.key, len(g.rows), g.fixable, g.tone) for g in fd.groups] == [
        ("security", 1, 1, "warn"), ("rules", 2, 2, "warn"), ("skills", 1, 1, ""), ("notes", 1, 0, "")]
    assert fd.total == 4
    rules = fd.groups[1]
    assert rules.actions[0]["label"] == "Fix all 2" and rules.actions[0]["attrs"] == {"data-a-fixall": "rules"}
    sec = fd.groups[0].rows[0]
    p = json.loads(sec.fix)
    assert sec.fix_label == "Move to env" and fields(p) == list(MCP_FIX) and p["ok"] == "Apply"
    assert len(p["title"]) <= 90
    assert fd.groups[2].rows[0].fix_label == "Link 2" and not fd.groups[3].rows[0].fix
    assert len({r.id for g in fd.groups for r in g.rows}) == 5  # real, stable ids (a hash of kind, agent, text)


# ---------------------------------------------------------------- the detail
def _detail_ctx(tab: str, **kw) -> dict:
    base = ctx(agent=CLAUDE, tab=tab, base="/agents/claude", counts={"skills": 2, "rules": 1, "mcp": 0, "scripts": 1},
               icols={}, cols={})
    return {**base, **kw}


def test_detail_skills_switch_plans_and_menus():
    rows = [{"name": "ntfy", "desc": "Publish", "scope": "shared", "path": "~/.agents/skills/ntfy", "on": True,
             "why": "", "edit": True},
            {"name": "short", "desc": "Tiny.", "scope": "agent", "path": "~/.claude/skills/short", "on": True,
             "why": "built-in skill of another agent", "edit": False}]
    dv = agents.detail(_detail_ctx("skills", rows=rows, total=2, show="all", q="ab",
                                   shows=(("all", "All"), ("enabled", "Enabled"), ("disabled", "Disabled")),
                                   hidden=[("s", "skills"), ("sort", "-name")], self_url="/agents/claude?s=skills"))
    assert [t["key"] for t in dv.tabs] == ["skills", "rules", "mcp", "scripts"] and dv.tabs[0]["cur"]
    assert dv.tabs[1]["href"] == "/agents/claude?s=rules" and dv.new == "skill"
    assert [s["href"] for s in dv.segs] == ["/agents/claude?s=skills&q=ab&sort=-name",
                                           "/agents/claude?s=skills&q=ab&sort=-name&show=enabled",
                                           "/agents/claude?s=skills&q=ab&sort=-name&show=disabled"]
    ntfy, short = dv.items
    sw = json.loads(ntfy.sw)
    assert fields(sw) == [("action", "skill_toggle"), ("agent", "claude"), ("skill", "ntfy")] and sw["quiet"]
    assert {i["id"] for i in ntfy.menu if "id" in i} == {"edit", "copy", "rm"} and short.why
    assert {i["id"] for i in short.menu if "id" in i} == {"copy"}
    assert ("Binary", "~/.local/bin/claude", "mono") in dv.kv and ("MCP config", "none found", "muted") in dv.kv


def test_detail_rules_mcp_scripts_and_editor_modes():
    rule = RuleInfo("CLAUDE.md", "claude", "~/.claude/CLAUDE.md", "x", "symlink", "~/agents/AGENTS.md", 12, "drift")
    dv = agents.detail(_detail_ctx("rules", rows=[rule], can_edit=set(), can_delete=set(),
                                   sync_sources=[("canonical", "Canonical")], sync_targets=[("claude", "Claude", True)],
                                   validation=None))
    r = dv.items[0]
    assert (r.state, r.word) == ("waiting", "differs from the canonical") and "→ ~/agents/AGENTS.md" in r.desc
    assert {i["id"] for i in r.menu if "id" in i} == {"sync", "copy"} and dv.sync_targets
    dv = agents.detail(_detail_ctx("mcp", rows=[INV.mcp[0]]))
    assert dv.items[0].warn == "key in args" and "Per-agent on/off is not supported" in dv.note and not dv.new
    dv = agents.detail(_detail_ctx("scripts", rows=list(INV.scripts), can_edit={"~/.agents/hooks/tmux-agents"}))
    assert fields(plans_of(dv.items[0].attrs)["rm"])[0] == ("action", "script_delete")
    dv = agents.detail(_detail_ctx("skills", mode="edit", kind="skill", obj="ntfy", can_delete=True,
                                   back="/agents/claude?s=skills"))
    rm = json.loads(dv.rm)
    assert dv.mode == "edit" and not dv.items and rm["typed"] == "ntfy" and rm["after"] == "/agents/claude?s=skills"
    assert not agents.detail(_detail_ctx("rules", mode="edit", kind="rule", obj="AGENTS.md", can_delete=False)).rm


def test_finding_kinds_and_backups_are_the_cores():
    from pm.agents.inventory import mutate as agentsmut
    from pm.web import agentslib

    assert agents_base.FINDING_KINDS == agentslib.FINDING_KINDS
    assert agents_base.BACKUPS == "~/" + agentsmut.BACKUP_REL


def test_view_model_errors_are_none_and_logged_once(caplog):
    env = jinja2.Environment()
    env.globals["dt_agents"] = agents.dt_agents
    assert env.from_string("{{ dt_agents('library') is none }}").render(inv=None) == "True"
    assert env.from_string("{{ dt_agents('detail') is none }}").render() == "True"


# ---------------------------------------------------------------- templates (plain Jinja, the real dt files)
@pytest.fixture
def tpl():
    e = jinja2.Environment(loader=jinja2.FileSystemLoader(TPL), autoescape=True, undefined=jinja2.StrictUndefined)
    e.globals["dt_agents"] = agents.dt_agents
    return e


class _Rows(HTMLParser):
    """Collects the direct children of every .p-gr row: (row id, child tag, child attrs)."""
    VOID = {"input", "br", "img", "hr", "meta", "link", "use"}

    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, bool]] = []  # (tag, is a row)
        self.kids: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.stack and self.stack[-1][1]:
            self.kids.append((tag, a))
        if tag not in self.VOID:
            self.stack.append((tag, "p-gr" in (a.get("class") or "").split() and a.get("role") == "row"))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def _cells_only(html: str) -> None:
    """Every direct child of a .p-gr row is a role=cell (parts/_table.html contract)."""
    p = _Rows()
    p.feed(html)
    assert p.kids and all(a.get("role") == "cell" for _, a in p.kids), [k for k in p.kids if k[1].get("role") != "cell"]


def _clean(html: str) -> None:
    assert ' style=' not in html and "<script" not in html and " onclick" not in html
    assert not re.search(r'hx-trigger="[^"]*every ', html)


def test_installed_partial(tpl):
    html = tpl.get_template("_agents_installed.html").render(**ctx())
    _clean(html)
    _cells_only(html)
    assert 'class="p-g a-inst"' in html and 'data-list="agents"' in html and 'data-p-filter-rows="#a-inst"' in html
    assert 'data-a-login="claude"' in html and html.count("data-a-login=") == 3
    assert re.search(r'class="p-gg fold"[^>]*data-p-grp="notinst"', html)  # Not installed: folded
    assert "Review findings" in html and 'href="/agents?t=findings"' in html
    narrow = tpl.get_template("_agents_installed.html").render(**ctx(agent=CLAUDE, icols={}))
    assert "a-narrow" in narrow and "data-a-login" not in narrow and "Review findings" not in narrow
    assert 'class="p-gr a-row sel"' in narrow


def test_library_fragment(tpl):
    html = tpl.get_template("_agents_lib_list.html").render(**lib_ctx())
    _clean(html)
    _cells_only(html)
    assert html.startswith("{#") is False and '<div id="a-lib" class="a-lib" hx-get="/agents?t=library"' in html
    assert 'hx-target="#a-lib"' in html and 'data-p-filter="agents-lib"' in html and 'hx-trigger="change"' in html
    row = html[html.index('data-row-id="rule:AGENTS.md"'):]
    plans = json.loads(unescape(re.search(r'data-a-plans="([^"]+)"', row).group(1)))
    assert set(plans) == {"sync"} and "2 differ" in row
    assert "backed up" in html and "pm trash" not in html


def test_mcp_usage_findings_partials(tpl):
    mc = tpl.get_template("_agents_mcp.html").render(**ctx(
        view="mcp", servers=list(INV.mcp), cols={}, no_mcp=[], cov_agents=[CLAUDE, CURSOR],
        cov_rows=[{"name": "context7", "has": [False, True]}], labels={}))
    _clean(mc)
    assert "key in args" in mc and 'class="p-g a-cov a-n2"' in mc and "a-add" not in mc and ">+<" not in mc
    assert mc.count('class="a-cc" role="cell"><i class="dot running"') == 1 and "not configured" in mc
    u = tpl.get_template("_agents_usage.html").render(**ctx(
        view="usage", agg=_agg(), r="30", limits=[Window("5h", 40, "in 1h", "ok", "5-hour limit")],
        ranges=(("7", "7 days"), ("30", "30 days"))))
    _clean(u)
    assert 'class="p-sum"' in u and '<i class="h20">' in u and "progress" in u and "5-hour limit" in u
    from pm.web import agentslib

    fd = tpl.get_template("agents/_findings.html").render(**ctx(view="findings", **agentslib.findings_ctx(INV)))
    _clean(fd)
    _cells_only(fd)
    runs = [json.loads(unescape(m)) for m in re.findall(r'data-a-run="([^"]+)"', fd)]
    assert len(runs) == 4 and all(r["fields"][0][0] == "action" for r in runs)
    assert 'data-a-fixall="rules"' in fd and re.search(r'class="p-gg fold"[^>]*data-p-grp="notes"', fd)


def test_detail_partials_and_modes(tpl):
    rows = [{"name": "ntfy", "desc": "Publish", "scope": "shared", "path": "~/.agents/skills/ntfy", "on": False,
             "why": "", "edit": True}]
    c = _detail_ctx("skills", rows=rows, total=1, show="all", q="", shows=(("all", "All"),), hidden=[],
                    self_url="/agents/claude?s=skills")
    sk = tpl.get_template("_agents_skills_list.html").render(**c)
    _clean(sk)
    _cells_only(sk)
    assert '<div id="a-sk"' in sk and 'name="enable" value="0"' in sk and "data-a-sw=" in sk
    sw = json.loads(unescape(re.search(r'data-a-sw="([^"]+)"', sk).group(1)))
    assert sw["quiet"] and sw["fields"][0] == ["action", "skill_toggle"]
    dlg = tpl.get_template("agents/_dlg.html").render(**c, dv=agents.detail(c))
    assert 'id="a-new"' in dlg and 'value="skill_create"' in dlg and 'name="description"' in dlg
    assert "data-a-plan" in dlg and 'id="a-sync"' not in dlg
    for mode, extra in (("edit", {"kind": "skill", "obj": "ntfy", "desc": "d", "body": "b", "can_delete": True}),
                        ("review", {"plan": type("P", (), {"ops": [type("O", (), {"kind": "write", "path": "~/x",
                                                                                   "detail": "d"})()],
                                                           "token": "t0"})(),
                                    "hidden": [("name", "ntfy")], "action": "skill_edit", "ctx_agent": "claude",
                                    "ctx_tab": "skills", "destructive": False}),
                        ("result", {"lines": ["wrote ~/x"]}), ("notice", {"notice": "Refused."})):
        mc = _detail_ctx("skills", mode=mode, back="/agents/claude?s=skills", **extra)
        html = tpl.get_template("agents/_mode.html").render(**mc, dv=agents.detail(mc))
        _clean(html)
        assert {"edit": 'data-a-plan data-a-after="/agents/claude?s=skills"', "review": 'action="/agents/apply"',
                "result": "wrote ~/x", "notice": 'role="alert">Refused.'}[mode] in html


# ---------------------------------------------------------------- the whole pages (core routes, real dt theme)
@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".agents/skills/ntfy").mkdir(parents=True)
    (h / ".agents/skills/ntfy/SKILL.md").write_text("---\nname: ntfy\ndescription: Publish.\n---\n\n# ntfy\n")
    (h / ".claude/skills").mkdir(parents=True)
    (h / "agents").mkdir()
    (h / "agents/AGENTS.md").write_text("# rules\n")
    (h / ".claude/CLAUDE.md").write_text("# other\n")
    b = h / ".local/bin/claude"
    b.parent.mkdir(parents=True)
    b.write_text("#!/bin/sh\necho 1.0.0\n")
    b.chmod(b.stat().st_mode | stat.S_IXUSR)
    from pm.agents.inventory import read as agentsinv
    from pm.web import agentslib

    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setattr(agentslib, "home", lambda: h)
    agentsinv.clear_cache()
    yield h
    agentsinv.clear_cache()


@pytest.fixture
def aenv(settings, snap, home):
    class RealDt(DtTheme):
        def themes(self):
            return [themes.Theme(name="dt", templates=TPL, static=THEME / "static", label="dt")]

    return make_env(settings, RealDt())


@pytest.mark.parametrize("t", ["installed", "library", "mcp", "usage", "findings"])
def test_page_sections_render(aenv, t):
    r = aenv.c.get(f"/agents?t={t}")
    assert r.status_code == 200, r.text[:300]
    html = r.text
    assert '<main id="main" class="x-main ">' in html and 'id="a-page"' in html and "could not be built" not in html
    assert f'data-a-view="{t}"' in html and "/static/t/dt/js/agents.js" in html and "agents-page.css" not in html
    assert re.search(rf'id="p-tab-{t}" aria-selected="true"', html)


def test_library_htmx_fragment_and_detail_page(aenv):
    r = aenv.c.get("/agents?t=library&q=ntfy", headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text.lstrip().startswith('<div id="a-lib"') and "ntfy" in r.text
    r = aenv.c.get("/agents/claude?s=skills")
    assert r.status_code == 200 and "could not be built" not in r.text
    html = r.text
    assert 'class="a-split"' in html and 'hx-get="/agents/claude/auth"' in html
    assert "/static/css/agents-page.css" in html  # the classic login fragment's look (README A22)
    assert 'id="a-new"' in html and 'id="a-sk"' in html
    r = aenv.c.get("/agents/claude?s=skills&q=nt", headers={"HX-Request": "true"})
    assert r.text.lstrip().startswith('<div id="a-sk"')
    for s in ("rules", "mcp", "scripts"):
        r = aenv.c.get(f"/agents/claude?s={s}")
        assert r.status_code == 200 and "could not be built" not in r.text, s


def test_editor_and_review_pages_render_in_the_detail(aenv):
    r = aenv.c.get("/agents/claude/edit?kind=skill&name=ntfy")
    assert r.status_code == 200 and "Review and save" in r.text and 'name="description"' in r.text
    r = aenv.post("/agents/review", action="skill_edit", name="ntfy", description="Publish more.", body="# ntfy\n",
                  ctx_agent="claude", ctx_tab="skills", headers={"Sec-Fetch-Mode": "navigate"})
    assert r.status_code == 200 and 'action="/agents/apply"' in r.text and "Review changes" in r.text
    r = aenv.post("/agents/review", action="skill_delete", name="ntfy", confirm="nope", ctx_agent="claude",
                  ctx_tab="skills", headers={"Sec-Fetch-Mode": "navigate"})
    assert r.status_code == 400 and 'role="alert"' in r.text and "Type the exact name" in r.text
    j = aenv.post("/agents/review", action="skill_toggle", agent="claude", skill="ntfy", enable="1",
                  headers={"X-Requested-With": "fetch"}).json()
    assert j["ok"] and j["ops"] and j["token"]  # the JSON the page's review dialog lists


# ---------------------------------------------------------------- JS and CSS rules
def test_js_apply_needs_no_rescan_and_remove_says_backup():
    # the core's /agents/apply drops the inventory cache itself: only the head's Re-scan posts /agents/rescan
    assert JS.count("'/agents/rescan'") == 1 and "rescan(false)" not in JS and "busy(b, rescan)" in JS
    assert "undo: plan.undo" in JS
    sk = plans_of({r.id: r for r in agents_library.library(lib_ctx()).rows}["skill:ntfy"].attrs)
    assert sk["rm"]["undo"] == agents_base.BACKED and agents_base.BACKUPS in agents_base.BACKED


def test_js_reviews_shows_ops_then_applies_and_toasts_after_ok():
    assert "'/agents/review'" in JS and "'/agents/apply'" in JS and "'/agents/rescan'" in JS
    assert "/agents/auth.json" in JS and "status === 409" in JS
    i_confirm, i_apply, i_toast = JS.index("pm7.confirm({ title: what"), JS.index("await apply(plan.fields"), \
        JS.index("pm7.toast(plan.done")
    assert i_confirm < i_apply < i_toast
    assert "innerHTML" not in JS and "fetch(" not in JS.replace("pm7.fetch(", "")
    assert "addEventListener('keydown'" not in JS and "setInterval" not in JS


def test_css_only_page_classes_and_tokens():
    sels = re.findall(r"(?:^|})\s*([^{}@]+)\{", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S))
    for s in sels:
        for part in s.split(","):
            part = part.strip()
            if part.startswith(("from", "to")) or not part:
                continue
            assert re.search(r"\.a-|#a-|\.x-dlg\.a-dlg", part), part
    assert "#" not in re.sub(r"#a-[\w-]+", "", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S))
