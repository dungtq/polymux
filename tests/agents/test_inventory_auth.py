import json
import os
import stat
from pathlib import Path

import pytest

from pm.agents.inventory import auth
from pm.core.config import Settings
from pm.core.errors import Conflict, Invalid
from pm.core.proc import ProcTimeout
from pm.tmux.ctl import SEP


@pytest.fixture(autouse=True)
def _clean_cache():
    auth.clear_cache()
    yield
    auth.clear_cache()


def fake_bin(tmp_path: Path, out: str, code: int = 0, name: str = "agent") -> Path:
    """Executable that logs its argv and NO_OPEN_BROWSER, prints ``out`` and exits ``code``."""
    p = tmp_path / name
    (tmp_path / "out.txt").write_text(out)
    p.write_text(
        "#!/bin/sh\n"
        f'echo "$* NOB=$NO_OPEN_BROWSER" >> "{tmp_path}/calls.log"\n'
        f'cat "{tmp_path}/out.txt"\n'
        f"exit {code}\n"
    )
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def calls(tmp_path: Path) -> list[str]:
    f = tmp_path / "calls.log"
    return f.read_text().splitlines() if f.exists() else []


# ---------------------------------------------------------------- parsers


CLAUDE_IN = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                        "email": "u@example.com", "orgId": "x", "orgName": "Org", "subscriptionType": "max"})


@pytest.mark.parametrize("name,text,want", [
    ("claude", CLAUDE_IN, ("in", "u@example.com", "claude.ai", "max")),
    ("claude", json.dumps({"loggedIn": False}), ("out", "", "", "")),
    ("claude", "Not logged in. Run claude auth login", ("out", "", "", "")),
    ("claude", "garbage {", ("unknown", "", "", "")),
    ("codex", "Logged in using ChatGPT\n", ("in", "", "ChatGPT", "")),
    ("codex", "Logged in using an API key - sk-proj-***ABCD\n", ("in", "", "API key", "")),
    ("codex", "Not logged in\n", ("out", "", "", "")),
    ("codex", "something else", ("unknown", "", "", "")),
    ("devin", "Logged in (via Devin).\n  Email: d@example.com\n  Plan: Pro Team\n  Token: abc\n",
     ("in", "d@example.com", "Devin", "Pro Team")),
    ("devin", "Not logged in. Run `devin auth login`.", ("out", "", "", "")),
    ("devin", "Error: not authenticated", ("out", "", "", "")),
    ("devin", "?", ("unknown", "", "", "")),
    ("cursor", "\x1b[32m✓\x1b[0m Logged in as c@example.com\n", ("in", "c@example.com", "", "")),
    ("cursor", "Not logged in", ("out", "", "", "")),
    ("cursor", "", ("unknown", "", "", "")),
])
def test_parse_status(name, text, want):
    st = auth.parse_status(name, text)
    assert (st.state, st.account, st.method, st.plan) == want
    assert st.name == name and st.supported


def test_fields_capped():
    st = auth.parse_status("claude", json.dumps({"loggedIn": True, "email": "a" * 500, "authMethod": 3}))
    assert len(st.account) == auth.FIELD_MAX and st.method == ""


# ---------------------------------------------------------------- status / logout


def test_status_argv_cache_and_fresh(tmp_path):
    b = fake_bin(tmp_path, CLAUDE_IN)
    st = auth.status("claude", str(b))
    assert st.state == "in" and st.account == "u@example.com"
    assert auth.status("claude", str(b)) == st
    assert calls(tmp_path) == ["auth status --json NOB="]
    auth.status("claude", str(b), fresh=True)
    assert len(calls(tmp_path)) == 2
    auth.clear_cache("claude")
    auth.status("claude", str(b))
    assert len(calls(tmp_path)) == 3


