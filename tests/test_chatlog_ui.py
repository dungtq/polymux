"""Agent page chat around the items (pm.web.routes.chat): header actions, tool chip families, uploads.

The conversation itself (GET /a/<id>/items) and the live strip are tested in tests/web/test_routes_chatitems.py."""

import re

from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.web.render import STATIC_DIR
from pm.web.routes import chat as routes_chat


def test_tool_family():
    fam = routes_chat.tool_family
    assert [fam(n) for n in ("Bash", "exec_command", "BashOutput")] == ["shell"] * 3
    assert [fam(n) for n in ("Read", "Grep", "Glob", "list_dir", "codebase_search")] == ["read"] * 5
    assert [fam(n) for n in ("Edit", "Write", "MultiEdit", "apply_patch", "NotebookEdit")] == ["edit"] * 5
    assert [fam(n) for n in ("WebFetch", "WebSearch", "web_search")] == ["web"] * 3
    assert [fam(n) for n in ("Task", "Agent", "TodoWrite", "Skill")] == ["agent"] * 4
    assert fam("mcp__claude_ai_Gmail__search") == "mcp" and fam("") == "other" and fam("Zork") == "other"


def test_chat_css_tool_tokens_both_themes():
    css = (STATIC_DIR / "css" / "chat.css").read_text()
    tokens = (STATIC_DIR / "css" / "tokens.css").read_text()
    for f in ("shell", "read", "edit", "web", "agent", "mcp"):
        assert re.search(rf"--tool-{f}:light-dark\(#[0-9a-f]{{6}}, #[0-9a-f]{{6}}\)", tokens), f
        assert f".tf-{f} {{ --tc: var(--tool-{f}); }}" in css
    assert "prefers-reduced-motion: reduce) { .chat-working .spin { animation: none; }" in css
    assert ".composer .tokbar" in (STATIC_DIR / "css" / "composer.css").read_text()


def test_agent_header_new_chat_and_no_clone(env):
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    head = t[t.index('class="agent-actions"'):t.index('class="cform"')]  # actions sit above the composer box
    assert "Launch like this" not in t and ">Clone</a>" not in head and "launch?like=" not in t
    assert "fork-menu" not in t and "fork-group" not in t and 'name="where"' not in head and "Fork in split pane" not in t
    assert len(re.findall(r'action="/a/claude-12/fork"', head)) == 1 and ">Fork</button>" in head
    assert "data-newchat" in head and ">New Chat</button>" in head and 'action="/a/claude-12/newchat"' in head
    assert 'id="ci"' in t and "/chat/log" not in t
    assert t.index('id="ci"') < t.index('id="chat"') < t.index('data-composer="live"')  # items, live strip, composer
    bar = t[t.index('data-composer="live"'):]
    assert bar.index('id="chat-stats"') < bar.index('class="cform"')  # token bar right above the composer box
    assert "/static/js/chatlog.js" in t and "/static/js/chat-items.js" in t and "/static/js/newchat.js" in t


# ---------------------------------------------------------------- uploaded images in messages
def _upload(s, name="2105586e23a3997081fbc0ff", ext="png"):
    d = s.state_dir / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.{ext}").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)
    return d / f"{name}.{ext}"


def test_upload_route_auth_strict_name_and_type(env):
    s, c = env
    p = _upload(s)
    assert c.get(f"/uploads/{p.name}", follow_redirects=False).status_code in (303, 401)
    login(s, c)
    r = c.get(f"/uploads/{p.name}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    for bad in ("..%2Fx.png", "x.png", "2105586e23a3997081fbc0ff.txt", "2105586E23A3997081FBC0FF.png"):
        assert c.get(f"/uploads/{bad}").status_code == 404
    (s.state_dir / "uploads" / "bbbbbbbbbbbbbbbbbbbbbbbb.png").write_bytes(b"<script>alert(1)</script>")
    assert c.get("/uploads/bbbbbbbbbbbbbbbbbbbbbbbb.png").status_code == 404
    (s.state_dir / "uploads" / "cccccccccccccccccccccccc.png").symlink_to("/etc/hostname")
    assert c.get("/uploads/cccccccccccccccccccccccc.png").status_code == 404


def test_screenshot_path_outside_uploads_is_auto_imported(tmp_path, monkeypatch):
    from pm.chat import text as chat_text
    from pm.core import uploads as uploads_mod
    shot_root = tmp_path / "claude-x"
    shot_root.mkdir()
    png = shot_root / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    (shot_root / "evil.png").write_bytes(b"not an image")
    monkeypatch.setattr(uploads_mod, "AUTO_ROOTS", (shot_root,))
    up = tmp_path / "uploads"
    html = chat_text.render_message(f"look\n{png}\n{shot_root}/evil.png\n/etc/passwd.png", up)
    assert html.count('class="chat-img"') == 1 and "/uploads/" in html
    assert len(list(up.iterdir())) == 1
