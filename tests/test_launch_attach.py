import re

from helpers import agent_page_csrf, login, png_bytes
from helpers import chat_env as env  # noqa: F401  (fixture)

H = {"origin": "https://pm.example.test"}


def test_launch_page_has_attach_ui(env):  # noqa: F811
    s, c = env
    login(s, c)
    t = c.get("/p/alpha/launch").text
    root = t[t.index('data-composer="launch"') - 60:t.index("</section>", t.index('data-composer="launch"'))]
    assert 'data-upload="/p/alpha/image"' in root and "data-cmp-chips" in root and "data-attach" in root  # the composer's own
    assert "data-cmp-file" in root and "data-paste-img" in root and 'name="prompt"' in root
    assert 'id="imgform"' not in t and "data-cmp-upload" not in root  # no second (nested) form inside the Launch form
    js = c.get("/static/js/launch.js").text  # paths go out with the prompt; a launch waits for its uploads
    assert "chipsReady(root)" in js and "Still uploading" in js and "e.detail.parameters.prompt" in js
    assert "makeChip" not in js and "FileReader" not in js and "pmLines" not in js  # composer-attach.js does the rest
    assert "is-drop" in c.get("/static/js/composer-attach.js").text


def test_project_image_upload(env):  # noqa: F811
    s, c = env
    login(s, c)
    tok = agent_page_csrf(c)
    r = c.post("/p/alpha/image", files={"image": ("a.png", png_bytes(), "image/png")}, headers={**H, "x-csrf-token": tok})
    assert r.status_code == 200, r.text
    p = re.search(r'data-image-path="([^"]+)"', r.text).group(1)
    assert p.startswith(str(s.state_dir / "uploads"))
    assert c.post("/p/alpha/image", files={"image": ("a.png", png_bytes(), "image/png")}, headers=H).status_code == 403
    assert c.post("/p/nope/image", files={"image": ("a.png", png_bytes(), "image/png")},
                  headers={**H, "x-csrf-token": tok}).status_code == 404
    assert "project_image" in s.audit_file.read_text()


def test_topbar_launch_uses_current_project(env):  # noqa: F811
    s, c = env
    login(s, c)
    assert 'href="/launch?project=alpha"' in c.get("/p/alpha").text
    assert 'href="/launch?project=alpha"' in c.get("/a/claude-12").text
    r = c.get("/launch?project=alpha&embed=1", follow_redirects=False)
    assert r.headers["location"].startswith("/p/alpha/launch?")
    r = c.get("/launch?project=zzz", follow_redirects=False)  # unknown: falls back to the recent project
    assert r.headers["location"].startswith("/p/alpha/launch?")


def test_launch_button_is_primary_and_touch_selects_fit(env):  # noqa: F811
    s, c = env
    login(s, c)
    t = c.get("/p/alpha/launch").text
    btn = re.search(r"<button[^>]*data-launch-open[^>]*>", t).group(0)
    assert 'class="primary"' in btn  # #189: the shared green primary, no launch.css colour of its own
    css = c.get("/static/css/launch.css").text
    assert "[data-launch-open] {" not in css and re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css) is None
    touch = css[css.index("(pointer: coarse)"):]
    touch = touch[:touch.index("\n}\n")]
    assert ".launch-compact select { height: auto; min-height: var(--ctl-h); }" in touch  # #187: 16px text not cut
