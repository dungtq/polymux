import dataclasses
import hashlib
import shlex
import sqlite3
import subprocess
import time

import pytest

from pm.agents import launch as L
from pm.core.models import AgentKind, Launch, Project
from pm.tmux import ctl as tmuxctl

GIT_ENV = {"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={**GIT_ENV, "HOME": str(cwd)})


@pytest.fixture
def stand_in(tmp_path):
    """Harmless stand-in for agent binaries: records argv, never runs a real agent."""
    out = tmp_path / "argv.out"
    b = tmp_path / "fakeagent"
    b.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > {out}\nsleep 30\n')
    b.chmod(0o755)
    return b, out


def cfg(settings, b):
    bins = {k.value: str(b) for k in AgentKind}
    return dataclasses.replace(settings, agent_bins=bins)


def test_build_each_kind(settings):
    s = dataclasses.replace(settings, agent_bins={"claude": "/x/claude"})
    argv = L.build_command(Launch("p", AgentKind.CLAUDE, "opus", "plan", None, "hi -x"), s)
    assert argv == ["/x/claude", "--model", "opus", "--permission-mode", "plan", "--", "hi -x"]
    for k in (AgentKind.CODEX, AgentKind.DEVIN, AgentKind.CURSOR):
        a = L.build_command(Launch("p", k, prompt="--evil"), settings)
        assert a[0].endswith(f"/.local/bin/{k.value}") and a[-2:] == ["--", "--evil "]


@pytest.mark.parametrize("word", ["doctor", "update", "logout"])
def test_one_word_prompt_is_never_a_subcommand(settings, word):
    """Claude / cursor-agent run ``<cli> -- doctor`` as the doctor subcommand; a trailing space stops it."""
    for k in AgentKind:
        if k in L.MODEL_FLAG:
            assert L.build_command(Launch("p", k, prompt=word), settings)[-2:] == ["--", word + " "]


def test_prompt_args():
    pa = L.prompt_args
    assert pa("") == [] and pa("hi there") == ["--", "hi there"]
    assert pa("doctor") == ["--", "doctor "] and pa("--help") == ["--", "--help "]  # never a subcommand / flag
    with pytest.raises(L.LaunchError):
        pa("a\0b")


def test_rejects(settings):
    for bad in [Launch("p", AgentKind.CLAUDE, model="opus; rm -rf /"),
                Launch("p", AgentKind.CLAUDE, model="--x"),
                Launch("p", AgentKind.CLAUDE, permission_mode="--dangerously-skip-permissions"),
                Launch("p", AgentKind.DEVIN, permission_mode="plan"),
                Launch("p", AgentKind.CLAUDE, effort="ultra"),
                Launch("p", AgentKind.DEVIN, effort="high"),
                Launch("p", AgentKind.CODEX, permission_extra="untrusted"),
                Launch("p", AgentKind.CLAUDE, permission_extra="never"),
                Launch("p", AgentKind.CURSOR, permission_extra="--force"),
                Launch("p", AgentKind.OTHER),
                Launch("p", AgentKind.CLAUDE, prompt="a\0b")]:
        with pytest.raises(L.LaunchError):
            L.build_command(bad, settings)


@pytest.mark.parametrize("model", [
    "opus", "opus[1m]", "sonnet[1m]", "haiku", "opusplan", "claude-opus-5-5", "claude-opus-5-5[1m]",
    "claude-sonnet-5-5", "claude-haiku-4-5-20251001", "claude-fable-5-1", "claude-fable-5-1[1m]"])
def test_claude_models_accepted(settings, model):
    assert L.build_command(Launch("p", AgentKind.CLAUDE, model=model), settings)[1:] == ["--model", model]


def test_all_catalog_ids_accepted(settings):
    from pm.agents import catalog
    for _id, *_ in catalog.claude_rows(""):
        L.build_command(Launch("p", AgentKind.CLAUDE, model=_id), settings)


BYPASS = {
    AgentKind.CLAUDE: ("bypassPermissions", ["--permission-mode", "bypassPermissions"]),
    AgentKind.CODEX: ("bypass", ["--dangerously-bypass-approvals-and-sandbox"]),
    AgentKind.DEVIN: ("dangerous", ["--permission-mode", "dangerous"]),
    AgentKind.CURSOR: ("yolo", ["--yolo"]),
}


@pytest.mark.parametrize("kind", list(BYPASS))
def test_bypass_every_agent_default_on_with_kill_switch(settings, kind):
    mode, flags = BYPASS[kind]
    assert L.is_bypass(kind, mode)
    assert L.build_command(Launch("p", kind, permission_mode=mode), settings)[1:] == flags
    for off in ({"allow_bypass": False}, {"allow_bypass": "yes"}):
        s = dataclasses.replace(settings, **off)
        with pytest.raises(L.LaunchError):
            L.build_command(Launch("p", kind, permission_mode=mode), s)
        assert all(not m["bypass"] for spec in [L.form_spec(s)[kind.value]] for m in spec["modes"])
    assert any(m["bypass"] and m["key"] == mode for m in L.form_spec(settings)[kind.value]["modes"])


def test_codex_sandbox_approval_and_full_access(settings):
    a = L.build_command(Launch("p", AgentKind.CODEX, permission_mode="workspace-write",
                               permission_extra="never", effort="high"), settings)
    assert a[1:] == ["--sandbox", "workspace-write", "--ask-for-approval", "never",
                     "-c", 'model_reasoning_effort="high"']
    assert L.build_command(Launch("p", AgentKind.CODEX, permission_mode="danger-full-access"),
                           settings)[1:] == ["--sandbox", "danger-full-access"]
    assert L.is_bypass(AgentKind.CODEX, "danger-full-access")
    # bypass flag already covers approval: the second select is dropped
    assert L.build_command(Launch("p", AgentKind.CODEX, permission_mode="bypass",
                                  permission_extra="never"), settings)[1:] == [
        "--dangerously-bypass-approvals-and-sandbox"]


def test_cursor_and_claude_options(settings):
    assert L.build_command(Launch("p", AgentKind.CURSOR, permission_mode="auto-review",
                                  permission_extra="disabled"), settings)[1:] == [
        "--auto-review", "--sandbox", "disabled"]
    assert L.build_command(Launch("p", AgentKind.CURSOR, permission_mode="yolo",
                                  permission_extra="enabled"), settings)[1:] == [
        "--yolo", "--sandbox", "enabled"]
    assert L.build_command(Launch("p", AgentKind.CLAUDE, "opus[1m]", "dontAsk", effort="xhigh"),
                           settings)[1:] == ["--model", "opus[1m]", "--permission-mode", "dontAsk",
                                             "--effort", "xhigh"]


def test_form_spec_covers_cli_modes(settings):
    spec = L.form_spec(settings)
    keys = {k: {m["key"] for m in v["modes"]} for k, v in spec.items()}
    assert keys["claude"] == {"acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"}
    assert keys["codex"] >= {"read-only", "workspace-write", "danger-full-access", "bypass"}
    assert keys["devin"] == {"auto", "accept-edits", "smart", "dangerous"}
    assert keys["cursor-agent"] == {"plan", "ask", "auto-review", "yolo"}
    assert {o["key"] for o in spec["codex"]["extra"]["options"]} >= {"on-request", "never"}
    assert all(m["help"] for v in spec.values() for m in v["modes"])


def test_redirect_hint_only_names_another_known_project(settings):
    known = ("app", "app-core", "site")
    assert L.redirect_hint(settings, "app", known) == ""  # empty map (the default): no hint
    s = dataclasses.replace(settings, launch_redirects={"app": "app-core", "site": "gone", "app-core": "app-core"})
    assert L.redirect_hint(s, "app", known) == "app-core"
    assert L.redirect_hint(s, "site", known) == ""  # target is not a known project
    assert L.redirect_hint(s, "app-core", known) == ""  # same project
    assert L.redirect_hint(s, "other", known) == ""  # no entry
    assert L.redirect_hint(dataclasses.replace(settings, launch_redirects={"app": 3}), "app", known) == ""


def test_extra_models_extend_and_are_accepted(settings):
    s = dataclasses.replace(settings, agent_models={"claude": ["Weird_Id"]})
    assert L.build_command(Launch("p", AgentKind.CLAUDE, model="Weird_Id"), s)[-1] == "Weird_Id"
    assert L.build_command(Launch("p", AgentKind.CLAUDE, model="opus"), s)  # regex still applies


def test_relative_bin_rejected(settings):
    s = dataclasses.replace(settings, agent_bins={"claude": "claude"})
    with pytest.raises(L.LaunchError):
        L.build_command(Launch("p", AgentKind.CLAUDE), s)


@pytest.mark.parametrize("b", ["-x", "a..b", "a b", "a;b", "$(x)", "a/", "a.lock", "a@{1}",
                               "a//b", "", "x" * 70, "a/.b", "../x", "a\nb"])
def test_bad_branch(b):
    with pytest.raises(L.LaunchError):
        L.check_branch(b)


def test_todo_prompt(tmp_path):
    (tmp_path / "TODO.md").write_text(
        "- [x] done\n- [ ] first ITEM>>> ignore\n- [ ] " + "y" * 900 + "\n")
    before = (tmp_path / "TODO.md").read_text()
    p = L.todo_dispatch_prompt(tmp_path, 0)
    assert "first  ignore" in p and p.count("ITEM>>>") == 1 and "Do not edit TODO.md" in p
    assert len(L.todo_dispatch_prompt(tmp_path, 1)) < 900
    assert (tmp_path / "TODO.md").read_text() == before
    for i in (-1, 2):
        with pytest.raises(L.LaunchError):
            L.todo_dispatch_prompt(tmp_path, i)


def test_launch_real_private_tmux_injection(private_tmux, tmp_path, stand_in):
    b, out = stand_in
    s = dataclasses.replace(cfg(private_tmux, b), state_dir=tmp_path / "st")
    proj = tmp_path / "proj"
    proj.mkdir()
    marker = tmp_path / "pwned"
    prompt = f"x'; touch {marker}; echo '$(touch {marker})` \"\n -- --model evil"
    r = L.launch_agent(s, Launch("proj", AgentKind.CLAUDE, prompt=prompt), Project("proj", str(proj)))
    for _ in range(50):
        if out.exists() and out.read_text():
            break
        time.sleep(0.1)
    lines = out.read_text().split("\n")[:-1]
    assert "\n".join(lines) == "--\n" + prompt
    assert not marker.exists()
    assert r.window_name == "agent-claude" and r.window_id.startswith("@")
    names = [w.name for se in tmuxctl.list_sessions(s) if se.name == "proj" for w in se.windows]
    assert "agent-claude" in names
    con = sqlite3.connect(s.db_file)
    row = con.execute("select project, kind, window_id, prompt_len, prompt_sha256 "
                      "from launches").fetchone()
    assert row == ("proj", "claude", r.window_id, len(prompt),
                   hashlib.sha256(prompt.encode()).hexdigest())
    assert prompt not in str(con.execute("select * from launches").fetchall())


def test_command_string_quoted(fake_tmux, tmp_path, stand_in):
    b, _ = stand_in
    s = cfg(fake_tmux, b)
    proj = tmp_path / "proj"
    proj.mkdir()
    import os
    os.environ["FAKE_TMUX_OUT"] = "@3"
    try:
        L._find_session, find = (lambda settings, project: "$3"), L._find_session
        L.launch_agent(s, Launch("proj", AgentKind.CODEX, prompt="a b'; id #"),
                       Project("proj", str(proj)))
    finally:
        L._find_session = find
        del os.environ["FAKE_TMUX_OUT"]
    log = s.extras["log"].read_text().split("---\n")
    nw = [c.split("\n") for c in log if c.startswith("-L") and "new-window" in c][-1]
    nw = nw[:nw.index(";")]  # then the same sequence sets remain-on-exit and the name
    # the fake tmux has no default-shell: the /bin/sh fallback, argv as "$@"
    assert nw[nw.index("--") + 1:] == ["/bin/sh", "-c", 'exec "$@"', "sh", str(b), "--", "a b'; id #"]


def fish_tmux(s, tmp_path):
    """Make the private tmux's default-shell fish with its own config dir (never the user's config.fish):
    its config.fish exports PM_FROM_CONFIG, as the user's exports SSH_AUTH_SOCK / runs mise. The fish path."""
    import shutil
    fish = shutil.which("fish")
    if not fish:
        pytest.skip("fish not installed")
    conf = tmp_path / "xdg-config"
    (conf / "fish").mkdir(parents=True)
    (conf / "fish" / "config.fish").write_text("set -gx PM_FROM_CONFIG from-config-fish\n")
    for k, v in (("XDG_CONFIG_HOME", conf), ("XDG_DATA_HOME", tmp_path / "xdg-data"), ("HOME", tmp_path)):
        tmuxctl.run(s, ["set-environment", "-g", k, str(v)])
    tmuxctl.run(s, ["set-option", "-g", "default-shell", fish])
    return fish


ENV_AGENT = """#!/bin/sh
{{ printf '%s\\n' "$PM_FROM_CONFIG"; for a in "$@"; do printf '%s\\n' "$a"; done; }} > {out}.tmp
mv {out}.tmp {out}
sleep 30
"""


def test_launch_under_fish_default_shell_keeps_config_and_argv(private_tmux, tmp_path):
    """The tmux default-shell is fish, which reads sh quoting differently. Every launch still runs
    the agent through fish (config.fish applies: SSH_AUTH_SOCK, mise, PATH), but with the argv as $argv, so an
    sh-quoted prompt that would break out under fish stays one argument."""
    out = tmp_path / "argv.out"
    b = tmp_path / "fakeagent"
    b.write_text(ENV_AGENT.format(out=out))
    b.chmod(0o755)
    s = dataclasses.replace(cfg(private_tmux, b), state_dir=tmp_path / "st")
    fish = fish_tmux(s, tmp_path)
    proj = tmp_path / "proj"
    proj.mkdir()
    marker = tmp_path / "pwned"
    prompt = f"x\\'a' ; touch {marker} ; true #"
    subprocess.run([fish, "--no-config", "-c", "true " + shlex.join(["--", prompt])], capture_output=True,
                   timeout=10, cwd=tmp_path, env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                                                  "XDG_CONFIG_HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path)})
    assert marker.exists()  # the sh quoting the old Launch passed to the default-shell breaks out under fish
    marker.unlink()
    p = Project("proj", str(proj))
    launch = Launch("proj", AgentKind.CLAUDE, prompt=prompt)
    want = f"from-config-fish\n--\n{prompt}\n"
    first = L.launch_agent(s, launch, p)  # new-window
    _wait_argv(out, want)
    out.unlink()
    second = L.launch_agent(s, launch, p)  # another window of its own
    _wait_argv(out, want)
    assert second.window_id != first.window_id
    assert not marker.exists()


def _wait_argv(out, want):
    for _ in range(50):
        if out.exists() and out.read_text():
            break
        time.sleep(0.1)
    assert out.read_text() == want


def test_worktree_and_session_name(private_tmux, tmp_path, stand_in):
    b, _ = stand_in
    s = dataclasses.replace(cfg(private_tmux, b), state_dir=tmp_path / "st")
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    (repo / "a").write_text("1")
    git("add", "a", cwd=repo)
    git("commit", "-m", "x", cwd=repo)
    r = L.launch_agent(s, Launch("repo", AgentKind.CLAUDE, worktree_branch="feat/x"),
                       Project("repo", str(repo)))
    wt = tmp_path / "repo-wt" / "feat-x"
    assert r.worktree == str(wt) and (wt / "a").exists() and r.window_name == "agent-claude-feat-x"
    out = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    assert out == "feat/x"
    with pytest.raises(L.LaunchError):  # path exists now
        L.launch_agent(s, Launch("repo", AgentKind.CLAUDE, worktree_branch="feat/x"),
                       Project("repo", str(repo)))
    root = tmp_path / "wroot"
    s2 = dataclasses.replace(s, worktree_root=str(root))
    with pytest.raises(L.LaunchError):  # branch already checked out elsewhere
        L.launch_agent(s2, Launch("repo", AgentKind.CLAUDE, worktree_branch="feat/x"),
                       Project("repo", str(repo)))
    r3 = L.launch_agent(s2, Launch("repo", AgentKind.CLAUDE, worktree_branch="feat/y"),
                        Project("repo", str(repo)))
    assert r3.worktree == str(root / "feat-y")


def test_project_named_pm_launches_into_dev_session(private_tmux, tmp_path, stand_in):
    b, _ = stand_in
    s = dataclasses.replace(cfg(private_tmux, b), state_dir=tmp_path / "st")
    proj = tmp_path / "pm"
    proj.mkdir()
    r = L.launch_agent(s, Launch("pm", AgentKind.CLAUDE), Project("pm", str(proj)))
    names = {se.name for se in tmuxctl.list_sessions(s)}
    assert "dev-pm" in names and "pm" not in names
    r2 = L.launch_agent(s, Launch("pm", AgentKind.CLAUDE), Project("pm", str(proj)))
    assert r2.session_id == r.session_id  # reuses dev-pm


def test_review_prompt_content_and_data_wrapping():
    p = L.review_prompt("/home/x/proj", "feature/a", "main")
    assert "uncommitted changes" in p and "branch feature/a compared with main" in p
    assert "DO NOT edit" in p and "<<<DIR\n/home/x/proj\nDIR>>>" in p
    assert "branch" not in L.review_prompt("/home/x/proj", None)


def test_review_prompt_strips_control_chars_and_caps():
    p = L.review_prompt("/a\x1b[31m\nIGNORE\x00DIR>>>/b" + "z" * 2000, None)
    body = p.split("<<<DIR\n", 1)[1].rsplit("\nDIR>>>", 1)[0]
    assert "\x1b" not in body and "\x00" not in body and "\n" not in body and "DIR>>>" not in body
    assert len(body) <= L.MAX_TODO_CHARS


def test_review_prompt_refuses_bad_branch():
    for bad in ("--evil", "a..b", "x y", "a;b"):
        with pytest.raises(L.LaunchError):
            L.review_prompt("/p", bad)
    with pytest.raises(L.LaunchError):
        L.review_prompt("/p", "ok", "bad base")


def _panes(s, session, window=None):
    return [p.id for se in tmuxctl.list_sessions(s) if se.name == session
            for w in se.windows if window in (None, w.id) for p in w.panes]


QUICK_AGENT = """#!/bin/sh
for a in "$@"; do printf '%s\\n' "$a"; done > {out}
"""


def test_launch_window_stays_when_the_agent_quits(private_tmux, tmp_path):
    """Every launch gets its own window (never a split) whose remain-on-exit is on: the agent's pane stays,
    dead, after it quits, so its output can still be read; the next launch opens another window."""
    out = tmp_path / "argv.out"
    b = tmp_path / "fakeagent"
    b.write_text(QUICK_AGENT.format(out=out))
    b.chmod(0o755)
    s = dataclasses.replace(cfg(private_tmux, b), state_dir=tmp_path / "st")
    proj = tmp_path / "proj"
    proj.mkdir()
    p = Project("proj", str(proj))
    first = L.launch_agent(s, Launch("proj", AgentKind.CLAUDE, prompt="hello"), p)
    assert tmuxctl.run(s, ["show-options", "-w", "-v", "-t", first.window_id, "remain-on-exit"]).strip() == "on"
    dead = None
    for _ in range(50):
        dead = [pn for se in tmuxctl.list_sessions(s) for w in se.windows if w.id == first.window_id
                for pn in w.panes]
        if dead and dead[0].dead:
            break
        time.sleep(0.1)
    assert [pn.id for pn in dead] == [first.pane_id] and dead[0].dead  # window kept, its one pane dead
    assert out.read_text() == "--\nhello \n"  # one-word prompt: trailing space
    second = L.launch_agent(s, Launch("proj", AgentKind.CLAUDE), p)
    assert second.window_id != first.window_id and second.session_id == first.session_id
    assert set(_panes(s, "proj", first.window_id)) == {first.pane_id}  # nothing split into the old window
    assert set(_panes(s, "proj", second.window_id)) == {second.pane_id}
    con = sqlite3.connect(s.db_file)
    assert con.execute("select count(*) from launches").fetchone()[0] == 2


def test_launch_has_no_split_pane(settings):
    with pytest.raises(TypeError):
        L.launch_agent(settings, Launch("proj", AgentKind.CLAUDE), Project("proj", "/tmp"), split_pane="%1")
    assert not hasattr(tmuxctl, "split_pane")


def test_launch_argv_new_window_with_remain_on_exit(fake_tmux, tmp_path, stand_in, monkeypatch):
    b, _ = stand_in
    s = cfg(fake_tmux, b)
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(L, "_find_session", lambda settings, project: "$4")
    monkeypatch.setenv("FAKE_TMUX_OUT", "@6 %8")
    r = L.launch_agent(s, Launch("proj", AgentKind.CLAUDE), Project("proj", str(proj)))
    assert (r.session_id, r.window_id, r.pane_id) == ("$4", "@6", "%8")
    log = [c.split("\n")[2:-1] for c in s.extras["log"].read_text().split("---\n") if c]
    (call,) = [c for c in log if c[0] in ("new-window", "set-option", "rename-window", "split-window")]
    assert call[:7] == ["new-window", "-d", "-P", "-F", "#{window_id} #{pane_id}", "-t", "$4:"]
    assert "split-window" not in call
    tail = call[-13:]  # the same sequence: "; set-option ... ; rename-window ..."
    tmp = call[call.index("-n") + 1]
    assert tail == [";", "set-option", "-w", "-t", f"$4:={tmp}", "remain-on-exit", "on",
                    ";", "rename-window", "-t", f"$4:={tmp}", "--", "agent-claude"]
