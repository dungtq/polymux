import re
import time

import pytest

from pm.core.config import Settings
from pm.tmux import ctl as t

S = t.SEP


def line(*f):
    return S.join(f)


def test_parse(fake_tmux, monkeypatch):
    out = "\n".join([
        line("$1", "work", "1", "@1", "0", "main", "1", "%1", "123", "fish", "/home/me", "1", "t"),
        line("$1", "work", "1", "@1", "0", "main", "1", "%2", "124", "vim", "/tmp", "0", ""),
        line("$2", "pm-x", "0", "@2", "0", "w", "0", "%3", "5", "claude", "/x", "1", "a|b"),
        "garbage line",
    ])
    monkeypatch.setenv("FAKE_TMUX_OUT", out)
    ss = t.list_sessions(fake_tmux)
    assert [s.id for s in ss] == ["$1", "$2"]
    assert ss[0].windows[0].panes[1].command == "vim"
    assert ss[0].attached == 1 and ss[0].protected
    assert ss[1].windows[0].panes[0].title == "a|b"


def test_no_server_is_empty(fake_tmux, monkeypatch):
    monkeypatch.setenv("FAKE_TMUX_ERR", "no server running on /tmp/x")
    assert t.list_sessions(fake_tmux) == ()
    monkeypatch.setenv("FAKE_TMUX_ERR", "boom")
    with pytest.raises(t.TmuxError):
        t.list_sessions(fake_tmux)


def test_socket_and_argv(fake_tmux):
    t.send_keys(fake_tmux, "%1", "-x; $(id)")
    args = fake_tmux.extras["log"].read_text().split("\n")
    assert args[:2] == ["-L", "pmtest-fake"]
    assert args[2:9] == ["send-keys", "-l", "-t", "%1", "--", "-x; $(id)", "---"]


@pytest.mark.parametrize("bad", ["", "1", "%", "%1;x", "%1 ", "-t", "$(id)", "@1\n", "work", "%-1"])
def test_bad_ids(fake_tmux, bad):
    with pytest.raises(t.TmuxError):
        t.capture_pane(fake_tmux, bad)
    with pytest.raises(t.TmuxError):
        t.send_key(fake_tmux, bad, "Enter")


def test_id_kind(fake_tmux):
    with pytest.raises(t.TmuxError):
        t.rename_session(fake_tmux, "@1", "ok")
    with pytest.raises(t.TmuxError):
        t.rename_window(fake_tmux, "$1", "ok")
    with pytest.raises(t.TmuxError):
        t.new_window(fake_tmux, "%1")


@pytest.mark.parametrize("bad", ["a;b", "a b", "-x", "$(id)", "a`b`", "", "x" * 65, "a\nb", "é"])
def test_bad_names(fake_tmux, bad):
    for f in (lambda: t.new_session(fake_tmux, bad),
              lambda: t.rename_session(fake_tmux, "$1", bad),
              lambda: t.rename_window(fake_tmux, "@1", bad),
              lambda: t.new_window(fake_tmux, "$1", bad)):
        with pytest.raises(t.TmuxError):
            f()
    assert not fake_tmux.extras["log"].exists()  # never reached tmux


def test_good_name_after_dashdash(fake_tmux):
    t.rename_session(fake_tmux, "$1", "a.b_c-1")
    assert "--\na.b_c-1\n" in fake_tmux.extras["log"].read_text()


def test_keys_allowlist(fake_tmux):
    assert t.ALLOWED_KEYS == set(
        "y n 1 2 3 4 5 6 7 8 9 Enter Escape Tab BTab BSpace Space Up Down Left Right Home End "
        "PageUp PageDown C-c C-d C-l C-x C-s".split())
    for k in sorted(t.ALLOWED_KEYS):
        t.send_key(fake_tmux, "%1", k)
    for k in ("C-z", "enter", "Enter;ls", "-l", "", "F1", "M-x"):
        with pytest.raises(t.TmuxError):
            t.send_key(fake_tmux, "%1", k)
    with pytest.raises(t.TmuxError):
        t.send_keys(fake_tmux, "%1", "x", literal=False)


def test_protected():
    s = Settings(unprotected_sessions=("scratch-*", "pmx", "pm*"))
    assert t.is_protected("main", s)
    assert not t.is_protected("scratch-1", s)
    assert t.is_protected("pm", s) and t.is_protected("pmx", s)  # pm* always wins
    assert t.is_protected("anything", Settings())


