from pathlib import Path

from helpers import search_client

from pm.core.models import Agent, AgentKind, Project, TmuxSession, TmuxWindow
from pm.infra.snapshot import Snapshot
from pm.web.routes import search as routes_search

ROOT = Path(__file__).resolve().parents[1] / "src/pm"


def _snap():
    win = [TmuxWindow("@1", "$1", i, f"win{i}") for i in range(2)]
    ag = Agent("claude-aaaaaa", AgentKind.CLAUDE, "%1", "$1", 1, "/x", project="alpha", label="dev-1-claude")
    return Snapshot(ts=0, sessions=(TmuxSession("$1", "devbox", windows=tuple(win)),), agents=(ag,),
                    projects=tuple(Project(f"alpha{i}", f"/d/alpha{i}") for i in range(12)))


def test_requires_session(fake_tmux, tmp_path):
    c, _ = search_client(fake_tmux, tmp_path)
    r = c.get("/search?q=a", follow_redirects=False)
    assert r.status_code in (303, 401, 403)


def test_hits_grouped_and_bounded():
    groups = routes_search.search_hits(_snap(), [], "alpha")
    kinds = {k: h for k, _t, h in groups}
    assert len(kinds["project"]) == routes_search.MAX_PER_GROUP
    assert kinds["agent"][0].href == "/a/claude-aaaaaa"
    assert routes_search.search_hits(_snap(), [], "devbox")[0][2][0].href == "/tmux/s1"
    assert routes_search.search_hits(_snap(), [], "win1")[0][0] == "tmux"
    assert routes_search.search_hits(_snap(), [], "zzz") == []


def test_route_fragment(fake_tmux, tmp_path, monkeypatch):
    c, s = search_client(fake_tmux, tmp_path)
    c.post("/login", data={"token": s.token_file.read_text().strip()}, follow_redirects=False)

    async def snap(_r):
        return _snap()

    monkeypatch.setattr(routes_search, "snapshot", snap)
    r = c.get("/search?q=ALPHA1")
    assert r.status_code == 200 and 'href="/p/alpha1"' in r.text and "<script" not in r.text
    assert c.get("/search?q=").text.strip() == ""
    assert "No match" in c.get("/search?q=<b>nope").text and "<b>" not in c.get("/search?q=<b>nope").text


def test_topbar_markup(fake_tmux, tmp_path):
    c, s = search_client(fake_tmux, tmp_path)
    c.post("/login", data={"token": s.token_file.read_text().strip()}, follow_redirects=False)
    html = c.get("/audit").text
    assert 'id="gsearch"' in html and "/static/js/search.js" in html
    assert (ROOT / "static/js/search.js").is_file()


def test_shortcut_contract():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "src/pm/static/js/search.js").read_text()
    assert "e.metaKey || e.ctrlKey" in js and 'k !== "k" && k !== "p"' in js
    assert 'dialog[open]' in js and "e.isComposing" in js
    assert 'k === "p" && e.shiftKey' in js  # Cmd/Ctrl+Shift+P belongs to the command palette


def test_content_groups(fake_tmux, tmp_path, monkeypatch):
    c, s = search_client(fake_tmux, tmp_path)
    c.post("/login", data={"token": s.token_file.read_text().strip()}, follow_redirects=False)
    proj = tmp_path / "dev" / "alpha"
    proj.mkdir()
    (proj / "a.txt").write_text("find the zebra here\n")

    async def snap(_r):
        return Snapshot(ts=0, sessions=(), agents=(), projects=(Project("alpha", str(proj)),))

    monkeypatch.setattr(routes_search, "snapshot", snap)
    monkeypatch.setattr(routes_search.chatlog, "search", lambda _s, q, n: [
        {"agent_id": "claude-aaaaaa", "role": "user", "ts": 0.0, "seq": 1, "snippet": "a <b>zebra</b>"}])
    t = c.get("/search?q=zebra").text
    assert 'aria-label="Chats"' in t and 'href="/a/claude-aaaaaa"' in t and "<b>" not in t
    assert 'aria-label="Files"' in t and 'href="/p/alpha/files?path=a.txt"' in t and "alpha/a.txt:1" in t
    assert "Chats" not in c.get("/search?q=ze").text  # under 3 chars: names only
