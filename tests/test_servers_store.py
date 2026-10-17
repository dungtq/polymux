"""Remote servers: validation, storage, host-key pinning, test connection, one-time-password install.

ssh / ssh-keyscan are fakes on a private PATH (never the real ssh, ~/.ssh or any host). The fake ssh records
argv, env and stdin; the tests assert the install password is in none of them, nor in audit or responses.
"""

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
from pm.infra import servers_store as ss
from pm.infra import sshkeys

H = {"origin": "https://pm.example.test"}
PW = "Zq9!hunter2-Secret"
need_keygen = pytest.mark.skipif(subprocess.run(["which", "ssh-keygen"], capture_output=True).returncode != 0,
                                 reason="ssh-keygen missing")

FAKE_SSH = r"""#!/bin/sh
d=$(dirname "$0")
{ printf 'ARGV'; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\n'; } >> "$d/ssh.log"
env >> "$d/env.log"
if [ -n "$SSH_ASKPASS_REQUIRE" ]; then
  got=$("$SSH_ASKPASS" "user@host's password: ")
  cat > "$d/stdin.log"
  [ "$got" = "$(cat "$d/expect")" ] || { echo "Permission denied (password)." >&2; exit 255; }
  [ "$(cat "$d/install" 2>/dev/null)" = "fail" ] && exit 1
  exit 0
fi
mode=$(cat "$d/probe" 2>/dev/null || echo ok)
case "$mode" in
  ok) exit 0 ;;
  denied) echo "user@h: Permission denied (publickey)." >&2; exit 255 ;;
  changed) echo "@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @" >&2; echo "Host key verification failed." >&2; exit 255 ;;
  refused) echo "ssh: connect to host h port 22: Connection refused" >&2; exit 255 ;;
  hang) exec sleep 30 ;;
esac
"""


@pytest.fixture
def fake(tmp_path, monkeypatch):
    b = tmp_path / "fakebin"
    b.mkdir()
    (b / "ssh").write_text(FAKE_SSH)
    (b / "ssh").chmod(0o755)
    (b / "ssh-keyscan").write_text('#!/bin/sh\necho "# scan"\ncat "$(dirname "$0")/hostkey"\n')
    (b / "ssh-keyscan").chmod(0o755)
    k = tmp_path / "hk"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k)], check=True)
    blob = k.with_name("hk.pub").read_text().split()[1]
    (b / "hostkey").write_text(f"example.test ssh-ed25519 {blob}\n")
    (b / "expect").write_text(PW)
    monkeypatch.setenv("PATH", f"{b}:{os.environ['PATH']}")
    fp = sshkeys.fingerprint_of(blob)
    return b, fp


def _tok(c):
    import test_ssh_keys
    return test_ssh_keys._tok(c)


def _audit(s):
    return [json.loads(x) for x in s.audit_file.read_text().splitlines()]


def _st(tmp_path):
    return Settings(state_dir=tmp_path / "state")


# ------------------------------------------------------------------ unit
def test_validators():
    for h in ("example.com", "a", "192.0.2.10", "::1", "srv-1.example.test"):
        assert ss.valid_host(h), h
    for h in ("", "-oProxyCommand=x", "-h", "a b", "a;b", "a\nb", "ex..com", "host$(x)", "é.com", "a/b",
              "x" * 254, "user@host", "[::1]", ".a", "a."):
        assert not ss.valid_host(h), h
    for u in ("dt", "_x", "a-b_1"):
        assert ss.valid_user(u)
    for u in ("", "-x", "Root", "a b", "a;b", "x" * 33, "a\n", "1a"):
        assert not ss.valid_user(u)
    assert ss.parse_port("") == 22 and ss.parse_port(" 2222 ") == 2222
    for p in ("0", "65536", "-1", "2e3", "22 22", "abc", "٢٢"):
        with pytest.raises(ss.ServerError):
            ss.parse_port(p)


def test_store_add_remove_perms(tmp_path):
    s = _st(tmp_path)
    r = ss.add(s, "web", "example.test", "", "dt")
    assert r["port"] == 22 and r["hostkey_fp"] == ""
    assert stat.S_IMODE(ss.servers_file(s).stat().st_mode) == 0o600
    with pytest.raises(ss.ServerError, match="already"):
        ss.add(s, "web2", "EXAMPLE.test", "22", "dt")
    with pytest.raises(ss.ServerError, match="name"):
        ss.add(s, "web", "other.test", "22", "dt")
    for bad in (("n", "-oProxyCommand=x", "22", "dt"), ("n", "h.test", "0", "dt"), ("n", "h.test", "22", "-x"),
                ("-n", "h.test", "22", "dt"), ("n", "h.test", "22", "root;id")):
        with pytest.raises(ss.ServerError):
            ss.add(s, *bad)
    assert [x["id"] for x in ss.load(s)] == [r["id"]]
    ss.remove(s, r["id"])
    assert ss.load(s) == []
    with pytest.raises(ss.ServerError):
        ss.remove(s, "zzzzzzzz")


