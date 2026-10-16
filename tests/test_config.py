"""config.Settings typed fields for the former [extras] keys, and load_settings promotion/warnings."""

import dataclasses
import logging
from pathlib import Path

import pytest

from pm.core import config
from pm.core.config import EXTRAS_FIELDS, Settings, load_settings
from pm.core.models import Tier


@pytest.fixture
def warns(caplog, monkeypatch):
    # setup_logging (run by other tests) stops pm.* propagating; let records reach caplog here
    monkeypatch.setattr(logging.getLogger("pm"), "propagate", True)
    caplog.set_level(logging.WARNING, logger="pm.config")
    return lambda: [r.getMessage() for r in caplog.records if r.name == "pm.config"]


def _load(tmp_path, text: str) -> Settings:
    p = tmp_path / "pm.toml"
    p.write_text(text)
    return load_settings(p)


def test_defaults_match_old_extras_fallbacks():
    s = Settings()
    assert len(EXTRAS_FIELDS) == 15 and all(k in Settings.__dataclass_fields__ for k in EXTRAS_FIELDS)
    assert (s.agent_bins, s.agent_models, s.layouts) == ({}, {}, {})
    assert (s.snippets, s.servers) == ([], [])
    assert s.allow_bypass is True and s.disable_poll is False and s.watchdog is False
    assert s.worktree_root is None and s.ntfy_ca is None
    assert s.alerts_host == "" and s.ntfy_ui_url == ""
    from helpers import NEUTRAL_DEFAULTS

    from pm.notify import ntfy as notify
    assert NEUTRAL_DEFAULTS["ntfy_url"] == notify.DEFAULT_URL == ""
    assert s.ntfy_debounce_s == notify.DEBOUNCE_S and s.ntfy_dedupe_s == notify.DEDUPE_S
    assert Settings().agent_bins is not s.agent_bins  # no shared mutable default


def test_autoexit_defaults_and_session_list(tmp_path):
    s = Settings()
    assert s.autoexit_sessions == ("*",) and s.idle_after_s == 28800
    s = _load(tmp_path, 'autoexit_sessions = ["work*", "dev"]\n')
    assert s.autoexit_sessions == ("work*", "dev") and isinstance(s.autoexit_sessions, tuple)


def test_constructor_extras_are_not_promoted():
    # only load_settings promotes [extras]; Settings(extras=...) is a plain dict for unknown keys
    s = Settings(extras={"disable_poll": True, "log": "keep"})
    assert s.disable_poll is False and s.extras == {"disable_poll": True, "log": "keep"}
    s2 = dataclasses.replace(Settings(), allow_bypass=False)
    assert s2.allow_bypass is False and s2.extras == {}


def test_load_promotes_extras_table(tmp_path, warns):
    s = _load(tmp_path, 'port = 9000\n[extras]\ndisable_poll = true\nntfy_debounce_s = 5\n'
                        'agent_bins = { claude = "/bin/c" }\nmystery = 1\n')
    assert s.port == 9000 and s.disable_poll is True and s.ntfy_debounce_s == 5
    assert s.agent_bins == {"claude": "/bin/c"}
    assert s.extras == {"mystery": 1}
    assert warns() == ["config: unknown extras key 'mystery'"]


def test_top_level_wins_over_extras(tmp_path, warns):
    s = _load(tmp_path, 'allow_bypass = true\nalerts_host = "top"\n[extras]\nallow_bypass = false\n'
                        'alerts_host = "ext"\n')
    assert s.allow_bypass is True and s.alerts_host == "top" and s.extras == {}
    assert warns() == []


def test_unknown_top_level_key_warns(tmp_path, warns):
    s = _load(tmp_path, 'tmux_bin = "/usr/bin/tmux"\nbogus = 3\ntier_ceiling = 2\n'
                        'extra_projects = ["~/a"]\n')
    assert s.tmux_bin == "/usr/bin/tmux" and s.tier_ceiling == Tier(2)
    assert s.extra_projects == (Path.home() / "a",)
    assert not hasattr(s, "bogus") and s.extras == {}
    assert warns() == ["config: unknown key 'bogus' ignored"]


def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "none.toml") == Settings()
    assert config.DEFAULT_CONFIG.name == "pm.toml"


def test_ntfy_defaults_match_notify_constants():
    """config cannot import notify (cycle); the typed defaults must stay equal to notify's constants."""
    from helpers import NEUTRAL_DEFAULTS

    from pm.core.config import Settings
    from pm.notify import ntfy as notify
    s = Settings()
    assert NEUTRAL_DEFAULTS["ntfy_url"] == notify.DEFAULT_URL == ""
    assert s.ntfy_debounce_s == notify.DEBOUNCE_S and s.ntfy_dedupe_s == notify.DEDUPE_S


