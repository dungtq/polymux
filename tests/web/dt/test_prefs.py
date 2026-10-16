"""pm.web.dt.prefs: the v7 "all devices" prefs store (Settings › Display, Chat colours): whitelist and value checks,
defaults dropped, rev bumps only on a change, a broken file = defaults, the stat-keyed cache."""

from __future__ import annotations

import json

import pytest

from pm.core.errors import Invalid
from pm.web.dt import prefs


def test_check_kinds():
    assert prefs.check("run_status", "plain") == "plain"
    assert prefs.check("kind_edge", "0") is False and prefs.check("model_pill", "1") is True
    assert prefs.check("col_k_claude", " #C96442 ") == "#c96442"
    for k, v in (("run_status", "loud"), ("kind_edge", "maybe"), ("col_dot_run", "red"), ("col_dot_run", "#fff"),
                 ("col_dot_run", "url(x)"), ("tool_pill", ""), ("nope", "1"), (None, "1")):
        with pytest.raises(Invalid):
            prefs.check(k, v)


def test_update_drops_defaults_and_resets():
    p = prefs.update(prefs.EMPTY, {"run_status": "off", "col_dot_run": "#112233", "kind_edge": "1"})
    assert dict(p.values) == {"run_status": "off", "col_dot_run": "#112233"}  # kind_edge on = the default
    assert p.get("kind_edge") is True and p.get("col_dot_wait") is None and p.get("run_status") == "off"
    p = prefs.update(p, {"col_dot_run": "", "run_status": "playful"})
    assert dict(p.values) == {}
    with pytest.raises(Invalid):
        prefs.update(p, {})
    with pytest.raises(Invalid):
        prefs.update(p, {"unknown": ""})  # a reset names a known key too


def test_apply_bumps_rev_only_on_change(settings):
    a = prefs.apply(settings, {"msg_bg": "tint"})
    assert a.rev == 1 and prefs.load(settings).get("msg_bg") == "tint"
    assert prefs.apply(settings, {"msg_bg": "tint"}).rev == 1  # nothing changed: no write
    b = prefs.apply(settings, {"msg_bg": "", "tool_pill": "both"})
    assert b.rev == 2 and dict(b.values) == {"tool_pill": "both"}
    raw = json.loads(prefs.path(settings).read_text())
    assert raw == {"rev": 2, "prefs": {"tool_pill": "both"}} and prefs.path(settings).name == "v7-prefs.json"
    with pytest.raises(Invalid):
        prefs.apply(settings, {"tool_pill": "neon"})
    assert prefs.load(settings).rev == 2


def test_broken_file_is_defaults_and_bad_values_dropped(settings):
    f = prefs.path(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{not json")
    assert prefs.load(settings) == prefs.EMPTY
    f.write_text(json.dumps({"rev": 4, "prefs": {"run_status": "off", "col_dot_run": "javascript:", "x": 1}}))
    p = prefs.load(settings)
    assert p.rev == 4 and dict(p.values) == {"run_status": "off"}


def test_read_cached_follows_the_file(settings):
    assert prefs.read_cached(settings) == prefs.EMPTY  # no file yet
    prefs.apply(settings, {"run_status": "plain"})
    assert prefs.read_cached(settings).get("run_status") == "plain"
    prefs.apply(settings, {"run_status": "off"})
    assert prefs.read_cached(settings).get("run_status") == "off"
    assert prefs.as_json(prefs.read_cached(settings)) == {"rev": 2, "prefs": {"run_status": "off"}}


def test_every_colour_key_has_a_token():
    assert all(prefs.SCHEMA[k] == prefs.COLOUR for k in prefs.COLOURS)
    assert all(v.startswith("--") for v in prefs.COLOURS.values())