def test_autoexit_settings():
    s = Settings(unprotected_sessions=("scratch-*",), autoexit_sessions=("work*", "pmx"))
    a = t.autoexit_settings(s)
    assert a.unprotected_sessions == ("work*", "pmx") and s.unprotected_sessions == ("scratch-*",)
    assert not t.is_protected("work1", a) and t.is_protected("scratch-1", a)
    assert t.is_protected("pmx", a)  # pm* always wins
    d = t.autoexit_settings(Settings(unprotected_sessions=()))  # default allow-list "*"
    assert not t.is_protected("anything", d) and t.is_protected("pm-1", d)
    assert t.is_protected("anything", t.autoexit_settings(Settings(autoexit_sessions=())))


def test_timeout(tmp_path):
    sh = tmp_path / "slow"
    sh.write_text("#!/bin/sh\nsleep 5\n")
    sh.chmod(0o755)
    with pytest.raises(t.TmuxError):
        t.run(Settings(tmux_bin=str(sh)), ["x"], timeout=0.2)


def test_round_trip(private_tmux):
    s = private_tmux
    assert s.tmux_socket.startswith("pmtest-")
    sid = t.new_session(s, "rt-one")
    assert sid.startswith("$")
    wid = t.new_window(s, sid, "second")
    t.rename_window(s, wid, "renamed")
    t.rename_session(s, sid, "rt-two")
    ss = {x.name: x for x in t.list_sessions(s)}
    assert {"pmtest-base", "rt-two"} <= set(ss)
    rt = ss["rt-two"]
    assert rt.protected  # everything protected by default
    assert "renamed" in [w.name for w in rt.windows]
    pane = rt.windows[0].panes[0].id
    t.send_keys(s, pane, "echo hi-$((20+22))")
    t.send_key(s, pane, "Enter")
    for _ in range(40):
        if "hi-42" in t.capture_pane(s, pane).replace("echo hi-$((20+22))", ""):
            break
        time.sleep(0.1)
    else:
        pytest.fail(t.capture_pane(s, pane))


def test_send_falls_back_to_paste_buffer_when_client_read_only(monkeypatch):
    from pm.tmux import ctl as tmuxctl
    calls = []

    def fake_run(settings, args, timeout=5.0, input_=None):
        calls.append((args[0], input_))
        if args[0] == "send-keys":
            raise tmuxctl.TmuxError("client is read-only")
        return ""

    monkeypatch.setattr(tmuxctl, "run", fake_run)
    tmuxctl.send_keys(None, "%1", "hi;")
    assert [c[0] for c in calls] == ["send-keys", "list-clients", "load-buffer", "paste-buffer"]
    assert calls[2][1] == b"hi;"
    with pytest.raises(tmuxctl.TmuxError):  # control keys are never pasted as caret text
        tmuxctl.send_key(None, "%1", "Enter")


# ---- argv words for tmux, per-shell quoting for typed commands -------------------------------------------------
INJECT = ["printf", "%s|", "x\\", "; echo INJECTED #"]
NASTY = ["x\\", "; echo INJECTED #", "a'b", 'a"b', "$(echo P)", "`echo P`", "$HOME", "*", "~", "{a,b}", "=x",
         "%self", "a\\'b", "it's\\", "--model=x", "é ü", "", "-", "a b", ";", "x;", "x\\;", "fix it;", "x\\\\;",
         "kill-server"]  # a word ending in ';' would end the tmux command (the next one would run as tmux's)


def _shell(name):
    import shutil
    path = shutil.which(name)
    if not path:
        pytest.skip(f"{name} not installed")
    return path


def _run_shell(name, line, tmp_path):
    """``line`` through ``name -c`` without user rc files (fish --no-config, zsh -f); stdout."""
    import subprocess
    flags = {"fish": ["--no-config"], "zsh": ["-f"], "bash": ["--norc", "--noprofile"]}.get(name, [])
    p = subprocess.run([_shell(name), *flags, "-c", line], capture_output=True, timeout=10, cwd=tmp_path,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path)})
    return p.stdout.decode()


