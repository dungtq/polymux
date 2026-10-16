"""dt Servers page (docs/themes/dt/PRD.md §6.9): the view model (pm.web.dt.servers, servers_res), the page templates and
the fragments the core returns (_servers.html for GET /servers/panel, _vms.html for GET /servers/vms), the meters part,
and the rules of js/servers.js (the A21 poll stays in the trigger; no timer, fetch or confirm of its own)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

import jinja2
import pytest

from pm.infra.libvirt import Disk, Domain, Pool, VmInfo, Volume
from pm.infra.resources import Resources, Row
from pm.infra.servers import Summary, Tile
from pm.infra.servers_model import Container, DockerInfo, HostCheck, Libvirt, Metric, Panel, TmuxSummary
from pm.web import themes
from pm.web.dt import servers, servers_res

from .kit import Tree

THEME = themes.HERE / "dt"
G = 1024**3
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(THEME / "templates"), autoescape=True,
                         undefined=jinja2.StrictUndefined)
ENV.globals["dt_servers"] = servers.dt_servers


def req(path="/servers", **q):
    return NS(url=NS(path=path), query_params=q, path_params={})


def panel(**kw) -> Panel:
    cts = (Container("web-1", "nginx:1", "running", "Up 3 days (healthy)", "healthy", "80/tcp, 443/tcp", "web",
                     "/dev/web"),
           Container("api-1", "app:2", "running", "Up 2 hours (unhealthy)", "unhealthy", "", "web", "/dev/web"),
           Container("init-1", "busy", "exited", "Exited (0) 2 days ago", "", "", "web", "/dev/web"),
           Container("job", "busy", "exited", "Exited (3) 1 hour ago", "", ""),
           Container("nginx-proxy", "nginx", "running", "Up 5 weeks", "", "80/tcp"))
    base = dict(ts=0.0, hostname="ws1", docker=DockerInfo(True, cts, 3, 5, 1),
                metrics=(Metric("Kernel", "6.8"), Metric("Uptime", "3d 2h"),
                         Metric("Load", "6.54 6.28 6.59 on 40 CPU", 16.0, "ok"),
                         Metric("Memory", "59.7 GiB / 125.8 GiB (47%)", 47.0, "ok"),
                         Metric("Disk /", "1.6 TiB / 1.7 TiB (93%)", 93.0, "crit"), Metric("Logins", "4"),
                         Metric("pm service", "active, up 3h 10m", None, "ok"), Metric("tmux server", "tmux 3.4")),
                hosts=(HostCheck("ws2", "10.0.0.2", 22, "UAT", True, 3.0),
                       HostCheck("ws3", "10.0.0.3", 22, "RE", False, None)),
                libvirt=Libvirt(1, 3), tmux=TmuxSummary(True, 4, 10, 20, 1), ntfy_ok=True, ntfy_24h=9)
    return Panel(**{**base, **kw})


def vminfo(**kw) -> VmInfo:
    doms = (Domain("lab-a", "running", 2, 2 * 1024 * 1024, True, True, False,
                   (Disk("vda", "disk", "/img/a.qcow2", 20 * G, 6 * G),)),
            Domain("lab-b", "shut off", 4, 8 * 1024 * 1024, False, True, False, ()),
            Domain("build-vm", "shut off", 8, None, False, True, True, ()))
    vols = (Volume("a.qcow2", "/img/a.qcow2", "file", 20 * G, 6 * G, ("lab-a",)),
            Volume("old.qcow2", "/img/old.qcow2", "file", 20 * G, G, (), True))
    return VmInfo(0, **{"domains": doms, "volumes": vols, "pool": Pool("images", 100 * G, 85 * G, 15 * G), **kw})


def res() -> Resources:
    progs = tuple(Row(f"p{i}", 2, float(i), i * G, 0, ((100 + i, 7, i * G, 1.0, "T" if i == 1 else "S"),))
                  for i in range(1, 13))
    return Resources(0, 4, 16 * G, 8 * G, 120.0, programs=progs, tmux=(Row("dev", 5, 3.0, G, 2),),
                     containers=(), vms=(Row("lab-a", 1, 0.0, 2 * G),), docker_pending=True)


def ctx(tab="overview", path="/servers", **q):
    p = panel()
    c = {"p": p, "tab": tab, "stamp": "19:09:19", "summary": Summary("crit", ()), "can_ct": True,
         "ct_protected": lambda n: n.startswith("nginx"), "vm": vminfo(), "request": req(path, t=tab, **q),
         "res": res(), "rtiles": (Tile("cpu", "CPU", "30%", "ok", "120% of 400%", 30.0),), "can_proc": True,
         "pdesc": lambda pid, start: f"cmd {pid}", "csrf": "x"}
    return c


def items(menu):
    return {i["id"]: i for i in menu if not i.get("sep")}


# ---------------------------------------------------------------- view model
def test_urls_keep_state_and_sort_reverses():
    q = {"t": "services", "f": "exited", "q": "x"}
    assert servers_res.url(q, g="stack") == "/servers?t=services&f=exited&g=stack&q=x"
    cols = servers_res.sort_cols(q, "name", [{"label": "Name", "sort": ("name", False)},
                                              {"label": "Up", "sort": ("up", True)}, {"sr": "A"}])
    assert "so=-name" in cols[0]["href"] and cols[0]["key"] == "name"
    assert "so=-up" in cols[1]["href"] and "href" not in cols[2]  # numbers start descending


def test_page_tabs_stats_and_poll_url():
    v = servers.page(ctx("services", f="unhealthy", so="-up", q="web"))
    assert [t["key"] for t in v["tabs"]] == ["overview", "services", "resources", "vms"]
    sv = next(t for t in v["tabs"] if t["key"] == "services")
    assert sv["cur"] and sv["count"] == "3/5" and sv["bad"]
    assert v["stats"][0] == "ws1" and v["stats"][1] == {"text": "Needs attention", "cls": "bad"}
    assert v["panel_url"] == "/servers/panel?t=services&f=unhealthy&so=-up"  # q stays in the box, not the poll
    assert not v["fragment"] and v["q"] == "web"


def test_overview_status_attention_and_meters():
    o = servers.page(ctx())["ov"]
    assert o["status"]["word"] == "Needs attention" and "1 of 2 hosts reachable" in o["status"]["note"]
    texts = " | ".join(a["text"] for a in o["attn"])
    assert "1 unhealthy: api-1" in texts and "exited with an error" in texts and "job" in texts
    assert "one-shot" in texts and "ws3" in texts and "Disk /" in texts
    assert "old.qcow2 is not used by any VM" in texts
    hrefs = {a["href"] for a in o["attn"]}
    assert {"/servers?t=services&f=unhealthy", "#sv-hosts", "/servers?t=vms&im=unused"} <= hrefs
    m = {x["key"]: x for x in o["meters"]}
    assert m["load"]["value"] == "6.54" and m["load"]["small"] == "6.28 6.59" and "40 CPUs" in m["load"]["sub"]
    assert m["disk-/"]["level"] == "crit" and m["disk-/"]["sub"] == "1.6 TiB / 1.7 TiB"
    assert m["containers"]["href"] == "/servers?t=services" and m["containers"]["level"] == "crit"
    assert m["vms"]["value"] == "1" and m["vms"]["small"] == "/ 3" and m["tmux"]["href"] == "/tmux"
    assert [h["dot"] for h in o["hosts"]] == ["running", "failed"]


def test_services_filter_groups_and_locks():
    sv = servers.page(ctx("services"))["sv"]
    assert [r["name"] for r in sv["rows"]] == ["api-1", "init-1", "job", "nginx-proxy", "web-1"]
    b = {r["name"]: r["bucket"] for r in sv["rows"]}
    assert b == {"api-1": "unhealthy", "init-1": "exited", "job": "exited", "nginx-proxy": "running",
                 "web-1": "running"}
    ng = items(next(r for r in sv["rows"] if r["name"] == "nginx-proxy")["menu"])
    assert all(ng[k]["disabled"] and "protected" in ng[k]["why"] for k in ("restart", "stop", "kill"))
    web = next(r for r in sv["rows"] if r["name"] == "web-1")
    wm = items(web["menu"])
    assert web["vis"]["id"] == "restart" and wm["kill"]["danger"] and wm["kill"]["post"].endswith("/web-1/kill")
    assert wm["stack"]["post"] == "/servers/stacks/web/restart" and "web" in wm["stack"]["confirm"]
    assert wm["start"]["disabled"] and web["up"] == "3d" and web["ports"] == 2
    job = items(next(r for r in sv["rows"] if r["name"] == "job")["menu"])
    assert job["start"]["post"].endswith("/job/start") and job["kill"]["disabled"]
    ex = servers.page(ctx("services", f="exited", g="stack"))["sv"]
    assert [r["name"] for r in ex["rows"]] == ["init-1", "job"]
    assert [g["label"] for g in ex["groups"]] == ["web", "No stack"]
    assert ex["groups"][0]["actions"][0]["attrs"] == {"data-sv-stack": "web"} and ex["groups"][1]["actions"] is None
    off = servers.page({**ctx("services"), "can_ct": False})["sv"]
    assert all(items(r["menu"])["stop"]["disabled"] for r in off["rows"]) and not off["groups"]


def test_resources_top_ten_sort_and_typed_signals():
    r = servers.page(ctx("resources"))["res"]
    assert r["total"] == 12 and r["shown"] == 10 and r["rows"][0]["name"] == "p12"  # RAM, biggest first
    assert r["all_href"].endswith("all=1") and r["rows"][0]["share"] == 100
    m = items(r["rows"][0]["menu"])
    assert m["term"]["post"] == "/servers/proc/program/term" and m["term"]["typed"] == "p12"
    assert m["kill"]["vals"] == {"program": "p12", "confirm": "p12"} and m["kill"]["danger"]
    procs = r["rows"][-1]["procs"]
    assert procs[0]["desc"].startswith("cmd")
    pm_ = items(next(x for x in servers.page(ctx("resources", all="1"))["res"]["rows"] if x["name"] == "p1")
                ["procs"][0]["menu"])
    assert pm_["kill"]["typed"] == "101" and "101" in pm_["kill"]["confirm"] and pm_["cont"]["post"].endswith("/cont")
    by_name = servers.page(ctx("resources", so="name"))["res"]["rows"]
    assert by_name[0]["name"] == "p1"
    c = servers.page(ctx("resources", r="containers"))["res"]
    assert c["text"] == "Collecting container stats…" and not c["rows"]
    segs = servers_res.res_segs(res(), {"r": "tmux"})
    assert [s["count"] for s in segs] == [12, 1, "…", 1] and segs[1]["cur"]
    t = items(servers.page(ctx("resources", r="tmux"))["res"]["rows"][0]["menu"])
    assert t["open"]["href"] == "/tmux?q=dev"
    ro = items(servers.page({**ctx("resources"), "can_proc": False})["res"]["rows"][0]["menu"])
    assert ro["term"]["disabled"] and "proc_manage" in ro["term"]["why"]


def test_vms_sort_pool_images_and_typed_menus():
    v = servers_res.vms(vminfo(), {"so": "-cpu"})
    assert [r["name"] for r in v["rows"]] == ["build-vm", "lab-b", "lab-a"]
    a = items(next(r for r in v["rows"] if r["name"] == "lab-a")["menu"])
    assert a["destroy"]["typed"] == "lab-a" and a["destroy"]["post"] == "/servers/vms/lab-a/destroy"
    assert a["undefine"]["disabled"] and a["undefine"]["why"] == "shut it down first"
    b = items(next(r for r in v["rows"] if r["name"] == "lab-b")["menu"])
    assert b["undefine"]["typed"] == "lab-b" and b["undefine"]["vals"] == {"confirm": "lab-b"}
    e = items(next(r for r in v["rows"] if r["name"] == "build-vm")["menu"])
    assert e["start"]["disabled"] and e["start"]["why"] == "protected VM"
    assert v["pool"]["level"] == "warn" and round(v["pool"]["pct"]) == 85
    assert [s["count"] for s in v["img_segs"]] == [2, 1]
    assert "so=-cpu" in v["img_segs"][1]["href"] and "t=vms" in v["img_segs"][1]["href"]
    un = servers_res.vms(vminfo(), {"im": "unused"})
    assert [i["name"] for i in un["images"]] == ["old.qcow2"]
    d = items(un["images"][0]["menu"])["delete"]
    assert d["typed"] == "old.qcow2" and d["post"] == "/servers/images/old.qcow2/delete"
    used = items(servers_res.vms(vminfo(), {})["images"][0]["menu"])["delete"]
    assert used["disabled"] and "lab-a" in used["why"]
    assert servers_res.vms(VmInfo(0, available=False, error="no virsh"), {})["text"].endswith("no virsh.")


# ---------------------------------------------------------------- templates
def render(name, c):
    return ENV.get_template(name).render(**c)


def test_panel_fragment_swaps_the_head_stats_out_of_band():
    html = render("_servers.html", ctx("overview", path="/servers/panel"))
    assert 'hx-swap-oob="innerHTML:#sv-head-stats"' in html and "Needs attention" in html
    page_part = render("_servers.html", ctx("overview"))
    assert "hx-swap-oob" not in page_part  # the page renders the head itself


def test_overview_markup():
    root = Tree(render("_servers.html", ctx())).root
    assert root.one("p-status").attrs["role"] == "status"
    links = [a.attrs.get("href") for a in root.by_cls("p-attn-i")]
    assert "/servers?t=services&f=unhealthy" in links
    cells = root.one("p-sum").by_cls("p-m")
    assert len(cells) >= 8 and any(c.tag == "a" for c in cells)
    bars = root.find(lambda n: n.tag == "progress")
    assert bars and all(b.attrs["max"] == "100" for b in bars) and "style" not in render("_servers.html", ctx())
    assert [r.attrs["data-row-id"] for r in root.one("sv-hosts").by_cls("p-gr")] == ["ws2", "ws3"]


def test_services_markup_rows_details_and_band_actions():
    root = Tree(render("_servers.html", ctx("services", g="stack"))).root
    rows = root.by_cls("p-gr")
    assert {r.attrs["data-row-id"] for r in rows} == {"web-1", "api-1", "init-1", "job", "nginx-proxy"}
    for r in rows:
        assert all(k.attrs.get("role") == "cell" for k in r.kids) and r.attrs["data-p-q"]
    assert len(root.by_cls("p-det")) == 5
    assert root.find(lambda n: n.attrs.get("data-sv-stack") == "web")
    menu = json.loads(rows[0].by_cls("p-mbtn")[0].attrs["data-menu"])
    assert all(i.get("refresh") == "#sv-panel" for i in menu if i.get("post"))


def test_resources_and_vms_markup():
    root = Tree(render("_servers.html", ctx("resources"))).root
    assert len(root.by_cls("p-gr")) == 10 and len(root.by_cls("p-foot")) == 2
    assert "Show all 12" in render("_servers.html", ctx("resources"))
    assert root.find(lambda n: n.tag == "progress" and "in" in n.cls)
    vroot = Tree(render("_vms.html", {"vm": vminfo(), "request": req("/servers/vms", so="name"), "csrf": "x"})).root
    assert [r.attrs["data-row-id"] for r in vroot.one("sv-vm").by_cls("p-gr")] == ["build-vm", "lab-a", "lab-b"]
    assert vroot.one("p-sum").cls >= {"p-sum", "one"} and len(vroot.one("sv-img").by_cls("p-gr")) == 2
    off = render("_vms.html", {"vm": VmInfo(0, available=False), "request": req("/servers/vms")})
    assert "libvirt is not available" in off


def test_meter_part_levels_and_clamp():
    t = ENV.from_string('{% import "parts/_meter.html" as m %}{{ m.meters(items) }}|{{ m.bar(140, inline=true) }}')
    html = t.render(items=[{"label": "Disk", "value": "93%", "pct": 93, "level": "crit", "sub": "x"},
                           {"label": "ntfy", "value": "n/a", "pct": 10, "level": "na"},
                           {"label": "VMs", "value": "1", "small": "/ 3", "href": "/servers?t=vms"}])
    root = Tree(html).root
    a, b, c = root.by_cls("p-m")
    assert "red" in a.cls and "(critical)" in a.get_text() and a.find(lambda n: n.tag == "progress")
    assert "na" in b.cls and not b.find(lambda n: n.tag == "progress")
    assert c.tag == "a" and c.attrs["href"] == "/servers?t=vms"
    assert re.search(r'class="bar in" max="100" value="100" aria-hidden="true"', html)


# ---------------------------------------------------------------- page frame and script rules
def test_page_frame_poll_and_assets():
    src = (THEME / "templates" / "servers.html").read_text()
    assert re.search(r'<div id="sv-panel"[^>]*hx-trigger="every 10s, pm7:refresh, res-changed from:body', src)
    assert '{% block mainclass %}{% endblock %}' in src and "css/servers.css" in src and "js/servers.js" in src
    assert 'id="sv-vms"' in src and "every" not in src.split('id="sv-vms"')[1].split(">")[0]


def test_servers_js_rules():
    js = (THEME / "static" / "js" / "servers.js").read_text()
    assert "setInterval" not in js and "setTimeout" not in js and not re.search(r"(?<![.\w])fetch\(", js)
    assert "innerHTML" not in js and "pm7.confirm" not in js and "addEventListener('keydown'" not in js
    assert "d.hidden" in js and "visibilitychange" in js and "pm7.menus.run" in js and "if (!pm7 || pm7.servers)" in js
    assert max(len(ln) for ln in js.splitlines()) <= 120


@pytest.mark.parametrize("name", ["servers.html", "_servers.html", "_resources.html", "_vms.html"])
def test_overrides_exist_for_every_core_template(name):
    assert (THEME / "templates" / name).is_file()


def test_gated_menu_item():
    from pm.web.dt import menus
    assert menus.gated("a", "A", why="off", need=False, need_why="x", post="/p") == {
        "id": "a", "label": "A", "disabled": True, "why": "off"}
    assert menus.gated("a", "A", need=False, need_why="not running", danger=True, post="/p")["why"] == "not running"
    assert menus.gated("a", "A", post="/p", done="ok") == {"id": "a", "label": "A", "post": "/p", "done": "ok"}
