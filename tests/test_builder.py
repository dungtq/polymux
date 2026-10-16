import os
import time

from fastapi.testclient import TestClient
from helpers import builder_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.web import render
from pm.web.routes import builder as routes_builder


def _site(proj):
    (proj / "index.html").write_text("<h1>hi</h1>")
    (proj / "style.css").write_text("h1{}")
    (proj / "sub").mkdir()
    (proj / "sub" / "index.html").write_text("<p>sub</p>")


def _prefix(c):
    import re
    html = c.get("/a/claude-12/builder").text
    m = re.search(r'src="(/a/claude-12/builder/f/[^"]+/)index\.html"', html)
    assert m, html
    return m.group(1)


def test_requires_login(env):
    s, c, proj, _ = env
    _site(proj)
    for u in ("/a/claude-12/builder", "/a/claude-12/builder/frame/index.html",
              "/a/claude-12/builder/mtime", "/a/claude-12/builder/files"):
        assert c.get(u, follow_redirects=False).status_code in (303, 401), u


def test_empty_and_ready_state(env):
    s, c, proj, _ = env
    login(s, c)
    r = c.get("/a/claude-12/builder")
    assert r.status_code == 200 and "<iframe" not in r.text and "No index.html" in r.text
    assert c.get("/a/codex-99/builder").status_code == 404
    _site(proj)
    r = c.get("/a/claude-12/builder")
    assert "<iframe" in r.text and "/builder/f/" in r.text


def test_entry_fallback_order(env):
    s, c, proj, _ = env
    login(s, c)
    (proj / "dist").mkdir()
    (proj / "dist" / "index.html").write_text("d")
    (proj / "public").mkdir()
    (proj / "public" / "index.html").write_text("p")
    assert routes_builder._entry(proj.resolve()) == "public/index.html"
    (proj / "index.html").write_text("r")
    assert routes_builder._entry(proj.resolve()) == "index.html"
    assert c.get("/a/claude-12/builder/frame/").text == "r"  # dir -> index.html
    assert c.get("/a/claude-12/builder/frame/dist").text == "d"


def test_frame_headers_and_types(env):
    s, c, proj, _ = env
    login(s, c)
    _site(proj)
    (proj / "a.png").write_bytes(b"\x89PNG")
    (proj / "x.exe").write_bytes(b"MZ")
    r = c.get("/a/claude-12/builder/frame/index.html")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store"
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp and "frame-ancestors 'self'" in csp
    assert c.get("/a/claude-12/builder/frame/style.css").headers["content-type"].startswith("text/css")
    assert c.get("/a/claude-12/builder/frame/a.png").headers["content-type"] == "image/png"
    assert c.get("/a/claude-12/builder/frame/x.exe").status_code == 404
    assert c.get("/a/claude-12/builder/frame/sub/").text == "<p>sub</p>"


def test_traversal_dotfiles_symlink_size(env):
    s, c, proj, tmp = env
    login(s, c)
    _site(proj)
    (tmp / "outside.html").write_text("secret")
    (proj / ".git").mkdir()
    (proj / ".git" / "config.html").write_text("g")
    (proj / ".env").write_text("K=1")
    (proj / "link.html").symlink_to(tmp / "outside.html")
    (proj / "dirlink").symlink_to(tmp, target_is_directory=True)
    (proj / "ghost.html").symlink_to(proj / ".git" / "config.html")
    (proj / "big.html").write_bytes(b"a" * (routes_builder.MAX_BYTES + 1))
    (proj / "ok.html").write_bytes(b"a" * routes_builder.MAX_BYTES)
    for bad in ("../outside.html", "%2e%2e/outside.html", "..%2foutside.html", "sub/../../outside.html",
                f"/{tmp}/outside.html", "//etc/passwd", ".git/config.html", ".env", "link.html",
                "dirlink/outside.html", "ghost.html", "big.html", "missing.html", "a\\..\\b"):
        r = c.get("/a/claude-12/builder/frame/" + bad)
        assert r.status_code == 404, bad
        assert "secret" not in r.text
    assert c.get("/a/claude-12/builder/frame/ok.html").status_code == 200


