import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from helpers import ORIGIN, make_client, make_settings

from pm.core.models import Tier
from pm.projects import backlog as rb

ORIGIN = "https://pm.example.test"
ALPHA = "## Inbox\n\n- [ ] a1\n- [ ] a2\n- [x] a3\n\n## Done\n"
SEED = "# t\n\n## Inbox\n\n- [ ] one (2026-01-01)\n\n## Next\n\n- [ ] two\n\n## Done\n"


@pytest.fixture
def env(fake_tmux, tmp_path):
    dev = tmp_path / "dev"
    (dev / "pm").mkdir(parents=True)
    (dev / "alpha").mkdir()
    (dev / "empty").mkdir()
    f = dev / "pm" / "BACKLOG.md"
    f.write_text(SEED)
    (dev / "alpha" / "BACKLOG.md").write_text(ALPHA)
    s = make_settings(fake_tmux, tmp_path, state="st", tier_ceiling=Tier.SAFE, disable_poll=True)
    c = make_client(s, static=True, base_url=ORIGIN, logged_in=True)
    return c, f, s


def fields(c, name="pm"):
    html = c.get(f"/backlog?p={name}&edit=1").text
    return (re.search(r'name="csrf" value="([^"]+)"', html).group(1),
            re.search(r'name="token" value="([0-9a-f]{16})"', html).group(1))


def post(c, url, csrf, origin=ORIGIN, project="pm", **data):
    h = {"Origin": origin} if origin else {}
    return c.post(url, data={"csrf": csrf, "project": project, **data}, headers=h, follow_redirects=False)


def audit(s):
    return s.audit_file.read_text() if s.audit_file.exists() else ""


def test_render(env):
    c, f, s = env
    r = c.get("/backlog")
    assert r.status_code == 200
    assert "one (2026-01-01)" in r.text and "bl-item" in r.text and "Inbox" in r.text
    assert 'href="/backlog"' in r.text  # nav


def test_requires_login(env):
    c, f, s = env
    c2 = TestClient(c.app, base_url="https://pm.example.test")
    assert c2.get("/backlog", follow_redirects=False).status_code in (303, 401)
    assert c2.post("/backlog/add", data={"text": "x"}, headers={"Origin": ORIGIN}).status_code in (401, 403)


def test_add(env):
    c, f, s = env
    csrf, tok = fields(c)
    r = post(c, "/backlog/add", csrf, token=tok, text="  hello   <b>world</b> ")
    assert r.status_code == 303
    t = f.read_text()
    assert re.search(r"- \[ \] hello <b>world</b> \(\d{4}-\d\d-\d\d\)\n\n## Next", t)
    assert "<b>" not in c.get("/backlog").text.replace("&lt;b&gt;", "")  # escaped
    log = audit(s)
    assert "backlog_add" in log and "hello" not in log


def test_add_creates_inbox_and_empty_section():
    assert rb.add_item("# t\n", "a", "D").endswith("## Inbox\n\n- [ ] a (D)\n")
    out = rb.add_item("## Inbox\n## Next\n", "a", "D")
    assert out == "## Inbox\n\n- [ ] a (D)\n\n## Next\n"
    with pytest.raises(rb.Bad):
        rb.add_item("", "   ", "D")


def test_toggle(env):
    c, f, s = env
    csrf, tok = fields(c)
    line = f.read_text().split("\n").index("- [ ] one (2026-01-01)")
    t = "one (2026-01-01)"
    assert post(c, "/backlog/toggle", csrf, line=str(line), text=t).status_code == 303
    assert "- [x] one (2026-01-01)" in f.read_text()
    post(c, "/backlog/toggle", csrf, line=str(line), text=t)
    assert f.read_text() == SEED
    r = post(c, "/backlog/toggle", csrf, line="0", text=t)  # moved line, text still unique: ok
    assert r.status_code == 303
    r = post(c, "/backlog/toggle", csrf, line="0", text="# t")  # heading is not an item
    assert r.status_code == 409
    r = post(c, "/backlog/toggle", csrf, line="0")  # no text
    assert r.status_code == 400


