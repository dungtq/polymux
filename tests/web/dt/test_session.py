"""dt session tab frame (PRD §6.3, templates/agent.html, agent_ended.html, builder.html, session/_frame.html,
shell/_classic.html page flag, css/session.css, js/session.js): the real dt theme rendered through the app for a live
agent, ?st= / embed variants, an ended record and the Builder, plus static rules of the page files."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pm.agents import registry
from pm.infra import snapshot as services
from pm.web import themes
from pm.web.dt import DtTheme

from .conftest import agent, make_env, make_snap
from .kit import Doc

AID = "claude-1c70ee"
THEME = themes.HERE / "dt"


@pytest.fixture
def live(settings, snap, monkeypatch):
    snap.value = make_snap([agent(AID, "alpha")])

    async def fake_snapshot(*_a, **_k):
        return snap.value

    monkeypatch.setattr(services, "snapshot_async", fake_snapshot)
    return make_env(settings, DtTheme())


def page(env, url):
    r = env.c.get(url)
    assert r.status_code == 200, (url, r.status_code, r.text[:300])
    return r.text


def scripts(src: str) -> list[str]:
    return [re.sub(r"\?v=\w+$", "", s) for s in re.findall(r'<script src="([^"]+)"', src)]


def menu_items(doc: Doc) -> dict[str, dict]:
    btn = doc.one("button", class_="c-more")
    return {it["id"]: it for it in json.loads(btn["data-menu"]) if not it.get("sep")}


# ---------------------------------------------------------------- live agent page
def test_live_frame_header(live):
    src = page(live, f"/a/{AID}")
    doc = Doc(src)
    assert doc.one("main")["class"].split() == ["x-main"]  # a ported page: no .x-cl on main
    sess = doc.one("section", class_="c-sess")
    assert sess["data-agent"] == AID and sess["data-c-mode"] == "live" and "data-menu-scope" in sess
    doc.one("header", class_="c-hd")
    assert "running" in doc.one("i", data_c_dot=True)["class"]
    assert doc.one("h1", class_="c-nm sfx")["title"] == AID
    doc.one(id="agent-state")  # classic chat.js / slash.js hook
    hook = doc.one("span", class_="agent-head")  # classic agentmode.js hook
    assert hook["data-mode"] == "live" and hook["data-agent"] == AID and "hidden" in hook
    assert doc.one("a", class_="c-bbtn")["href"] == f"/a/{AID}/builder"
    doc.one("button", data_c_toaside=True)
    doc.one("button", data_c_font=True)
    assert {b["data-chat-view"] for b in doc.find("button", data_chat_view=True)} == {"detail", "compact"}


def test_live_tabs_drive_the_classic_radios(live):
    doc = Doc(page(live, f"/a/{AID}"))
    tabs = doc.find(role="tab", data_p_tab=True)
    assert [t["data-p-tab"] for t in tabs] == ["chat", "sub", "pane"]  # no Terminal: terminal off in tests
    assert [t["aria-selected"] for t in tabs] == ["true", "false", "false"]
    for t in tabs:
        assert doc.one(id=t["aria-controls"])["role"] == "tabpanel"
    radios = doc.find("input", name="agent-tab")
    assert {r["id"] for r in radios} == {"tab-chat", "tab-pane", "tab-sub"}
    assert [r["id"] for r in radios if "checked" in r] == ["tab-chat"]
    assert "hidden" not in doc.one(id="c-pn-chat") and "hidden" in doc.one(id="c-pn-pane")


def test_live_chat_column_and_composer(live):
    doc = Doc(page(live, f"/a/{AID}"))
    assert doc.one(id="chatsplit")["class"].split()[:2] == ["c-split", "chatsplit"]
    doc.one(id="mdpane")
    doc.one("div", class_="c-chat", data_ci_scroll=True)  # classic chatlog.js scroller
    for hook in ("ci", "chat", "sa-live", "ci-pending", "pane", "keypad-wrap", "sa-body"):
        doc.one(id=hook)
    comp = doc.one("section", class_="composer", data_composer="live")
    assert "composer--flow" in comp["class"]
    doc.one("template", id="cmp-continue")


def test_live_menu_items(live):
    items = menu_items(Doc(page(live, f"/a/{AID}")))
    assert list(items) == ["newchat", "continue", "fork", "review", "usage", "btw", "copy", "exit", "exitkill"]
    assert items["newchat"]["click"] == "form[data-newchat] button[type=submit]"
    assert items["fork"]["post"] == f"/a/{AID}/fork" and items["fork"]["confirm"]
    assert items["review"]["href"] == f"/p/alpha/launch?review={AID}"
    assert items["exit"]["post"] == f"/a/{AID}/exit" and "typed" not in items["exit"]
    kill = items["exitkill"]
    assert kill["post"] == f"/a/{AID}/exitkill" and kill["typed"] == AID and kill["danger"]  # V7-SEC-6
    assert items["copy"]["copy"] == AID
    assert {items[k]["event"] for k in ("continue", "usage", "btw")} == {"c:continue", "c:usage", "c:btw"}


@pytest.mark.parametrize(("st", "want"), [("pane", "pane"), ("sub", "sub"), ("term", "chat"), ("nope", "chat")])
def test_live_first_tab_from_query(live, st, want):
    doc = Doc(page(live, f"/a/{AID}?st={st}"))
    assert [t["data-p-tab"] for t in doc.find(role="tab", aria_selected="true", data_p_tab=True)] == [want]
    assert [r["id"] for r in doc.find("input", name="agent-tab") if "checked" in r] == [f"tab-{want}"]
    assert "hidden" not in doc.one(id=f"c-pn-{want}")


def test_embed_is_chat_and_composer_only(live):
    doc = Doc(page(live, f"/a/{AID}?embed=1"))
    assert "min" in doc.one("header", class_="c-hd")["class"]
    assert not doc.find(role="tab", data_p_tab=True) and not doc.find("input", name="agent-tab")
    assert not doc.find("h1", class_="c-nm") and not doc.find(class_="c-bbtn") and not doc.find(data_c_toaside=True)
    assert not doc.find(id="c-pn-pane") and not doc.find(id="keypad-wrap")
    doc.one("section", class_="composer", data_composer="live")
    doc.one("button", class_="c-more")


def test_classic_page_scripts_in_order(live):
    src = page(live, f"/a/{AID}")
    s = scripts(src)
    page_js = ["/static/js/agentmode.js", "/static/js/chat-send.js", "/static/js/atfiles.js",
               "/static/js/issue-save.js", "/static/js/snippets.js", "/static/js/modelpick.js",
               "/static/js/newchat.js", "/static/js/continuepop.js", "/static/js/chat.js", "/static/js/chat-md.js",
               "/static/js/chatlog.js", "/static/js/chat-items.js", "/static/js/subagents.js", "/static/js/pane.js",
               "/static/js/canvas.js"]
    idx = [s.index(x) for x in page_js]
    assert idx == sorted(idx)
    assert s.index("/static/js/client-errors.js") < idx[0] and idx[-1] < s.index("/static/t/dt/js/core.js")
    assert s.index("/static/t/dt/js/core.js") < s.index("/static/t/dt/js/session.js")
    assert "/static/js/composer-dock.js" not in s and "/static/js/jumprail.js" not in s
    assert '/static/t/dt/css/session.css' in src and '/static/css/pane.css' in src


def test_no_session_scripts_on_other_pages(live):
    s = scripts(page(live, "/tmux"))
    assert "/static/js/chat.js" not in s and "/static/t/dt/js/session.js" not in s


# ---------------------------------------------------------------- ended record
def test_ended_frame(live, monkeypatch):
    rec = registry.Record(AID, "claude", "0123abcd-0000-4000-8000-000000000000", 0, 100.0, 200.0, "%9", "alpha",
                          "/x", "", "", 300.0)
    monkeypatch.setattr(registry, "resolve", lambda *_a: registry.Resolved(ended=rec))
    src = page(live, f"/a/{AID}")
    doc = Doc(src)
    assert doc.one("section", class_="c-sess")["data-c-mode"] == "ended"
    assert "ended" in doc.one("i", data_c_dot=True)["class"]
    assert doc.one("span", class_="agent-head")["data-mode"] == "ended"
    assert not doc.find(role="tab", data_p_tab=True) and not doc.find(class_="c-bbtn")
    doc.one("section", class_="composer", data_composer="ended")
    items = menu_items(doc)
    assert list(items) == ["resume", "fork", "copy"]
    assert items["resume"]["post"] == f"/a/{AID}/resume"
    s = scripts(src)
    assert "/static/js/chat-send.js" not in s and "/static/js/canvas.js" in s and "/static/js/pane.js" not in s


# ---------------------------------------------------------------- Builder
def test_builder_frame(live):
    src = page(live, f"/a/{AID}/builder")
    doc = Doc(src)
    assert doc.one("section", class_="c-sess")["data-c-mode"] == "builder"
    b = doc.one("a", class_="c-bbtn")
    assert "on" in b["class"] and b["href"] == f"/a/{AID}" and b["aria-current"] == "page"
    tabs = doc.find("a", role="tab", data_p_tab=True)
    assert [(t["data-p-tab"], t["href"]) for t in tabs] == [
        ("chat", f"/a/{AID}"), ("sub", f"/a/{AID}?st=sub"), ("pane", f"/a/{AID}?st=pane")]
    assert not doc.find("span", class_="agent-head")  # #builder[data-mode] is agentmode.js's hook here
    assert doc.one(id="builder")["data-mode"] == "live"
    for hook in ("bld-msgs", "bld-stage", "bld-size", "bld-zoom", "bld-src", "bld-files", "bt-chat", "bt-prev"):
        doc.one(id=hook)
    s = scripts(src)
    order = ["/static/js/builder.js", "/static/js/builder-screen.js", "/static/js/builder-api.js",
             "/static/js/chat-send.js", "/static/js/chatlog.js", "/static/js/chat-items.js",
             "/static/js/continuepop.js", "/static/js/chat.js", "/static/js/chat-md.js"]
    idx = [s.index(x) for x in order]
    assert idx == sorted(idx)
    assert "/static/js/canvas.js" not in s and "/static/js/pane.js" not in s
    assert "/static/css/builder.css" in src


# ---------------------------------------------------------------- static rules of the page files
def test_page_files_static_rules():
    css = (THEME / "static/css/session.css").read_text()
    js = (THEME / "static/js/session.js").read_text()
    for f, text in (("session.css", css), ("session.js", js)):
        lines = text.splitlines()
        assert len(lines) <= 500, f
        assert all(len(ln) <= 120 for ln in lines), f
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)  # tokens only (V7-UI-2)
    assert ":root" not in css
    assert "addEventListener('keydown'" not in js  # page keys go through pm7.keys (PARTS §12)
    assert "localStorage" not in js and "fetch(" not in js  # pm7.store / pm7.fetch only (V7-ISO-6, V7-CODE-6)
    for name in ("agent.html", "agent_ended.html", "builder.html", "session/_frame.html"):
        tpl = (THEME / "templates" / name).read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)|\sstyle=|\son[a-z]+=", tpl), name  # CSP (V7-SEC-2)
        assert "jumprail" not in tpl and "composer-dock" not in tpl, name
