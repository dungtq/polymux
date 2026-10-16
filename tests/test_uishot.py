import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("uishot", Path(__file__).resolve().parent.parent / "tools" / "uishot.py")
u = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(u)

INFO = {"agents": {"running": "abc123", "ended_sid": "z9"}, "project": "fixproj"}


def test_slug():
    assert u.slug("/") == "root"
    assert u.slug("/a/abc123?x=1") == "a-abc123-x-1"


def test_expand():
    assert u.expand_route("/a/{agent:running}", INFO) == "/a/abc123"
    assert u.expand_route("/p/{project}", INFO) == "/p/fixproj"
    assert u.expand_route("/inbox", INFO) == "/inbox"
    with pytest.raises(KeyError):
        u.expand_route("/a/{agent:nope}", INFO)


def test_smoke_expands():
    assert all("{" not in u.expand_route(r, INFO) for r in u.SMOKE)


def test_overflow():
    assert not u.has_h_overflow(390, 390)
    assert not u.has_h_overflow(391, 390)
    assert u.has_h_overflow(392, 390)


def test_filter_console():
    items = [
        {"kind": "console", "type": "log", "text": "hi"},
        {"kind": "console", "type": "error", "text": "boom"},
        {"kind": "pageerror", "text": "TypeError"},
        {"kind": "response", "status": 200, "url": "http://x/a"},
        {"kind": "response", "status": 404, "url": "http://x/favicon.ico"},
        {"kind": "response", "status": 500, "url": "http://x/api"},
    ]
    assert u.filter_console(items) == ["console.error: boom", "pageerror: TypeError", "HTTP 500 http://x/api"]


def test_new_lines(tmp_path):
    p = tmp_path / "c.jsonl"
    assert u.count_lines(p) == 0 and u.new_lines(p, 0) == []
    p.write_text('{"a":1}\n{"a":2}\n')
    n = u.count_lines(p)
    assert n == 2
    p.write_text('{"a":1}\n{"a":2}\n{"a":3}\n\n')
    assert u.new_lines(p, n) == ['{"a":3}']


def test_parse_last_json():
    assert u.parse_last_json('noise\n{"a": 1}\nmore {"b": 2}\n') == {"b": 2}
    with pytest.raises(ValueError):
        u.parse_last_json("nothing")


def test_check_safe(tmp_path):
    with pytest.raises(SystemExit):
        u.check_safe("http://127.0.0.1:8093", str(tmp_path / "t"))
    with pytest.raises(SystemExit):
        u.check_safe("http://127.0.0.1:8095", str(u.real_home() / ".config" / "pm" / "token"))
    with pytest.raises(SystemExit):
        u.check_safe("http://127.0.0.1:8095", str(u.real_home() / ".local/state/pm/x"))
    u.check_safe("http://127.0.0.1:8095", str(tmp_path / "token"))


def test_parse_set_cookie():
    assert u.parse_set_cookie("pm_session=abc.def; HttpOnly; Secure; Path=/") == {"name": "pm_session", "value": "abc.def"}
    assert u.parse_set_cookie("a=1; Path=/\nb=2") == {"name": "a", "value": "1"}
    assert u.parse_set_cookie(None) is None and u.parse_set_cookie("junk") is None


def test_ui_cookies():
    assert u.ui_cookies("https://pm.example.test:8443/a/x", "dt") == [
        {"name": "pm_ui", "value": "dt", "domain": "pm.example.test", "path": "/"}]
    assert u.ui_cookies("http://127.0.0.1:8095", "dt")[0]["domain"] == "127.0.0.1"
    assert u.ui_cookies("http://127.0.0.1:8095", None) == [] and u.ui_cookies("http://x", "") == []


class _FakeCtx:
    def __init__(self, **kw):
        self.kw, self.cookies, self.scripts = kw, [], []

    def add_init_script(self, s):
        self.scripts.append(s)

    def add_cookies(self, c):
        self.cookies += c


class _FakeBrowser:
    def new_context(self, **kw):
        return _FakeCtx(**kw)


@pytest.mark.parametrize("ui", [None, "dt"])
def test_new_context_sets_ui_cookie(ui):
    ctx = u.new_context(_FakeBrowser(), "http://127.0.0.1:8095", "dark", 390, ui)
    assert ctx.kw == {"viewport": {"width": 390, "height": 900}, "color_scheme": "dark"}
    assert "pm-theme','dark'" in ctx.scripts[0]
    assert ctx.cookies == ([] if ui is None else u.ui_cookies("http://127.0.0.1:8095", ui))


def test_ui_option_parsed(monkeypatch):
    seen = {}

    def stop(args):
        seen["ui"] = args.ui
        raise SystemExit(0)

    monkeypatch.setattr(u, "get_info", stop)
    with pytest.raises(SystemExit):
        u.main(["--smoke", "--ui", "dt"])
    assert seen["ui"] == "dt"