def test_quote_for_shell_unit():
    assert t.quote_for_shell("fish", INJECT) == "printf '%s|' 'x\\\\' '; echo INJECTED #'"
    assert t.quote_for_shell("bash", INJECT) == "printf '%s|' 'x\\' '; echo INJECTED #'"
    assert t.quote_for_shell("/usr/bin/fish", ["it's"]) == "'it\\'s'"
    assert t.quote_for_shell("-fish", ["a b"]) == t.quote_for_shell("fish", ["a b"])  # login shell
    assert t.quote_for_shell("zsh", ["it's"]) == "'it'\"'\"'s'"
    assert t.quote_for_shell("sh", ["/x/claude", "--model", "haiku"]) == "/x/claude --model haiku"
    assert t.quote_for_shell("fish", ["=x", "~", ""]) == "'=x' '~' ''"
    for shell in ("nu", "xonsh", "pwsh", "", "tcsh"):  # unknown: refuse, never guess
        with pytest.raises(t.ShellQuoteError):
            t.quote_for_shell(shell, ["x"])
    for argv in ([], ["a\nb"], ["a\rb"], ["a\x1bb"], ["a\0b"]):
        with pytest.raises(t.ShellQuoteError):
            t.quote_for_shell("fish", argv)


@pytest.mark.parametrize("name", ["fish", "sh", "bash", "zsh", "dash"])
def test_quote_for_shell_is_one_literal_argument_per_word_in_a_real_shell(name, tmp_path):
    """The injection case (an argument ending in a backslash, then '; cmd') stays literal in every shell."""
    out = _run_shell(name, t.quote_for_shell(name, INJECT), tmp_path)
    assert out == "x\\|; echo INJECTED #|"
    out = _run_shell(name, t.quote_for_shell(name, ["printf", "<%s>", *NASTY]), tmp_path)
    assert out == "".join(f"<{a}>" for a in NASTY)


def test_sh_quoting_is_not_fish_quoting(tmp_path):
    """Why the shell matters: shlex.join (POSIX) typed into fish runs the injected command."""
    import shlex
    out = _run_shell("fish", shlex.join(INJECT), tmp_path)
    assert out.endswith("INJECTED\n") and "; echo" not in out


SH_FALLBACK = ["/bin/sh", "-c", 'exec "$@"', "sh"]


def test_command_words():
    """argv runs through the user's default-shell (its startup files apply) as positional arguments."""
    argv = ["/x/a", "b c; d", "x\\"]
    assert t.command_words(argv, "/usr/bin/fish") == ["/usr/bin/fish", "-c", "exec $argv", *argv]
    assert t.command_words(argv, "/bin/zsh") == ["/bin/zsh", "-c", 'exec "$@"', "zsh", *argv]
    assert t.command_words(["/x/a b"], "/usr/bin/bash") == ["/usr/bin/bash", "-c", 'exec "$@"', "bash", "/x/a b"]
    for shell in ("", "fish", "/usr/bin/nu", "/opt/x/elvish"):  # unknown / not absolute: /bin/sh
        assert t.command_words(argv, shell) == [*SH_FALLBACK, *argv]
    for bad in ([], ["a\0b"], ["a", 1], ["-c", "x"]):
        with pytest.raises(t.TmuxError):
            t.command_words(bad, "/usr/bin/fish")


def test_arg_escapes_a_trailing_semicolon():
    assert [t.arg(w) for w in ("x;", ";", "x\\;", "x;y", "a")] == ["x\\;", "\\;", "x\\\\;", "x;y", "a"]
    assert t.command_words(["/x/a", "fix it;", "b"], "/usr/bin/fish")[-2:] == ["fix it\\;", "b"]
    assert t.command_words(["/x/a;"], "")[-1] == "/x/a\\;"
    from pathlib import Path
    assert t.arg(Path("/w/d;")) == "/w/d\\;" and t.arg(Path("/w")) == "/w"


def test_unknown_default_shell_is_logged_once(monkeypatch, caplog):
    from pm.core.cache import LRU
    monkeypatch.setattr(t, "_warned", LRU(max_entries=16))
    with caplog.at_level("WARNING", logger="pm.tmux.ctl"):
        t.command_words(["/x/a"], "/usr/bin/nu")
        t.command_words(["/x/a"], "/usr/bin/nu")
    assert sum("default-shell" in r.message for r in caplog.records) == 1


