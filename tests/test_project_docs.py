"""Project page: right-hand tabs, read-only file/skill views, dirty tab (confined, masked, GET only)."""

import os

from helpers import TOKEN, login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.projects import projfiles


def _proj(s):
    return s.dev_root / "alpha"


def _home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def test_default_tab_changelog_then_readme(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = _proj(s)
    (p / "README.md").write_text("# Hello readme\n")
    h = c.get("/p/alpha").text
    assert 'data-pj="changelog" aria-selected="false"' in h and 'data-pj="readme" aria-selected="true"' in h
    assert "Hello readme" in h
    (p / "CHANGELOG.md").write_text("# Changes\n- shipped <b>x</b>\n")
    h = c.get("/p/alpha").text
    assert 'data-pj="changelog" aria-selected="true"' in h and "shipped" in h and "Hello readme" not in h
    assert "<b>x</b>" not in h
    assert h.index('id="pj-body"') < h.index("shipped")
    assert h.index(">Browse<") < h.index(">README<") < h.index(">CHANGELOG<")
    for t in ("Browse", "README", "CHANGELOG"):
        assert t in h


def test_view_masks_and_strips(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    (_proj(s) / "CLAUDE.md").write_text(f"x \x1b[31mred\x1b[0m\nexport GH={TOKEN}\n<script>a()</script>\n")
    h = c.get("/p/alpha/view/claude").text
    assert TOKEN not in h and "\x1b" not in h and "<script>" not in h
    assert "red" in h


def test_view_missing_unknown_and_symlink(env, tmp_path):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    assert "No AGENTS.md here." in c.get("/p/alpha/view/agents").text
    assert c.get("/p/alpha/view/passwd").status_code == 404
    assert c.get("/p/nope/view/readme").status_code == 404
    secret = tmp_path / "secret.md"
    secret.write_text("TOPSECRET")
    os.symlink(secret, _proj(s) / "AGENTS.md")
    r = c.get("/p/alpha/view/agents")
    assert "TOPSECRET" not in r.text and "symlink" in r.text


def test_view_size_cap_and_auth(env, monkeypatch):
    s, c = env
    r = c.get("/p/alpha/view/readme", follow_redirects=False)
    assert r.status_code == 303
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    monkeypatch.setattr(projfiles, "MAX_BYTES", 100)
    (_proj(s) / "README.md").write_text("a" * 500)
    h = c.get("/p/alpha/view/readme").text
    assert "cut at" in h and "a" * 101 not in h


def test_view_is_get_only(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    assert c.post("/p/alpha/view/readme").status_code in (403, 405)


def test_dirty_tab(env, monkeypatch):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    from pm.git import ctl as gitctl
    monkeypatch.setattr(gitctl, "dirty_files", lambda path, limit=60: ([("M", "a<i>.py")], 1))
    h = c.get("/p/alpha/view/dirty").text
    assert "a&lt;i&gt;.py" in h and "dirty-files" in h


def test_skills_listed_and_viewed(env, tmp_path, monkeypatch):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    home = _home(tmp_path, monkeypatch)
    g = home / ".agents/skills/gskill"
    g.mkdir(parents=True)
    (g / "SKILL.md").write_text("---\nname: gskill\ndescription: Global thing\n---\n# G\nbody\n")
    (home / ".claude/skills").mkdir(parents=True)
    os.symlink(g, home / ".claude/skills/gskill")  # same dir via symlink: listed once
    loc = _proj(s) / ".claude/skills/lskill"
    loc.mkdir(parents=True)
    (loc / "SKILL.md").write_text("# Local\nDoes local things\n")
    page = c.get("/p/alpha").text
    assert 'id="skills"' not in page and 'href="/p/alpha/view/skills" data-pj="skills"' in page  # a docs tab, not a block
    h = c.get("/p/alpha/view/skills").text
    assert h.count("gskill</a>") == 1 and "Global thing" in h and "lskill</a>" in h and "Does local things" in h
    assert 'role="tablist"' in h and 'aria-label="Skill scope"' in h  # Local / Global sub-tabs
    r = c.get("/p/alpha/skill/global/agents/gskill").text
    assert "body" in r
    r = c.get("/p/alpha/skill/local/claude/lskill").text
    assert "Does local things" in r


def test_skill_confinement(env, tmp_path, monkeypatch):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    home = _home(tmp_path, monkeypatch)
    out = tmp_path / "outside/evil"
    out.mkdir(parents=True)
    (out / "SKILL.md").write_text("OUTSIDE-SECRET")
    sk = _proj(s) / ".claude/skills"
    sk.mkdir(parents=True)
    os.symlink(out, sk / "evil")
    for u in ("/p/alpha/skill/local/claude/evil", "/p/alpha/skill/local/claude/..%2f..",
              "/p/alpha/skill/local/claude/.hidden", "/p/alpha/skill/bad/claude/x",
              "/p/alpha/skill/local/other/x"):
        r = c.get(u)
        assert "OUTSIDE-SECRET" not in r.text and r.status_code == 404
    assert "OUTSIDE-SECRET" not in c.get("/p/alpha").text
    assert home.exists()


def test_claude_agents_are_doc_tabs(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    h = c.get("/p/alpha").text
    assert "<b>Project files</b>" not in h and 'id="docs"' not in h
    for k in ("claude", "agents"):
        if f'data-pj="{k}"' in h:  # tab only when the file exists
            assert h.index('data-pj="changelog"') < h.index(f'data-pj="{k}"') < h.index('id="pj-body"')
    (s.dev_root / "alpha" / "CLAUDE.md").write_text("# c\n")
    h = c.get("/p/alpha").text
    assert 'href="/p/alpha/view/claude" data-pj="claude"' in h and 'data-pj="agents"' not in h
    assert "projview.js" in h


def test_describe_front_matter():
    assert projfiles._describe("---\nname: a\ndescription: >\n  one\n  two\n---\nx") == "one two"
    assert projfiles._describe("# T\n\nfirst line\n") == "first line"


def test_browse_tab_replaces_files_button(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    h = c.get("/p/alpha").text
    assert 'data-pj="browse"' in h and "pj-files-btn" not in h
    assert 'href="/p/alpha/files"' in h
    assert c.get("/p/alpha/view/dirty").status_code == 200


def test_features_tab_after_readme(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    h = c.get("/p/alpha").text
    assert 'data-pj="features"' not in h  # tab only when the file exists
    (s.dev_root / "alpha" / "FEATURES.md").write_text("# Feats\n- **One** thing\n")
    h = c.get("/p/alpha").text
    assert h.index(">README<") < h.index(">FEATURES<") < h.index(">CHANGELOG<")
    v = c.get("/p/alpha/view/features")
    assert v.status_code == 200 and "One" in v.text


def test_agents_tab_first_and_replace_hooks(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    h = c.get("/p/alpha").text
    assert h.index('data-pj-agents') < h.index('data-pj="browse"')
    assert ">Agents<" in h
    js = c.get("/static/js/projview.js").text
    assert "pj-docsview" in js and "pj-repl" in js


def test_backlog_pane_head_has_open_done_toggle(env):
    s, c = env
    login(s, c)
    c.headers["X-Requested-With"] = "pm"
    p = _proj(s)
    (p / "BACKLOG.md").write_text("# Backlog\n\n## Inbox\n\n- [ ] one\n- [ ] two\n\n## Done\n\n- [x] old\n")
    h = c.get("/p/alpha").text
    head = h[h.index('id="pj-p-backlog"'):h.index('id="backlog-card"')]
    assert 'class="bl-seg"' in head
    assert 'aria-pressed="true" data-bl-view="open">Open <span class="bl-seg-n">2</span>' in head
    assert 'aria-pressed="false" data-bl-view="done">Done <span class="bl-seg-n">1</span>' in head
    assert head.index("bl-seg") < head.index("data-bl-file")  # toggle sits in the head row, before Edit file
    assert 'id="backlog-card" data-bl-view="open"' in h
    assert "2 open" not in h  # the old count line is gone
    (p / "BACKLOG.md").write_text("# Backlog\n\n## Inbox\n\n- [ ] one\n")
    h = c.get("/p/alpha").text
    assert 'data-bl-view="open"' in h and 'data-bl-view="done">' not in h  # no done items: no Done button


def test_direct_navigation_lands_on_project_page(env):
    """Pane fragments opened directly (address bar, plain link) redirect to the project page with that tab open."""
    s, c = env
    login(s, c)
    nav = {"Sec-Fetch-Mode": "navigate"}
    for hdr in ({}, nav, {**nav, "X-Requested-With": "pm"}):
        for url, loc in (("/p/alpha/files", "/p/alpha#browse"),
                         ("/p/alpha/files?path=sub dir/a b.md", "/p/alpha#browse=sub%20dir/a%20b.md"),
                         ("/p/alpha/view/readme", "/p/alpha#view=readme"),
                         ("/p/alpha/skill/global/agents/gskill", "/p/alpha#skill=global/agents/gskill")):
            r = c.get(url, headers=hdr, follow_redirects=False)
            assert r.status_code == 303 and r.headers["location"] == loc, (url, hdr)
    for hdr in ({"X-Requested-With": "pm"}, {"HX-Request": "true"}, {"Sec-Fetch-Mode": "cors", "X-Requested-With": "pm"}):
        r = c.get("/p/alpha/files", headers=hdr, follow_redirects=False)
        assert r.status_code == 200 and 'aria-label="Files"' in r.text and "<html" not in r.text
    js = c.get("/static/js/projview.js").text
    assert "#(browse|view|skill)" in js and "'/files' + (hv ? '?path=' + encodeURIComponent(hv)" in js
