"""Project-scoped VM routes under /p/{name}/vms/{vm}/... (web.routes.servers) with an in-memory fake virsh."""

from helpers import ORIGIN, audit_text, make_client, make_settings, pane_line
from infra.fakevirt import FakeVirt

from pm.core.models import Tier
from pm.infra import libvirt
from pm.projects import settings as pset
from pm.security import auth
from pm.security.csrf import Csrf


def _env(fake_tmux, tmp_path, monkeypatch, login=True, pats="lab-*", **over):
    fv = FakeVirt()
    monkeypatch.setattr(libvirt, "run_argv", fv.run_argv)
    s = make_settings(fake_tmux, tmp_path, state="st", disable_poll=True, virsh_bin="virsh",
                      virsh_write_sg=False, **over)
    alpha = tmp_path / "dev" / "alpha"
    alpha.mkdir(parents=True)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", str(alpha)))
    if pats:
        pset.save_vms(s, "alpha", pats)
    c = make_client(s, logged_in=login)
    tok = ""
    if login:
        sess = c.app.state.sessions.session_from_cookie(c.cookies.get(auth.COOKIE_NAME))
        tok = Csrf(s.state_dir, s.allowed_origins).token(sess.id)
    return c, tok, s, fv


def _post(c, tok, url, **data):
    return c.post(url, data={"csrf": tok, **data}, headers={"Origin": ORIGIN})


def test_reads_need_login_and_project(fake_tmux, tmp_path, monkeypatch):
    c, _tok, _s, fv = _env(fake_tmux, tmp_path, monkeypatch, login=False)
    for u in ("/p/alpha/vms/lab-1/snapshots", "/p/alpha/vms/lab-1/console"):
        assert c.get(u, follow_redirects=False).status_code in (303, 401, 403)
    assert c.post("/p/alpha/vms/lab-2/start").status_code in (401, 403)
    assert not fv.calls


def test_snapshots_and_console_json(fake_tmux, tmp_path, monkeypatch):
    c, _tok, _s, fv = _env(fake_tmux, tmp_path, monkeypatch)
    assert c.get("/p/nope/vms/lab-1/snapshots").status_code == 404
    assert c.get("/p/alpha/vms/prod/snapshots").status_code == 404  # outside the patterns
    r = c.get("/p/alpha/vms/lab-1/snapshots")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert [s["name"] for s in r.json()["snapshots"]] == ["s1", "s2"] and r.json()["vm"] == "lab-1"
    r = c.get("/p/alpha/vms/lab-1/console")
    assert r.json() == {"vm": "lab-1", "state": "running", "url": "vnc://127.0.0.1:5901", "read_only": True}
    assert not fv.writes


def test_no_patterns_means_no_vms(fake_tmux, tmp_path, monkeypatch):
    c, tok, _s, fv = _env(fake_tmux, tmp_path, monkeypatch, pats="")
    assert c.get("/p/alpha/vms/lab-1/snapshots").status_code == 404
    assert _post(c, tok, "/p/alpha/vms/lab-2/start").status_code == 404
    assert not fv.writes


def test_start_shutdown_csrf_origin_and_audit(fake_tmux, tmp_path, monkeypatch):
    c, tok, s, fv = _env(fake_tmux, tmp_path, monkeypatch)
    assert c.post("/p/alpha/vms/lab-2/start", headers={"Origin": ORIGIN}).status_code == 403
    assert c.post("/p/alpha/vms/lab-2/start", data={"csrf": tok},
                  headers={"Origin": "https://evil.example"}).status_code == 403
    assert not fv.writes
    r = _post(c, tok, "/p/alpha/vms/lab-2/start")
    assert r.status_code == 200 and "Started lab-2" in r.text and r.headers["hx-trigger"] == "vms-changed"
    assert _post(c, tok, "/p/alpha/vms/lab-2/shutdown").status_code == 200
    assert fv.writes == [["start", "--", "lab-2"], ["shutdown", "--", "lab-2"]]
    assert '"pvm_start"' in audit_text(s) and '"pvm_shutdown"' in audit_text(s)
    assert _post(c, tok, "/p/alpha/vms/prod/shutdown").status_code == 404
    assert _post(c, tok, "/p/nope/vms/lab-2/start").status_code == 404
    assert '"unknown project"' in audit_text(s)