def test_default_shell_read_and_cached(fake_tmux, monkeypatch):
    from pm.core.cache import LRU
    monkeypatch.setattr(t, "_shells", LRU(max_entries=16))
    log = fake_tmux.extras["log"]
    for bad in ("", "@3", "fish"):  # unreadable: "" and nothing cached
        monkeypatch.setenv("FAKE_TMUX_OUT", bad)
        assert t.default_shell(fake_tmux) == ""
    monkeypatch.setenv("FAKE_TMUX_OUT", "/usr/bin/fish\n")
    assert t.default_shell(fake_tmux) == "/usr/bin/fish"
    monkeypatch.setenv("FAKE_TMUX_OUT", "/bin/bash")
    assert t.default_shell(fake_tmux) == "/usr/bin/fish"  # cached
    assert log.read_text().count("default-shell") == 4
    assert "show-options\n-gv\ndefault-shell" in log.read_text()
    monkeypatch.setattr(t, "SHELL_TTL_S", 0.0)
    assert t.default_shell(fake_tmux) == "/bin/bash"  # re-read after the TTL
    monkeypatch.setenv("FAKE_TMUX_ERR", "no server running")
    monkeypatch.setattr(t, "_shells", LRU(max_entries=16))
    assert t.default_shell(fake_tmux) == ""


def test_new_agent_window_and_respawn_run_argv_through_the_default_shell(fake_tmux, monkeypatch):
    from pm.core.cache import LRU
    monkeypatch.setattr(t, "_shells", LRU(max_entries=16))
    monkeypatch.setenv("FAKE_TMUX_OUT", "/usr/bin/fish")
    t.default_shell(fake_tmux)  # cached: the fake prints the same text for every call
    monkeypatch.setenv("FAKE_TMUX_OUT", "@3 %4")
    t.new_agent_window(fake_tmux, "$1", "agent-x", "/work", ["/x/a", "x\\"])
    t.respawn_pane(fake_tmux, "%4", "/work", ["/x/a"])
    log = [c.split("\n")[2:-1] for c in fake_tmux.extras["log"].read_text().split("---\n") if c]
    starts = [c for c in log if c[0] in ("new-window", "respawn-pane")]
    nw = starts[0][:starts[0].index(";")]  # the start words end before the sequence's next command
    assert nw[nw.index("--") + 1:] == ["/usr/bin/fish", "-c", "exec $argv", "/x/a", "x\\"]
    assert starts[1][starts[1].index("--") + 1:] == ["/usr/bin/fish", "-c", "exec $argv", "/x/a"]


def _sequence(call: list[str]) -> list[list[str]]:
    """One tmux argv split into its commands at the bare ``;`` words."""
    out = [[]]
    for w in call:
        if w == ";":
            out.append([])
        else:
            out[-1].append(w)
    return out


def test_new_agent_window_keeps_the_window_when_the_agent_quits(fake_tmux, monkeypatch):
    """One background window per agent, its window-level remain-on-exit set in the same command sequence
    (an agent that quits at once keeps its window too), by a unique temporary name, then the real name.
    Never a split, never the pane option that _respawn_chat unsets again."""
    monkeypatch.setenv("FAKE_TMUX_OUT", "@3 %4\n")
    assert t.new_agent_window(fake_tmux, "$1", "agent-claude", "/work;", ["/x/a", ";", "x;"]) == ("@3", "%4")
    log = [c.split("\n")[2:-1] for c in fake_tmux.extras["log"].read_text().split("---\n") if c]
    calls = [c for c in log if c[0] in ("new-window", "set-option", "rename-window", "split-window")]
    assert len(calls) == 1  # one tmux call
    nw, opt, ren = _sequence(calls[0])
    tmp = nw[nw.index("-n") + 1]
    assert re.fullmatch(r"pm-new-[0-9a-f]{12}", tmp)
    assert nw[:nw.index("--")] == ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-t", "$1:",
                                   "-n", tmp, "-c", "/work\\;"]
    assert nw[-3:] == ["/x/a", "\\;", "x\\;"]  # user words never end the command
    assert opt == ["set-option", "-w", "-t", f"$1:={tmp}", "remain-on-exit", "on"]
    assert ren == ["rename-window", "-t", f"$1:={tmp}", "--", "agent-claude"]
    t.new_agent_window(fake_tmux, "$1", "agent-claude", "/w", ["/x/a"])
    log = [c.split("\n")[2:-1] for c in fake_tmux.extras["log"].read_text().split("---\n") if c]
    tmps = {_sequence(c)[0][_sequence(c)[0].index("-n") + 1] for c in log if c[0] == "new-window"}
    assert len(tmps) == 2  # a fresh temporary name per window
    for sid, name in (("%1", "agent-x"), ("$1", "-n"), ("$1", "a b"), ("$1", "x;"), ("$1; kill-server", "x")):
        with pytest.raises(t.TmuxError):
            t.new_agent_window(fake_tmux, sid, name, "/w", ["/x/a"])
    for out in ("%4 @3", "bogus", "@3 bogus"):
        monkeypatch.setenv("FAKE_TMUX_OUT", out)
        with pytest.raises(t.TmuxError):
            t.new_agent_window(fake_tmux, "$1", "agent-x", "/w", ["/x/a"])