@pytest.mark.parametrize("name,argv", [
    ("codex", "login status NOB="), ("devin", "auth status NOB="), ("cursor", "status NOB=1"),
])
def test_status_argv_per_kind(tmp_path, name, argv):
    b = fake_bin(tmp_path, "Not logged in", code=1)
    assert auth.status(name, str(b)).state == "out"
    assert calls(tmp_path) == [argv]


def test_status_unknown_never_returns_raw(tmp_path):
    b = fake_bin(tmp_path, "secret-ish output sk-123", code=2)
    st = auth.status("codex", str(b))
    assert st.state == "unknown" and st.error == "exit 2" and "sk-" not in repr(st)


def test_status_timeout_is_unknown(tmp_path, monkeypatch):
    b = fake_bin(tmp_path, "")

    def boom(*a, **k):
        raise ProcTimeout("x")

    monkeypatch.setattr(auth, "run_argv", boom)
    assert auth.status("devin", str(b)) == auth.AuthStatus("devin", True, "unknown", error="timed out")


@pytest.mark.parametrize("name,binary", [
    ("nope", "/bin/sh"), ("claude", ""), ("claude", "sh"), ("claude", "/nonexistent/claude"),
    ("claude", "/etc"),
])
def test_invalid_name_or_binary(name, binary, fake_tmux):
    with pytest.raises(Invalid):
        auth.status(name, binary)
    with pytest.raises(Invalid):
        auth.logout(name, binary)
    with pytest.raises(Invalid):
        auth.login_start(fake_tmux, name, binary)


def test_not_executable(tmp_path):
    p = tmp_path / "claude"
    p.write_text("x")
    with pytest.raises(Invalid):
        auth.status("claude", str(p))


def test_logout(tmp_path):
    b = fake_bin(tmp_path, CLAUDE_IN)
    auth.status("claude", str(b))
    auth.logout("claude", str(b))
    assert calls(tmp_path)[-1] == "auth logout NOB="
    auth.status("claude", str(b))  # cache dropped: probes again
    assert len(calls(tmp_path)) == 3


def test_logout_failure_is_conflict(tmp_path):
    b = fake_bin(tmp_path, "", code=1)
    with pytest.raises(Conflict):
        auth.logout("codex", str(b))
    assert calls(tmp_path) == ["logout NOB="]


# ---------------------------------------------------------------- login windows


FAKE_TMUX = """#!/bin/sh
{ for a in "$@"; do printf '%s\\n' "$a"; done; echo ---; } >> "$FAKE_TMUX_LOG"
for a in "$@"; do
  case "$a" in
    list-panes) cat "$FAKE_DIR/panes"; exit 0;;
    capture-pane) cat "$FAKE_DIR/capture"; exit 0;;
    new-window|new-session) printf '@7 %%9\\n'; exit 0;;
    show-options) printf '/usr/bin/fish\\n'; exit 0;;
    kill-window|set-option|send-keys) exit 0;;
  esac
done
exit 0
"""


@pytest.fixture
def tmux(tmp_path, monkeypatch):
    d = tmp_path / "tmux"
    d.mkdir()
    exe = d / "tmux"
    exe.write_text(FAKE_TMUX)
    exe.chmod(0o755)
    (d / "panes").write_text("")
    (d / "capture").write_text("")
    monkeypatch.setenv("FAKE_DIR", str(d))
    monkeypatch.setenv("FAKE_TMUX_LOG", str(d / "log"))
    s = Settings(tmux_bin=str(exe), tmux_socket="pmtest-auth", state_dir=tmp_path)
    s.extras["dir"] = d
    return s


def set_panes(s, rows):
    """rows: (session id, session name, window id, window name, pane id, dead)."""
    lines = [SEP.join([sid, sname, "0", wid, "0", wname, "1", pid, "1", "sh", "/", "1", "", "1" if dead else "0"])
             for sid, sname, wid, wname, pid, dead in rows]
    (s.extras["dir"] / "panes").write_text("\n".join(lines) + ("\n" if lines else ""))


