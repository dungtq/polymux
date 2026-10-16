import asyncio
import dataclasses
import hashlib
import json
import subprocess
import time

import pytest
from helpers import ORIGIN, audit_text, fake, mk_client, post, snap_with  # noqa: F401  (fake is a fixture)

from pm.agents import deliver
from pm.agents import message as msg
from pm.chat import outbox
from pm.core.models import Agent, AgentKind, AgentState, Tier
from pm.infra import snapshot as services
from pm.security.audit import AuditError
from pm.tmux import ctl as tmuxctl

GIT_ENV = {"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def test_guard_failures(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(dataclasses.replace(st, require_unlock=True),
                                      unlocked=False)
    assert post(c, tok, "/a/claude-1/message", text="hi").status_code == 403  # locked
    sess.unlocked_until = time.time() + 99
    assert post(c, tok, "/a/claude-1/message", csrf=False, text="hi").status_code == 403
    assert post(c, "bad", "/a/claude-1/message", text="hi").status_code == 403
    assert post(c, tok, "/a/claude-1/message", origin="https://evil.example",
                text="hi").status_code == 403
    app, c, sess, tok, st = mk_client(st, ceiling=Tier.SAFE)
    for url in ("/a/claude-1/message", "/a/claude-1/key", "/a/claude-1/stop", "/panic",
                "/tmux/session/s2/kill", "/p/x/launch"):
        assert post(c, tok, url, text="hi").status_code == 403, url
    assert not calls


def test_default_needs_no_unlock(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st, unlocked=False)
    assert sess.unlocked_until == 0 and not st.require_unlock
    assert post(c, tok, "/a/claude-1/message", text="hi").status_code == 200
    assert post(c, "bad", "/a/claude-1/message", text="hi").status_code == 403


