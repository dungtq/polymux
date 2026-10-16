import re
import time
from dataclasses import replace

import pytest
from helpers import patch_panel, servers_client, servers_panel

from pm.infra import servers


def test_auth_required(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch, login=False)
    for u in ("/servers", "/servers/panel"):
        r = c.get(u, follow_redirects=False)
        assert r.status_code in (303, 401, 403)


def test_page_render_escaping_and_nav(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    r = c.get("/servers")
    assert r.status_code == 200
    h = r.text
    sv = c.get("/servers?t=services").text
    assert "<script>alert" not in sv and "&lt;script&gt;alert" in sv
    assert not re.search(r"<script(?![^>]*\bsrc=)", h) and "<style" not in h
    assert not re.search(r"\sstyle=", h)
    assert 'hx-trigger="every 10s, vms-changed' in h and "data-nodiff" in h and "stale" in h
    assert "Overall health" in h
    m = re.search(r'<a href="/servers" class="([^"]*)"', h)
    assert m and "on" in m.group(1).split()
    assert h.count('<nav class="tabs srv-tabs tabs-seg"') == 1
    assert "rebox" in h and "RE only" in h


def test_panel_fragment(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    r = c.get("/servers/panel")
    assert r.status_code == 200 and "<html" not in r.text and "Updated" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_nav_item_on_other_pages(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/audit").text
    m = re.search(r'<a href="/servers" class="([^"]*)"', h)
    assert m and "on" not in m.group(1).split()


def test_summary_strip_in_page_and_fragment(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    page, frag = c.get("/servers").text, c.get("/servers/panel").text
    for h in (page, frag):
        assert 'class="sum-strip"' in h and 'id="sum-hosts"' in h and 'id="sum-containers"' in h
        assert "rebox" in h and "CRIT" in h  # host down -> crit overall
        assert not re.search(r"\sstyle=", h)
    assert page.index("sum-strip") < page.index("srv-grid")
    assert not re.search(r"<script(?![^>]*\bsrc=)", page) and "<style" not in page


def test_panel_etag_204_and_changed(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    r = c.get("/servers/panel")
    et = r.headers["etag"]
    assert r.status_code == 200 and et
    r2 = c.get("/servers/panel", headers={"If-None-Match": et})
    assert r2.status_code == 204 and r2.content == b""
    # a new timestamp alone is not a change; different content is
    patch_panel(monkeypatch, lambda: replace(servers_panel(), ts=time.time() + 5))
    assert c.get("/servers/panel", headers={"If-None-Match": et}).status_code == 204
    patch_panel(monkeypatch, lambda: replace(servers_panel(), hostname="other-host"))
    r3 = c.get("/servers/panel", headers={"If-None-Match": et})
    assert r3.status_code == 200 and r3.headers["etag"] != et


def test_container_filter_param(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    assert c.get("/servers?ct=bad").status_code == 200
    assert 'href="/servers?t=services&amp;ct=bad"' in c.get("/servers?t=services").text
    assert c.get("/servers/panel?ct=bad").status_code == 200
    assert c.get("/servers?ct=junk").status_code == 200


# ---------------------------------------------------------------- tabs
def _current(h):
    return re.findall(r'<a class="tab srv-tab" href="([^"]*)" aria-current="page"', h)


def test_tab_overview_default_only_its_content(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    for u in ("/servers", "/servers?t=overview", "/servers?t=bogus", "/servers?t="):
        h = c.get(u).text
        assert _current(h) == ["/servers"], u
        assert 'id="srv-h-host"' in h and 'id="srv-h-hosts"' in h and "sum-strip" in h
        assert 'id="srv-h-docker"' not in h and 'id="srv-h-vms"' not in h
        assert 'id="srv-h-tmux"' not in h  # duplicated tmux block is gone


def test_tab_services_only_its_content(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/servers?t=services").text
    assert _current(h) == ["/servers?t=services"]
    assert 'id="srv-h-docker"' in h and "&lt;script&gt;alert" in h
    for gone in ("sum-strip", 'id="srv-h-host"', 'id="srv-h-hosts"', 'id="srv-h-vms"'):
        assert gone not in h
    assert 'hx-get="/servers/panel?t=services"' in h


def test_tab_vms_only_its_content(fake_tmux, tmp_path, monkeypatch):
    c, csrf, log = _vm_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/servers?t=vms").text
    assert _current(h) == ["/servers?t=vms"]
    assert 'id="srv-h-vms"' in h and 'id="vms-panel"' in h
    for gone in ("sum-strip", 'id="srv-h-host"', 'id="srv-h-docker"'):
        assert gone not in h
    # other tabs do not ship the VM table
    assert "alpha" not in c.get("/servers?t=services").text


def test_tab_labels_show_counts(fake_tmux, tmp_path, monkeypatch):
    patch_panel(monkeypatch, lambda: replace(servers_panel(), libvirt=servers.Libvirt(3, 14)))
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    patch_panel(monkeypatch, lambda: replace(servers_panel(), libvirt=servers.Libvirt(3, 14)))
    for u in ("/servers", "/servers?t=services", "/servers?t=vms", "/servers/panel?t=vms"):
        h = c.get(u).text
        assert re.search(r'Services <span class="srv-tabn" title="1 running of 1">1/1</span>', h), u
        assert re.search(r'VMs <span class="srv-tabn" title="3 running of 14">3/14</span>', h), u


def test_tab_labels_without_data_have_no_count(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    patch_panel(monkeypatch, lambda: replace(servers_panel(), docker=servers.DockerInfo(False), libvirt=None))
    h = c.get("/servers").text
    assert "srv-tabn" not in h and ">Services</a>" in h and ">VMs</a>" in h


def test_panel_fragment_is_tab_aware(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    ov, sv, vm = (c.get(f"/servers/panel?t={t}").text for t in ("overview", "services", "vms"))
    assert "srv-h-hosts" in ov and "srv-h-docker" not in ov
    assert "srv-h-docker" in sv and "srv-h-hosts" not in sv
    assert "srv-h-docker" not in vm and "srv-h-hosts" not in vm and "srv-tabs" in vm
    assert _current(sv) == ["/servers?t=services"]
    assert "srv-h-hosts" in c.get("/servers/panel?t=nope").text


def test_ct_bad_keeps_tab(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/servers?t=services&ct=bad").text
    assert _current(h) == ["/servers?t=services"]
    assert 'hx-get="/servers/panel?t=services&amp;ct=bad"' in h
    assert re.search(r'<a class="srv-chip on" href="/servers\?t=services&amp;ct=bad"', h)
    assert 'href="/servers?t=services"' in h  # "All" chip stays on the tab
    assert "No unhealthy containers." in h
    f = c.get("/servers/panel?t=services&ct=bad").text
    assert "No unhealthy containers." in f and _current(f) == ["/servers?t=services"]


# ---------------------------------------------------------------- VMs
def _vm_client(fake_tmux, tmp_path, monkeypatch, ceiling=None):
    import json

    from pm.infra import libvirt
    state = tmp_path / "vstate.json"
    state.write_text(json.dumps({"alpha": "shut off", "beta": "running", "eve-ng-x": "running"}))
    monkeypatch.setenv("FAKE_VIRSH_STATE", str(state))
    monkeypatch.setenv("FAKE_VIRSH_LOG", str(tmp_path / "vlog"))
    libvirt.invalidate()
    extra = {} if ceiling is None else {"tier_ceiling": ceiling}
    s = replace(fake_tmux, virsh_bin=str(__import__("pathlib").Path(__file__).parent / "bin" / "virsh"),
                virsh_write_sg=False, **extra)
    c = servers_client(s, tmp_path, monkeypatch)
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/servers/vms").text).group(1)
    return c, csrf, tmp_path / "vlog"


def _vwrites(log):
    import json
    return [json.loads(x) for x in log.read_text().splitlines() if x.startswith('["-c"')] if log.exists() else []


def test_vms_listing(fake_tmux, tmp_path, monkeypatch):
    c, csrf, log = _vm_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/servers?t=vms").text
    assert 'id="srv-h-vms"' in h and "alpha" in h and "orphan.qcow2" in h
    assert "/servers/vms/alpha/start" in h and "/servers/vms/beta/shutdown" in h
    assert "/servers/vms/eve-ng-x/" not in h  # protected: no actions
    assert "/servers/images/orphan.qcow2/delete" in h and "/servers/images/alpha.qcow2/delete" not in h
    assert not _vwrites(log)


def test_vm_start_requires_csrf_origin_auth(fake_tmux, tmp_path, monkeypatch):
    c, csrf, log = _vm_client(fake_tmux, tmp_path, monkeypatch)
    assert c.post("/servers/vms/alpha/start").status_code == 403
    assert c.post("/servers/vms/alpha/start", data={"csrf": csrf},
                  headers={"Origin": "https://evil.example"}).status_code == 403
    c2 = servers_client(fake_tmux, tmp_path, monkeypatch, login=False)
    assert c2.post("/servers/vms/alpha/start", data={"csrf": csrf}).status_code in (401, 403)
    assert not _vwrites(log)
    r = c.post("/servers/vms/alpha/start", data={"csrf": csrf}, headers={"Origin": "https://pm.example.test"})
    assert r.status_code == 200 and "Started alpha" in r.text and r.headers["hx-trigger"] == "vms-changed"
    assert _vwrites(log)[-1][2:] == ["start", "--", "alpha"]
    assert "vm_start" in (tmp_path / "st" / "audit.jsonl").read_text()


def test_vm_force_off_and_protected(fake_tmux, tmp_path, monkeypatch):
    c, csrf, log = _vm_client(fake_tmux, tmp_path, monkeypatch)
    h = {"Origin": "https://pm.example.test"}
    assert c.post("/servers/vms/beta/destroy", data={"csrf": csrf, "confirm": "x"}, headers=h).status_code == 403
    assert c.post("/servers/vms/eve-ng-x/shutdown", data={"csrf": csrf}, headers=h).status_code == 403
    assert c.post("/servers/vms/ghost/shutdown", data={"csrf": csrf}, headers=h).status_code == 404
    assert not _vwrites(log)
    assert c.post("/servers/vms/beta/destroy", data={"csrf": csrf, "confirm": "beta"}, headers=h).status_code == 200


def test_vm_tier_ceiling(fake_tmux, tmp_path, monkeypatch):
    from pm.core.models import Tier
    c, csrf, log = _vm_client(fake_tmux, tmp_path, monkeypatch, ceiling=Tier.SAFE)
    h = {"Origin": "https://pm.example.test"}
    assert c.post("/servers/vms/beta/destroy", data={"csrf": csrf, "confirm": "beta"}, headers=h).status_code == 403
    assert c.post("/servers/images/orphan.qcow2/delete", data={"csrf": csrf, "confirm": "orphan.qcow2"}, headers=h).status_code == 403
    assert not _vwrites(log)
    assert c.post("/servers/vms/alpha/start", data={"csrf": csrf}, headers=h).status_code == 200


@pytest.fixture(autouse=True)
def _no_real_virsh(request, monkeypatch):
    """Page tests must not shell out to the real libvirt; the VM tests use the fake virsh."""
    if "vm" not in request.node.name:
        from pm.infra import libvirt
        monkeypatch.setattr(libvirt, "get", lambda settings, force=False: libvirt.VmInfo(0.0))


def test_resources_tab(fake_tmux, tmp_path, monkeypatch):
    from pm.infra import resources
    res = resources.Resources(
        time.time(), 4, 8 * 1024**3, 4 * 1024**3, 120.0,
        (resources.Row("<b>prog</b>", 2, 50.0, 10**8),), (resources.Row("main", 3, 5.0, 10**7, 1),),
        (resources.Row("web", 0, 1.0, 10**7),), ())
    monkeypatch.setattr(resources, "get", lambda settings, **kw: res)
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/servers?t=resources&s=cpu").text
    assert "Resource summary" in h and "&lt;b&gt;prog" in h and "<b>prog" not in h
    assert "tmux sessions" in h and "Containers" in h and 'href="/servers?t=resources"' in h
    assert "s=cpu" in h  # the poll keeps the sort
    f = c.get("/servers/panel?t=resources")
    assert f.status_code == 200 and "Programs" in f.text


def test_route_passes_the_ntfy_collector(fake_tmux, tmp_path, monkeypatch):
    """infra may not import notify, so the route injects ntfy_fn; without it the ntfy section is 'unknown'."""
    from pm.notify import feed
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    seen = {}

    async def swr(settings, tracker=None, first_wait_s=2.0, **kw):
        seen.update(kw)
        return servers_panel()
    monkeypatch.setattr(servers, "get_panel_swr", swr)
    assert c.get("/servers").status_code == 200
    assert seen.get("ntfy_fn") is feed.collect_ntfy


def _proj_vms_client(fake_tmux, tmp_path, monkeypatch, login=True):
    from helpers import pane_line
    c = servers_client(fake_tmux, tmp_path, monkeypatch, login=login)
    (tmp_path / "dev" / "alpha").mkdir(exist_ok=True)
    monkeypatch.setenv("FAKE_TMUX_OUT", pane_line("$1", "work", "@1", "%12", str(tmp_path / "dev" / "alpha")))
    return c


def test_project_vms_auth_and_404(fake_tmux, tmp_path, monkeypatch):
    c = _proj_vms_client(fake_tmux, tmp_path, monkeypatch, login=False)
    assert c.get("/p/alpha/vms", follow_redirects=False).status_code in (303, 401, 403)
    c = _proj_vms_client(fake_tmux, tmp_path, monkeypatch)
    assert c.get("/p/nope/vms").status_code == 404


def test_project_vms_renders_table_or_unavailable(fake_tmux, tmp_path, monkeypatch):
    c = _set_vms(fake_tmux, tmp_path, monkeypatch, "alpha")
    h = c.get("/p/alpha/vms").text
    assert "alpha" in h and 'name="csrf"' in h and "<html" not in h
    from pm.infra import libvirt
    monkeypatch.setattr(libvirt, "get", lambda settings, **kw: libvirt.VmInfo(1.0, available=False, error="virsh not installed"))
    h = c.get("/p/alpha/vms")
    assert h.status_code == 200 and "virsh not installed" in h.text


def test_project_vms_hint_when_no_patterns(fake_tmux, tmp_path, monkeypatch):
    c, _csrf, log = _vm_client(fake_tmux, tmp_path, monkeypatch)
    (tmp_path / "dev" / "alpha").mkdir(exist_ok=True)
    from pm.infra import libvirt
    monkeypatch.setattr(libvirt, "get", lambda *a, **k: pytest.fail("libvirt must not be called"))
    h = c.get("/p/alpha/vms").text
    assert "No VMs configured for this project" in h and "dt-vm" not in h


def _set_vms(fake_tmux, tmp_path, monkeypatch, pats):
    c, _csrf, _log = _vm_client(fake_tmux, tmp_path, monkeypatch)
    (tmp_path / "dev" / "alpha").mkdir(exist_ok=True)
    from pm.projects import settings as pset
    pset.save_vms(replace(fake_tmux, state_dir=tmp_path / "st"), "alpha", pats)
    return c


def test_project_vms_filtered_by_pattern(fake_tmux, tmp_path, monkeypatch):
    from pm.infra import libvirt
    monkeypatch.setattr(libvirt, "get", lambda *a, **k: libvirt.VmInfo(
        1.0, domains=(libvirt.Domain("lab-1", "running", 1, 1024, False, True, False),
                      libvirt.Domain("prod", "running", 1, 1024, False, True, False)),
        volumes=(libvirt.Volume("lab-1.qcow2", "/x/lab-1.qcow2", "file", 1, 1, ("lab-1",)),
                 libvirt.Volume("prod.qcow2", "/x/prod.qcow2", "file", 1, 1, ("prod",)))))
    c = _set_vms(fake_tmux, tmp_path, monkeypatch, "lab-*")
    h = c.get("/p/alpha/vms").text
    assert "lab-1" in h and "lab-1.qcow2" in h and "prod" not in h
    from pm.projects import settings as pset
    pset.save_vms(replace(fake_tmux, state_dir=tmp_path / "st"), "alpha", "zzz*")
    h = c.get("/p/alpha/vms").text
    assert "lab-1" not in h and "prod" not in h and "No domains." in h


def test_overview_gets_cached_vm_listing_without_virsh(fake_tmux, tmp_path, monkeypatch):
    from pm.infra import libvirt
    from pm.web.routes import servers as sv_routes

    seen = {}
    real_html = sv_routes.html

    def spy(request, name, **ctx):
        seen[ctx["tab"]] = ctx["vm"]
        return real_html(request, name, **ctx)

    def no_virsh(*a, **kw):
        raise AssertionError("virsh called")

    marker = object()
    monkeypatch.setattr(libvirt, "get", no_virsh)
    monkeypatch.setattr(libvirt, "cached", lambda: marker)
    monkeypatch.setattr(sv_routes, "html", spy)
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    assert c.get("/servers").status_code == 200 and seen["overview"] is marker
    assert c.get("/servers?t=services").status_code == 200 and seen["services"] is None