def test_load_ignores_junk(tmp_path):
    s = _st(tmp_path)
    s.state_dir.mkdir()
    ss.servers_file(s).write_text(json.dumps({"servers": [
        {"id": "aaaaaaaa", "name": "ok", "host": "h.test", "port": 22, "user": "dt"},
        {"id": "bbbbbbbb", "name": "x", "host": "-oProxyCommand=y", "port": 22, "user": "dt"},
        {"id": "../etc", "name": "x", "host": "h.test", "port": 22, "user": "dt"}, 5, None]}))
    assert [x["id"] for x in ss.load(s)] == ["aaaaaaaa"]


def test_classify():
    assert ss.classify(0, "") == "ok"
    assert ss.classify(255, "Permission denied (publickey).") == "auth_denied"
    assert ss.classify(255, "REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed.") == "hostkey_changed"
    assert ss.classify(255, "Host key verification failed.") == "hostkey_unknown"
    assert ss.classify(255, "Connection refused") == "unreachable"
    assert ss.classify(255, "Connection timed out") == "timeout"
    assert ss.classify(1, "") == "error"


def test_argv_shape(tmp_path):
    s = _st(tmp_path)
    srv = {"host": "h.test", "port": 2222, "user": "me"}
    a = ss.probe_argv(s, srv)
    assert a[0] == "ssh" and a[1:3] == ["-F", "/dev/null"] and a[-3:] == ["--", "me@h.test", "true"]
    assert "BatchMode=yes" in a and "StrictHostKeyChecking=yes" in a and "2222" in a
    b = ss.install_argv(s, srv, "root")
    assert b[b.index("--") + 1] == "root@h.test" and "PubkeyAuthentication=no" in b
    assert "BatchMode=no" in b and all(PW not in x for x in b)


def test_shell_argv_is_interactive_key_only(tmp_path):
    s = _st(tmp_path)
    a = ss.shell_argv(s, {"host": "h.test", "port": 2222, "user": "me"})
    assert a[0] == "ssh" and a[1:3] == ["-F", "/dev/null"] and a[-2:] == ["--", "me@h.test"]  # no remote command
    assert "BatchMode=yes" not in a and "PasswordAuthentication=no" in a and "StrictHostKeyChecking=yes" in a
    assert f"UserKnownHostsFile={ss.known_hosts(s)}" in a and a[a.index("-i") + 1] == str(sshkeys.key_path(s))
    assert ss.shell_window_name("lab 2.x") == "lab-2.x"


def test_open_shell_in_the_ssh_session(tmp_path, monkeypatch):
    s = _st(tmp_path)
    srv = ss.add(s, "lab one", "h.test", "", "me")
    calls = []
    monkeypatch.setattr(ss.tmuxctl, "list_sessions", lambda st: ())
    monkeypatch.setattr(ss.tmuxctl, "new_session", lambda st, name, cwd: calls.append(("new", name)) or "$7")
    monkeypatch.setattr(ss.tmuxctl, "new_agent_window",
                        lambda st, sid, name, cwd, argv: calls.append(("win", sid, name, argv[-1])) or ("@9", "%9"))
    with pytest.raises(ss.ServerError, match="generate the pm key first"):
        ss.open_shell(s, srv["id"])
    sshkeys.key_path(s).parent.mkdir(parents=True, exist_ok=True)
    sshkeys.key_path(s).write_text("k")
    with pytest.raises(ss.ServerError, match="pin the host key first"):
        ss.open_shell(s, srv["id"])
    assert calls == []
    ss._apply(s, srv["id"], hostkey_fp="SHA256:x")
    got, wid = ss.open_shell(s, srv["id"])
    assert wid == "@9" and got["id"] == srv["id"]
    assert calls == [("new", "ssh"), ("win", "$7", "lab-one", "me@h.test")]
    calls.clear()
    from pm.core.models import TmuxSession
    monkeypatch.setattr(ss.tmuxctl, "list_sessions", lambda st: (TmuxSession("$3", "ssh"),))
    ss.open_shell(s, srv["id"])
    assert calls == [("win", "$3", "lab-one", "me@h.test")]  # the existing session is reused

    def boom(*a, **k):
        raise ss.tmuxctl.TmuxError("no server")
    monkeypatch.setattr(ss.tmuxctl, "new_agent_window", boom)
    with pytest.raises(ss.ServerError, match="tmux could not open the window"):
        ss.open_shell(s, srv["id"])
    with pytest.raises(ss.ServerError, match="unknown server"):
        ss.open_shell(s, "ffffffff")