def tmux_calls(s) -> list[list[str]]:
    f = s.extras["dir"] / "log"
    if not f.exists():
        return []
    out, cur = [], []
    for ln in f.read_text().splitlines():
        if ln == "---":
            out.append(cur)
            cur = []
        else:
            cur.append(ln)
    return [c[2:] for c in out]  # drop -L pmtest-auth


def verbs(s) -> list[str]:
    return [c[0] for c in tmux_calls(s)]


def test_login_view_none(tmux):
    assert auth.login_view(tmux, "claude") == auth.LoginView(False)
    set_panes(tmux, [("$1", "work", "@1", "auth-claude", "%1", False)])  # wrong session: ignored
    assert auth.login_view(tmux, "claude").active is False


def test_login_start_new_session(tmux, tmp_path):
    b = fake_bin(tmp_path, "", name="cursor-agent")
    (tmux.extras["dir"] / "capture").write_text(
        "\x1b[1mOpen\x1b[0m https://cursor.com/loginDeepControl?challenge=abc&uuid=1).\n\n\n")
    v = auth.login_start(tmux, "cursor", str(b))
    assert v == auth.LoginView(True, "@7", "%9", "Open https://cursor.com/loginDeepControl?challenge=abc&uuid=1).",
                               ("https://cursor.com/loginDeepControl?challenge=abc&uuid=1",), False)
    c = tmux_calls(tmux)
    assert c[0][0] == "list-panes" and c[1] == ["show-options", "-gv", "default-shell"]
    assert c[2] == ["new-session", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-s", "agents-auth",
                    "-n", "auth-cursor", "-c", str(Path.home()), "-e", "NO_OPEN_BROWSER=1", "--",
                    "/usr/bin/fish", "-c", "exec $argv", str(b), "login"]  # through the user's shell, argv as $argv
    assert c[3] == ["set-option", "-w", "-t", "@7", "remain-on-exit", "on"]
    assert c[4][:2] == ["capture-pane", "-p"]


def test_login_start_existing_session_new_window(tmux, tmp_path):
    b = fake_bin(tmp_path, "", name="codex")
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-claude", "%3", False)])
    auth.login_start(tmux, "codex", str(b))
    c = tmux_calls(tmux)
    assert c[2] == ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-t", "$4:", "-n", "auth-codex",
                    "-c", str(Path.home()), "--", "/usr/bin/fish", "-c", "exec $argv", str(b), "login", "--device-auth"]


def test_login_start_reuses_alive_window(tmux, tmp_path):
    b = fake_bin(tmp_path, "")
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-claude", "%3", False)])
    v = auth.login_start(tmux, "claude", str(b))
    assert (v.active, v.window_id, v.pane_id, v.dead) == (True, "@2", "%3", False)
    assert verbs(tmux) == ["list-panes", "capture-pane"]


def test_login_start_replaces_dead_window(tmux, tmp_path):
    b = fake_bin(tmp_path, "")
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-devin", "%3", True),
                     ("$4", "agents-auth", "@5", "auth-claude", "%6", False)])
    v = auth.login_start(tmux, "devin", str(b))
    assert v.window_id == "@7"
    c = tmux_calls(tmux)
    assert c[1] == ["kill-window", "-t", "@2"]
    assert c[4][0] == "new-window" and c[4][-4:] == [str(b), "auth", "login", "--force-manual-token-flow"]


def test_login_view_dead_and_text_tail(tmux):
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-claude", "%3", True)])
    body = "\n".join(f"line {i}" for i in range(60)) + "\nkey sk-ant-api03-" + "A" * 40 + "\n\n"
    (tmux.extras["dir"] / "capture").write_text(body)
    v = auth.login_view(tmux, "claude")
    assert v.active and v.dead
    lines = v.text.splitlines()
    assert len(lines) == 40 and lines[0] == "line 21" and "sk-ant" not in v.text