def test_edit_save(env):
    c, f, s = env
    assert "<textarea" in c.get("/backlog?p=pm&edit=1").text
    csrf, tok = fields(c)
    r = post(c, "/backlog/save", csrf, token=tok, content="## Inbox\r\n\r\n- [ ] only\r\n\r\n\r\n")
    assert r.status_code == 303
    assert f.read_text() == "## Inbox\n\n- [ ] only\n"
    assert "only" not in audit(s) and "backlog_save" in audit(s)
    assert not [p for p in f.parent.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]


def test_save_conflict_no_overwrite(env):
    c, f, s = env
    csrf, tok = fields(c)
    f.write_text(SEED + "- [ ] edited elsewhere\n")
    r = post(c, "/backlog/save", csrf, token=tok, content="mine")
    assert r.status_code == 409 and "changed since" in r.text
    assert f.read_text() == SEED + "- [ ] edited elsewhere\n"
    assert "mine" in r.text  # user's edit kept in the textarea
    assert "Overwrite anyway" in r.text and "Reload current" in r.text and "bl-diff" in r.text


def test_item_ops_survive_unrelated_edits(env):
    c, f, s = env
    csrf, _ = fields(c)
    f.write_text("# new top\n\n" + SEED + "- [ ] added by agent\n")  # shifts every line
    assert post(c, "/backlog/toggle", csrf, line="4", text="two").status_code == 303  # stale index
    assert "- [x] two" in f.read_text() and "# new top" in f.read_text()
    assert post(c, "/backlog/add", csrf, text="from phone").status_code == 303
    t = f.read_text()
    assert "from phone" in t and "added by agent" in t


def test_toggle_item_changed_or_gone_409(env):
    c, f, s = env
    csrf, _ = fields(c)
    f.write_text(SEED.replace("- [ ] two", "- [ ] two (reworded)"))
    before = f.read_text()
    r = post(c, "/backlog/toggle", csrf, line="8", text="two")
    assert r.status_code == 409 and "changed or removed" in r.text
    assert f.read_text() == before
    assert "conflict" in audit(s) and "reworded" not in audit(s)


def test_csrf_origin_rejected(env):
    c, f, s = env
    csrf, tok = fields(c)
    assert post(c, "/backlog/add", "bad", token=tok, text="x").status_code == 403
    assert post(c, "/backlog/add", csrf, origin="https://evil.example", token=tok, text="x").status_code == 403
    assert post(c, "/backlog/save", csrf, origin=None, token=tok, content="x").status_code == 403
    assert f.read_text() == SEED


def test_tier_view_rejected(env):
    c, f, s = env
    csrf, tok = fields(c)
    c.app.state.settings = replace(c.app.state.settings, tier_ceiling=Tier.VIEW)
    assert post(c, "/backlog/add", csrf, token=tok, text="x").status_code == 403
    assert f.read_text() == SEED


def test_size_cap(env):
    c, f, s = env
    csrf, tok = fields(c)
    r = post(c, "/backlog/save", csrf, token=tok, content="- [ ] " + "a" * (rb.MAX_BYTES + 1))
    assert r.status_code == 413 and f.read_text() == SEED
    r = post(c, "/backlog/add", csrf, token=tok, text="a" * (rb.MAX_ITEM + 1))
    assert r.status_code == 400 and f.read_text() == SEED


def test_audit_failure_fails_closed(env, monkeypatch):
    c, f, s = env
    csrf, tok = fields(c)
    from pm.security import audit as au

    def boom(self, e):
        raise au.AuditError("x")
    monkeypatch.setattr(au.AuditLog, "write", boom)
    assert post(c, "/backlog/add", csrf, token=tok, text="x").status_code == 503
    assert f.read_text() == SEED