@need_keygen
def test_shell_route_audits_and_answers(env, fake, monkeypatch):
    s, c = env
    tok, sid = _setup(s, c, fake)
    p = f"/settings/servers/{sid}/shell"
    j = c.post(p, headers={**H, "accept": "application/json"}, data={"csrf": tok}).json()
    assert j == {"ok": False, "notice": "Shell not opened: pin the host key first."}
    monkeypatch.setattr(ss, "open_shell", lambda st, i: (ss.get(st, i), "@5"))
    j = c.post(p, headers={**H, "accept": "application/json"}, data={"csrf": tok}).json()
    assert j == {"ok": True, "notice": "Opened a shell on web in tmux session ssh."}
    sh = [a for a in _audit(s) if a["action"] == "ssh_shell"]
    assert [a["result"] for a in sh] == ["attempt", "denied", "attempt", "ok"]
    assert sh[-1]["target"] == sid and sh[-1]["detail"]["window"] == "@5" and sh[-1]["detail"]["host"] == "example.test"


# ------------------------------------------------------------------ routes
def _setup(s, c, fake):
    login(s, c)
    tok = _tok(c)
    assert c.post("/settings/ssh/keygen", data={"csrf": tok}, headers=H).status_code == 200
    r = c.post("/settings/servers/add", headers=H, data={"csrf": tok, "name": "web", "host": "example.test",
                                                          "port": "", "user": "dt"})
    assert "Server web added" in r.text and "host key not pinned" in r.text
    return tok, ss.load(s)[0]["id"]


@need_keygen
def test_full_flow_and_password_never_leaks(env, fake):
    s, c = env
    b, fp = fake
    tok, sid = _setup(s, c, fake)
    p = f"/settings/servers/{sid}"
    # install / test refuse before pinning
    assert "pin the host key first" in c.post(p + "/install", headers=H, data={"csrf": tok, "user": "dt", "password": PW}).text
    assert "host key not pinned" in c.post(p + "/test", headers=H, data={"csrf": tok}).text
    assert not (b / "ssh.log").exists()
    # scan shows the fingerprint, pins nothing
    j = c.post(p + "/scan", headers=H, data={"csrf": tok}).json()
    assert j["fp"] == fp and ss.load(s)[0]["hostkey_fp"] == ""
    # pin with a wrong fingerprint is refused; the right one is pinned
    assert "nothing pinned" in c.post(p + "/pin", headers=H, data={"csrf": tok, "fp": "SHA256:nope"}).text
    assert not ss.known_hosts(s).exists()
    assert "pinned" in c.post(p + "/pin", headers=H, data={"csrf": tok, "fp": fp}).text
    kh = ss.known_hosts(s)
    assert kh.read_text().startswith("example.test ssh-ed25519 ") and stat.S_IMODE(kh.stat().st_mode) == 0o600
    assert ss.load(s)[0]["hostkey_fp"] == fp
    assert "already pinned" in c.post(p + "/pin", headers=H, data={"csrf": tok, "fp": fp}).text
    # test connection classes
    for mode, word in (("ok", "connected"), ("denied", "authentication failed"), ("changed", "host key changed"),
                       ("refused", "unreachable")):
        (b / "probe").write_text(mode)
        r = c.post(p + "/test", headers=H, data={"csrf": tok})
        assert word in r.text, (mode, r.text[:200])
        assert "Permission denied" not in r.text and "Connection refused" not in r.text
    (b / "probe").write_text("hang")
    ss.TEST_TIMEOUT_S = 0.5
    try:
        assert "timed out" in c.post(p + "/test", headers=H, data={"csrf": tok}).text
    finally:
        ss.TEST_TIMEOUT_S = 10.0
    (b / "probe").write_text("ok")
    # install with the one-time password
    (b / "ssh.log").unlink()
    r = c.post(p + "/install", headers=H, data={"csrf": tok, "user": "root", "password": PW})
    assert r.status_code == 200 and "key installed on web and key login works" in r.text
    assert PW not in r.text and 'value="' + PW not in r.text
    # fake ssh got the right password via askpass, the pubkey on stdin
    assert (b / "stdin.log").read_text().strip() == sshkeys.managed_key(s).line
    log = (b / "ssh.log").read_text()
    assert "[root@example.test]" in log and "[--]" in log and "BatchMode=no" in log
    # password absent from argv, env dump, audit, state dir contents and response
    envlog = (b / "env.log").read_text()
    assert "SSH_ASKPASS_REQUIRE=force" in envlog and "PM_ASKPASS_FIFO=" in envlog
    for blob in (log, envlog, s.audit_file.read_text(), r.text, ss.servers_file(s).read_text()):
        assert PW not in blob and "hunter2" not in blob
    for root, _, files in os.walk(s.state_dir):
        for fn in files:
            assert PW.encode() not in open(os.path.join(root, fn), "rb").read(), fn
    # no fifo/tempdir left behind
    assert not [x for x in os.listdir(sshkeys.ssh_dir(s)) if x.startswith("askpass-")]
    ins = [a for a in _audit(s) if a["action"] == "ssh_key_install"]
    assert [a["result"] for a in ins] == ["attempt", "denied", "attempt", "ok"]
    assert ins[-1]["detail"]["install_user"] == "root" and "password" not in json.dumps(ins).lower()
    assert str(len(PW)) not in json.dumps(ins[-1]["detail"].get("result", ""))
    # wrong password -> class only
    (b / "expect").write_text("other")
    r = c.post(p + "/install", headers=H, data={"csrf": tok, "user": "root", "password": PW})
    assert "authentication failed" in r.text and PW not in r.text and "Permission denied" not in r.text
    assert PW not in s.audit_file.read_text()
    # remove drops the pinned key
    r = c.post(p + "/remove", headers=H, data={"csrf": tok})
    assert "removed" in r.text and ss.load(s) == [] and kh.read_text() == ""