def test_urls_dedup_cap():
    text = " ".join(f"https://e.com/{i}," for i in range(8)) + " https://e.com/0 https://" + "x" * 3000
    assert auth._urls(text) == tuple(f"https://e.com/{i}" for i in range(5))


def test_login_send(tmux):
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-codex", "%3", False)])
    auth.login_send(tmux, "codex", "  CODE-1234 \n")
    c = tmux_calls(tmux)
    assert c[1] == ["send-keys", "-l", "-t", "%3", "--", "CODE-1234"]
    assert c[2] == ["send-keys", "-t", "%3", "--", "Enter"]


@pytest.mark.parametrize("text", ["", "   ", "a\nb", "a\x1bb", "a\tb", "x" * 4097, None])
def test_login_send_invalid_text(tmux, text):
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-codex", "%3", False)])
    with pytest.raises(Invalid):
        auth.login_send(tmux, "codex", text)
    assert verbs(tmux) == []


def test_login_send_needs_alive_window(tmux):
    with pytest.raises(Invalid):
        auth.login_send(tmux, "codex", "x")
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-codex", "%3", True)])
    with pytest.raises(Invalid):
        auth.login_send(tmux, "codex", "x")
    with pytest.raises(Invalid):
        auth.login_send(tmux, "nope", "x")


def test_login_cancel(tmux):
    auth.login_cancel(tmux, "claude")  # none: no error
    assert verbs(tmux) == ["list-panes"]
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-claude", "%3", False)])
    auth.login_cancel(tmux, "claude")
    assert tmux_calls(tmux)[-1] == ["kill-window", "-t", "@2"]


def test_tmux_failure_is_conflict(tmux, monkeypatch):
    monkeypatch.setenv("FAKE_DIR", "/nonexistent")
    (tmux.extras["dir"] / "tmux").write_text("#!/bin/sh\necho boom >&2\nexit 1\n")
    with pytest.raises(Conflict):
        auth.login_view(tmux, "claude")


def test_supported():
    assert all(auth.supported(k) for k in auth.KINDS)
    assert not auth.supported("agy") and not auth.supported(None)  # type: ignore[arg-type]
    assert not auth.supported("pm") and auth.AUTH_SESSION == "agents-auth"
    assert not os.path.basename(auth.AUTH_SESSION).startswith("pm")


def test_cached_never_probes(tmp_path, monkeypatch):
    b = fake_bin(tmp_path, CLAUDE_IN)
    assert auth.cached("claude", str(b)) is None
    assert not (tmp_path / "calls.log").exists()
    st = auth.status("claude", str(b))
    assert auth.cached("claude", str(b)) == st
    assert auth.cached("claude", "/other/bin") is None  # cached for another binary
    assert auth.cached("cline", str(b)) is None and auth.cached("../x", str(b)) is None
    assert calls(tmp_path) == ["auth status --json NOB="]
    later = auth.time.monotonic() + auth.STATUS_TTL + 1
    monkeypatch.setattr(auth.time, "monotonic", lambda: later)
    assert auth.cached("claude", str(b)) is None  # expired
    assert len(calls(tmp_path)) == 1


# ---------------------------------------------------------------- named accounts


def acct_settings(tmp_path, **kw) -> Settings:
    return Settings(state_dir=tmp_path / "state", accounts={"claude": ("work",), "codex": ("team",)}, **kw)


def env_bin(tmp_path: Path, out: str = "Not logged in") -> Path:
    """Executable that logs its account env vars, prints ``out``."""
    p = tmp_path / "envagent"
    p.write_text(f'#!/bin/sh\necho "$* C=$CLAUDE_CONFIG_DIR H=$CODEX_HOME" >> "{tmp_path}/env.log"\necho "{out}"\n')
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def test_status_account_env_and_folder(tmp_path):
    s = acct_settings(tmp_path)
    b = env_bin(tmp_path)
    auth.status("claude", str(b), account="work", settings=s)
    d = tmp_path / "state/accounts/claude/work"
    assert (tmp_path / "env.log").read_text().strip() == f"auth status --json C={d} H="
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    auth.status("codex", str(b), account="team", settings=s, fresh=True)
    assert (tmp_path / "env.log").read_text().splitlines()[-1] == f"login status C= H={tmp_path}/state/accounts/codex/team"


