"""Topbar Reload button: POST /service/reload restarts the pm user unit (never on a dev instance)."""
import re
from dataclasses import replace

from helpers import servers_client

from pm.web.routes import control as routes_control


def _csrf(c):
    return re.search(r'name="csrf-token" content="([^"]+)"', c.get("/audit").text).group(1)


def _calls(monkeypatch):
    calls = []
    monkeypatch.setattr(routes_control, "_restart_unit", lambda u: calls.append(u))
    return calls


def test_button_in_topbar_next_to_settings(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/audit").text
    assert 'action="/service/reload"' in h and 'aria-label="Reload pm service"' in h
    assert h.index('action="/service/reload"') < h.index('href="/settings"') < h.index('action="/logout"')  # Service group, then Account


def test_reload_requires_csrf_and_login(fake_tmux, tmp_path, monkeypatch):
    calls = _calls(monkeypatch)
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    assert c.post("/service/reload").status_code == 403
    c2 = servers_client(fake_tmux, tmp_path, monkeypatch, login=False)
    assert c2.post("/service/reload", data={"csrf": "x"}).status_code in (401, 403)
    assert calls == []


def test_reload_schedules_restart_of_unit(fake_tmux, tmp_path, monkeypatch):
    calls = _calls(monkeypatch)
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    r = c.post("/service/reload", data={"csrf": _csrf(c)}, headers={"Origin": "https://pm.example.test"})
    assert r.status_code == 200 and "Reloading pm" in r.text
    import time
    for _ in range(20):
        if calls:
            break
        time.sleep(0.1)
    assert calls in ([], ["pm.service"])  # scheduled on the app loop; never any other unit
    assert "pm_reload" in (tmp_path / "st" / "audit.jsonl").read_text()


def test_reload_refused_on_dev_instance_or_bad_unit(fake_tmux, tmp_path, monkeypatch):
    calls = _calls(monkeypatch)
    for kw in ({"dev_banner": True}, {"reload_unit": ""}, {"reload_unit": "x; rm -rf /"}):
        c = servers_client(replace(fake_tmux, **kw), tmp_path, monkeypatch)
        r = c.post("/service/reload", data={"csrf": _csrf(c)}, headers={"Origin": "https://pm.example.test"})
        assert r.status_code == 403, kw
    assert calls == []
    assert 'action="/service/reload"' not in servers_client(replace(fake_tmux, dev_banner=True), tmp_path, monkeypatch).get("/audit").text


def test_reload_banner_and_script_on_every_page(fake_tmux, tmp_path, monkeypatch):
    h = servers_client(fake_tmux, tmp_path, monkeypatch).get("/audit").text
    assert 'id="reloadbar"' in h and '/static/js/reload.js' in h
    assert 'hx-target="#flash"' not in h.split('/service/reload')[1][:200]


def test_reload_js_has_no_background_ping():
    """No periodic /healthz ping and no ping on tab focus: idle or waking tabs must never reload by
    themselves. Only the tab that pressed Reload reloads; others show "Reload now" after a 502/503/504."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "src/pm/static/js/reload.js").read_text()
    code = "\n".join(ln for ln in js.splitlines() if not ln.lstrip().startswith(("/*", "*", "//")))
    assert "visibilitychange" not in code and "setInterval" not in code
    assert "schedule(5000)" not in code
    assert "x.status === 0" not in code
    assert code.count("location.reload()") == 2  # Reload-now button + own restart


def test_unitctl_allowed_and_fixed_argv(monkeypatch):
    from pm.core.config import Settings
    from pm.infra import unitctl
    assert unitctl.reload_allowed(Settings())
    for kw in ({"dev_banner": True}, {"reload_unit": ""}, {"reload_unit": "x; rm -rf /"},
               {"reload_unit": "pm.service --now"}, {"reload_unit": "pm.timer"}):
        assert not unitctl.reload_allowed(replace(Settings(), **kw)), kw
    seen = []
    monkeypatch.setattr(unitctl, "spawn_detached", lambda argv: seen.append(argv))
    routes_control._restart_unit("pm.service")
    assert seen == [["/usr/bin/systemctl", "--user", "restart", "--no-block", "--", "pm.service"]]
    import pytest
    with pytest.raises(ValueError):
        unitctl.restart_unit("-H evil")
    assert len(seen) == 1
