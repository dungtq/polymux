import os
import re

import pytest
from fastapi.testclient import TestClient
from helpers import ORIGIN, make_client, make_settings

from pm.core.models import Tier
from pm.projects import plans

ORIGIN = "https://pm.example.test"


@pytest.fixture
def env(fake_tmux, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    dev = tmp_path / "dev"
    (dev / "pm").mkdir(parents=True)
    s = make_settings(fake_tmux, tmp_path, state="st", tier_ceiling=Tier.SAFE, disable_poll=True)
    c = make_client(s, static=True, base_url=ORIGIN, logged_in=True)
    html = c.get("/p/pm").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', html).group(1)
    return c, dev / "pm", s, csrf


def post(c, csrf, path, origin=ORIGIN, **data):
    h = {"Origin": origin} if origin else {}
    return c.post(path, data={"csrf": csrf, **data}, headers=h, follow_redirects=False)


def audit_lines(s):
    return s.audit_file.read_text().splitlines() if s.audit_file.exists() else []


# ------------------------------------------------------------------ plans.py
def test_stem_validation():
    assert plans.stem_of("a-b_c.1") == "a-b_c.1"
    assert plans.stem_of("x.md") == "x"
    for bad in ["", ".hidden", "a/b", "../x", "a..b", "a b", "-x", "a\x00", "a.", "é"]:
        with pytest.raises(plans.PlanError):
            plans.stem_of(bad)
    with pytest.raises(plans.PlanError):
        plans.stem_of("x" * 65, plans.NEW_MAXLEN)
    assert plans.stem_of("x" * 90)  # existing agent files may have longer names


def test_crud_roundtrip(env):
    c, proj, s, _ = env
    assert plans.list_plans(str(proj)) == []
    plans.create_plan(s, str(proj), "one", "# One\n")
    assert (proj / "plans" / "one.md").read_text() == "# One\n"
    with pytest.raises(plans.PlanError) as e:
        plans.create_plan(s, str(proj), "one", "x")
    assert e.value.status == 409
    text, tok, _ = plans.read_plan(str(proj), "one")
    assert plans.save_plan(s, str(proj), "one", "# Uno\n", tok) == (6, 6)
    with pytest.raises(plans.PlanError) as e:
        plans.save_plan(s, str(proj), "one", "late", tok)  # stale token
    assert e.value.status == 409 and e.value.current == b"# Uno\n"
    assert plans.rename_plan(s, str(proj), "one", "two") == "two"
    assert [p.name for p in plans.list_plans(str(proj))] == ["two"]
    plans.create_plan(s, str(proj), "one", "")
    with pytest.raises(plans.PlanError) as e:
        plans.rename_plan(s, str(proj), "two", "one")
    assert e.value.status == 409
    plans.delete_plan(s, str(proj), "two")
    with pytest.raises(plans.PlanError) as e:
        plans.read_plan(str(proj), "two")
    assert e.value.status == 404
    assert not [f for f in os.listdir(proj / "plans") if f.startswith(".")]
    assert (os.stat(proj / "plans" / "one.md").st_mode & 0o777) == 0o644


def test_size_cap_and_nul(env):
    _, proj, s, _ = env
    with pytest.raises(plans.PlanError) as e:
        plans.create_plan(s, str(proj), "big", "x" * (plans.MAX_BYTES + 1))
    assert e.value.status == 413 and not (proj / "plans" / "big.md").exists()
    with pytest.raises(plans.PlanError):
        plans.create_plan(s, str(proj), "nul", "a\x00b")


def test_symlinks_refused(env, tmp_path):
    _, proj, s, _ = env
    outside = tmp_path / "secret.md"
    outside.write_text("secret")
    plans.create_plan(s, str(proj), "ok", "fine")
    os.symlink(outside, proj / "plans" / "link.md")
    assert [p.name for p in plans.list_plans(str(proj))] == ["ok"]
    for fn in (lambda: plans.read_plan(str(proj), "link"),
               lambda: plans.delete_plan(s, str(proj), "link"),
               lambda: plans.rename_plan(s, str(proj), "link", "new")):
        with pytest.raises(plans.PlanError):
            fn()
    assert outside.read_text() == "secret"
    # symlinked plans dir
    other = tmp_path / "other"
    other.mkdir()
    (proj / "plans2").mkdir()
    os.rename(proj / "plans", proj / "plans-old")
    os.symlink(other, proj / "plans")
    assert plans.list_plans(str(proj)) == []
    with pytest.raises(plans.PlanError):
        plans.create_plan(s, str(proj), "x", "y")
    assert os.listdir(other) == []


# ------------------------------------------------------------------ routes
def test_pages_render_and_markdown(env):
    c, proj, s, csrf = env
    assert "No plans yet" in c.get("/p/pm").text
    r = c.get("/p/pm/plans")
    assert r.status_code == 200 and "No plans yet" in r.text
    plans.create_plan(s, str(proj), "idea", "# Title\n\n**bold** <b>x</b>\n")
    html = c.get("/p/pm/plans/idea").text
    assert "<h1>Title</h1>" in html and "<strong>bold</strong>" in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "idea.md" in c.get("/p/pm").text and "idea.md" in c.get("/p/pm/plans").text
    assert 'name="text"' in c.get("/p/pm/plans/idea?edit=1").text
    assert c.get("/p/pm/plans/nope").status_code == 404
    assert c.get("/p/pm/plans/..%2Fx").status_code == 404
    assert c.get("/p/unknown/plans").status_code == 404


def test_plan_embed_for_drawer(env):
    """Project page drawer: ?embed=1 is framable by pm, read-only, no chrome; the plain page is not framable."""
    c, proj, s, csrf = env
    plans.create_plan(s, str(proj), "idea", "# Title\n")
    r = c.get("/p/pm/plans/idea?embed=1")
    assert r.status_code == 200 and "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert 'class="v5-embed"' in r.text and "<h1>Title</h1>" in r.text
    assert 'target="_top">Edit</a>' in r.text and "/rename" not in r.text and 'class="v5-side' not in r.text
    assert 'name="text"' not in c.get("/p/pm/plans/idea?embed=1&edit=1").text
    assert "frame-ancestors 'none'" in c.get("/p/pm/plans/idea").headers["content-security-policy"]


def test_route_crud(env):
    c, proj, s, csrf = env
    r = post(c, csrf, "/p/pm/plans/new", name="Plan-1", text="# hi\n")
    assert r.status_code == 303 and r.headers["location"].endswith("/plans/Plan-1?ok=created")
    f = proj / "plans" / "Plan-1.md"
    assert f.read_text() == "# hi\n"
    tok = re.search(r'name="token" value="([0-9a-f]{16})"',
                    c.get("/p/pm/plans/Plan-1?edit=1").text).group(1)
    r = post(c, csrf, "/p/pm/plans/Plan-1/save", text="# new\n", token=tok)
    assert r.status_code == 303 and f.read_text() == "# new\n"
    r = post(c, csrf, "/p/pm/plans/Plan-1/rename", new_name="Plan-2")
    assert r.status_code == 303 and (proj / "plans" / "Plan-2.md").exists() and not f.exists()
    r = post(c, csrf, "/p/pm/plans/Plan-2/delete")  # no confirm
    assert r.status_code == 400 and (proj / "plans" / "Plan-2.md").exists()
    r = post(c, csrf, "/p/pm/plans/Plan-2/delete", confirm="1")
    assert r.status_code == 303 and not (proj / "plans" / "Plan-2.md").exists()
    log = "\n".join(audit_lines(s))
    for a in ("plan_create", "plan_save", "plan_rename", "plan_delete"):
        assert a in log
    assert "# new" not in log and "# hi" not in log


def test_edit_conflict_shows_diff_and_keeps_file(env):
    c, proj, s, csrf = env
    plans.create_plan(s, str(proj), "p", "a\nb\n")
    tok = re.search(r'name="token" value="([0-9a-f]{16})"',
                    c.get("/p/pm/plans/p?edit=1").text).group(1)
    (proj / "plans" / "p.md").write_text("a\nb\nagent line\n")  # external writer
    r = post(c, csrf, "/p/pm/plans/p/save", text="mine\n", token=tok)
    assert r.status_code == 409 and "agent line" in r.text and "mine" in r.text
    assert (proj / "plans" / "p.md").read_text() == "a\nb\nagent line\n"


def test_guards(env):
    c, proj, s, csrf = env
    assert post(c, csrf, "/p/pm/plans/new", origin="https://evil.example", name="x").status_code == 403
    assert post(c, "bad", "/p/pm/plans/new", name="x").status_code == 403
    assert post(c, csrf, "/p/nope/plans/new", name="x").status_code == 404
    assert post(c, csrf, "/p/pm/plans/new", name="../evil", text="x").status_code == 400
    assert post(c, csrf, "/p/pm/plans/new", name="a/b", text="x").status_code == 400
    assert not (proj / "plans").exists() or not os.listdir(proj / "plans")
    anon = TestClient(c.app, base_url="https://pm.example.test")
    assert anon.post("/p/pm/plans/new", data={"name": "x"}, headers={"Origin": ORIGIN},
                     follow_redirects=False).status_code in (401, 403, 303)
    assert not (proj / "plans" / "x.md").exists()


def test_fail_closed_when_audit_broken(env, monkeypatch):
    c, proj, s, csrf = env
    from pm.security.audit import AuditError, AuditLog

    def boom(self, entry):
        raise AuditError("disk full")

    monkeypatch.setattr(AuditLog, "write", boom)
    r = post(c, csrf, "/p/pm/plans/new", name="x", text="y")
    assert r.status_code == 503 and not (proj / "plans" / "x.md").exists()


def test_flock_busy_is_503(env):
    from pm.core import filelock

    c, proj, s, csrf = env
    plans.create_plan(s, str(proj), "p", "a")
    f = proj / "plans" / "p.md"
    with filelock.locked(filelock.lock_path(s.state_dir, f), 1.0):
        with pytest.raises(plans.PlanError) as e:
            # same-thread re-entry would deadlock on the in-process guard; use a short timeout path
            import threading
            res = []
            t = threading.Thread(target=lambda: res.append(_try(s, proj)))
            t.start()
            t.join(10)
            raise res[0]
    assert e.value.status == 503


def _try(s, proj):
    try:
        plans.delete_plan(s, str(proj), "p")
    except plans.PlanError as e:
        return e
    return AssertionError("not locked")


# ------------------------------------------------------------------ agent locations
def _agent(tmp_path, kind, name, text):
    d = tmp_path / "home" / f".{kind}" / "plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)
    return d / name


def test_agent_plans_filtered_by_project(env, tmp_path):
    c, proj, s, csrf = env
    mine = _agent(tmp_path, "claude", "cool-name.md", f"# x\nworks in {proj}/src\n")
    _agent(tmp_path, "cursor", "a_1234abcd.plan.md", f"cwd {proj}\n")
    _agent(tmp_path, "claude", "other.md", f"in {proj}-other and /home/me/dev/zzz\n")
    got = {(p.src, p.name) for p in plans.list_plans(str(proj))}
    assert got == {("claude", "cool-name"), ("cursor", "a_1234abcd.plan")}
    assert len(plans.list_plans(str(proj), all_agent=True)) == 3
    page = c.get("/p/pm/plans").text
    assert "cool-name.md" in page and "other.md" not in page and "Cursor" in page
    assert "other.md" in c.get("/p/pm/plans?all=1").text
    assert "cool-name.md" in c.get("/p/pm").text
    r = c.get("/p/pm/plans/cool-name?src=claude")
    assert r.status_code == 200 and "works in" in r.text
    assert c.get("/p/pm/plans/cool-name").status_code == 404  # not a project plan
    assert c.get("/p/pm/plans/cool-name?src=bogus").status_code == 404
    assert mine.exists()


def test_agent_route_crud(env, tmp_path):
    c, proj, s, csrf = env
    r = post(c, csrf, "/p/pm/plans/new", name="n1", text="# hi\n", src="claude")
    assert r.status_code == 303 and r.headers["location"].endswith("/plans/n1?ok=created&src=claude")
    f = tmp_path / "home" / ".claude" / "plans" / "n1.md"
    assert f.read_text().startswith("# hi") and str(proj) in f.read_text()
    assert [(p.src, p.name) for p in plans.list_plans(str(proj))] == [("claude", "n1")]
    tok = re.search(r'name="token" value="([0-9a-f]{16})"',
                    c.get("/p/pm/plans/n1?edit=1&src=claude").text).group(1)
    r = post(c, csrf, "/p/pm/plans/n1/save", text=f"# new {proj}\n", token=tok, src="claude")
    assert r.status_code == 303 and f.read_text() == f"# new {proj}\n"
    r = post(c, csrf, "/p/pm/plans/n1/rename", new_name="n2", src="claude")
    assert r.status_code == 303 and (f.parent / "n2.md").exists() and not f.exists()
    assert not (proj / "plans").exists()
    assert post(c, csrf, "/p/pm/plans/n2/delete", src="claude").status_code == 400
    r = post(c, csrf, "/p/pm/plans/n2/delete", src="claude", confirm="1")
    assert r.status_code == 303 and not (f.parent / "n2.md").exists()
    assert post(c, csrf, "/p/pm/plans/new", name="z", text="x", src="../etc").status_code == 404
    log = "\n".join(audit_lines(s))
    assert '"src": "claude"' in log or "claude" in log
    assert "# hi" not in log and "# new" not in log


def test_agent_dir_safety(env, tmp_path):
    c, proj, s, csrf = env
    home = tmp_path / "home"
    outside = tmp_path / "secret.md"
    outside.write_text(f"secret {proj}")
    d = _agent(tmp_path, "claude", "ok.md", f"{proj}").parent
    os.symlink(outside, d / "link.md")
    assert [p.name for p in plans.list_plans(str(proj))] == ["ok"]
    for fn in (lambda: plans.read_plan(str(proj), "link", "claude"),
               lambda: plans.delete_plan(s, str(proj), "link", "claude")):
        with pytest.raises(plans.PlanError):
            fn()
    assert outside.read_text() == f"secret {proj}"
    # symlinked agent dir (e.g. to ~/.cfg) is refused
    (home / ".cfg").mkdir()
    os.rename(d, home / ".claude" / "plans-old")
    os.symlink(home / ".cfg", d)
    assert plans.list_plans(str(proj)) == []
    with pytest.raises(plans.PlanError):
        plans.create_plan(s, str(proj), "x", "y", "claude")
    assert os.listdir(home / ".cfg") == []


def test_project_local_plans_directory(env):
    _, proj, s, _ = env
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text('{"plansDirectory": "docs/agent-plans"}')
    plans.create_plan(s, str(proj), "loc", "x", "claude-project")
    assert (proj / "docs" / "agent-plans" / "loc.md").exists()
    assert [(p.src, p.name) for p in plans.list_plans(str(proj))] == [("claude-project", "loc")]
    (proj / ".claude" / "settings.json").write_text('{"plansDirectory": "../../etc"}')
    assert plans.list_plans(str(proj)) == []
    with pytest.raises(plans.PlanError):
        plans.create_plan(s, str(proj), "bad", "x", "claude-project")


def test_plans_list_and_new_in_popup_embed(env):
    """Project page Plans popup: the list and the create form are framable (?embed=1), links and form keep embed."""
    c, proj, s, csrf = env
    plans.create_plan(s, str(proj), "idea", "# Title\n")
    r = c.get("/p/pm/plans?embed=1")
    assert r.status_code == 200 and "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert 'class="v5-embed"' in r.text and "/plans/idea?embed=1" in r.text and 'name="embed" value="1"' in r.text
    assert "frame-ancestors 'none'" in c.get("/p/pm/plans").headers["content-security-policy"]
    r = post(c, csrf, "/p/pm/plans/new", name="made", text="# x\n", embed="1")
    assert r.status_code == 303 and r.headers["location"].endswith("&embed=1")
    r = post(c, csrf, "/p/pm/plans/new", name="bad name!", text="x", embed="1")
    assert r.status_code == 400 and 'class="v5-embed"' in r.text


# ------------------------------------------------------------------ root PLAN.md (read-only)
def test_root_plan_listed_read_and_counted(env):
    c, proj, s, _ = env
    (proj / "PLAN.md").write_text("# Spec\n")
    plans.create_plan(s, str(proj), "PLAN", "# Other\n")  # plans/PLAN.md: same stem, other src
    got = plans.list_plans(str(proj))
    assert sorted((p.src, p.name) for p in got) == [("project", "PLAN"), ("root", "PLAN")]
    assert [p.src for p in plans.list_plans(str(proj), src="root")] == ["root"]
    assert plans.counts(str(proj)) == {"root": 1, "project": 1}
    assert plans.read_plan(str(proj), "PLAN", "root")[0] == "# Spec\n"
    assert plans.read_plan(str(proj), "PLAN")[0] == "# Other\n"
    with pytest.raises(plans.PlanError) as e:
        plans.read_plan(str(proj), "other", "root")
    assert e.value.status == 404
    r = c.get("/p/pm/plans/PLAN?src=root")
    assert r.status_code == 200 and "Spec" in r.text
    assert "?edit=1" not in r.text and "/rename" not in r.text and "/delete" not in r.text
    assert "/plans/PLAN/save" not in c.get("/p/pm/plans/PLAN?src=root&edit=1").text
    assert 'value="root"' not in c.get("/p/pm/plans").text


def test_root_plan_writes_refused(env):
    c, proj, s, csrf = env
    (proj / "PLAN.md").write_text("# Spec\n")
    tok = plans.read_plan(str(proj), "PLAN", "root")[1]
    for fn in (lambda: plans.create_plan(s, str(proj), "x", "t", "root"),
               lambda: plans.save_plan(s, str(proj), "PLAN", "t", tok, "root"),
               lambda: plans.rename_plan(s, str(proj), "PLAN", "new", "root"),
               lambda: plans.delete_plan(s, str(proj), "PLAN", "root")):
        with pytest.raises(plans.PlanError) as e:
            fn()
        assert e.value.status == 403
    assert (proj / "PLAN.md").read_text() == "# Spec\n"
    for act, data in (("save", {"text": "x", "token": tok}), ("rename", {"new_name": "n"}),
                      ("delete", {"confirm": "1"})):
        assert post(c, csrf, f"/p/pm/plans/PLAN/{act}", src="root", **data).status_code == 403
    assert (proj / "PLAN.md").read_text() == "# Spec\n"


def test_root_plan_symlink_refused(env, tmp_path):
    c, proj, s, _ = env
    secret = tmp_path / "secret.md"
    secret.write_text("secret")
    (proj / "PLAN.md").symlink_to(secret)
    assert plans.list_plans(str(proj), src="root") == []
    assert plans.counts(str(proj)) == {}
    with pytest.raises(plans.PlanError):
        plans.read_plan(str(proj), "PLAN", "root")
    assert c.get("/p/pm/plans/PLAN?src=root").status_code == 400
