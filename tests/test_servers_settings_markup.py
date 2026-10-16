"""v4 markup on Servers / Settings: column-header tables, dots, key columns."""
from helpers import servers_client


def test_servers_v4_tables(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    ov, sv = c.get("/servers").text, c.get("/servers?t=services").text
    assert 'class="dt dt-hs"' in ov and 'class="dt dt-ct"' in sv
    assert 'class="dt dt-tm"' not in ov + sv  # tmux block dropped
    h = ov + sv
    assert 'role="columnheader"' in h
    assert "sdot sdot-run" in h and "sdot sdot-err" in h  # container up, host down
    assert "<details class=\"dt-item" in sv
    assert 'class="srv-pill' not in h


def test_servers_panel_fragment_has_tables(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    h = c.get("/servers/panel").text
    assert "dt-head" in h and "hc-role" in h and "tc-name" not in h
    assert "RE only" in h


def test_settings_sections_hairline(fake_tmux, tmp_path, monkeypatch):
    c = servers_client(fake_tmux, tmp_path, monkeypatch)
    assert "card set-sec" in c.get("/settings").text
    p = c.get("/settings/panel").text
    assert "card set-sec" in p and "this session" in p
    assert 'class="primary"' in p