def test_missing_file_renders_and_add_creates(env):
    c, f, s = env
    f.unlink()
    assert c.get("/backlog").status_code == 200
    csrf, tok = fields(c)
    assert post(c, "/backlog/add", csrf, token=tok, text="first").status_code == 303
    assert "## Inbox" in f.read_text()


def p_csrf(c, name):
    html = c.get(f"/p/{name}").text
    return html, re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_project_page_section(env):
    c, f, s = env
    html, csrf = p_csrf(c, "alpha")
    assert html.index('<h2 id="todo-h">TODO</h2>') < html.index('id="backlog"')
    assert "a1" in html and "Open <span class=\"bl-seg-n\">2</span>" in html and "Done <span class=\"bl-seg-n\">1</span>" in html
    assert 'value="alpha"' in html and 'value="project"' in html
    html, _ = p_csrf(c, "empty")
    assert "No backlog yet" in html and 'name="token"' not in html.split('id="backlog"')[1].split("panic")[0]
    assert "Edit file" not in html.split('id="backlog"')[1]


def test_project_add_creates_file_and_returns_to_project(env):
    c, f, s = env
    csrf = p_csrf(c, "empty")[1]
    r = post(c, "/backlog/add", csrf, project="empty", back="project", text="first")
    assert r.status_code == 303 and r.headers["location"] == "/p/empty?added=1#backlog"
    t = (f.parent.parent / "empty" / "BACKLOG.md").read_text()
    assert "## Inbox" in t and "- [ ] first" in t
    log = audit(s)
    assert '"project":"empty"' in log and "first" not in log


def test_group_page_collapsed_sorted(env):
    c, f, s = env
    html = c.get("/backlog").text
    groups = re.findall(r'<details class="bl-group"[^>]*>', html)
    assert len(groups) == 2 and all(" open" not in g for g in groups)  # pm, alpha (not empty)
    assert html.index('data-project="alpha"') < html.index('data-project="pm"')  # 2 open vs 2: name
    assert "Expand all" in html and "Collapse all" in html
    assert '<option value="pm">' in html and 'value="empty"' not in html


def test_group_sorted_by_open_desc(env):
    c, f, s = env
    (f.parent.parent / "alpha" / "BACKLOG.md").write_text("- [ ] x\n")
    html = c.get("/backlog").text
    assert html.index('data-project="pm"') < html.index('data-project="alpha"')


def test_per_project_toggle_save(env):
    c, f, s = env
    csrf, tok = fields(c, "alpha")
    a = f.parent.parent / "alpha" / "BACKLOG.md"
    line = a.read_text().split("\n").index("- [ ] a1")
    assert post(c, "/backlog/toggle", csrf, project="alpha", line=str(line), text="a1").status_code == 303
    assert "- [x] a1" in a.read_text() and f.read_text() == SEED
    csrf, tok = fields(c, "alpha")
    assert post(c, "/backlog/save", csrf, project="alpha", token=tok, content="- [ ] z").status_code == 303
    assert a.read_text() == "- [ ] z\n" and f.read_text() == SEED
    assert "z" in c.get("/backlog?p=alpha&edit=1").text


def test_unknown_project_404(env):
    c, f, s = env
    csrf, tok = fields(c)
    assert c.get("/backlog?p=nope&edit=1").status_code == 404
    for bad in ("nope", "../pm", "/etc", ""):
        for url, kw in (("/backlog/add", {"text": "x"}), ("/backlog/toggle", {"line": "1", "text": "x"}),
                        ("/backlog/save", {"content": "x", "token": tok})):
            assert post(c, url, csrf, project=bad, **kw).status_code == 404
    assert f.read_text() == SEED


def test_lookup_unknown_is_domain_error(env):
    c, f, s = env
    with pytest.raises(rb.Bad) as e:
        rb.lookup(s, "nope")
    assert e.value.status == 404
    assert rb.lookup(s, "pm") == f