def test_signed_prefix(env):
    s, c, proj, _ = env
    login(s, c)
    _site(proj)
    pre = _prefix(c)
    anon = TestClient(c.app, base_url="https://testserver")  # no cookie: the sandboxed iframe case
    r = anon.get(pre + "style.css")
    assert r.status_code == 200 and "allow-same-origin" not in r.headers["content-security-policy"]
    assert anon.get(pre + "../../x").status_code == 404
    assert anon.get(pre + ".env").status_code == 404
    exp, sig = pre.split("/f/")[1].strip("/").split(".")
    base = "/a/claude-12/builder/f/"
    assert anon.get(f"{base}{exp}.{'0' * len(sig)}/style.css").status_code == 404
    assert anon.get(f"{base}{int(exp) + 1}.{sig}/style.css").status_code == 404
    assert anon.get(f"{base}{int(time.time()) - 5}.{sig}/style.css").status_code == 404
    assert anon.get(pre.replace("claude-12", "claude-13") + "style.css").status_code == 404
    assert anon.get("/a/claude-12/builder/frame/style.css", follow_redirects=False).status_code in (303, 401)


def test_mtime_and_files(env):
    s, c, proj, _ = env
    login(s, c)
    assert c.get("/a/claude-12/builder/mtime").json() == {"mtime": 0, "n": 0}
    _site(proj)
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / "x.js").write_text("x")
    (proj / ".git").mkdir()
    (proj / ".git" / "y.js").write_text("y")
    (proj / "notes.md").write_text("n")
    a = c.get("/a/claude-12/builder/mtime").json()
    assert a["n"] == 4 and a["mtime"] > 0  # index, style, sub/index, notes.md; not node_modules or .git
    st = (proj / "style.css").stat()
    os.utime(proj / "style.css", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    b = c.get("/a/claude-12/builder/mtime").json()
    assert b["mtime"] > a["mtime"] and b["n"] == a["n"]
    files = c.get("/a/claude-12/builder/files").json()["files"]
    assert sorted(files) == ["index.html", "style.css", "sub/index.html"]


def test_files_cap(env):
    s, c, proj, _ = env
    login(s, c)
    for i in range(250):
        (proj / f"f{i:03}.html").write_text("x")
    assert len(c.get("/a/claude-12/builder/files").json()["files"]) == routes_builder.MAX_LIST


def test_composer_placeholder_is_plain_text(env):
    s, c, proj, _ = env
    login(s, c)
    t = c.get("/a/claude-12/builder").text
    assert 'data-composer="builder"' in t and 'placeholder="Message to ' in t
    comp = t.split('data-composer="builder"', 1)[1].split("</section>", 1)[0]
    assert 'data-keys' not in t and 'href="/a/claude-12/builder"' not in comp  # no Keys toggle, no Builder link
    assert 'data-composer="builder"' in t and "composer--flow" in t and 'data-draft-key="/a/claude-12/builder"' in t
    ph = t.split('placeholder="', 1)[1].split('"', 1)[0]
    assert "<" not in ph and "&lt;" not in ph


def test_csp_frame_src_on_builder_page_only(env):
    s, c, proj, _ = env
    login(s, c)
    _site(proj)
    r = c.get("/a/claude-12/builder")
    csp = r.headers["content-security-policy"]
    assert csp == render.CSP + "; frame-src 'self' https://mock.example.test"
    assert 'data-frame-hosts="https://mock.example.test"' in r.text and 'name="mode" value="builder"' in r.text
    page = c.get("/a/claude-12")
    assert page.headers["content-security-policy"] == render.CSP and "frame-src" not in render.CSP
    assert 'name="mode"' not in page.text  # the full chat page sends no Builder mode
    framed = c.get("/a/claude-12/builder/frame/index.html").headers["content-security-policy"]
    assert framed == routes_builder.CSP and "frame-src" not in framed


def test_csp_frames_drops_bad_hosts():
    assert render.csp_frames(()) == render.CSP + "; frame-src 'self'"
    assert render.csp_frames(("https://a.me", "https://b.me; script-src *", "https://c.me/x")) == (
        render.CSP + "; frame-src 'self' https://a.me")


# ------------------------------------------------------------------ API pane
def _api_csrf(c):
    import re
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', c.get("/a/claude-12/builder").text)
    assert m
    return {"X-CSRF-Token": m.group(1), "Origin": "https://pm.example.test"}


def _ports(c, *ports):
    import dataclasses
    c.app.state.settings = dataclasses.replace(c.app.state.settings, builder_api_ports=ports)


def test_api_list_login_and_shapes(env):
    import json
    s, c, proj, _ = env
    assert c.get("/a/claude-12/builder/api", follow_redirects=False).status_code in (303, 401)
    assert c.post("/a/claude-12/builder/api/send", json={}).status_code == 401
    login(s, c)
    assert c.get("/a/claude-12/builder/api").json() == {"source": None, "endpoints": [], "ports": []}
    (proj / "openapi.json").write_text("{bad")
    j = c.get("/a/claude-12/builder/api").json()
    assert j["endpoints"] == [] and "not valid JSON" in j["error"]
    (proj / "openapi.json").write_text(json.dumps({"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {
        "200": {"description": "ok", "content": {"application/json": {"example": {"x": 1}}}}}}}}}))
    _ports(c, 5000, 8093, 8094)
    r = c.get("/a/claude-12/builder/api")
    j = r.json()
    assert r.headers["cache-control"] == "no-store" and j["source"] == "openapi.json" and j["ports"] == [5000]
    assert j["endpoints"][0]["responses"][0]["body"] == {"x": 1}
    t = c.get("/a/claude-12/builder").text
    assert 'data-api-url="/a/claude-12/builder/api"' in t and 'id="bld-api"' in t and "/static/js/builder-api.js" in t