def test_unknown_agent_404_and_bad_input(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/a/nope-9/message", text="hi").status_code == 404
    assert post(c, tok, "/a/claude-1/key", key="C-z").status_code == 400
    assert post(c, tok, "/a/claude-1/snippet", index="99").status_code == 400
    assert post(c, tok, "/a/claude-1/snippet", index="x").status_code == 400
    assert post(c, tok, "/a/claude-1/message", text="  ").status_code == 400
    assert '"result":"denied"' in audit_text(st)


def test_message_real_pane_and_audit(private_tmux, tmp_path, monkeypatch):
    s = dataclasses.replace(private_tmux, state_dir=tmp_path / "st", token_file=tmp_path / "tok",
                            totp_file=tmp_path / "totp", dev_root=tmp_path / "dev",
                            extra_projects=())
    out = tmp_path / "got.txt"
    sid = tmuxctl.new_session(s, "pmtest-route")
    pane = next(p for ss in tmuxctl.list_sessions(s) if ss.id == sid
                for w in ss.windows for p in w.panes)
    tmuxctl.run(s, ["send-keys", "-t", pane.id, "--", f"cat > {out}", "Enter"])
    a = Agent(id="claude-7", kind=AgentKind.CLAUDE, pane_id=pane.id, session_id=sid,
              pid=pane.pid, cwd="/")
    snap_with(monkeypatch, [a])
    app, c, sess, tok, st = mk_client(s)
    secret = "super-secret-needle-123"
    r = post(c, tok, "/a/claude-7/message", text=secret)  # stored in the outbox, not typed by the request
    assert r.status_code == 200 and "ci-ob" in r.text
    (m,) = outbox.pending(st, "claude-7")
    monkeypatch.setattr(deliver, "_D", deliver.Deliverer(st, lambda x: x.state is AgentState.IDLE, lambda _aid: a))
    assert asyncio.run(deliver.send_now(st, a, m.id)).state in ("queued", "inpane")
    assert post(c, tok, "/a/claude-7/key", key="y").status_code == 200
    assert post(c, tok, "/a/claude-7/interrupt").status_code == 200
    for _ in range(30):
        if tmuxctl.capture_pane(s, pane.id).count(secret):
            break
        time.sleep(0.2)
    assert secret in tmuxctl.capture_pane(s, pane.id)
    log = audit_text(st)
    assert secret not in log and hashlib.sha256(secret.encode()).hexdigest() not in log
    assert f'"len":{len(secret)}' in log and f'"outbox":{m.id}' in log
    assert app.state.hub.last_id >= 3  # outbox + inbox events published
    # pane that no longer runs the agent -> 409
    snap_with(monkeypatch, [dataclasses.replace(a, pid=2**22 - 5)])
    assert post(c, tok, "/a/claude-7/key", key="y").status_code == 409


def test_stop_reports_still_running(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    from pm.agents import stop as stop_mod
    from pm.agents.stop import StopResult
    monkeypatch.setattr(stop_mod, "stop_agent",
                        lambda s, a: StopResult(a.id, "still_running", "x"))
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/claude-1/stop")
    assert r.status_code == 200 and "flash err" in r.text and "still running" in r.text
    assert '"status":"still_running"' in audit_text(st)


def test_panic_needs_word(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent, dataclasses.replace(agent, id="claude-2", project="pm")])
    from pm.agents import stop as stop_mod
    seen = []
    monkeypatch.setattr(stop_mod, "panic", lambda s, ags, scope: seen.append((len(ags), scope))
                        or {a.id: stop_mod.StopResult(a.id, "stopped") for a in stop_mod.select(ags, scope)})
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/panic", scope="all").status_code == 403
    assert post(c, tok, "/panic", scope="all", confirm="panic").status_code == 403
    assert not seen
    r = post(c, tok, "/panic", scope="all", confirm="PANIC")
    assert r.status_code == 200 and "claude-1: stopped" in r.text and seen == [(2, "all")]
    assert post(c, tok, "/panic", scope="bogus", confirm="PANIC").status_code == 400


def test_kills(fake, monkeypatch):
    st, calls, agent = fake
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/tmux/session/s1/kill", confirm="prot").status_code == 403  # protected
    assert post(c, tok, "/tmux/window/w1/kill", confirm="w").status_code == 403
    assert post(c, tok, "/tmux/pane/p1/kill", confirm="%1").status_code == 403
    assert post(c, tok, "/tmux/session/s2/kill", confirm="wrong").status_code == 403
    assert post(c, tok, "/tmux/session/w2/kill", confirm="free1").status_code == 400
    assert post(c, tok, "/tmux/session/s99/kill", confirm="x").status_code == 403
    assert not calls
    assert '"result":"denied"' in audit_text(st)
    assert post(c, tok, "/tmux/session/s2/kill", confirm="free1").status_code == 200
    assert post(c, tok, "/tmux/pane/p2/kill", confirm="%2").status_code == 200
    assert [a[0] for a in calls] == ["kill-session", "kill-pane"]
    assert app.state.hub.last_id >= 2  # tmux_changed: an inbox event per kill (and a forced snapshot rebuild)
    monkeypatch.setenv("TMUX_PANE", "%2")  # own session now refused
    assert post(c, tok, "/tmux/window/w2/kill", confirm="w").status_code == 403


def test_audit_failure_blocks(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)

    class Broken:
        def write(self, e):
            raise AuditError("x")
    app.state.audit = Broken()
    assert post(c, tok, "/a/claude-1/key", key="y").status_code == 503
    assert post(c, tok, "/tmux/session/s2/kill", confirm="free1").status_code == 503
    assert not calls
    # a message is stored first, but a failed audit cancels it so the deliverer never sends it
    assert post(c, tok, "/a/claude-1/message", text="hello").status_code == 503
    from pm.chat import outbox
    assert [m.state for m in outbox.pending(st, "claude-1")] == []


def _repo(tmp_path):
    repo = tmp_path / "dev" / "repo"
    repo.mkdir(parents=True)
    env = {**GIT_ENV, "HOME": str(tmp_path)}
    for a in (["init", "-b", "main"], ["add", "a"], ["commit", "-m", "x"]):
        if a[0] == "add":
            (repo / "a").write_text("1")
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, env=env)
    (repo / "TODO.md").write_text("- [x] done\n- [ ] first open\n- [ ] second open\n")
    return repo