def test_save_keeps_file_mode(env):
    c, f, s = env
    f.chmod(0o640)
    csrf, tok = fields(c)
    assert post(c, "/backlog/save", csrf, token=tok, content="- [ ] m").status_code == 303
    assert f.read_text() == "- [ ] m\n" and (f.stat().st_mode & 0o777) == 0o640


def test_symlink_refused(env, tmp_path):
    c, f, s = env
    outside = tmp_path / "outside.md"
    outside.write_text("- [ ] secret\n")
    link = f.parent.parent / "empty" / "BACKLOG.md"
    link.symlink_to(outside)
    csrf = p_csrf(c, "empty")[1]
    assert "secret" not in c.get("/backlog").text
    assert "secret" not in c.get("/p/empty").text
    for url, kw in (("/backlog/add", {"text": "x"}), ("/backlog/save", {"content": "x", "token": "0" * 16}),
                    ("/backlog/toggle", {"line": "0", "text": "x"})):
        assert post(c, url, csrf, project="empty", **kw).status_code == 400
    assert outside.read_text() == "- [ ] secret\n" and link.is_symlink()
    assert c.get("/backlog?p=empty&edit=1").status_code == 400


def test_symlinked_project_dir_target_checked(env, tmp_path):
    from pm.projects.backlog import resolve_path
    d = tmp_path / "dev" / "alpha"
    assert resolve_path(str(d)) == d / "BACKLOG.md"


def test_added_item_shown_even_past_open_limit(env):
    """Bug: a quick-add on a project page 'did not show up'. add_item appends to Inbox; with
    >30 open items in earlier sections the new item fell past the page's 30-item limit."""
    c, f, s = env
    many = "## Next\n\n" + "".join(f"- [ ] n{i}\n" for i in range(40)) + "\n## Inbox\n\n- [ ] old\n"
    f.write_text(many)
    html, csrf = p_csrf(c, "pm")
    r = post(c, "/backlog/add", csrf, back="project", text="brand-new-item")
    assert r.status_code == 303 and r.headers["location"] == "/p/pm?added=1#backlog"
    page = c.get("/p/pm?added=1")
    assert "brand-new-item" in page.text and page.headers["cache-control"] == "no-store"
    sec = page.text.split('id="backlog"')[1]
    assert sec.index("old") < sec.index("brand-new-item") < sec.index("n0")
    assert "data-inbox" in sec and "/static/js/backlog.js" in sec


def test_edit_cancel_and_save_return_to_origin(env):
    c, f, s = env
    # Came from the project page 'Edit file' link
    html, csrf = p_csrf(c, "pm")
    assert 'data-bl-file="/backlog?p=pm&amp;edit=1&amp;embed=1"' in html and "Open full backlog" not in html
    ed = c.get("/backlog?p=pm&edit=1&back=project").text
    assert ed.count('href="/p/pm#backlog"') == 2 and 'name="back" value="project"' in ed
    # Came from /backlog (no back): back to the project's group
    ed = c.get("/backlog?p=pm&edit=1").text
    assert ed.count('href="/backlog#bl-pm"') == 2 and 'name="back"' not in ed
    # allow-list: anything but 'project' is ignored, no open redirect
    ed = c.get("/backlog?p=pm&edit=1&back=https://evil.example").text
    assert "evil" not in ed and ed.count('href="/backlog#bl-pm"') == 2
    csrf, tok = fields(c)
    r = post(c, "/backlog/save", csrf, token=tok, back="project", content="- [ ] z")
    assert r.status_code == 303 and r.headers["location"] == "/p/pm#backlog"
    r = post(c, "/backlog/save", csrf, token=fields(c)[1], back="//evil.example", content="- [ ] y")
    assert r.status_code == 303 and r.headers["location"] == "/backlog#bl-pm"
    # conflict re-render keeps back=project and the Cancel target
    r = post(c, "/backlog/save", csrf, token="0" * 16, back="project", content="mine")
    assert r.status_code == 409
    assert 'name="back" value="project"' in r.text and 'href="/p/pm#backlog"' in r.text


