import json

from helpers import agent_page_csrf, login
from helpers import chat_env as env  # noqa: F401  (fixture)

from pm.core import prefs
from pm.web.routes import settings as routes_settings
from pm.web.routes.chat import sniff_file

H = {"origin": "https://pm.example.test"}


def test_defaults_and_clamping(env):
    s, _ = env
    assert prefs.composer_prefs(s) == prefs.COMPOSER_DEFAULTS
    assert prefs.save_composer_prefs(s, 9999, ["png"])["paste_lines"] == 500
    assert prefs.save_composer_prefs(s, -4, ["png"])["paste_lines"] == 0
    assert prefs.save_composer_prefs(s, "junk", ["png"])["paste_lines"] == 10


def test_sanitise_and_persist(env):
    s, _ = env
    got = prefs.save_composer_prefs(s, 7, [".PNG", "a b", "../x", "toolongext1", "Md", "md", "", "z!"])
    assert got == {"paste_lines": 7, "exts": ["png", "md"], "send_key": "enter"}
    assert prefs.composer_prefs(s) == got
    assert len(prefs.save_composer_prefs(s, 1, [f"e{i}" for i in range(80)])["exts"]) == 40
    # other prefs saves keep composer
    prefs.save(s.prefs_file, {"launch": {}, "notify": {}})
    assert prefs.composer_prefs(s)["paste_lines"] == 1
    assert json.loads(s.prefs_file.read_text())["composer"]["paste_lines"] == 1


def _client(env):
    s, c = env
    c.app.include_router(routes_settings.router)
    return s, c


def test_settings_post_needs_csrf_and_saves(env):
    s, c = _client(env)
    login(s, c)
    bad = c.post("/settings/composer", data={"paste_lines": "3", "exts": "png"}, headers=H)
    assert bad.status_code in (400, 403)
    assert prefs.composer_prefs(s)["paste_lines"] == 10
    tok = agent_page_csrf(c)
    h = {**H, "x-csrf-token": tok}
    ok = c.post("/settings/composer", data={"paste_lines": "3", "exts": "png, TXT zip", "csrf": tok}, headers=h)
    assert ok.status_code == 200 and "Composer settings saved" in ok.text
    assert prefs.composer_prefs(s) == {"paste_lines": 3, "exts": ["png", "txt", "zip"], "send_key": "enter"}
    assert 'name="paste_lines"' in ok.text
    e = c.post("/settings/composer", data={"paste_lines": "x", "exts": "png", "csrf": tok}, headers=h)
    assert "must be a number" in e.text
    e = c.post("/settings/composer", data={"paste_lines": "3", "exts": "!!", "csrf": tok}, headers=h)
    assert "at least one" in e.text
    assert prefs.composer_prefs(s)["paste_lines"] == 3
    audit = s.audit_file.read_text()
    assert "composer_save" in audit and "hello" not in audit


def test_send_key_pref(env):
    s, _ = env
    assert prefs.COMPOSER_DEFAULTS["send_key"] == "enter" and prefs.SEND_KEYS == ("enter", "mod-enter")
    assert prefs.save_composer_prefs(s, 10, ["png"], "mod-enter")["send_key"] == "mod-enter"
    assert prefs.save_composer_prefs(s, 4, ["png"])["send_key"] == "mod-enter"  # None keeps the stored key
    assert prefs.save_composer_prefs(s, 4, ["png"], "ctrl")["send_key"] == "enter"  # invalid -> default
    s.prefs_file.write_text(json.dumps({"composer": {"send_key": "shift-enter"}}))
    assert prefs.composer_prefs(s)["send_key"] == "enter"


def test_send_key_route_panel_and_page_ctx(env):
    from pm.web import render
    s, c = _client(env)
    login(s, c)
    tok = agent_page_csrf(c)
    h = {**H, "x-csrf-token": tok}
    base = {"paste_lines": "3", "exts": "png", "csrf": tok}
    r = c.post("/settings/composer", data={**base, "send_key": "mod-enter"}, headers=h)
    assert r.status_code == 200 and "Composer settings saved" in r.text
    assert prefs.composer_prefs(s)["send_key"] == "mod-enter"
    assert 'name="send_key" value="mod-enter" checked' in r.text and "Send with" in r.text
    assert '"send_key":"mod-enter"' in s.audit_file.read_text()
    r = c.post("/settings/composer", data={**base, "send_key": "alt-enter"}, headers=h)
    assert "must be Enter or Ctrl+Enter" in r.text and prefs.composer_prefs(s)["send_key"] == "mod-enter"
    assert '"why":"send_key"' in s.audit_file.read_text()
    r = c.post("/settings/composer", data=base, headers=h)  # no field: keep the stored key
    assert r.status_code == 200 and prefs.composer_prefs(s)["send_key"] == "mod-enter"

    class Req:  # page_ctx exposes the pref to every template
        class app:
            class state:
                settings = s
    assert render.page_ctx(Req)["composer_send_key"]() == "mod-enter"
    prefs.save_composer_prefs(s, 3, ["png"], "enter")
    assert render.page_ctx(Req)["composer_send_key"]() == "enter"


def test_upload_honours_custom_list(env):
    s, c = env
    login(s, c)
    h = {**H, "x-csrf-token": agent_page_csrf(c)}

    def post(name, data):
        return c.post("/a/claude-12/image", files={"image": (name, data, "application/octet-stream")}, headers=h)
    assert post("a.log", b"x\n").status_code == 200
    prefs.save_composer_prefs(s, 10, ["txt", "zip", "xyz"])
    assert post("a.log", b"x\n").status_code == 415
    r = post("a.log", b"x\n")
    assert "txt" in r.text and "zip" in r.text
    assert post("a.txt", b"x\n").status_code == 200
    assert post("a.xyz", b"x\n").status_code == 200
    assert post("a.zip", b"PK\x03\x04\x00\x00").status_code == 200
    assert post("doc.pdf", b"%PDF-1.4").status_code == 415
    assert post("a.png", b"\x89PNG\r\n\x1a\n").status_code == 415


def test_sniff_rules():
    png = b"\x89PNG\r\n\x1a\n"
    assert sniff_file(png, "a.png", ["png"]) == "png"
    assert sniff_file(png, "a.png", ["txt"]) is None
    assert sniff_file(b"not png", "a.png", ["png"]) is None
    assert sniff_file(b"%PDF-1", "a.pdf", ["txt"]) is None
    assert sniff_file(b"\x00\x01", "a.dat", ["dat"]) is None
    assert sniff_file(b"\x00\x01", "a.gz", ["gz"]) == "gz"
    assert sniff_file(b"hi", "a.dat", ["dat"]) == "dat"


def test_hard_block():
    for ext in "exe dll bat cmd com msi scr sh bash zsh ps1 vbs jar app dmg apk".split():
        assert sniff_file(b"echo", f"x.{ext}", [ext, "txt"]) is None, ext
    assert sniff_file(b"hi", "../a.txt", ["txt"]) is None
    assert sniff_file(b"hi", "d\\a.txt", ["txt"]) is None