def test_launch_worktree_and_dispatch(private_tmux, tmp_path, monkeypatch):
    out = tmp_path / "argv.out"
    b = tmp_path / "fakeagent"
    b.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > {out}\nsleep 30\n')
    b.chmod(0o755)
    repo = _repo(tmp_path)
    s = dataclasses.replace(
        private_tmux, state_dir=tmp_path / "st", token_file=tmp_path / "tok",
        totp_file=tmp_path / "totp", dev_root=tmp_path / "dev", extra_projects=(),
        agent_bins={k.value: str(b) for k in AgentKind}, worktree_root=str(tmp_path / "wt"))
    app, c, sess, tok, st = mk_client(s)
    prompt = "do the secret thing"
    r = post(c, tok, "/p/repo/launch", kind="claude", model="", permission_mode="plan",
             branch="feat/x", prompt=prompt)
    assert r.status_code == 303 and r.headers["location"] == "/p/repo"
    assert (tmp_path / "wt" / "feat-x" / "a").exists()
    for _ in range(30):
        if out.exists() and prompt in out.read_text():
            break
        time.sleep(0.2)
    assert prompt in out.read_text()
    assert prompt not in audit_text(st) and "agent_launch" in audit_text(st)
    # HTMX gets a 200 result fragment with a link to the new agent, never a 303
    r = c.post("/p/repo/launch", headers={"Origin": ORIGIN, "HX-Request": "true"},
               data={"csrf": tok, "kind": "claude", "prompt": "hx"})
    assert r.status_code == 200 and "Launched claude in window" in r.text
    import re as _re
    assert _re.search(r'href="/a/claude-\d+"', r.text) and 'href="/p/repo"' in r.text
    r = c.post("/p/repo/launch", headers={"Origin": ORIGIN, "HX-Request": "true"},
               data={"csrf": tok, "kind": "claude", "branch": "../x"})
    assert r.status_code == 400 and "launch refused" in r.text
    assert post(c, tok, "/p/repo/launch", kind="nope").status_code == 400
    assert post(c, tok, "/p/repo/launch", kind="claude", branch="../x").status_code == 400
    assert post(c, tok, "/p/missing/launch", kind="claude").status_code == 404
    out.unlink()
    r = post(c, tok, "/p/repo/dispatch", index="1", kind="claude")
    assert r.status_code == 303
    for _ in range(30):
        if out.exists() and "second open" in out.read_text():
            break
        time.sleep(0.2)
    assert "second open" in out.read_text() and "first open" not in out.read_text()
    assert "second open" not in audit_text(st)
    assert post(c, tok, "/p/repo/dispatch", index="5").status_code == 400
    assert (repo / "TODO.md").read_text().count("[ ]") == 2


def test_send_failure_reason_in_flash_and_audit(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)

    def boom(s, a, timeout=0, **kw):
        raise tmuxctl.TmuxError("can't find pane: %1\x1b[31m")
    monkeypatch.setattr(tmuxctl, "run", boom)
    r = post(c, tok, "/a/claude-1/snippet", index="0")  # snippets still type in directly
    assert r.status_code == 409
    assert "send failed: tmux: can't find pane: %1" in r.text
    assert "\x1b" not in r.text
    log = audit_text(st)
    assert '"reason":"send failed"' in log and "can't find pane" in log
    assert "sha256" in log


