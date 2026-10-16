"""Header theme toggle: real markup + real CSS/JS in Chromium, and static cache busting."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.web.assets import version_static
from pm.web.render import STATIC_DIR

ROOT = Path(__file__).resolve().parents[1]
PW = next(iter(sorted(Path.home().glob(".npm/_npx/*/node_modules/playwright-core"))), None)
CHROME = shutil.which("google-chrome") or shutil.which("chromium")

NODE = r"""
const {chromium}=require(process.argv[1]);
const fs=require('fs'),path=require('path');
const [,, , page, st, chrome]=process.argv;
(async()=>{
 const b=await chromium.launch({executablePath:chrome});
 const out=[];
 for(const scheme of ['light','dark']){
  const ctx=await b.newContext({colorScheme:scheme}); const p=await ctx.newPage();
  await p.route('https://x.test/**',r=>{const u=new URL(r.request().url());
    if(u.pathname.startsWith('/static/')){const f=path.join(st,u.pathname.slice(8));
      return fs.existsSync(f)?r.fulfill({path:f}):r.fulfill({status:404,body:''});}
    if(u.pathname==='/p')return r.fulfill({contentType:'text/html',body:fs.readFileSync(page,'utf8')});
    r.fulfill({status:404,body:''});});
  await p.goto('https://x.test/p');
  const bg=()=>p.evaluate(()=>getComputedStyle(document.body).backgroundColor);
  const tog=async()=>{ if(!(await p.$('#acct-menu[open]'))) await p.click('#acct-menu > summary'); await p.click('#acct-menu [data-theme-toggle]'); };
  const a=await bg(); await tog(); const c=await bg();
  await tog(); const d=await bg();
  out.push([scheme,a,c,d]); await ctx.close();
 }
 await b.close(); console.log(JSON.stringify(out));
})();
"""


def test_static_versioned_and_no_cache(env):
    s, c = env
    login(s, c)
    html = c.get("/p").text
    assert re.search(r'src="/static/js/theme\.js\?v=[0-9a-f]{10}"', html)
    assert re.search(r'href="/static/css/chrome\.css\?v=[0-9a-f]{10}"', html)
    assert 'data-theme-toggle' in html
    assert version_static('<script src="/static/nope.js">', STATIC_DIR) == '<script src="/static/nope.js">'
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pm.web.assets import NoCacheStatic
    app = FastAPI()
    app.mount("/static", NoCacheStatic(directory=STATIC_DIR), name="static")
    r = TestClient(app).get("/static/js/theme.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


@pytest.mark.skipif(PW is None or CHROME is None or shutil.which("node") is None, reason="playwright/chrome missing")
def test_toggle_changes_colours_in_both_os_schemes(env, tmp_path):
    s, c = env
    login(s, c)
    page = tmp_path / "page.html"
    page.write_text(c.get("/p").text)
    r = subprocess.run(["node", "-e", NODE, str(PW), "--", str(page), str(STATIC_DIR), CHROME],
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr
    import json
    for scheme, before, after, again in json.loads(r.stdout):
        assert before != after, scheme
        assert again == before, scheme