def test_new_agent_window_persists_in_a_real_tmux(private_tmux, tmp_path):
    """Private -L pmtest-* server: the window stays (pane dead) after the command exits, and a pane option
    set then unset (message._respawn_chat) leaves the window's remain-on-exit in force."""
    s = private_tmux
    sid = next(x.id for x in t.list_sessions(s) if x.name == "pmtest-base")
    wid, pid = t.new_agent_window(s, sid, "agent-quick", str(tmp_path), ["/bin/sh", "-c", "sleep 0.3"])
    assert wid.startswith("@") and pid.startswith("%")
    assert t.run(s, ["show-options", "-w", "-v", "-t", wid, "remain-on-exit"]).strip() == "on"
    names = [w.name for x in t.list_sessions(s) for w in x.windows if w.id == wid]
    assert names == ["agent-quick"]
    gone, _ = t.new_agent_window(s, sid, "agent-quick", str(tmp_path), ["/bin/true"])  # same name, exits at once
    for _ in range(20):
        if any(p.dead for x in t.list_sessions(s) for w in x.windows if w.id == gone for p in w.panes):
            break
        time.sleep(0.1)
    assert [p.dead for x in t.list_sessions(s) for w in x.windows if w.id == gone for p in w.panes] == [True]

    def pane():
        return next((p for x in t.list_sessions(s) for w in x.windows for p in w.panes if p.id == pid), None)

    for _ in range(50):
        if (p := pane()) is not None and p.dead:
            break
        time.sleep(0.1)
    assert pane() is not None and pane().dead  # the window outlived its agent
    t.set_remain_on_exit(s, pid, True)
    t.set_remain_on_exit(s, pid, False)
    t.respawn_pane(s, pid, str(tmp_path), ["/bin/sh", "-c", "sleep 0.3"])
    time.sleep(0.2)
    for _ in range(50):
        if (p := pane()) is not None and p.dead:
            break
        time.sleep(0.1)
    assert pane() is not None and pane().dead  # respawned and exited again: still kept


@pytest.mark.parametrize("name", ["fish", "bash", "zsh", "dash", "sh"])
def test_start_words_in_a_real_tmux_per_default_shell(private_tmux, tmp_path, name):
    """Real tmux with each default-shell (own HOME / XDG dirs: never the user's rc files): a program path with
    spaces, quotes, a trailing backslash and ';', and the injection argv run literally; fish's config.fish
    still applies (it exports SSH_AUTH_SOCK / runs mise for the user). Words and a cwd ending in ';' (tmux's
    command separator, even after --) arrive unchanged."""
    s = private_tmux
    sid = next(x.id for x in t.list_sessions(s) if x.name == "pmtest-base")
    conf = tmp_path / "xdg-config"
    (conf / "fish").mkdir(parents=True)
    (conf / "fish" / "config.fish").write_text("set -gx PM_FROM_CONFIG yes\n")
    for k, v in (("XDG_CONFIG_HOME", conf), ("XDG_DATA_HOME", tmp_path / "xdg-data"), ("HOME", tmp_path)):
        t.run(s, ["set-environment", "-g", k, str(v)])
    t.run(s, ["set-option", "-g", "default-shell", _shell(name)])
    odd = tmp_path / "a b' x\\; touch PWNED #"
    odd.mkdir()
    out = tmp_path / "argv.out"
    prog = odd / "prog"
    prog.write_text(f'#!/bin/sh\n{{ printf "{{%s}}[%s]" "$PWD" "$PM_FROM_CONFIG"; for a in "$@"; do printf "<%s>" "$a";'
                    f' done; }} > {out}.tmp; mv {out}.tmp {out}\n')
    work = tmp_path / "work;"
    work.mkdir()
    prog.chmod(0o755)
    env = f"{{{work}}}" + ("[yes]" if name == "fish" else "[]")
    for argv, want in (([str(prog)], env), ([str(prog), *INJECT[2:]], env + "<x\\><; echo INJECTED #>"),
                       ([str(prog), *NASTY], env + "".join(f"<{a}>" for a in NASTY))):
        out.unlink(missing_ok=True)
        t.run(s, ["new-window", "-d", "-t", sid + ":", "-c", t.arg(str(work)), "--", *t.start_words(s, argv)])
        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.1)
        assert out.read_text() == want
    assert not (tmp_path / "PWNED").exists()