def test_sent_not_confirmed_when_screen_unchanged(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    r = post(c, tok, "/a/claude-1/snippet", index="0")  # fake capture-pane returns ""
    assert r.status_code == 200 and "Sent (not confirmed)" in r.text
    assert '"submitted":0' in audit_text(st)


def test_all_quick_keys_accepted(fake, monkeypatch):
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    app, c, sess, tok, st = mk_client(st)
    for k in msg.QUICK_KEYS:
        assert post(c, tok, "/a/claude-1/key", key=k).status_code == 200, k
    assert post(c, tok, "/a/claude-1/key", key="C-z").status_code == 400


def test_message_routes_never_build_a_snapshot(fake, monkeypatch):
    """message / key / interrupt / stop read the published snapshot: no (forced) rebuild."""
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])  # published=snap; _build fails the test if reached
    before = services.build_count()

    def boom(*a, **k):
        pytest.fail("build_snapshot called by a mutation route")

    monkeypatch.setattr(services, "build_snapshot", boom)
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/a/claude-1/message", text="hi").status_code == 200
    assert post(c, tok, "/a/claude-1/key", key="y").status_code == 200
    assert post(c, tok, "/a/claude-1/interrupt").status_code == 200
    assert services.build_count() == before


def test_launch_model_choice_and_page(private_tmux, tmp_path):
    _repo(tmp_path)
    s = dataclasses.replace(
        private_tmux, state_dir=tmp_path / "st", token_file=tmp_path / "tok",
        totp_file=tmp_path / "totp", dev_root=tmp_path / "dev", extra_projects=(),
        agent_bins={k.value: "/bin/true" for k in AgentKind})
    app, c, sess, tok, st = mk_client(s)
    hx = {"Origin": ORIGIN, "HX-Request": "true"}
    # preset wins; a preset that fails validation is refused like any model
    r = c.post("/p/repo/launch", headers=hx,
               data={"csrf": tok, "kind": "claude", "model_choice": "Bad Model!"})
    assert r.status_code == 400 and "launch refused" in r.text
    # custom uses the free-text field and is validated the same way
    r = c.post("/p/repo/launch", headers=hx,
               data={"csrf": tok, "kind": "claude", "model_choice": "custom", "model": "x y"})
    assert r.status_code == 400


def _bypass_client(private_tmux, tmp_path, agent_bin, **kw):
    _repo(tmp_path)
    s = dataclasses.replace(
        private_tmux, state_dir=tmp_path / "st", token_file=tmp_path / "tok",
        totp_file=tmp_path / "totp", dev_root=tmp_path / "dev", extra_projects=(),
        agent_bins={k.value: str(agent_bin) for k in AgentKind}, **kw)
    return mk_client(s)


def _sleeper(tmp_path):
    b = tmp_path / "fakeagent"
    b.write_text("#!/bin/sh\nsleep 30\n")
    b.chmod(0o755)
    return b


def test_bypass_needs_confirm_and_is_audited(private_tmux, tmp_path):
    app, c, sess, tok, st = _bypass_client(private_tmux, tmp_path, _sleeper(tmp_path))
    hx = {"Origin": ORIGIN, "HX-Request": "true"}
    for kind, mode in (("claude", "bypassPermissions"), ("codex", "bypass"), ("devin", "dangerous"),
                       ("cursor-agent", "yolo"), ("codex", "danger-full-access")):
        r = c.post("/p/repo/launch", headers=hx,
                   data={"csrf": tok, "kind": kind, "permission_mode": mode})
        assert r.status_code == 400, (kind, mode)
        r = c.post("/p/repo/dispatch", headers=hx,
                   data={"csrf": tok, "kind": kind, "index": "0", "permission_mode": mode})
        assert r.status_code == 400, (kind, mode)
    log = audit_text(st)
    assert log.count("bypass not confirmed") == 10 and '"bypass":1' in log
    assert '"permission_mode":"bypassPermissions"' in log
    # confirmed: launched, audited with permission_mode and bypass=1
    r = c.post("/p/repo/launch", headers=hx, data={
        "csrf": tok, "kind": "claude", "permission_mode": "bypassPermissions",
        "confirm_bypass": "1", "model_choice": "opus", "variant": "opus[1m]", "effort": "high"})
    assert r.status_code == 200 and "Launched claude" in r.text
    last = audit_text(st).strip().splitlines()[-1]
    assert '"result":"ok"' in last and '"bypass":1' in last
    assert '"permission_mode":"bypassPermissions"' in last and '"model":"opus[1m]"' in last
    assert '"effort":"high"' in last