def test_status_default_account_env_untouched(tmp_path):
    b = env_bin(tmp_path)
    auth.status("claude", str(b))
    assert (tmp_path / "env.log").read_text().strip() == "auth status --json C= H="
    assert not (tmp_path / "state").exists()


def test_cache_is_per_account(tmp_path):
    s = acct_settings(tmp_path)
    b = env_bin(tmp_path)
    auth.status("claude", str(b))
    assert auth.cached("claude", str(b)) is not None and auth.cached("claude", str(b), account="work") is None
    auth.status("claude", str(b), account="work", settings=s)
    assert len((tmp_path / "env.log").read_text().splitlines()) == 2
    auth.status("claude", str(b), account="work", settings=s)  # cached
    assert len((tmp_path / "env.log").read_text().splitlines()) == 2
    auth.clear_cache("claude", account="work")
    assert auth.cached("claude", str(b), account="work") is None and auth.cached("claude", str(b)) is not None


def test_account_needs_valid_name_and_settings(tmp_path):
    s = acct_settings(tmp_path)
    b = env_bin(tmp_path)
    with pytest.raises(Invalid):
        auth.status("claude", str(b), account="nope", settings=s)
    with pytest.raises(Invalid):
        auth.status("claude", str(b), account="work")  # no settings
    with pytest.raises(Invalid):
        auth.logout("devin", str(b), account="work", settings=s)  # kind without accounts
    assert not (tmp_path / "state/accounts/claude/nope").exists()


def test_logout_account(tmp_path):
    s = acct_settings(tmp_path)
    b = env_bin(tmp_path)
    auth.status("claude", str(b), account="work", settings=s)
    auth.logout("claude", str(b), account="work", settings=s)
    assert (tmp_path / "env.log").read_text().splitlines()[-1].startswith("auth logout C=")
    assert auth.cached("claude", str(b), account="work") is None


def test_window_name():
    assert auth._window_name("claude") == "auth-claude"
    assert auth._window_name("claude", "work") == "auth-claude-work"


def test_login_start_account_window_and_env(tmux, tmp_path):
    object.__setattr__(tmux, "accounts", {"claude": ("work",)})
    b = fake_bin(tmp_path, "", name="claude")
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-claude", "%3", False)])  # default window: not reused
    auth.login_start(tmux, "claude", str(b), account="work")
    d = tmp_path / "accounts/claude/work"
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    c = tmux_calls(tmux)
    assert c[2] == ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-t", "$4:", "-n", "auth-claude-work",
                    "-c", str(Path.home()), "-e", f"CLAUDE_CONFIG_DIR={d}", "--", "/usr/bin/fish", "-c",
                    "exec $argv", str(b), "auth", "login"]


def test_login_window_lookup_per_account(tmux):
    set_panes(tmux, [("$4", "agents-auth", "@2", "auth-claude", "%3", False),
                     ("$4", "agents-auth", "@5", "auth-claude-work", "%6", False)])
    assert auth.login_view(tmux, "claude").window_id == "@2"
    assert auth.login_view(tmux, "claude", account="work").window_id == "@5"
    assert auth.login_view(tmux, "claude", account="alt").active is False


def test_login_start_unknown_account(tmux, tmp_path):
    b = fake_bin(tmp_path, "", name="claude")
    with pytest.raises(Invalid):
        auth.login_start(tmux, "claude", str(b), account="work")  # not configured
    assert verbs(tmux) == []
