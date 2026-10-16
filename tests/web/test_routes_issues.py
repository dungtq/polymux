"""Issues routes (web/routes/issues.py) and the tracker branches of /backlog/* and the TODO toggle.

Project ``trk`` is imported into the tracker, ``plain`` stays in file mode. Everything lives under tmp_path
(dev root, state, pm.db); git is isolated from the user's config; tmux is the fake stub."""

import json
import re

import pytest
from helpers import ORIGIN, make_client, make_settings, pane_line, png_bytes, snap_with

from pm.core.models import Agent, AgentKind, AgentState, Tier
from pm.core.uploads import store_upload
from pm.infra import snapshot as services
from pm.projects.tracker import issues, mdimport, store

BACKLOG = "# trk backlog\n\n## Inbox\n\n- [ ] first (2026-01-01)\n- [x] old done\n\n## Next\n\n- [ ] second\n"
TODO = "# TODO\n\n## P1\n\n- [ ] task a\n- [ ] task b\n"
XRW = {"Origin": ORIGIN, "X-Requested-With": "pm"}
SECRET = "very-secret-body-text"


@pytest.fixture
def env(fake_tmux, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    dev = tmp_path / "dev"
    for name in ("trk", "plain"):
        (dev / name).mkdir(parents=True)
        (dev / name / "BACKLOG.md").write_text(BACKLOG)
        (dev / name / "TODO.md").write_text(TODO)
    s = make_settings(fake_tmux, tmp_path, state="st", tier_ceiling=Tier.SAFE, disable_poll=True)
    mdimport.import_project(s, "trk", dev / "trk")
    agents = [Agent(id=f"claude-{n}", kind=AgentKind.CLAUDE, pane_id=f"%{n}", session_id="$1", pid=4242,
                    cwd=str(dev / p), project=p, state=AgentState.IDLE, state_since=1.0)
              for n, p in ((12, "trk"), (13, "plain"))]
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", str(dev / "trk")))
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes, **kw: agents)
    c = make_client(s, static=True, base_url=ORIGIN, logged_in=True)
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/p/trk").text).group(1)
    return c, s, csrf, dev


def post(c, url, csrf, *, xrw=True, **data):
    return c.post(url, data={"csrf": csrf, **data}, headers=XRW if xrw else {"Origin": ORIGIN},
                  follow_redirects=False)


def audit(s):
    return s.audit_file.read_text() if s.audit_file.exists() else ""


def upload(s) -> str:
    return str(store_upload(s, png_bytes(), "png"))


# ------------------------------------------------------------------ project page + fragments
def test_project_page_issues_tab_for_tracker_backlog_for_file_mode(env):
    c, s, csrf, dev = env
    html = c.get("/p/trk").text
    assert 'data-pj-pane="pj-p-issues"' in html and 'Issues <span class="pj-n">2</span>' in html
    assert 'id="pj-p-backlog"' not in html and "/static/js/issues.js" in html
    sec = html.split('id="pj-p-issues"')[1].split("</section>")[0]
    assert "Edit file" not in sec and 'data-iss-row="1"' in sec and 'action="/p/trk/issues"' in sec
    # no element id equals a tab hash (#issues, #todo): the browser must not scroll the pane to a heading
    assert 'id="issues"' not in html and 'id="todo"' not in html and '<h2 id="iss-h">' in sec
    assert '<div class="pj-pane-head"><h2 id="todo-h">TODO</h2></div>' in html
    plain = c.get("/p/plain").text
    assert 'data-pj-pane="pj-p-backlog"' in plain and "pj-p-issues" not in plain and "Edit file" in plain


