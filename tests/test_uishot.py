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
    """#206: uishot must not override the instance's --pm-bin / --extra-toml / --limits."""
    cmd = u.devpm_start_cmd(8330)
    assert cmd[-3:] == ["start", "--port", "8330"]
    assert not any(a in cmd for a in ("--pm-bin", "--extra-toml", "--limits"))