def test_edit_item(env):
    c, f, s = env
    csrf, tok = fields(c)
    line = f.read_text().split("\n").index("- [ ] one (2026-01-01)")
    t = "one (2026-01-01)"
    r = post(c, "/backlog/edit", csrf, line=str(line), text=t, new_text="  uno   (2026-01-01) \n")
    assert r.status_code == 303
    assert "- [ ] uno (2026-01-01)" in f.read_text() and "one (" not in f.read_text()
    assert "backlog_edit" in audit(s) and "uno" not in audit(s)
    # mark and indent kept for a done item; stale text -> 409; empty / missing -> 400
    post(c, "/backlog/toggle", csrf, line=str(line), text="uno (2026-01-01)")
    r = post(c, "/backlog/edit", csrf, line=str(line), text="uno (2026-01-01)", new_text="x")
    assert r.status_code == 303 and "- [x] x\n" in f.read_text()
    assert post(c, "/backlog/edit", csrf, line=str(line), text="gone", new_text="y").status_code == 409
    assert post(c, "/backlog/edit", csrf, line=str(line), text="x", new_text="  ").status_code == 400
    assert post(c, "/backlog/edit", csrf, line=str(line), text="x").status_code == 400
    assert post(c, "/backlog/edit", csrf, origin="https://evil.example", line="1", text="x", new_text="z").status_code == 403
    assert post(c, "/backlog/edit", "bad", line="1", text="x", new_text="z").status_code == 403


def test_edit_button_rendered(env):
    c, f, s = env
    html = c.get("/backlog").text
    assert 'data-bl-edit' in html and 'aria-label="Edit item"' in html


def test_embedded_editor_save_cancel_and_done(env):
    """Project page Backlog tab: the editor is framed (?embed=1), Cancel is a button, save lands on a 'done' page."""
    c, f, s = env
    r = c.get("/backlog?p=pm&edit=1&embed=1")
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"] and 'class="v5-embed"' in r.text
    assert 'name="back" value="embed"' in r.text and "data-bl-close" in r.text and 'href="/p/pm#backlog"' not in r.text
    csrf, tok = fields(c)
    r = post(c, "/backlog/save", csrf, token=tok, back="embed", content="- [ ] z")
    assert r.status_code == 303 and r.headers["location"] == "/backlog?p=pm&embed=1&done=1"
    d = c.get(r.headers["location"]).text
    assert "data-bl-done" in d and "/static/js/backlog.js" in d and 'id="bl-edit"' not in d
    assert "data-bl-done" not in c.get("/backlog?p=pm&embed=0&done=1").text


def test_project_summary_done_items(env):
    _, _, s = env
    out = rb.project_summary(s, "alpha", limit=1)
    assert [b["text"] for b in out["items"]] == ["a1"]
    assert [b["text"] for b in out["done_items"]] == ["a3"]
    assert out["done_more"] == 0 and out["more"] == 1


def test_tracker_project_beside_file_mode(env):
    """/backlog lists a tracker project from pm.db (no Edit file, #num links); file-mode projects are unchanged."""
    from pm.projects.tracker import mdimport, store

    c, f, s = env
    mdimport.import_project(s, "alpha", f.parent.parent / "alpha")
    html = c.get("/backlog").text
    alpha = html.split('data-project="alpha"')[1].split("</details>")[0]
    pm_group = html.split('data-project="pm"')[1].split("</details>")[0]
    assert 'href="/p/alpha#issue=1"' in alpha and "Edit file" not in alpha
    assert "Edit file" in pm_group and "#issue=" not in pm_group
    csrf, tok = fields(c)
    assert post(c, "/backlog/toggle", csrf, project="alpha", line="1", text="a1").status_code == 303
    assert store.get_item(s, "alpha", "backlog", 1).done
    assert post(c, "/backlog/save", csrf, project="alpha", token=tok, content="- [ ] z").status_code == 409
    assert c.get("/p/alpha").text.count('data-pj-pane="pj-p-issues"') == 1