def test_non_bypass_launch_needs_no_confirm_and_has_no_bypass_flag(private_tmux, tmp_path):
    app, c, sess, tok, st = _bypass_client(private_tmux, tmp_path, _sleeper(tmp_path))
    r = c.post("/p/repo/launch", headers={"Origin": ORIGIN, "HX-Request": "true"}, data={
        "csrf": tok, "kind": "codex", "permission_mode": "workspace-write",
        "permission_extra": "never"})
    assert r.status_code == 200
    last = audit_text(st).strip().splitlines()[-1]
    assert '"permission_extra":"never"' in last and "bypass" not in last


def test_bypass_kill_switch_refuses_even_when_confirmed(private_tmux, tmp_path):
    app, c, sess, tok, st = _bypass_client(private_tmux, tmp_path, _sleeper(tmp_path),
                                           allow_bypass=False)
    r = c.post("/p/repo/launch", headers={"Origin": ORIGIN, "HX-Request": "true"}, data={
        "csrf": tok, "kind": "claude", "permission_mode": "bypassPermissions",
        "confirm_bypass": "1"})
    assert r.status_code == 400
    assert "bypass permission mode is not enabled" in audit_text(st)


def test_launch_from_popup_redirect_stays_framable(private_tmux, tmp_path):
    import re as _re
    app, c, sess, tok, st = _bypass_client(private_tmux, tmp_path, _sleeper(tmp_path))
    r = c.post("/p/repo/launch", headers={"Origin": ORIGIN, "HX-Request": "true",
                                           "HX-Current-URL": "http://x/p/repo/launch?embed=1"},
               data={"csrf": tok, "kind": "claude", "open": "1"})
    assert _re.fullmatch(r"/a/claude-\d+\?embed=1", r.headers["HX-Redirect"])


def test_launch_open_redirects_without_polling(private_tmux, tmp_path, monkeypatch):
    """#157: open=1 answers after ONE fresh snapshot (no 3 s poll for the registry): the pane based id
    when the agent is not detected yet; the agent page redirects it once it resolves."""
    from pm.web.routes import agents as routes_agents
    app, c, sess, tok, st = _bypass_client(private_tmux, tmp_path, _sleeper(tmp_path))
    calls, real = [], services.snapshot_async

    async def counted(settings, max_age_s=services.FALLBACK_MAX_AGE_S):
        calls.append(max_age_s)
        return await real(settings, max_age_s)
    monkeypatch.setattr(routes_agents.services, "snapshot_async", counted)
    t0 = time.monotonic()
    r = c.post("/p/repo/launch", headers={"Origin": ORIGIN, "HX-Request": "true"},
               data={"csrf": tok, "kind": "claude", "open": "1"})
    assert r.status_code == 200 and r.headers["HX-Redirect"].startswith("/a/claude-")
    assert calls == [0.0] and time.monotonic() - t0 < 2.0


