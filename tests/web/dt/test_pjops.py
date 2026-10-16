"""dt project view, Git / Stack / VMs tabs (docs/themes/dt/PRD.md §6.4, PARTS.md §14): the VMs view model
(pm.web.dt.pjops), the dt _project_vms.html fragment, the three tab partials (content only, own assets), and the
rules of their scripts: every git mutation asks the plan first, push is gitdev's route only, no force path, no
innerHTML, no own fetch / timer. The shared helpers of js/pjops.js (params, result) run in node."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import jinja2
import pytest

from pm.infra.libvirt import Disk, Domain, VmInfo
from pm.web import themes
from pm.web.dt import pjops

from .kit import Tree

THEME = themes.HERE / "dt"
JS = THEME / "static" / "js"
OPS_JS = {k: (JS / f"pj{k}.js").read_text() for k in ("ops", "git", "stack", "vms")}
G = 1024**3

ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(THEME / "templates"), autoescape=True,
                         undefined=jinja2.StrictUndefined)
ENV.globals["dt_pjops"] = pjops.dt_pjops


def doms():
    return (Domain("fix-web", "running", 2, 2 * 1024 * 1024, True, True, False,
                   (Disk("vda", "disk", "/img/web.qcow2", 20 * G, 6 * G),)),
            Domain("fix-lab", "shut off", 4, 8 * 1024 * 1024, False, True, False,
                   (Disk("vda", "disk", "/img/a.qcow2", 16 * G, None), Disk("vdb", "disk", "/img/b.qcow2", 4 * G))),
            Domain("fix-vm1", "running", 8, None, False, True, True, ()))


def items(menu):
    return {i["id"]: i for i in menu if not i.get("sep")}


# ---------------------------------------------------------------- view model
def test_states_without_rows():
    assert pjops.view(None, (), "p")["state"] == "none"
    off = pjops.view(VmInfo(0, available=False, error="virsh missing"), ("fix-*",), "p")
    assert off["state"] == "off" and "virsh missing" in off["text"]
    empty = pjops.view(VmInfo(0, ()), ("fix-*",), "p")
    assert empty["state"] == "empty" and "fix-*" in empty["text"] and not empty["rows"]


def test_rows_words_and_visible_action():
    v = pjops.view(VmInfo(0, doms()), ("fix-*",), "my proj")
    assert v["state"] == "ok" and v["stats"].startswith("2 of 3 running")
    web, lab, prot = v["rows"]
    assert (web["dot"], lab["dot"]) == ("running", "idle")
    assert web["act"]["url"] == "/p/my%20proj/vms/fix-web/shutdown" and web["act"]["label"] == "Shut down"
    assert lab["act"]["url"].endswith("/fix-lab/start") and lab["disk"].endswith("+1")
    assert prot["act"] is None and prot["protected"] and prot["ram"] == "?"  # protected: no visible action


def test_menu_typed_destroy_and_disabled_reasons():
    web, lab, prot = pjops.view(VmInfo(0, doms()), ("fix-*",), "p")["rows"]
    m = items(web["menu"])
    off = m["off"]
    assert off["typed"] == "fix-web" and off["vals"] == {"confirm": "fix-web"} and "fix-web" in off["confirm"]
    assert off["post"] == "/p/p/vms/fix-web/force-off" and off["danger"]
    assert m["del"]["disabled"] and m["del"]["why"] == "shut it down first"  # undefine needs shut off
    assert m["det"]["det"] is True and m["revert"]["event"] == "pjv:revert"
    lm = items(lab["menu"])
    assert lm["del"]["typed"] == "fix-lab" and lm["del"]["post"].endswith("/fix-lab/delete")
    assert lm["off"]["disabled"] and lm["off"]["why"] == "not running"
    em = items(prot["menu"])
    assert all(em[k]["disabled"] and em[k]["why"] == "protected VM" for k in ("start", "stop", "snap", "off", "del"))
    ro = items(pjops.view(VmInfo(0, doms(), writable=False), ("fix-*",), "p")["rows"][0]["menu"])
    assert ro["off"]["disabled"] and "vm_manage" in ro["off"]["why"]


# ---------------------------------------------------------------- templates
def render_vms(vm, patterns=("fix-*",)):
    req = NS(path_params={"name": "p"})
    return ENV.get_template("_project_vms.html").render(vm=vm, patterns=patterns, request=req, csrf="x")


def test_vms_fragment_rows_are_grid_rows():
    root = Tree(render_vms(VmInfo(0, doms()))).root
    rows = root.by_cls("p-gr")
    assert [r.attrs["data-row-id"] for r in rows] == ["fix-web", "fix-lab", "fix-vm1"]
    for r in rows:
        assert all(k.attrs.get("role") == "cell" for k in r.kids)
    post = [n.attrs["data-pjv-post"] for n in root.find(lambda n: "data-pjv-post" in n.attrs)]
    assert post == ["/p/p/vms/fix-web/shutdown", "/p/p/vms/fix-lab/start"]
    menu = json.loads(rows[0].by_cls("p-mbtn")[0].attrs["data-menu"])
    assert any(i.get("typed") == "fix-web" for i in menu)
    assert len(root.by_cls("p-det")) == 3 and root.find(lambda n: "data-pjv-live" in n.attrs)


@pytest.mark.parametrize("vm,patterns,word", [(None, (), "patterns"), (VmInfo(0, ()), ("fix-*",), "No VM matches"),
                                              (VmInfo(0, available=False), ("fix-*",), "libvirt")])
def test_vms_fragment_empty_states(vm, patterns, word):
    root = Tree(render_vms(vm, patterns)).root
    assert not root.by_cls("p-gr") and word in root.one("p-empty").get_text()
    assert "New VM" not in render_vms(vm, patterns)


@pytest.mark.parametrize("key", ["git", "stack", "vms"])
def test_partials_are_content_only_with_own_assets(key):
    html = ENV.get_template(f"project/_tab_{key}.html").render(project=NS(name="a b"))
    assert "<section" not in html and 'class="pj-tab"' not in html  # the frame renders the panel
    root = Tree(html).root
    top = root.find(lambda n: n.attrs.get("data-pjops") == key)
    assert len(top) == 1 and top[0].attrs["data-pj-base"] == "/p/a%20b"
    srcs = [n.attrs["src"] for n in root.find(lambda n: n.tag == "script")]
    assert srcs == ["/static/t/dt/js/pjops.js", f"/static/t/dt/js/pj{key}.js"]
    assert all((THEME / "static" / s.removeprefix("/static/t/dt/")).is_file() for s in srcs)
    assert "Add a stack" not in html and "New VM" not in html


# ---------------------------------------------------------------- script rules
def test_scripts_use_dom_text_only_and_shared_helpers():
    for name, src in OPS_JS.items():
        assert "innerHTML" not in src and "outerHTML" not in src and "insertAdjacentHTML" not in src, name
        assert not re.search(r"(?<![.\w])fetch\(", src), name  # pm7.fetch only
        assert "setInterval" not in src and "setTimeout" not in src, name  # no timers (V7-CODE-7)
        assert "addEventListener('keydown'" not in src, name
        assert "if (!pm7 || pm7.pj" in src, name  # once-guard: several partials load the same files


def test_every_git_mutation_asks_the_plan_first():
    src = OPS_JS["git"]
    posts = set(re.findall(r"'(/git/[a-z-]+)'", src))
    assert posts == {"/git/stage", "/git/unstage", "/git/discard", "/git/commit", "/git/stash", "/git/stash-pop",
                     "/git/stash-drop", "/git/checkout", "/git/merge", "/git/branch-delete", "/git/pull",
                     "/git/pull-stash", "/git/push", "/git/fetch", "/git/status"}
    assert src.count("P().post(") == 2  # run() (after plan + question) and Fetch (no plan in the core)
    actions = set(re.findall(r"action: (?:on \? )?'([a-z-]+)'", src)) | {"unstage"}
    assert {"stage", "unstage", "discard", "commit", "stash", "stash-pop", "stash-drop", "checkout", "merge",
            "branch-delete", "pull", "push"} <= actions


def test_no_force_push_path():
    src = OPS_JS["git"]
    # no force flag, form field or URL anywhere (prose like "never forced" is fine)
    assert not re.search(r"--force|-f\b|force[_:-]?\s*[:=]|/force|force_with_lease|['\"]force", src), "a force path"
    assert re.findall(r"'/git/push[^']*'", src) == ["'/git/push'"]


def test_typed_questions_use_the_plans_value():
    src = OPS_JS["git"]
    assert "p.risk === 'typed' ? p.confirm" in src and "form.confirm = p.confirm" in src


# ---------------------------------------------------------------- node: pjops.js helpers
NODE = r"""
globalThis.window = { pm7: { why: async (r, w) => w + ' (' + r.status + ')' } };
globalThis.document = { addEventListener() {} };
eval(require('fs').readFileSync(process.argv[1], 'utf8'));
const P = window.pm7.pjops;
(async () => {
  const out = {};
  out.params = P.params({ paths: ['a', 'b c'], untracked: null, branch: 'x' }).toString();
  out.ok = await P.result(new Response(JSON.stringify({ ok: true, text: 'Staged 1 file.', clash: ['s.css'] }),
    { headers: { 'Content-Type': 'application/json' } }), 'Stage');
  out.bad = await P.result(new Response(JSON.stringify({ ok: false, error: 'nope', files: ['f'] }),
    { status: 409, headers: { 'Content-Type': 'application/json' } }), 'Merge');
  out.none = await P.result(null, 'Push');
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_pjops_params_and_results_in_node():
    got = json.loads(subprocess.run(["node", "-e", NODE, str(JS / "pjops.js")], capture_output=True, text=True,
                                    check=True).stdout)
    assert got["params"] == "paths=a&paths=b+c&branch=x"
    assert got["ok"] == {"ok": True, "text": "Staged 1 file.", "clash": ["s.css"], "files": []}
    assert got["bad"]["ok"] is False and got["bad"]["files"] == ["f"] and got["bad"]["text"] == "Merge (409)"
    assert got["none"]["ok"] is False and "no answer" in got["none"]["text"]