@need_keygen
def test_repin_needs_typed_name_and_confirmed_new_key(env, fake, tmp_path):
    s, c = env
    b, fp = fake
    tok, sid = _setup(s, c, fake)
    p = f"/settings/servers/{sid}"
    assert "pinned" in c.post(p + "/pin", headers=H, data={"csrf": tok, "fp": fp}).text
    k2 = tmp_path / "hk2"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(k2)], check=True)
    blob2 = k2.with_name("hk2.pub").read_text().split()[1]
    (b / "hostkey").write_text(f"example.test ssh-ed25519 {blob2}\n")
    fp2 = c.post(p + "/scan", headers=H, data={"csrf": tok}).json()["fp"]
    assert fp2 == sshkeys.fingerprint_of(blob2) != fp
    r = c.post(p + "/repin", headers=H, data={"csrf": tok, "fp": fp2, "confirm": "Web"})
    assert "type the server name" in r.text and json.loads(r.headers["hx-trigger"])["pm:notice"]["ok"] is False
    assert "nothing pinned" in c.post(p + "/repin", headers=H, data={"csrf": tok, "fp": fp, "confirm": "web"}).text
    assert ss.load(s)[0]["hostkey_fp"] == fp
    r = c.post(p + "/repin", headers=H, data={"csrf": tok, "fp": fp2, "confirm": "web"})
    assert "Host key of web replaced" in r.text and ss.load(s)[0]["hostkey_fp"] == fp2
    assert ss.known_hosts(s).read_text() == f"example.test ssh-ed25519 {blob2}\n"
    rows = [a for a in _audit(s) if a["action"] == "ssh_hostkey_repin"]
    assert [a["result"] for a in rows] == ["attempt", "denied", "attempt", "denied", "attempt", "ok"]
    assert rows[-1]["detail"]["old_fp"] == fp[:19] and rows[-1]["detail"]["fp"] == fp2[:19]
    with pytest.raises(ss.ServerError, match="already pinned"):
        ss.pin_hostkey(s, sid, fp2)


@need_keygen
def test_install_verify_failed_and_timeout(env, fake, monkeypatch):
    s, c = env
    b, fp = fake
    tok, sid = _setup(s, c, fake)
    p = f"/settings/servers/{sid}"
    c.post(p + "/pin", headers=H, data={"csrf": tok, "fp": fp})
    (b / "probe").write_text("denied")
    r = c.post(p + "/install", headers=H, data={"csrf": tok, "user": "dt", "password": PW})
    assert "key login still fails" in r.text
    # a hung ssh is killed at the timeout
    (b / "ssh").write_text("#!/bin/sh\nexec sleep 30\n")
    monkeypatch.setattr(ss, "INSTALL_TIMEOUT_S", 0.5)
    r = c.post(p + "/install", headers=H, data={"csrf": tok, "user": "dt", "password": PW})
    assert "timed out" in r.text and PW not in r.text and PW not in s.audit_file.read_text()
    assert not [x for x in os.listdir(sshkeys.ssh_dir(s)) if x.startswith("askpass-")]