def test_launch_remembers_choices_and_open_redirects(private_tmux, tmp_path):
    from pm.core import prefs
    app, c, sess, tok, st = _bypass_client(private_tmux, tmp_path, _sleeper(tmp_path))
    hx = {"Origin": ORIGIN, "HX-Request": "true"}
    r = c.post("/p/repo/launch", headers=hx, data={
        "csrf": tok, "kind": "claude", "permission_mode": "plan", "effort": "high",
        "model_choice": "opus", "branch": "feat/y", "open": "1"})
    assert r.status_code == 200
    import re as _re
    assert _re.fullmatch(r"/a/claude-\d+", r.headers["HX-Redirect"])
    row = prefs.load(st.prefs_file)["projects"]["repo"]
    assert row["kind"] == "claude" and row["mode"] == "plan" and row["branch"] == "feat/y"
    assert row["model_choice"] == "opus" and row["effort"] == "high"
    # plain POST + open: 303 to the agent; bypass mode is never remembered
    r = c.post("/p/repo/launch", headers={"Origin": ORIGIN}, data={
        "csrf": tok, "kind": "claude", "permission_mode": "bypassPermissions",
        "confirm_bypass": "1", "open": "1"})
    assert r.status_code == 303 and r.headers["location"].startswith("/a/claude-")
    assert prefs.load(st.prefs_file)["projects"]["repo"]["mode"] == ""


def test_prefs_projects_survive_settings_save_and_are_validated(tmp_path):
    from pm.core import prefs
    p = tmp_path / "p.json"
    prefs.remember(p, "repo", {"kind": "codex", "mode": "x;rm", "branch": "a/b"})
    row = prefs.load(p)["projects"]["repo"]
    assert row["kind"] == "codex" and "mode" not in row or row.get("mode") == ""
    prefs.save(p, {"launch": {"agent": "claude"}, "notify": {}})  # settings form: no "projects"
    assert "repo" in prefs.load(p)["projects"]
    prefs.remember(p, "../evil", {"kind": "claude"})
    assert "../evil" not in prefs.load(p)["projects"]


def test_worktree_errors_name_path_and_branch(private_tmux, tmp_path):
    from pm.agents import launch as lm
    from pm.projects import projects
    _repo(tmp_path)
    s = dataclasses.replace(private_tmux, dev_root=tmp_path / "dev", extra_projects=(),
                            worktree_root=str(tmp_path / "wt"))
    proj = next(p for p in projects.discover(s) if p.name == "repo")
    lm.create_worktree(s, proj, "feat/z")
    with pytest.raises(lm.LaunchError, match="already exists"):
        lm.create_worktree(s, proj, "feat/z")
    (tmp_path / "wt" / "feat-z").rename(tmp_path / "wt" / "moved")
    hints = lm.worktree_hints(s, proj)
    assert hints["base"] == str(tmp_path / "wt") and "feat/z" in hints["branches"]
    assert "feat/z" in hints["taken"]
    with pytest.raises(lm.LaunchError, match="already checked out"):
        lm.create_worktree(s, proj, "feat/z")


def test_dispatch_marks_item_in_progress_and_refuses_double(private_tmux, tmp_path):
    from pm.agents import launch as lm
    from pm.core import prefs
    app, c, sess, tok, st = _bypass_client(private_tmux, tmp_path, _sleeper(tmp_path))
    hx = {"Origin": ORIGIN, "HX-Request": "true"}
    r = c.post("/p/repo/dispatch", headers=hx, data={"csrf": tok, "index": "0", "kind": "claude"})
    assert r.status_code == 200 and "Launched claude" in r.text
    key = lm.item_key("first open")
    row = prefs.dispatched(st.prefs_file, "repo")[key]
    assert row["agent"].startswith("claude-") and row["pane"].startswith("%")
    r = c.post("/p/repo/dispatch", headers=hx, data={"csrf": tok, "index": "0", "kind": "claude"})
    assert r.status_code == 409 and "already dispatched" in r.text
    assert "dispatch refused: item already in progress" in audit_text(st)
    # the other item is still free; the TODO.md file is untouched
    r = c.post("/p/repo/dispatch", headers=hx, data={"csrf": tok, "index": "1", "kind": "claude"})
    assert r.status_code == 200