def test_api_send_guarded_and_refused(env, monkeypatch):
    import httpx

    from pm.projects import apisend
    s, c, proj, _ = env
    login(s, c)
    hit = []
    monkeypatch.setattr(apisend, "_transport", httpx.MockTransport(
        lambda req: hit.append(req) or httpx.Response(302, headers={"Location": "https://evil.example/"})))
    h = _api_csrf(c)
    body = {"port": 5000, "method": "GET", "path": "/x", "query": "", "headers": {}, "body": "secret-body"}
    assert c.post("/a/claude-12/builder/api/send", json=body, headers={"Origin": h["Origin"]}).status_code == 403
    assert c.post("/a/claude-12/builder/api/send", json=body,
                  headers={**h, "Origin": "https://evil.example"}).status_code == 403
    r = c.post("/a/claude-12/builder/api/send", json=body, headers=h)  # no ports listed (default)
    assert r.status_code == 403 and "builder_api_ports" in r.json()["error"]
    _ports(c, 4000)
    assert c.post("/a/claude-12/builder/api/send", json=body, headers=h).status_code == 403  # port not listed
    _ports(c, 5000, 8093)
    for bad in ("//evil.example/x", "http://evil/x", "/a@evil", "x"):
        assert c.post("/a/claude-12/builder/api/send", json={**body, "path": bad}, headers=h).status_code == 400, bad
    assert c.post("/a/claude-12/builder/api/send", json={**body, "port": 8093}, headers=h).status_code == 403
    assert c.post("/a/claude-12/builder/api/send", content="nojson",
                  headers={**h, "Content-Type": "application/json"}).status_code == 400
    big = '{"body": "' + "x" * (apisend.MAX_REQUEST + 1) + '"}'
    assert c.post("/a/claude-12/builder/api/send", content=big,
                  headers={**h, "Content-Type": "application/json"}).status_code == 413
    assert not hit
    r = c.post("/a/claude-12/builder/api/send", json=body, headers=h)
    assert r.status_code == 200 and r.json()["status"] == 302 and len(hit) == 1  # redirect returned, not followed
    assert str(hit[0].url) == "http://127.0.0.1:5000/x"
    assert c.post("/a/codex-99/builder/api/send", json=body, headers=h).status_code == 404
    audit = s.audit_file.read_text()
    assert '"builder.api_send"' in audit and '"path":"/x"' in audit and '"port":"5000"' in audit
    assert "secret-body" not in audit and '"result":"denied"' in audit and '"result":"ok"' in audit
