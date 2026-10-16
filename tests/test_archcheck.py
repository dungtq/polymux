"""tools/archcheck.py: the repo passes against its baseline, and new offenders fail."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "archcheck.py"
BASELINE = ROOT / "tools" / "archcheck_baseline.json"


def _run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60, check=False)


def _tree(tmp_path: Path, files: dict[str, str], baseline: list[str] | None = None,
          counts: dict[str, int] | None = None) -> tuple[Path, Path]:
    pkg = tmp_path / "src" / "pm"
    (pkg / "static" / "js").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for rel, text in files.items():
        p = pkg / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps({"_comment": "x", "entries": baseline or [], "counts": counts or {}}))
    return tmp_path, bl


def _check(root: Path, bl: Path, *extra: str) -> subprocess.CompletedProcess:
    return _run("--root", str(root), "--baseline", str(bl), *extra)


def test_repo_passes_against_baseline():
    # the same call as tools/check.sh: src/pm plus the dt theme's static tree
    r = subprocess.run([sys.executable, "tools/archcheck.py", "--root", ".",
                        "--static-dir", "src/pm/web/themes/dt/static"], cwd=ROOT, capture_output=True,
                       text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "archcheck: OK" in r.stdout


def test_long_module_is_new(tmp_path):
    root, bl = _tree(tmp_path, {"big.py": "x = 1\n" * 601, "ok.py": "x = 1\n" * 600})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW module-lines:big.py (601 lines" in r.stdout
    assert "ok.py" not in r.stdout
    assert "archcheck: FAIL (1 new)" in r.stdout


def test_long_function_and_asset(tmp_path):
    body = "".join(f"            a{i} = {i}\n" for i in range(80))
    src = f"class C:\n    def m(self):\n        def inner():\n{body}\n    def short(self):\n        pass\n"
    root, bl = _tree(tmp_path, {"f.py": src, "static/js/big.js": "x;\n" * 501})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW func-lines:f.py:C.m.inner (81 lines" in r.stdout
    assert "NEW asset-lines:static/js/big.js" in r.stdout


def _handler(deco: str, n: int, name: str = "h") -> str:
    """A function of exactly ``n`` lines (decorator line excluded) under ``deco`` ("" = none)."""
    body = "".join(f"    a{i} = {i}\n" for i in range(n - 1))
    return (f"{deco}\n" if deco else "") + f"async def {name}():\n{body}"


def test_long_route_handler(tmp_path):
    routes = (_handler("@router.get('/a')", 41, "long_get") + _handler("@router.post('/b')", 40, "ok_post")
              + _handler("@app.post('/c')", 41, "long_app") + _handler("", 60, "helper"))
    root, bl = _tree(tmp_path, {**_pkgs("web", "web/routes", "agents"), "web/routes/r.py": routes,
                                "agents/x.py": _handler("@router.get('/d')", 60, "outside")})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW route-lines:web/routes/r.py:long_get (41 lines > 40" in r.stdout
    assert "NEW route-lines:web/routes/r.py:long_app (41 lines" in r.stdout
    assert "ok_post" not in r.stdout and "helper" not in r.stdout
    assert "route-lines:agents/" not in r.stdout
    assert "archcheck: FAIL (2 new)" in r.stdout
    root2, bl2 = _tree(tmp_path / "b", {**_pkgs("web"), "web/r.py": _handler("@router.websocket('/w')", 41)},
                       ["route-lines:web/r.py:h"])
    assert _check(root2, bl2).returncode == 0


def test_domain_importing_fastapi_is_layer_violation(tmp_path):
    root, bl = _tree(tmp_path, {
        "dom.py": "from fastapi import HTTPException\n",
        "dom2.py": "from .web import render\n",
        "web/__init__.py": "",
        "web/r.py": "import fastapi\n",
        "__init__.py": "def main():\n    from pm.web.app import create_app\n",
        "core/prefs.py": "import jinja2\n",
    })
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW layer:dom.py:fastapi" in r.stdout
    assert "NEW layer:dom2.py:pm.web" in r.stdout
    assert "web/r.py" not in r.stdout
    assert "layer:__init__.py" not in r.stdout
    assert "NEW layer:core/prefs.py:jinja2" in r.stdout  # core is domain side since the Phase 3 move


def test_subprocess_and_extras(tmp_path):
    root, bl = _tree(tmp_path, {
        "a.py": "def f():\n    import subprocess\n    subprocess.run(['ls'])\n",
        "b.py": "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('ls')\n",
        "c.py": "def f(s):\n    return s.extras.get('k1'), s.extras['k2'], launch._extras(s).get('k3')\n",
        "d.py": "import subprocess\nx: subprocess.Popen\ndef f(p: subprocess.Popen) -> subprocess.Popen:\n"
                "    return subprocess.TimeoutExpired\n",
    })
    r = _check(root, bl)
    assert "NEW subprocess:a.py (1 process-spawning call sites > baseline 0)" in r.stdout
    assert "NEW subprocess:b.py (1 " in r.stdout
    assert "d.py" not in r.stdout  # import + annotations + exceptions are not call sites
    for k in ("k1", "k2", "k3"):
        assert f"NEW extras:c.py:{k}" in r.stdout


SPAWN_SITES = {
    "alias.py": "import subprocess as sp\nsp.Popen(['x'])\nsp.check_output(['x'])\nsp.getoutput('x')\n",
    "fromimp.py": "from subprocess import run as r, call\nr(['x'])\ncall(['x'])\n",
    "aio.py": "from asyncio import create_subprocess_shell as c\nasync def f(loop):\n"
              "    await c('x')\n    await loop.subprocess_exec(p, 'x')\n    await loop.subprocess_shell(p, 'x')\n",
    "osmod.py": "import os\nfrom os import system as s2, execvp\nos.system('x')\nos.popen('x')\n"
                "os.execv('x', [])\nos.spawnlp(0, 'x')\nos.posix_spawnp('x', [], {})\ns2('x')\nexecvp('x', [])\n",
    "ptymod.py": "import pty\nfrom pty import fork\npty.spawn(['x'])\nfork()\n",
    "dyn.py": "import importlib\nm = importlib.import_module('subprocess')\nn = __import__('subprocess')\n"
              "importlib.import_module('json')\n",
    "ref.py": "import subprocess\nasync def f(loop):\n    await loop.run_in_executor(None, subprocess.run, ['x'])\n",
}


def test_spawn_sites_counted_through_aliases(tmp_path):
    root, bl = _tree(tmp_path, SPAWN_SITES)
    r = _check(root, bl)
    want = {"alias.py": 3, "fromimp.py": 2, "aio.py": 3, "osmod.py": 7, "ptymod.py": 2, "dyn.py": 2, "ref.py": 1}
    for rel, n in want.items():
        assert f"NEW subprocess:{rel} ({n} process-spawning" in r.stdout, r.stdout


def test_spawn_count_grows_shrinks_and_fixed(tmp_path):
    two = "import subprocess\nsubprocess.run(['a'])\nsubprocess.run(['b'])\n"
    root, bl = _tree(tmp_path, {"g.py": two, "l.py": two, "z.py": "x = 1\n"},
                     counts={"subprocess:g.py": 1, "subprocess:l.py": 3, "subprocess:z.py": 1})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW subprocess:g.py (2 process-spawning call sites > baseline 1)" in r.stdout
    assert "LOWER (update baseline count): subprocess:l.py (2 < 3)" in r.stdout
    assert "FIXED (remove from baseline): subprocess:z.py" in r.stdout
    assert "archcheck: FAIL (1 new)" in r.stdout
    root2, bl2 = _tree(tmp_path / "b", {"l.py": two}, counts={"subprocess:l.py": 3})
    assert _check(root2, bl2).returncode == 0
    assert _check(root2, bl2, "--strict").returncode == 1


def test_spawn_helper_is_exempt_but_shell_still_checked(tmp_path):
    spawn = "import subprocess\nsubprocess.run(['a'])\n"
    root, bl = _tree(tmp_path, {"core/__init__.py": "", "core/proc.py": spawn, "core/other.py": spawn})
    r = _check(root, bl)
    assert "core/proc.py" not in r.stdout
    assert "NEW subprocess:core/other.py (1 " in r.stdout
    root2, bl2 = _tree(tmp_path / "b", {"core/proc.py": "import subprocess\nsubprocess.run(['a'], shell=True)\n"})
    assert "ERROR shell-true:core/proc.py:2" in _check(root2, bl2).stdout


def test_shell_true_errors_even_if_baselined(tmp_path):
    files = {"s.py": "import subprocess\nsubprocess.run(['ls'], shell=True)\n"}
    root, bl = _tree(tmp_path, files, ["shell-true:s.py:2"], {"subprocess:s.py": 1})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "shell-true:s.py:2" in r.stdout
    assert "archcheck: FAIL" in r.stdout


def test_shell_must_be_constant_false(tmp_path):
    files = {"s.py": "import subprocess\nsubprocess.run(['ls'], shell=flag)\nsubprocess.run(['ls'], shell=1)\n"
                     "subprocess.run(['ls'], shell=False)\n"}
    root, bl = _tree(tmp_path, files, counts={"subprocess:s.py": 3})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "ERROR shell-true:s.py:2" in r.stdout and "ERROR shell-true:s.py:3" in r.stdout
    assert "s.py:4" not in r.stdout


def test_assets_recursive_skip_vendor(tmp_path):
    big = "x;\n" * 501
    root, bl = _tree(tmp_path, {"static/js/sub/deep.js": big, "static/css/a/b.css": big, "static/top.css": big,
                                "static/vendor/v.js": big, "static/js/vendor/w.js": big})
    r = _check(root, bl)
    for rel in ("static/js/sub/deep.js", "static/css/a/b.css", "static/top.css"):
        assert f"NEW asset-lines:{rel}" in r.stdout
    assert "vendor" not in r.stdout


def test_stale_entry_is_reported_and_fails_only_strict(tmp_path):
    root, bl = _tree(tmp_path, {"ok.py": "x = 1\n"}, ["module-lines:gone.py"])
    r = _check(root, bl)
    assert r.returncode == 0, r.stdout
    assert "FIXED (remove from baseline): module-lines:gone.py" in r.stdout
    assert _check(root, bl, "--strict").returncode == 1


def test_write_baseline_skips_shell_true(tmp_path):
    files = {"s.py": "import subprocess\nsubprocess.run(['ls'], shell=True)\n", "big.py": "x = 1\n" * 601}
    root, bl = _tree(tmp_path, files)
    _check(root, bl, "--write-baseline")
    data = json.loads(bl.read_text())
    assert data["entries"] == ["module-lines:big.py"]
    assert data["counts"] == {"subprocess:s.py": 1}


def _pkgs(*names: str) -> dict[str, str]:
    return {f"{n}/__init__.py": "" for n in names}


def test_allowed_and_same_package_edges_pass(tmp_path):
    root, bl = _tree(tmp_path, {
        **_pkgs("core", "security", "chat", "web", "web/routes"),
        "core/fsutil.py": "from pm.core import errors\n",
        "core/errors.py": "",
        "security/mask.py": "from pm.core.fsutil import x\n",
        "chat/live.py": "import pm.security.mask\nfrom . import log\n",
        "chat/log.py": "",
        "web/routes/r.py": "from pm.chat import live\nfrom ...security import mask\nfrom pm import core\n",
        "__init__.py": "from pm.web.routes import r\n",
    })
    r = _check(root, bl)
    assert r.returncode == 0, r.stdout
    assert "pkg-" not in r.stdout and "cycle" not in r.stdout


def test_disallowed_edge_fails(tmp_path):
    root, bl = _tree(tmp_path, {**_pkgs("core", "chat"), "core/a.py": "import os\nfrom pm.chat.x import y\n",
                                "chat/x.py": ""})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW pkg-edge:core->chat:core/a.py:2" in r.stdout


def test_local_and_type_checking_imports_count(tmp_path):
    root, bl = _tree(tmp_path, {
        **_pkgs("tmux", "git", "agents"),
        "tmux/a.py": "def f():\n    from pm.agents import b\n",
        "git/c.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pm.tmux.a import f\n",
        "agents/b.py": "",
    })
    r = _check(root, bl)
    assert "NEW pkg-edge:tmux->agents:tmux/a.py:2" in r.stdout
    assert "NEW pkg-edge:git->tmux:git/c.py:3" in r.stdout


def test_relative_import_resolution(tmp_path):
    files = {**_pkgs("core", "chat", "chat/sources", "git"), "core/fsutil.py": "", "git/g.py": "",
             "chat/sources/x.py": "from ..core import fsutil\n"}
    root, bl = _tree(tmp_path, {**files, "chat/sources/y.py": "from ...core import fsutil\nfrom ...git import g\n"})
    r = _check(root, bl)
    # ``..core`` from chat/sources is pm.chat.core (does not exist, same package); ``...core`` is pm.core.
    assert "chat/sources/x.py" not in r.stdout
    assert "NEW pkg-edge:chat->git:chat/sources/y.py:2" in r.stdout
    assert "chat/sources/y.py:1" not in r.stdout


def test_module_cycle_fails(tmp_path):
    root, bl = _tree(tmp_path, {**_pkgs("core"), "core/a.py": "from pm.core import b\n",
                                "core/b.py": "def f():\n    from . import a\n"})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW cycle:pm.core.a -> pm.core.b -> pm.core.a" in r.stdout


def test_package_cycle_reported(tmp_path):
    root, bl = _tree(tmp_path, {**_pkgs("core", "git"), "core/a.py": "import pm.git.b\n",
                                "git/b.py": "import pm.core.c\n", "core/c.py": ""})
    r = _check(root, bl)
    assert "NEW pkg-edge:core->git:core/a.py:1" in r.stdout
    assert "NEW pkg-cycle:core -> git -> core" in r.stdout
    assert "NEW cycle:" not in r.stdout


def test_web_may_import_anything_and_unknown_package(tmp_path):
    root, bl = _tree(tmp_path, {**_pkgs("web", "infra", "misc"), "web/app.py": "import pm.infra.s\n",
                                "infra/s.py": "", "misc/m.py": ""})
    r = _check(root, bl)
    assert "web/app.py" not in r.stdout
    assert "NEW pkg-unknown:misc/__init__.py" in r.stdout and "NEW pkg-unknown:misc/m.py" in r.stdout


def test_structural_keys_never_baselinable(tmp_path):
    files = {**_pkgs("core", "chat"), "core/a.py": "import pm.chat.x\n", "chat/x.py": ""}
    root, bl = _tree(tmp_path, files, ["pkg-edge:core->chat:core/a.py:1"])
    r = _check(root, bl)
    assert r.returncode == 1
    assert "ERROR baseline:pkg-edge:core->chat:core/a.py:1" in r.stdout
    assert "NEW pkg-edge:core->chat:core/a.py:1" in r.stdout
    root2, bl2 = _tree(tmp_path / "b", files)
    assert _check(root2, bl2, "--write-baseline").returncode == 1
    assert json.loads(bl2.read_text())["entries"] == []


def test_baseline_has_no_shell_true():
    data = json.loads(BASELINE.read_text())
    assert "only shrink" in data["_comment"]
    assert not [k for k in data["entries"] if k.startswith(("shell-true", "subprocess:", "pkg-", "cycle:"))]
    assert data["entries"] == sorted(data["entries"])
    assert list(data["counts"]) == sorted(data["counts"])
    assert all(k.startswith(("subprocess:", "css-color:", "js-csrf:", "js-fetch:")) and isinstance(v, int) and v > 0
               for k, v in data["counts"].items())
    assert not [k for k in data["entries"] + list(data["counts"]) if k.startswith("css-root-token:")]


def test_css_root_token_outside_tokens_errors_even_if_baselined(tmp_path):
    files = {
        "static/css/tokens.css": ":root{--a:#fff}\n:root[data-theme=dark]{--a:#000}\n",
        "static/css/x.css": "/* :root{--c:1} */\n@media (max-width:700px){\n  :root{--b:1px}\n}\n",
        "static/css/ok.css": ".x{--local:#123; color:var(--local)}\n:root[data-theme=dark] .i-sun{display:inline}\n",
        "static/css/y.css": "html, :root[data-theme=\"light\"]{--z:2}\n",
    }
    root, bl = _tree(tmp_path, files, ["css-root-token:static/css/x.css"])
    r = _check(root, bl)
    assert r.returncode == 1
    assert "ERROR baseline:css-root-token:static/css/x.css" in r.stdout
    assert "NEW css-root-token:static/css/x.css" in r.stdout and "--b" in r.stdout
    assert "NEW css-root-token:static/css/y.css" in r.stdout
    assert "css-root-token:static/css/tokens.css" not in r.stdout
    assert "ok.css" not in r.stdout  # component-local custom props, :root rule without tokens
    root2, bl2 = _tree(tmp_path / "b", {"static/css/tokens.css": files["static/css/tokens.css"]})
    assert _check(root2, bl2).returncode == 0


def test_css_color_count_skips_url_comment_custom_props(tmp_path):
    css = ("/* #fff rgb(1,2,3) */\n#id .a{--c:#abc; background:url(\"data:image/svg+xml,%23fff fill='#000'\")"
           " no-repeat}\n.b{color:#a1b2c3; border:1px solid rgba(0,0,0,.5); fill:hsl(1 2% 3%)}\n"
           ".c{color:#12345; width:calc(100% - 4px)}\n")
    root, bl = _tree(tmp_path, {"static/css/a.css": css, "static/css/tokens.css": ".t{color:#fff}\n"})
    r = _check(root, bl)
    assert "NEW css-color:static/css/a.css (3 colour literals" in r.stdout, r.stdout
    assert "css-color:static/css/tokens.css" not in r.stdout


def test_css_color_count_grows_and_shrinks(tmp_path):
    files = {"static/css/g.css": ".a{color:#fff;background:#000}\n", "static/css/l.css": ".a{color:#fff}\n"}
    root, bl = _tree(tmp_path, files, counts={"css-color:static/css/g.css": 1, "css-color:static/css/l.css": 2})
    r = _check(root, bl)
    assert r.returncode == 1
    assert "NEW css-color:static/css/g.css (2 colour literals outside tokens.css > baseline 1)" in r.stdout
    assert "LOWER (update baseline count): css-color:static/css/l.css (1 < 2)" in r.stdout
    root2, bl2 = _tree(tmp_path / "b", files, counts={"css-color:static/css/g.css": 2,
                                                      "css-color:static/css/l.css": 1})
    assert _check(root2, bl2, "--strict").returncode == 0


def test_js_fetch_and_csrf_counts(tmp_path):
    js = ("// fetch('/x') X-CSRF-Token\n/* fetch(a) */\nvar r = /\\/\\/fetch\\(/;\n"
          "fetch('/a'); pm.fetch('/b'); window.fetch('/c'); refetch('/d');\n"
          "var m = document.querySelector('meta[name=\"csrf-token\"]');\n"
          "var i = form.querySelector('input[name=csrf]'); h['X-CSRF-Token'] = t; var s = 'a // fetch(';\n")
    core = "fetch('/a'); document.querySelector('meta[name=\"csrf-token\"]'); 'X-CSRF-Token';\n"
    root, bl = _tree(tmp_path, {"static/js/a.js": js, "static/js/core.js": core, "static/js/sw.js": "fetch(r);\n",
                                "static/js/vendor/v.js": "fetch(x);\n"})
    r = _check(root, bl)
    assert "NEW js-fetch:static/js/a.js (3 fetch(" in r.stdout, r.stdout  # fetch('/a'), window.fetch('/c') and the string one
    assert "NEW js-csrf:static/js/a.js (3 CSRF" in r.stdout, r.stdout
    assert "static/js/core.js" not in r.stdout and "static/js/sw.js" not in r.stdout and "vendor" not in r.stdout


def _load(name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"{name}_mod", ROOT / "tools" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_js_fetch_catches_window_and_spaced_forms():
    rx = _load("archcheck_front")._JS_FETCH
    for hit in ("fetch(u)", "window.fetch(u)", "self.fetch(u)", "fetch (u)", "globalThis.fetch(u)"):
        assert rx.search(hit), hit
    for miss in ("pm.fetch(u)", "x.fetch(u)", "prefetch(u)"):
        assert not rx.search(miss), miss


# ---- archcheck_front split + --static-dir ----------------------------------------------------------

def test_archcheck_loads_by_path_and_uses_front_module():
    m = _load("archcheck")  # finds its sibling archcheck_front without tools/ on sys.path
    f = _load("archcheck_front")
    assert m.check_frontend.__module__ == "archcheck_front"
    assert m.COUNTED_LABELS["css-color:"] == f.FRONT_LABELS["css-color:"]
    assert set(m.COUNTED_LABELS) == {"subprocess:", "css-color:", "js-csrf:", "js-fetch:"}


def test_check_css_and_js_take_allowed_files_as_parameters():
    f = _load("archcheck_front")
    root_css = ":root{--a:1}\n.x{color:#fff}\n"
    assert f.check_css(root_css, "t/css/tokens.css", "t/css/tokens.css") == ({}, [])
    o, found = f.check_css(root_css, "static/css/tokens.css", "t/css/tokens.css")
    assert o == {"css-color:static/css/tokens.css": 1} and "define tokens in t/css/tokens.css" in found[0][1]
    assert f.check_css(root_css, "static/css/tokens.css") == ({}, [])  # default: today's TOKENS_CSS
    js = "fetch('/a'); h['X-CSRF-Token'] = t;\n"
    assert f.check_js(js, "t/js/core.js", {"t/js/core.js"}, {"t/js/core.js"}) == {}
    assert f.check_js(js, "static/js/core.js", {"t/js/core.js"}, {"t/js/core.js"}) == {
        "js-csrf:static/js/core.js": 1, "js-fetch:static/js/core.js": 1}
    assert f.check_js(js, "static/js/core.js") == {}


def _static(base: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return base


CLEAN_STATIC = {
    "css/tokens.css": ":root{--fg:#111;--z-pop:20}\n:root[data-theme=dark]{--fg:#eee}\n",
    "css/app.css": ".a{color:var(--fg);z-index:var(--z-pop)}\n.b{z-index:calc(var(--z-pop) + 1)}\n"
                   ".c{z-index:auto}\n/* .d{z-index:5} */\n",
    "js/core.js": "fetch('/x', {headers: {'X-CSRF-Token': t}});\n",
    "js/app.js": "pm.fetch('/y');\n",
    "js/vendor/v.js": "fetch(x); // " + "x" * 200 + "\n",
}


def test_static_dir_clean_tree_passes_without_scanning_src(tmp_path):
    _static(tmp_path / "theme", CLEAN_STATIC)
    r = _run("--static-dir", "theme", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.strip() == "archcheck: OK (1 static dir)"


def test_static_dir_each_rule_hits(tmp_path):
    files = {
        "css/tokens.css": ":root{--fg:#111}\n",
        "css/x.css": ".a{color:#fff;background:rgb(0,0,0)}\n:root{--b:1px}\n\n.p{\n  z-index: 100 !important;\n}\n"
                     ".q{z-index:-1}\n.r{z-index:var(--top)}\n.s{z-index:var(--z-pop)}\n",
        "css/big.css": ".a{}\n" * 501,
        "js/a.js": "fetch('/a'); window.fetch('/b');\nvar m = document.querySelector('meta[name=\"csrf-token\"]');\n"
                   "var s = '" + "y" * 120 + "';\n",
        "sw.js": "fetch(r);\n",
    }
    _static(tmp_path / "th", files)
    r = _run("--static-dir", "th/", cwd=tmp_path)
    out = r.stdout
    assert r.returncode == 1, out
    assert "NEW css-color:th/css/x.css (2 colour literals outside tokens.css)" in out
    assert "NEW css-root-token:th/css/x.css (custom properties --b" in out and "th/css/tokens.css" in out
    assert "NEW z-index:th/css/x.css:5 (z-index: 100; use a var(--z-...) token)" in out
    assert "NEW z-index:th/css/x.css:7 (z-index: -1;" in out
    assert "NEW z-index:th/css/x.css:8 (z-index: var(--top);" in out
    assert "z-index:th/css/x.css:9" not in out
    assert "NEW asset-lines:th/css/big.css (501 lines > 500)" in out
    assert "NEW js-fetch:th/js/a.js (2 fetch( call sites outside core.js)" in out
    assert "NEW js-csrf:th/js/a.js (1 CSRF lookups outside core.js)" in out
    assert "NEW js-fetch:th/sw.js (1 fetch(" in out  # only js/core.js is allowed in a theme tree
    assert "NEW line-len:th/js/a.js:3 (131 chars > 120)" in out
    assert "line-len:th/js/a.js:1" not in out and "tokens.css (" not in out
    assert out.strip().endswith(f"archcheck: FAIL ({out.count('NEW ')} new)")


def test_static_dir_line_len_on_css_and_exactly_120_ok(tmp_path):
    files = {"css/a.css": "/*" + "a" * 116 + "*/\n" + "/*" + "b" * 117 + "*/\n"}
    r = _run("--static-dir", str(_static(tmp_path / "t", files)), cwd=tmp_path)
    assert r.returncode == 1
    assert f"NEW line-len:{tmp_path}/t/css/a.css:2 (121 chars > 120)" in r.stdout
    assert ":1 (" not in r.stdout


def test_static_dir_repeatable_and_missing_dir(tmp_path):
    _static(tmp_path / "ok", CLEAN_STATIC)
    _static(tmp_path / "bad", {"css/a.css": ".a{z-index:3}\n"})
    r = _run("--static-dir", "ok", "--static-dir", "bad", "--static-dir", "nope", cwd=tmp_path)
    assert r.returncode == 1
    assert "NEW z-index:bad/css/a.css:1 (z-index: 3;" in r.stdout
    assert "NEW static-dir:nope (not a directory)" in r.stdout
    assert "ok/" not in r.stdout
    assert "archcheck: FAIL (2 new)" in r.stdout


def test_static_dir_with_root_adds_unbaselinable_findings(tmp_path):
    root, bl = _tree(tmp_path / "r", {"ok.py": "x = 1\n"})
    assert _check(root, bl).returncode == 0
    theme = _static(tmp_path / "theme", {"css/a.css": ".a{color:#fff}\n"})
    r = _check(root, bl, "--static-dir", str(theme))
    assert r.returncode == 1
    assert f"NEW css-color:{theme}/css/a.css (1 colour literals" in r.stdout
    w = _check(root, bl, "--static-dir", str(theme), "--write-baseline")
    assert w.returncode == 1
    assert "css-color" not in bl.read_text()
