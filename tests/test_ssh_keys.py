"""SSH keys: pm-managed key generation/listing, ~/.ssh read-only listing, settings routes + guard."""

import json
import os
import re
import stat
import subprocess
from dataclasses import replace

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.core.config import Settings
from pm.infra import sshkeys

H = {"origin": "https://pm.example.test"}
HAVE_KEYGEN = subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode == 0
need_keygen = pytest.mark.skipif(not HAVE_KEYGEN, reason="ssh-keygen missing")


def _st(tmp_path):
    return Settings(state_dir=tmp_path / "state", user_ssh_dir=tmp_path / "home-ssh")


def _tok(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/p/alpha").text).group(1)


@need_keygen
def test_generate_perms_fingerprint_and_no_overwrite(tmp_path):
    s = _st(tmp_path)
    assert sshkeys.managed_key(s) is None
    k = sshkeys.generate(s)
    d = sshkeys.ssh_dir(s)
    assert d == tmp_path / "state" / "ssh"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    priv = sshkeys.key_path(s)
    assert priv.name == "id_ed25519_pm" and stat.S_IMODE(priv.stat().st_mode) == 0o600
    assert k.type == "ssh-ed25519" and k.comment.startswith("pm@")
    out = subprocess.run(["ssh-keygen", "-lf", str(priv) + ".pub"], capture_output=True, text=True).stdout
    assert k.fingerprint in out  # python fingerprint == ssh-keygen -lf
    # passphrase-less
    assert subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(priv)], capture_output=True).returncode == 0
    before = priv.read_bytes()
    with pytest.raises(sshkeys.SshKeyError, match="never overwrites"):
        sshkeys.generate(s)
    assert priv.read_bytes() == before
    priv.with_name("id_ed25519_pm.pub").unlink()  # half state still refuses
    with pytest.raises(sshkeys.SshKeyError):
        sshkeys.generate(s)


def test_keygen_argv_is_fixed_list(tmp_path):
    a = sshkeys.keygen_argv(tmp_path / "k", "pm@x")
    assert a[:2] == ["ssh-keygen", "-q"] and ["-N", ""] == a[a.index("-N"):a.index("-N") + 2]
    assert "ed25519" in a and a[-2:] == ["-f", str(tmp_path / "k")]


def test_generate_failure_cleans_up(tmp_path, monkeypatch):
    s = _st(tmp_path)
    monkeypatch.setattr(sshkeys, "KEYGEN_BIN", "/nonexistent/ssh-keygen")
    with pytest.raises(sshkeys.SshKeyError, match="not available"):
        sshkeys.generate(s)
    monkeypatch.setattr(sshkeys, "KEYGEN_BIN", "false")
    with pytest.raises(sshkeys.SshKeyError, match="failed"):
        sshkeys.generate(s)
    assert not list(sshkeys.ssh_dir(s).iterdir())


def test_parse_pub_rejects_non_keys():
    good = "ssh-ed25519 " + "A" * 68 + " me@x"
    assert sshkeys.parse_pub(good) is not None
    for bad in ("", "-----BEGIN OPENSSH PRIVATE KEY-----", "ssh-ed25519 !!!", "command=\"x\" ssh-ed25519 AAAA",
                "ssh-ed25519 AAAA\nssh-ed25519 BBBB extra", "random text"):
        k = sshkeys.parse_pub(bad)
        assert k is None or k.line.count("\n") == 0 and bad.startswith("ssh-ed25519 AAAA")