def test_tab_fragment_and_redirect(env):
    c, s, csrf, dev = env
    r = c.get("/p/trk/issues", headers=XRW)
    assert r.status_code == 200 and 'data-iss-row="3"' in r.text and "<html" not in r.text
    assert r.headers["cache-control"] == "no-store"
    r = c.get("/p/trk/issues", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/trk#issues"
    assert c.get("/p/plain/issues", headers=XRW).status_code == 409
    assert c.get("/p/nope/issues", headers=XRW).status_code == 404


def test_detail_fragment_send_links_and_redirect(env):
    c, s, csrf, dev = env
    r = c.get("/p/trk/issues/1", headers=XRW)
    assert r.status_code == 200 and "first (2026-01-01)" in r.text and "#1" in r.text
    # the draft and the heading drop the " (YYYY-MM-DD)" suffix; the edit field keeps the stored title
    assert 'href="/a/claude-12#draft=Fix%20issue%20%231%3A%20first%0A%0ADetails%3A%20pmctl%20issue%20show%201"' in r.text
    assert '<h3 class="iss-dtitle" id="iss-dt">first</h3>' in r.text and 'value="first (2026-01-01)"' in r.text
    assert "claude-13" not in r.text  # other project's agent is not offered
    assert 'data-confirm="Delete issue #1?' in r.text and 'action="/p/trk/issues/1/done"' in r.text
    r = c.get("/p/trk/issues/1", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/trk#issue=1"
    assert c.get("/p/trk/issues/99", headers=XRW).status_code == 404
    store.set_done(s, "trk", "backlog", 1, True)
    assert 'action="/p/trk/issues/1/reopen"' in c.get("/p/trk/issues/1", headers=XRW).text


def test_detail_without_agents_links_launch(env, monkeypatch):
    c, s, csrf, dev = env
    snap_with(monkeypatch, agents=())
    r = c.get("/p/trk/issues/3", headers=XRW)
    assert r.status_code == 200 and "#draft=" not in r.text
    assert 'href="/p/trk/launch"' in r.text and "No agent runs in trk" in r.text


# ------------------------------------------------------------------ add
def test_add_title_only_json_and_form(env):
    c, s, csrf, dev = env
    r = post(c, "/p/trk/issues", csrf, title="  new   thing ")
    assert r.status_code == 200 and r.json() == {"ok": True, "num": 4, "project": "trk", "url": "/p/trk#issue=4"}
    assert re.search(r"- \[ \] new thing \(\d{4}-\d\d-\d\d\)\n", (dev / "trk/BACKLOG.md").read_text())
    r = post(c, "/p/trk/issues", csrf, xrw=False, title="via form")
    assert r.status_code == 303 and r.headers["location"] == "/p/trk#issue=5"
    log = audit(s)
    assert '"action":"issue_add"' in log and '"num":4' in log and "new thing" not in log and "via form" not in log


def test_add_with_body_and_images(env):
    c, s, csrf, dev = env
    path = upload(s)
    r = post(c, "/p/trk/issues", csrf, title="with pic", body=SECRET + "\n\nmore", images=path + "\n")
    assert r.status_code == 200 and r.json()["ok"]
    num = r.json()["num"]
    d = issues.detail(s, "trk", num)
    assert d["item"].body_md.startswith(SECRET) and len(d["images"]) == 1
    html = c.get(f"/p/trk/issues/{num}", headers=XRW).text
    url = d["images"][0]["url"]
    assert f'<a class="chat-img" href="{url}"' in html and SECRET in html
    row = c.get("/p/trk/issues", headers=XRW).text.split(f'data-iss-row="{num}"')[1].split("</div>")[0]
    assert 'title="1 image"' in row
    assert '<span class="iss-t" title="with pic (' in row and '">with pic</span>' in row  # date suffix: title only
    assert SECRET not in audit(s) and '"images":1' in audit(s)


@pytest.mark.parametrize("data,status", [
    ({"title": ""}, 400),
    ({"title": "x" * 501}, 400),
    ({"title": "ok", "images": "/etc/passwd"}, 400),
])
def test_add_refused(env, data, status):
    c, s, csrf, dev = env
    r = post(c, "/p/trk/issues", csrf, **data)
    assert r.status_code == status and r.json()["ok"] is False and r.json()["error"]
    assert len(store.list_items(s, "trk", "backlog")) == 3


def test_add_guards(env):
    c, s, csrf, dev = env
    assert post(c, "/p/plain/issues", csrf, title="x").status_code == 409
    assert "- [ ] x" not in (dev / "plain/BACKLOG.md").read_text()
    assert post(c, "/p/nope/issues", csrf, title="x").status_code == 404
    assert c.post("/p/trk/issues", data={"title": "x"}, headers=XRW).status_code == 403  # no CSRF
    r = c.post("/p/trk/issues", data={"csrf": csrf, "title": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert len(store.list_items(s, "trk", "backlog")) == 3


# ------------------------------------------------------------------ done / reopen / edit / note / delete
def test_done_reopen_edit_note_delete(env):
    c, s, csrf, dev = env
    assert post(c, "/p/trk/issues/1/done", csrf).json()["ok"]
    assert store.get_item(s, "trk", "backlog", 1).done and "- [x] first" in (dev / "trk/BACKLOG.md").read_text()
    assert post(c, "/p/trk/issues/1/reopen", csrf).json()["num"] == 1
    assert not store.get_item(s, "trk", "backlog", 1).done
    r = post(c, "/p/trk/issues/1/edit", csrf, title="renamed", body=SECRET)
    assert r.json()["ok"] and store.get_item(s, "trk", "backlog", 1).title == "renamed"
    assert post(c, "/p/trk/issues/1/edit", csrf, title=" ", body="").status_code == 400
    assert post(c, "/p/trk/issues/1/note", csrf, body="a note " + SECRET).json()["ok"]
    assert issues.detail(s, "trk", 1)["notes"][0]["body_md"].startswith("a note")
    assert post(c, "/p/trk/issues/1/note", csrf, body="  ").status_code == 400
    r = post(c, "/p/trk/issues/1/delete", csrf, xrw=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/trk#issues"
    with pytest.raises(Exception):
        store.get_item(s, "trk", "backlog", 1)
    assert post(c, "/p/trk/issues/1/done", csrf).status_code == 404
    log = audit(s)
    for a in ("issue_done", "issue_reopen", "issue_edit", "issue_note", "issue_delete"):
        assert f'"action":"{a}"' in log
    assert SECRET not in log and "renamed" not in log


def test_mutation_audit_failure_fails_closed(env, monkeypatch):
    c, s, csrf, dev = env
    from pm.security import audit as audit_mod

    def boom(self, entry):
        raise audit_mod.AuditError("disk full")
    monkeypatch.setattr(audit_mod.AuditLog, "write", boom)
    assert post(c, "/p/trk/issues/1/done", csrf).status_code == 503
    assert not store.get_item(s, "trk", "backlog", 1).done


def _fail_ok_lines(monkeypatch):
    """The attempt line still writes; every "ok" result line fails (disk full after the guard)."""
    from pm.security import audit as audit_mod
    real = audit_mod.AuditLog.write

    def write(self, entry):
        if entry.result == "ok":
            raise audit_mod.AuditError("disk full")
        real(self, entry)
    monkeypatch.setattr(audit_mod.AuditLog, "write", write)


def test_ok_audit_written_in_the_transaction(env, monkeypatch):
    """The store writes the route's "ok" line before its commit: a failed write rolls every change back."""
    c, s, csrf, dev = env
    before = (dev / "trk/BACKLOG.md").read_text()
    _fail_ok_lines(monkeypatch)
    assert post(c, "/p/trk/issues/1/done", csrf).status_code == 503
    assert post(c, "/p/trk/issues", csrf, title="lost").status_code == 503
    assert post(c, "/p/trk/issues/1/note", csrf, body="lost").status_code == 503
    assert save(c, csrf, text="lost too").status_code == 503
    assert post(c, "/backlog/toggle", csrf, xrw=False, project="trk", line="1", text="first").status_code == 503
    assert post(c, "/p/trk/todo/toggle", csrf, xrw=False, line="1", text="task a").status_code == 503
    assert not store.get_item(s, "trk", "backlog", 1).done and len(store.list_items(s, "trk", "backlog")) == 3
    assert not issues.detail(s, "trk", 1)["notes"] and not store.get_item(s, "trk", "todo", 1).done
    assert (dev / "trk/BACKLOG.md").read_text() == before


def test_one_ok_line_per_action(env):
    c, s, csrf, dev = env
    assert post(c, "/p/trk/issues/1/done", csrf).json()["ok"]
    assert post(c, "/p/trk/issues", csrf, title="one", images=upload(s)).json()["num"] == 4
    assert post(c, "/backlog/toggle", csrf, xrw=False, project="trk", line="3", text="second").status_code == 303
    assert post(c, "/p/trk/todo/toggle", csrf, xrw=False, line="1", text="task a").status_code == 303
    assert save(c, csrf, text="from composer").json()["num"] == 5
    lines = [json.loads(ln) for ln in audit(s).splitlines()]
    ok = [e for e in lines if e["result"] == "ok" and e["target"] in ("issues", "backlog", "todo")]
    assert [(e["action"], e["detail"]) for e in ok] == [
        ("issue_done", {"project": "trk", "num": 1}),
        ("issue_add", {"project": "trk", "num": 4, "images": 1}),
        ("backlog_toggle", {"project": "trk", "num": 3}),
        ("todo_toggle", {"project": "trk", "num": 1, "done": 1}),
        ("issue_add", {"project": "trk", "num": 5, "agent": "claude-12", "images": 0}),
    ]


# ------------------------------------------------------------------ images
def test_tracker_image_route(env, tmp_path):
    c, s, csrf, dev = env
    num = post(c, "/p/trk/issues", csrf, title="pic", images=upload(s)).json()["num"]
    name = issues.detail(s, "trk", num)["images"][0]["name"]
    r = c.get(f"/tracker/img/{name}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == png_bytes()
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    for bad in ("x.png", "../st/pm.db", "0" * 24 + ".png", "0" * 24 + ".svg"):
        assert c.get(f"/tracker/img/{bad}").status_code == 404
    link = s.tracker_dir / ("1" * 24 + ".png")
    link.symlink_to(tmp_path / "dev" / "trk" / "BACKLOG.md")
    assert c.get(f"/tracker/img/{link.name}").status_code == 404
    junk = s.tracker_dir / ("2" * 24 + ".png")
    junk.write_bytes(b"not an image")
    assert c.get(f"/tracker/img/{junk.name}").status_code == 404


def test_uploads_route_still_serves(env):
    c, s, csrf, dev = env
    name = upload(s).rsplit("/", 1)[1]
    r = c.get(f"/uploads/{name}")
    assert r.status_code == 200 and r.headers["content-security-policy"] == "default-src 'none'; sandbox"


# ------------------------------------------------------------------ composer "Save as issue"
def save(c, csrf, agent="claude-12", header=True, **data):
    h = {"Origin": ORIGIN, **({"X-CSRF-Token": csrf} if header else {})}
    return c.post(f"/a/{agent}/issue", data=data, headers=h, follow_redirects=False)


def test_save_as_issue(env):
    c, s, csrf, dev = env
    r = save(c, csrf, text="\n  Title line \n\n" + SECRET + "\nline 2\n", images=upload(s))
    assert r.status_code == 200
    j = r.json()
    assert j == {"ok": True, "num": 4, "project": "trk", "url": "/p/trk#issue=4"}
    it = store.get_item(s, "trk", "backlog", 4)
    assert it.title.startswith("Title line (") and it.body_md == SECRET + "\nline 2" and it.n_img == 1
    log = audit(s)
    assert '"action":"issue_add"' in log and '"agent":"claude-12"' in log and SECRET not in log
    assert "Title line" not in log


def test_save_as_issue_with_body_gets_ref_in_backlog(env):
    """An issue saved from the composer WITH a body (no image) renders `` [#N]`` at once."""
    c, s, csrf, dev = env
    assert save(c, csrf, text="Body only\nsome details").json()["num"] == 4
    assert re.search(r"- \[ \] Body only \(\d{4}-\d\d-\d\d\) \[#4\]\n", (dev / "trk/BACKLOG.md").read_text())
    assert save(c, csrf, text="Title only").json()["num"] == 5
    assert re.search(r"- \[ \] Title only \(\d{4}-\d\d-\d\d\)\n", (dev / "trk/BACKLOG.md").read_text())


@pytest.mark.parametrize("exc,status", [("sqlite", 503), ("os", 502)])
def test_save_as_issue_db_and_io_errors_are_audited_json(env, monkeypatch, exc, status):
    c, s, csrf, dev = env
    import sqlite3

    def fail(*a, **k):
        raise sqlite3.OperationalError("database is locked") if exc == "sqlite" else OSError("disk")
    monkeypatch.setattr(issues, "create_issue", fail)
    r = save(c, csrf, text="t")
    assert r.status_code == status and r.json()["ok"] is False and r.json()["error"]
    last = json.loads(audit(s).splitlines()[-1])
    assert last["action"] == "issue_add" and last["result"] == "error"
    assert last["detail"]["project"] == "trk" and last["detail"]["agent"] == "claude-12"


def test_save_as_issue_refused(env):
    c, s, csrf, dev = env
    r = save(c, csrf, agent="claude-13", text="x")
    assert r.status_code == 409 and r.json() == {"ok": False, "error": "plain does not use the issue tracker"}
    r = save(c, csrf, text=" \n  \n")
    assert r.status_code == 400 and r.json()["ok"] is False
    r = save(c, csrf, text="t", images="/tmp/x.png")
    assert r.status_code == 400 and "upload" in r.json()["error"]
    assert save(c, csrf, header=False, text="t").status_code == 403
    assert save(c, csrf, agent="claude-99", text="t").status_code == 404
    assert len(store.list_items(s, "trk", "backlog")) == 3
    assert json.loads(save(c, csrf, agent="claude-99", text="t").text)["ok"] is False


# ------------------------------------------------------------------ /backlog and TODO in tracker mode
def test_backlog_page_tracker_mode(env):
    c, s, csrf, dev = env
    html = c.get("/backlog").text
    trk = html.split('data-project="trk"')[1].split("</details>")[0]
    plain = html.split('data-project="plain"')[1].split("</details>")[0]
    assert "Edit file" not in trk and 'href="/p/trk#issues"' in trk and 'href="/p/trk#issue=1"' in trk
    assert "Edit file" in plain and "#issue=" not in plain
    r = c.get("/backlog?p=trk&edit=1")
    assert r.status_code == 409 and "uses the issue tracker" in r.text and 'id="bl-edit"' not in r.text


def test_backlog_routes_go_to_store(env):
    c, s, csrf, dev = env
    f = dev / "trk/BACKLOG.md"
    r = post(c, "/backlog/add", csrf, xrw=False, project="trk", text="from backlog page")
    assert r.status_code == 303 and r.headers["location"] == "/backlog#bl-trk"
    assert store.list_items(s, "trk", "backlog")[-1].title.startswith("from backlog page") or \
        "from backlog page" in f.read_text()
    r = post(c, "/backlog/toggle", csrf, xrw=False, project="trk", line="3", text="second")
    assert r.status_code == 303 and store.get_item(s, "trk", "backlog", 3).done
    r = post(c, "/backlog/edit", csrf, xrw=False, project="trk", line="3", text="second", new_text="second v2")
    assert r.status_code == 303 and store.get_item(s, "trk", "backlog", 3).title == "second v2"
    assert "- [x] second v2" in f.read_text()
    r = post(c, "/backlog/save", csrf, xrw=False, project="trk", token="0" * 16, content="- [ ] wiped")
    assert r.status_code == 409 and "wiped" not in f.read_text()
    assert post(c, "/backlog/toggle", csrf, xrw=False, project="trk", line="x", text="y").status_code == 400
    r = post(c, "/backlog/add", csrf, xrw=False, project="trk", text="p", back="project")
    assert r.headers["location"] == "/p/trk?added=1#backlog"
    log = audit(s)
    assert '"action":"backlog_add"' in log and "from backlog page" not in log and "second v2" not in log


def test_todo_toggle_tracker_mode(env):
    c, s, csrf, dev = env
    html = c.get("/p/trk").text
    sec = html.split('id="todo-card"')[1]
    assert 'name="line" value="1"' in sec and "task a" in sec
    r = post(c, "/p/trk/todo/toggle", csrf, xrw=False, line="1", text="task a")
    assert r.status_code == 303 and r.headers["location"] == "/p/trk#todo"
    assert store.get_item(s, "trk", "todo", 1).done and "- [x] task a" in (dev / "trk/TODO.md").read_text()
    assert post(c, "/p/trk/todo/toggle", csrf, xrw=False, line="1", text="task a").status_code == 303
    assert not store.get_item(s, "trk", "todo", 1).done
    assert post(c, "/p/trk/todo/toggle", csrf, xrw=False, line="99", text="x").status_code == 404
    assert '"num":1' in audit(s)


def test_save_as_issue_unknown_agent_is_audited(env):
    c, s, csrf, dev = env
    r = save(c, csrf, agent="claude-nope99", text="Title")
    assert r.status_code == 404 and r.json() == {"ok": False, "error": "unknown agent"}
    log = audit(s)
    assert '"action":"issue_add"' in log and "claude-nope99" in log and '"result":"denied"' in log


# ------------------------------------------------------------------ move / dispatch / Backlog page lists
def _second_tracker(dev, s):
    (dev / "trk2").mkdir()
    (dev / "trk2" / "BACKLOG.md").write_text("## Inbox\n")
    mdimport.import_project(s, "trk2", dev / "trk2", files=("backlog",))


def test_move_route(env):
    c, s, csrf, dev = env
    _second_tracker(dev, s)
    issues.add_note(s, "trk", 3, "note " + SECRET)
    r = post(c, "/p/trk/issues/3/move", csrf, to="trk2")
    assert r.status_code == 200 and r.json() == {"ok": True, "num": 1, "project": "trk2", "url": "/p/trk2#issue=1"}
    assert store.get_item(s, "trk2", "backlog", 1).title == "second" and "second" not in (
        dev / "trk/BACKLOG.md").read_text()
    assert [n["body_md"] for n in issues.detail(s, "trk2", 1)["notes"]] == ["note " + SECRET, "Moved from trk #3."]
    r = post(c, "/p/trk2/issues/1/move", csrf, xrw=False, to="trk")
    assert r.status_code == 303 and r.headers["location"] == "/p/trk#issue=4"
    log = audit(s)
    assert '"action":"issue_move"' in log and '"to":"trk2"' in log and '"new_num":1' in log and SECRET not in log
    assert post(c, "/p/trk/issues/4/move", csrf, to="plain").status_code == 409  # target not in the tracker
    assert post(c, "/p/trk/issues/4/move", csrf, to="nope").status_code == 404
    assert post(c, "/p/trk/issues/4/move", csrf, to="trk").status_code == 400  # same project
    assert post(c, "/p/trk/issues/99/move", csrf, to="trk2").status_code == 404
    assert post(c, "/p/plain/issues/1/move", csrf, to="trk").status_code == 409
    assert c.post("/p/trk/issues/4/move", data={"to": "trk2"}, headers=XRW).status_code == 403  # no CSRF
    assert store.get_item(s, "trk", "backlog", 4).title == "second"


def test_dispatch_route_launches_marks_and_refuses_double(env, monkeypatch):
    import dataclasses

    from pm.agents import launch as launch_mod
    from pm.core import prefs
    from pm.web import issuectx
    c, s, csrf, dev = env
    assert post(c, "/p/trk/issues/1/dispatch", csrf, kind="claude").status_code == 403  # tier ceiling SAFE
    s2 = dataclasses.replace(s, tier_ceiling=Tier.MESSAGE)
    from helpers import make_client
    c2 = make_client(s2, static=True, base_url=ORIGIN, logged_in=True)
    tok = re.search(r'name="csrf" value="([^"]+)"', c2.get("/p/trk").text).group(1)
    launched = []

    def fake_launch(settings, launch, project):
        launched.append(launch)
        return launch_mod.LaunchResult("$1", "@9", "agent-claude", project.path, pane_id="%91")
    monkeypatch.setattr(launch_mod, "launch_agent", fake_launch)
    store.edit_item(s, "trk", "backlog", 1, body=SECRET)
    hx = {"Origin": ORIGIN, "HX-Request": "true"}
    r = c2.post("/p/trk/issues/1/dispatch", headers=hx, data={"csrf": tok, "kind": "claude"})
    assert r.status_code == 200 and "Launched claude" in r.text
    (lc,) = launched
    assert lc.project == "trk" and "<<<ISSUE\n#1 first\n\n" + SECRET in lc.prompt
    row = prefs.dispatched(s.prefs_file, "trk")[issuectx.issue_key(1)]
    assert row["agent"] == "claude-91" and row["pane"] == "%91" and row["title"] == "#1 first"
    r = c2.post("/p/trk/issues/1/dispatch", headers=hx, data={"csrf": tok, "kind": "claude"})
    assert r.status_code == 409 and "already dispatched to claude-91" in r.text and len(launched) == 1
    assert c2.post("/p/trk/issues/99/dispatch", headers=hx, data={"csrf": tok}).status_code == 404
    assert c2.post("/p/trk/issues/2/dispatch", headers=hx, data={"csrf": tok, "kind": "nope"}).status_code == 400
    assert c2.post("/p/nope/issues/2/dispatch", headers=hx, data={"csrf": tok}).status_code == 404
    log = audit(s)
    assert '"action":"agent_dispatch_issue"' in log and "dispatch refused: issue already in progress" in log
    assert SECRET not in log and len(launched) == 1


def test_backlog_page_lists_for_themes(env, monkeypatch):
    from pm.core import prefs
    from pm.web import issuectx
    from pm.web.routes import backlog as backlog_routes
    c, s, csrf, dev = env
    seen = {}
    real = backlog_routes.html

    def spy(request, name, **ctx):
        seen.update(ctx)
        return real(request, name, **ctx)
    monkeypatch.setattr(backlog_routes, "html", spy)
    classic = c.get("/backlog").text
    il, tl = seen["issue_list"], seen["todo_list"]
    assert il["counts"] == {"open": 2, "done": 1, "all": 3} and [r["num"] for r in il["rows"]] == [3, 1]  # newest first
    assert all(r["agent"] is None for r in il["rows"]) and il["projects"] == ["trk"]
    assert {g["name"] for g in tl["groups"]} == {"trk", "plain"} and tl["counts"]["open"] == 4
    assert "issue_list" not in classic  # classic ignores the extra keys
    prefs.mark_dispatched(s.prefs_file, "trk", issuectx.issue_key(3), "claude-12", "%12", __import__("time").time())
    c.get("/backlog?status=all&q=%233&project=trk&limit=5&offset=0&todo=done")
    il = seen["issue_list"]
    assert [r["num"] for r in il["rows"]] == [3] and il["rows"][0]["agent"] == {"id": "claude-12", "state": "idle"}
    assert il["status"] == "all" and il["limit"] == 5 and seen["todo_list"]["status"] == "done"
    assert c.get("/backlog?limit=x&offset=-3&status=<b>").status_code == 200
    assert seen["issue_list"]["limit"] == 50 and seen["issue_list"]["status"] == "open"