def test_force_off_and_delete_typed_and_protected(fake_tmux, tmp_path, monkeypatch):
    c, tok, s, fv = _env(fake_tmux, tmp_path, monkeypatch)
    assert _post(c, tok, "/p/alpha/vms/lab-1/force-off", confirm="lab").status_code == 403
    assert _post(c, tok, "/p/alpha/vms/lab-eve/force-off", confirm="lab-eve").status_code == 403  # protected
    assert _post(c, tok, "/p/alpha/vms/lab-2/delete", confirm="").status_code == 403
    assert not fv.writes
    assert _post(c, tok, "/p/alpha/vms/lab-1/force-off", confirm="lab-1").status_code == 200
    assert _post(c, tok, "/p/alpha/vms/lab-1/delete", confirm="lab-1").status_code == 200
    assert fv.writes == [["destroy", "--", "lab-1"], ["undefine", "--", "lab-1"]]
    log = audit_text(s)
    assert '"pvm_force_off"' in log and '"pvm_delete"' in log and '"denied"' in log


def test_typed_actions_need_message_tier(fake_tmux, tmp_path, monkeypatch):
    c, tok, _s, fv = _env(fake_tmux, tmp_path, monkeypatch, tier_ceiling=Tier.SAFE)
    assert _post(c, tok, "/p/alpha/vms/lab-1/force-off", confirm="lab-1").status_code == 403
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots/s1/revert").status_code == 403
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots/s1/delete").status_code == 403
    assert not fv.writes
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots", snap="pre").status_code == 200  # SAFE


def test_snapshot_create_revert_delete(fake_tmux, tmp_path, monkeypatch):
    c, tok, s, fv = _env(fake_tmux, tmp_path, monkeypatch)
    r = _post(c, tok, "/p/alpha/vms/lab-1/snapshots", snap="pre-upgrade")
    assert r.status_code == 200 and "pre-upgrade" in r.text
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots", snap="pre-upgrade").status_code == 409
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots", snap="--x").status_code == 400
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots/ghost/revert").status_code == 404
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots/s1/revert").status_code == 200
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots/s1/delete").status_code == 200
    assert _post(c, tok, "/p/alpha/vms/lab-eve/snapshots", snap="x").status_code == 403
    assert fv.writes == [["snapshot-create-as", "--atomic", "--", "lab-1", "pre-upgrade"],
                         ["snapshot-revert", "--", "lab-1", "s1"], ["snapshot-delete", "--", "lab-1", "s1"]]
    log = audit_text(s)
    assert '"pvm_snapshot"' in log and '"lab-1:s1"' in log and '"pvm_snapshot_delete"' in log


def test_vm_manage_off_refuses_mutations(fake_tmux, tmp_path, monkeypatch):
    c, tok, _s, fv = _env(fake_tmux, tmp_path, monkeypatch, vm_manage=False)
    assert _post(c, tok, "/p/alpha/vms/lab-2/start").status_code == 403
    assert _post(c, tok, "/p/alpha/vms/lab-1/snapshots", snap="a").status_code == 403
    assert c.get("/p/alpha/vms/lab-1/snapshots").status_code == 200
    assert not fv.writes


def test_project_page_vm_count_from_the_cached_listing(fake_tmux, tmp_path, monkeypatch):
    from pm.web.routes import pages

    c, _tok, s, fv = _env(fake_tmux, tmp_path, monkeypatch)
    seen = []
    real = pages.html
    monkeypatch.setattr(pages, "html", lambda request, name, **ctx: seen.append(ctx.get("vm_count", "missing"))
                        or real(request, name, **ctx))
    monkeypatch.setattr(libvirt, "_cache", None)
    assert c.get("/p/alpha").status_code == 200
    assert not fv.calls  # the page never lists VMs itself
    assert c.get("/p/alpha/vms").status_code == 200  # the VM tab fills the cache
    assert c.get("/p/alpha").status_code == 200
    want = sum(1 for d in libvirt.cached().domains if d.name.startswith("lab-"))
    assert seen == [None, want] and want > 0