def test_user_keys_lists_only_pub_files(tmp_path):
    s = _st(tmp_path)
    d = s.user_ssh_dir
    d.mkdir()
    line = "ssh-ed25519 " + "A" * 68 + " bob@laptop"
    (d / "id_ed25519.pub").write_text(line + "\n")
    (d / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET\n")
    (d / "notes.txt").write_text("x")
    (d / "bad.pub").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET\n")
    (d / "link.pub").symlink_to(d / "id_ed25519")
    (d / "big.pub").write_text("ssh-ed25519 " + "A" * 20000)
    keys = sshkeys.user_keys(s)
    assert [k.name for k in keys] == ["id_ed25519.pub"] and keys[0].comment == "bob@laptop"
    assert sshkeys.user_keys(Settings(user_ssh_dir=tmp_path / "missing")) == []


@need_keygen
def test_settings_panel_and_keygen_route(env):
    s, c = env
    login(s, c)
    panel = c.get("/settings/panel").text
    for sec in ('id="access"', 'id="ssh"', 'id="git"', 'id="agents"', 'id="composer"'):
        assert sec in panel
    assert "No pm key yet" in panel and "Generate key" in panel
    page = c.get("/settings").text
    for a in ("#access", "#ssh", "#git", "#agents", "#composer"):
        assert f'href="{a}"' in page
    tok = _tok(c)
    r = c.post("/settings/ssh/keygen", data={"csrf": tok}, headers=H)
    assert r.status_code == 200 and "SSH key generated" in r.text
    assert 'id="pm-pubkey"' in r.text and "data-copy-text" in r.text and "Generate key" not in r.text
    k = sshkeys.managed_key(s)
    assert k and k.line in r.text and k.fingerprint in r.text
    assert "PRIVATE" not in r.text
    priv = sshkeys.key_path(s).read_text()
    assert priv.splitlines()[1] not in r.text
    audit = [json.loads(x) for x in s.audit_file.read_text().splitlines()]
    acts = [(a["action"], a["result"]) for a in audit if a["action"] == "ssh_keygen"]
    assert acts == [("ssh_keygen", "attempt"), ("ssh_keygen", "ok")]
    assert priv.splitlines()[1] not in s.audit_file.read_text()
    # second call refuses, key unchanged
    r2 = c.post("/settings/ssh/keygen", data={"csrf": tok}, headers=H)
    assert "never overwrites" in r2.text and sshkeys.managed_key(s) == k
    assert [a["result"] for a in map(json.loads, s.audit_file.read_text().splitlines())
            if a["action"] == "ssh_keygen"][-1] == "denied"
    # state dir is the tmp one: nothing in real home
    assert str(sshkeys.ssh_dir(s)).startswith(str(s.state_dir))


def test_keygen_guard_rejections(env):
    s, c = env
    r = c.post("/settings/ssh/keygen", data={"csrf": "x"}, headers=H)
    assert r.status_code == 401
    login(s, c)
    tok = _tok(c)
    assert c.post("/settings/ssh/keygen", data={"csrf": tok}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/settings/ssh/keygen", data={"csrf": "bad"}, headers=H).status_code == 403
    assert c.post("/settings/ssh/keygen", data={}, headers=H).status_code == 403
    c.app.state.settings = replace(s, tier_ceiling=s.tier_ceiling.__class__(0))
    c.app.state.settings.extras.update(s.extras)
    assert c.post("/settings/ssh/keygen", data={"csrf": tok}, headers=H).status_code == 403
    assert sshkeys.managed_key(s) is None


@need_keygen
def test_keygen_fails_closed_when_audit_broken(env, monkeypatch):
    s, c = env
    login(s, c)
    tok = _tok(c)
    from pm.security import audit as audit_mod

    def boom(self, entry):
        raise audit_mod.AuditError("disk full")

    monkeypatch.setattr(audit_mod.AuditLog, "write", boom)
    r = c.post("/settings/ssh/keygen", data={"csrf": tok}, headers=H)
    assert r.status_code == 503
    assert sshkeys.managed_key(s) is None and not os.path.exists(sshkeys.key_path(s))


def test_settings_lists_user_pubkey_read_only(env):
    s, c = env
    login(s, c)
    d = s.user_ssh_dir
    d.mkdir(parents=True)
    (d / "id_ed25519.pub").write_text("ssh-ed25519 " + "A" * 68 + " bob@laptop\n")
    (d / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nTOPSECRET\n")
    t = c.get("/settings/panel").text
    assert "id_ed25519.pub" in t and "bob@laptop" in t and "TOPSECRET" not in t
    assert "read-only" in t