def test_devpm_start_passes_only_the_port():
    """uishot must not override the instance's --pm-bin / --extra-toml / --limits."""
    cmd = u.devpm_start_cmd(8330)
    assert cmd[-3:] == ["start", "--port", "8330"]
    assert not any(a in cmd for a in ("--pm-bin", "--extra-toml", "--limits"))


def test_no_dev_badge_option_parsed(monkeypatch):
    seen = {}

    def stop(args):
        seen["hide"] = args.no_dev_badge
        raise SystemExit(0)

    monkeypatch.setattr(u, "get_info", stop)
    for argv, want in ((["--smoke"], False), (["--smoke", "--no-dev-badge"], True)):
        with pytest.raises(SystemExit):
            u.main(argv)
        assert seen["hide"] is want


def test_dev_badge_hidden_through_cssom_not_inline_style():
    assert {s.strip() for s in u.DEV_BADGE_SEL.split(",")} == {".x-dev", ".devtag"}
    assert "style.setProperty('display', 'none', 'important')" in u.HIDE_JS
    assert "add_style_tag" not in Path(u.__file__).read_text()  # pm's CSP refuses inline <style>


# #208: WebKit's page.screenshot() injects <style>body {}</style> (Playwright coreBundle.js 5:30); pm's CSP refuses it
CSP_TEXT = ("Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the "
            "style-src directive of the Content Security Policy.")
SHOT_CONSOLE = {"kind": "console", "type": "error", "text": CSP_TEXT, "src": "http://127.0.0.1:8095/tmux",
                "line": 0, "col": 0, "doc": "http://127.0.0.1:8095/tmux"}
SHOT_RECORD = {"client": "127.0.0.1", "col": 30, "kind": "csp", "line": 5, "method": "",
               "msg": "Refused style-src-elem: inline", "page": "/tmux", "status": 0, "ts": 1.0,
               "ua": "Safari · macOS", "url": ""}


def test_is_shot_noise_matches_the_screenshot_csp_line():
    assert u.is_shot_noise(SHOT_CONSOLE)
    assert u.is_shot_noise({**SHOT_CONSOLE, "src": "about:blank"})
    assert u.is_shot_noise({**SHOT_CONSOLE, "src": ""})
    assert u.is_shot_noise(SHOT_RECORD)


@pytest.mark.parametrize("change", [
    {"line": 5, "col": 30}, {"line": 12}, {"col": 4},                    # a script position
    {"src": "http://127.0.0.1:8095/static/js/app.js"},                 # a real source url
    {"text": "Refused to execute a script because its hash, its nonce, or 'unsafe-inline' does not appear in "
             "the script-src directive of the Content Security Policy."},  # a different directive
    {"type": "warning"},
])
def test_is_shot_noise_console_near_misses(change):
    assert not u.is_shot_noise({**SHOT_CONSOLE, **change})


@pytest.mark.parametrize("change", [
    {"line": 6}, {"col": 31}, {"line": 0, "col": 0},
    {"url": "/static/js/app.js"},
    {"msg": "Refused script-src-elem: inline"}, {"msg": "Refused style-src-attr: inline"},
    {"msg": "Refused style-src-elem: https://cdn.example.test"},
    {"msg": "Refused style-src-elem: https://inline.example.test"},
    {"kind": "js"},
])
def test_is_shot_noise_record_near_misses(change):
    assert not u.is_shot_noise({**SHOT_RECORD, **change})


def test_filter_console_drops_only_the_shot_noise():
    other_csp = {**SHOT_CONSOLE, "src": "http://127.0.0.1:8095/static/js/app.js", "line": 3, "col": 9}
    items = [SHOT_CONSOLE, other_csp]
    assert u.filter_console(items, skip_shot_noise=True) == [f"console.error: {CSP_TEXT}"]
    assert u.filter_console(items) == [f"console.error: {CSP_TEXT}"] * 2  # chromium: nothing dropped


def test_drop_shot_noise_lines():
    import json
    real = json.dumps({**SHOT_RECORD, "url": "/static/js/app.js", "line": 3, "col": 9})
    lines = [json.dumps(SHOT_RECORD), real, "not json", "[1]"]
    assert u.drop_shot_noise(lines) == [real, "not json", "[1]"]


def test_checks_read_before_the_screenshot():
    calls = []

    class Page:
        url = "http://x/r"

        def on(self, *a):
            pass

        def goto(self, *a, **k):
            calls.append("goto")

        def wait_for_timeout(self, *a):
            pass

        def evaluate(self, *a):
            calls.append("evaluate")
            return [10, 10]

        def screenshot(self, **k):
            calls.append("screenshot")

    assert u.shoot_routes(Page(), "http://x", ["/r"], Path("/nonexistent"), "light", 10, webkit=True) == 0
    assert calls == ["goto", "evaluate", "screenshot"]