def test_builder_frame_hosts_default_and_validation(tmp_path, warns):
    s = Settings()
    assert s.builder_frame_hosts == ("https://mock.example.test",) and s.builder_hint is True
    s = _load(tmp_path, 'builder_hint = false\nbuilder_frame_hosts = ["https://Mock.example.test/", "https://x.example.test:8443",'
                        ' "http://a.me", "https://a.me/path", "https://*.me", "https://a.me; script-src *",'
                        ' "https://a.me \'self\'", "https://a.me\\"", "https://mock.example.test"]\n')
    assert s.builder_hint is False
    assert s.builder_frame_hosts == ("https://mock.example.test", "https://x.example.test:8443")
    assert len(warns()) == 6
    assert _load(tmp_path, 'builder_frame_hosts = "https://one.me"\n').builder_frame_hosts == ("https://one.me",)
    assert config.frame_origins(["https://ok.me", 3, None]) == ("https://ok.me",)


def test_neutral_defaults_and_origin_from_base_url(tmp_path):
    """Out of the box pm is loopback-only with every site link off (tests run with helpers.SITE)."""
    from helpers import NEUTRAL_DEFAULTS
    assert NEUTRAL_DEFAULTS == {
        "base_url": "http://127.0.0.1:8093", "allowed_origins": (),  # () = (base_url,)
        "git_allowed_hosts": (), "builder_frame_hosts": (), "ntfy_url": "", "ntfy_topic_url": "",
        "ntfy_pm_topic_url": "", "display_utc_offset_h": 0.0, "vm_protected": ()}
    s = _load(tmp_path, 'base_url = "https://pm.example.lan/"\n')
    assert s.allowed_origins == ("https://pm.example.lan",)
    s = _load(tmp_path, 'base_url = "https://a.lan"\nallowed_origins = ["https://b.lan"]\n')
    assert s.allowed_origins == ("https://b.lan",)


def test_theme_and_plugins_keys(tmp_path, warns):
    s = Settings()
    assert (s.theme, s.plugins_enabled, s.plugins) == ("classic", True, {})
    s = _load(tmp_path, 'theme = "dark-ish"\nplugins_enabled = false\n[plugins.extra]\nkey = "v"\nn = 2\n'
                        '[plugins]\nflat = 3\n')
    assert s.theme == "dark-ish" and s.plugins_enabled is False
    assert s.plugins == {"extra": {"key": "v", "n": 2}} and s.extras == {}
    assert warns() == ["config: [plugins.flat] is not a table, ignored"]
    assert _load(tmp_path, 'plugins = 3\n').plugins == {}


def test_allowed_origins_derived_from_base_url_when_not_given():
    assert Settings(base_url="https://x.lan/", allowed_origins=()).allowed_origins == ("https://x.lan",)
    assert Settings(base_url="https://x.lan", allowed_origins=("https://y.lan",)).allowed_origins == ("https://y.lan",)


def test_config_file_is_the_load_path_never_from_toml(tmp_path, warns):
    missing = tmp_path / "none.toml"
    assert load_settings(missing).config_file == missing
    s = _load(tmp_path, 'config_file = "/etc/evil.toml"\nport = 9000\n')
    assert s.port == 9000 and s.config_file is not None and s.config_file.name != "evil.toml"
    assert s.config_file.exists() and "config_file" in " ".join(warns())
    assert Settings(config_file=missing) == Settings()  # not part of equality


def test_push_remote_default_and_override(tmp_path, warns):
    assert Settings().push_remote == "origin"
    assert _load(tmp_path, 'push_remote = "gitdev"\n').push_remote == "gitdev"
    assert _load(tmp_path, '[extras]\npush_remote = "x"\n').push_remote == "origin"  # typed key, not an extra
    assert warns() == ["config: unknown extras key 'push_remote'"]


def test_launch_redirects_keeps_valid_pairs(tmp_path, warns):
    assert Settings().launch_redirects == {}
    s = _load(tmp_path, 'launch_redirects = { app = "app-core", "my site" = "site", bad = 3, "" = "x", self = "self",'
                        ' empty = "" }\n')
    assert s.launch_redirects == {"app": "app-core", "my site": "site"} and s.extras == {}
    assert warns() == ["config: launch_redirects entry 'bad' = 3 ignored", "config: launch_redirects entry '' = 'x' "
                       "ignored", "config: launch_redirects entry 'self' = 'self' ignored",
                       "config: launch_redirects entry 'empty' = '' ignored"]
    assert _load(tmp_path, 'launch_redirects = ["app"]\n').launch_redirects == {}
    assert warns()[-1] == "config: launch_redirects is not a table, ignored"