def test_dispatch_mark_expires_when_agent_gone(tmp_path):
    from pm.core import prefs
    p = tmp_path / "prefs.json"
    prefs.mark_dispatched(p, "repo", "0123456789abcdef", "claude-5", "%5", 1000.0)
    prefs.mark_dispatched(p, "../x", "0123456789abcdef", "claude-5", "%5", 1000.0)
    prefs.mark_dispatched(p, "repo", "zz", "claude-5", "%5", 1000.0)
    assert list(prefs.dispatched(p, "repo")) == ["0123456789abcdef"]
    assert prefs.dispatched(p, "../x") == {}
    prefs.mark_dispatched(p, "repo", "fedcba9876543210", "claude-6", "%6", 1000.0 + 8 * 86400)
    assert list(prefs.dispatched(p, "repo")) == ["fedcba9876543210"]  # old mark pruned


def test_launch_templates_and_dispatch_preview_in_data():
    from pm.agents import launch as lm
    names = [n for n, _ in lm.TEMPLATES]
    assert "review" in names and "fix tests" in names
    assert all(0 < len(t) < 400 for _, t in lm.TEMPLATES)
    assert "<<<ITEM\nx y\nITEM>>>" in lm.dispatch_prompt("x\ty")


def test_model_switch_only_listed_ids(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # no real ~/.claude settings in the placeholder
    from pm.agents import catalog
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    monkeypatch.setattr(catalog, "kind_models", lambda s, k: [("sonnet", "Sonnet", "", ""), ("opus", "Opus", "", "")])
    sent = []
    monkeypatch.setattr(msg, "send_message", lambda s, a, text, **kw: sent.append(text) or msg.SendResult(True, a.id, a.pane_id))
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/a/claude-1/model", model="gpt-5").status_code == 400
    assert post(c, tok, "/a/claude-1/model", csrf=False, model="opus").status_code == 403
    assert sent == []
    assert post(c, tok, "/a/claude-1/model", model="gpt-5").headers.get("hx-trigger") is None
    r = post(c, tok, "/a/claude-1/model", model="opus")
    assert r.status_code == 200
    assert sent == ["/model opus"]
    ev = json.loads(r.headers["hx-trigger"])["pm:composer"]  # the open composer follows the switch
    assert ev["ph"].startswith("Message to opus-5-5") and ev["phShort"] == "Message opus-5-5"
    assert ev["pill"] == "opus-5-5"  # the heading pill follows too (chat.js, #155)
    from pm.agents import head
    assert head.live_model(agent) == "claude-opus-5-5"  # shown at once, before a reply carries it


def test_effort_switch_only_listed_levels(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    st, calls, agent = fake
    snap_with(monkeypatch, [agent])
    sent = []
    monkeypatch.setattr(msg, "send_message", lambda s, a, text, **kw: sent.append(text) or msg.SendResult(True, a.id, a.pane_id))
    app, c, sess, tok, st = mk_client(st)
    assert post(c, tok, "/a/claude-1/effort", effort="ultra").status_code == 400  # codex-only level
    assert post(c, tok, "/a/claude-1/effort", effort="high; rm").status_code == 400
    assert post(c, tok, "/a/claude-1/effort", csrf=False, effort="high").status_code == 403
    assert sent == []
    r = post(c, tok, "/a/claude-1/effort", effort="xhigh")
    assert r.status_code == 200
    assert sent == ["/effort xhigh"]
    ev = json.loads(r.headers["hx-trigger"])["pm:composer"]
    assert ev["ph"].startswith("Message to ") and ev["ph"].count(" \u00b7 xhigh") == 1
    assert ev["phShort"].endswith(" \u00b7 xhigh") and "\u00b7" not in r.headers["hx-trigger"]  # ascii-safe header
    from pm.agents import head
    assert head.live_effort(agent) == "xhigh"  # shown at once (reload too), before a reply carries it


COMPOSER_LIVE = '{% from "_composer_macros.html" import composer with context %}{{ composer("live") }}'


def test_effort_panel_renders_only_with_levels():
    from pm.web.render import env
    a = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=0, cwd="/tmp")
    tpl = env.from_string(COMPOSER_LIVE)
    ctx = {"agent": a, "project": None, "csrf": "x", "models": [], "slash_catalog": [], "request": None}
    t = tpl.render(**ctx, efforts=["low", "medium", "high"], cur_effort="high")
    assert 'data-mp-panel="effort"' in t and 'action="/a/claude-1/effort"' in t
    assert '<button type="submit" name="effort" value="high" class="mp-row" role="menuitemradio" aria-checked="true"' in t
    assert 'name="effort" value="low" class="mp-row" role="menuitemradio" aria-checked="false"' in t and "(default)" not in t
    t = tpl.render(**ctx, efforts=["low", "high"], cur_effort="")
    assert 'data-mp-pill-e>default<' in t and 'aria-checked="true"' not in t  # unknown: a placeholder, no guess
    t = tpl.render(**ctx, efforts=[], cur_effort="")
    assert 'data-mp-panel="effort"' not in t and "data-mpick" not in t  # nothing to switch: no pill


def test_composer_shows_the_settings_default_labelled():
    from pm.agents import catalog
    from pm.web.render import env
    a = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=0, cwd="/tmp")
    tpl = env.from_string(COMPOSER_LIVE)
    rows = [("sonnet", "sonnet (latest)", "", ""), (catalog.CLAUDE_FULL_IDS["sonnet"], "pinned", "", ""),
            ("opus", "opus (latest)", "", "")]
    ctx = {"agent": a, "project": None, "csrf": "x", "slash_catalog": [], "request": None, "models": rows,
           "efforts": ["low", "medium", "high"]}
    t = tpl.render(**ctx, cur_model="claude-sonnet-5-5", model_is_default=True, cur_effort="medium",
                   effort_is_default=True)
    assert 'data-mp-cur-s>claude-sonnet-5-5 (default)<' in t and 'data-mp-pill-m>sonnet-5-5<' in t
    assert 'name="model" value="claude-sonnet-5-5" class="mp-row" role="menuitemradio" aria-checked="true"' in t  # not the "latest" row
    assert 'name="model" value="sonnet" class="mp-row" role="menuitemradio" aria-checked="false"' in t
    assert 'aria-checked="true" data-mp-label="medium"><span class="mp-n">medium <span class="muted">(default)</span>' in t
    t = tpl.render(**ctx, cur_model="claude-fable-9", model_is_default=True, cur_effort="max", effort_is_default=True)
    assert 'data-mp-cur-s>claude-fable-9 (default)<' in t and 'models" ' in t  # no row matches: nothing ticked in More models
    assert 'name="model" value="sonnet" class="mp-row" role="menuitemradio" aria-checked="true"' not in t
    assert 'data-mp-pill-e>max<' in t
    t = tpl.render(**ctx, cur_model="claude-sonnet-5-5", cur_effort="medium")  # a live model: no label
    assert "(default)" not in t and 'name="model" value="sonnet" class="mp-row" role="menuitemradio" aria-checked="true"' in t
    t = tpl.render(**ctx, cur_model="", cur_effort="")  # nothing known: no guess
    assert 'data-mp-pill-m>model<' in t and 'data-mp-cur-s>not known yet<' in t and 'data-mp-pill-e>default<' in t


def test_composer_placeholder_follows_agent_state(tmp_path, monkeypatch):
    from pm.web.render import env
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text('{"model": "claude-sonnet-5-5", "effortLevel": "high"}')
    a = Agent(id="claude-ph", kind=AgentKind.CLAUDE, pane_id="%1", session_id="$1", pid=0, cwd=str(tmp_path),
              label="dev-pm-19-claude")
    t = env.from_string(COMPOSER_LIVE).render(agent=a, project=None, csrf="x", models=[], efforts=[],
                                                  slash_catalog=[], request=None)
    assert 'placeholder="Message to sonnet-5-5 \u00b7 high in dev-pm:19"' in t
    assert 'data-ph-short="Message sonnet-5-5 \u00b7 high"' in t