def test_route_validation_and_injection(env, fake):
    s, c = env
    login(s, c)
    tok = _tok(c)
    for host, user, port in (("-oProxyCommand=touch /tmp/x", "dt", "22"), ("h.test", "-oFoo", "22"),
                             ("h.test", "dt", "99999"), ("h t", "dt", "22")):
        r = c.post("/settings/servers/add", headers=H, data={"csrf": tok, "name": "n", "host": host,
                                                              "port": port, "user": user})
        assert "Server not added" in r.text
    assert ss.load(s) == []
    r = c.post("/settings/servers/zzzzzzzz/test", headers=H, data={"csrf": tok})
    assert "unknown server" in r.text


def test_guards(env, fake, monkeypatch):
    s, c = env
    paths = ["/settings/servers/add", "/settings/servers/aaaaaaaa/test", "/settings/servers/aaaaaaaa/install",
             "/settings/servers/aaaaaaaa/scan", "/settings/servers/aaaaaaaa/pin", "/settings/servers/aaaaaaaa/remove",
             "/settings/servers/aaaaaaaa/repin", "/settings/servers/aaaaaaaa/shell"]
    for p in paths:
        assert c.post(p, data={"csrf": "x"}, headers=H).status_code == 401
    login(s, c)
    tok = _tok(c)
    for p in paths:
        assert c.post(p, data={"csrf": tok}, headers={"origin": "https://evil.example"}).status_code == 403
        assert c.post(p, data={"csrf": "bad"}, headers=H).status_code == 403
        assert c.post(p, data={}, headers=H).status_code == 403
    c.app.state.settings = replace(s, tier_ceiling=s.tier_ceiling.__class__(1))
    c.app.state.settings.extras.update(s.extras)
    # install and repin (replaces a pinned host key) need tier 2
    assert c.post(paths[2], data={"csrf": tok, "password": PW}, headers=H).status_code == 403
    assert c.post(paths[6], data={"csrf": tok, "fp": "SHA256:x", "confirm": "x"}, headers=H).status_code == 403
    assert c.post(paths[7], data={"csrf": tok}, headers=H).status_code == 403  # a remote shell needs tier 2 too
    assert any(a["action"] == "ssh_hostkey_repin" and a["result"] == "denied" and a["detail"].get("reason") == "tier"
               for a in _audit(s))
    c.app.state.settings = replace(s, tier_ceiling=s.tier_ceiling.__class__(0))
    c.app.state.settings.extras.update(s.extras)
    assert c.post(paths[0], data={"csrf": tok, "name": "n", "host": "h.test", "user": "dt"}, headers=H).status_code == 403
    assert ss.load(s) == []
    assert not (fake[0] / "ssh.log").exists()


def test_audit_failure_fails_closed(env, fake, monkeypatch):
    s, c = env
    login(s, c)
    tok = _tok(c)
    from pm.security import audit as audit_mod

    def boom(self, entry):
        raise audit_mod.AuditError("disk full")

    monkeypatch.setattr(audit_mod.AuditLog, "write", boom)
    r = c.post("/settings/servers/add", headers=H, data={"csrf": tok, "name": "n", "host": "h.test", "user": "dt"})
    assert r.status_code == 503 and ss.load(s) == []
    r = c.post("/settings/servers/aaaaaaaa/install", headers=H, data={"csrf": tok, "password": PW})
    assert r.status_code == 503 and not (fake[0] / "ssh.log").exists()


def test_panel_has_servers_card_and_no_stub(env):
    s, c = env
    login(s, c)
    t = c.get("/settings/panel").text
    assert 'id="servers"' in t and "Add server" in t and "coming next" not in t
    page = c.get("/settings").text
    assert 'href="#servers"' in page and "soon" not in page and "/static/js/servers.js" in page


def test_scan_hostkey_maps_proc_errors(monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 1)

    def missing(*a, **k):
        raise FileNotFoundError("ssh-keyscan")

    s = {"host": "example.test", "port": 22}
    monkeypatch.setattr("pm.core.proc.subprocess.run", timeout)
    with pytest.raises(ss.ServerError, match="timed out"):
        ss.scan_hostkey(s)
    monkeypatch.setattr("pm.core.proc.subprocess.run", missing)
    with pytest.raises(ss.ServerError, match="not available"):
        ss.scan_hostkey(s)
