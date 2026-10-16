"""Sub-agents tab: listing, work view, masking, path safety, route + markup."""
import json
import os
import time
import types
from dataclasses import replace

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.agents import subagents
from pm.infra import snapshot as services

TOK = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def mk(tmp_path):
    t = tmp_path / "abc.jsonl"
    t.write_text("")
    d = tmp_path / "abc" / "subagents"
    d.mkdir(parents=True)
    (d / "agent-aaa.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "find the <b>routes</b>"}))
    lines = [
        {"type": "user", "isSidechain": True, "message": {"role": "user", "content": f"Look at routes; token {TOK}"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": "router"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
    ]
    (d / "agent-aaa.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    old = d / "agent-bbb.jsonl"
    old.write_text("{}\n")
    os.utime(old, (time.time() - 3600,) * 2)
    return t


def test_list_and_work(tmp_path):
    t = mk(tmp_path)
    subs = subagents.list_subs(t)
    assert [s.sid for s in subs] == ["agent-aaa", "agent-bbb"]
    a, b = subs
    assert a.kind == "Explore" and a.running and not b.running and b.kind == ""
    w = subagents.read_work(t, "agent-aaa")
    roles = [s.role for s in w.steps]
    assert roles == ["prompt", "text", "tool"] and w.steps[2].label == "Grep" and "router" in w.steps[2].text
    assert TOK not in w.steps[0].text  # masked like the chat


def test_no_dir_and_bad_ids(tmp_path):
    assert subagents.list_subs(None) == [] and subagents.list_subs(tmp_path / "none.jsonl") == []
    t = mk(tmp_path)
    for bad in ("../x", "agent-", "agent-a/b", "", "agent-aaa.jsonl", "x" * 99):
        assert subagents.read_work(t, bad) is None


def test_symlink_not_followed(tmp_path):
    t = mk(tmp_path)
    secret = tmp_path / "secret.jsonl"
    secret.write_text('{"type":"user","message":{"content":"S3CRET"}}\n')
    os.symlink(secret, tmp_path / "abc" / "subagents" / "agent-ccc.jsonl")
    assert "agent-ccc" not in [s.sid for s in subagents.list_subs(t)]
    assert subagents.read_work(t, "agent-ccc") is None


def _with_transcript(env, monkeypatch, tmp_path):
    s, c = env
    t = mk(tmp_path)
    base = services.agents_mod.detect_agents
    monkeypatch.setattr(services.agents_mod, "detect_agents",
                        lambda panes, **kw: [replace(a, transcript=str(t)) for a in base(panes)])
    login(s, c)
    return c


def test_route_and_tab(env, monkeypatch, tmp_path):
    c = _with_transcript(env, monkeypatch, tmp_path)
    page = c.get("/a/claude-12").text
    assert 'id="tab-sub"' in page and 'for="tab-sub"' in page and "Sub-agents" in page
    assert 'hx-get="/a/claude-12/subagents"' in page
    assert 'data-sa-poll="3000,15000"' in page and 'hx-trigger="load, every 3s"' not in page
    lst = c.get("/a/claude-12/subagents")
    assert lst.status_code == 200 and "Explore" in lst.text and "&lt;b&gt;routes" in lst.text
    assert "/a/claude-12/subagents?id=agent-aaa" in lst.text
    w = c.get("/a/claude-12/subagents?id=agent-aaa")
    assert w.status_code == 200 and "Grep" in w.text and TOK not in w.text
    assert c.get("/a/claude-12/subagents?id=agent-aaa", headers={"If-None-Match": w.headers["etag"]}).status_code == 304
    assert c.get("/a/claude-12/subagents?id=agent-zzz").status_code == 404
    assert c.get("/a/claude-12/subagents?id=..%2Fx").status_code == 404


def test_route_requires_login(env):
    s, c = env
    r = c.get("/a/claude-12/subagents", follow_redirects=False)
    assert r.status_code in (302, 303, 401)


def mk_wf(tmp_path):
    t = mk(tmp_path)
    w = tmp_path / "abc" / "subagents" / "workflows" / "wf_e4a7d81a-485"
    w.mkdir(parents=True)
    (w / "agent-wfx.meta.json").write_text(json.dumps(
        {"agentType": "workflow-subagent", "description": "newproj-tmux", "workflowPhase": "Build"}))
    (w / "agent-wfx.jsonl").write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "do the thing"}}) + "\n")
    (w / "journal.jsonl").write_text('{"type":"launched"}\n')
    return t, w


def test_workflow_subs(tmp_path):
    t, w = mk_wf(tmp_path)
    subs = {s.sid: s for s in subagents.list_subs(t)}
    x = subs["agent-wfx"]
    assert x.group == "wf_e4a7d81a-485" and x.group_label == "Build" and x.desc == "newproj-tmux" and x.running
    assert subs["agent-aaa"].group == ""
    assert subagents.read_work(t, "agent-wfx") is None  # flat lookup does not see it
    wk = subagents.read_work(t, "agent-wfx", "wf_e4a7d81a-485")
    assert wk and wk.steps[0].text == "do the thing" and wk.sub.group == "wf_e4a7d81a-485"
    for bad in ("wf_..", "../x", "wf_a/b", "wfx", "wf_"):
        assert subagents.read_work(t, "agent-wfx", bad) is None


def test_workflow_ignores_bad_dirs_and_symlinks(tmp_path):
    t, w = mk_wf(tmp_path)
    root = w.parent
    (root / "evil").mkdir()
    (root / "evil" / "agent-e.jsonl").write_text("{}\n")
    os.symlink(w, root / "wf_link")
    (w / "notagent.jsonl").write_text("{}\n")
    secret = tmp_path / "secret.jsonl"
    secret.write_text("{}\n")
    os.symlink(secret, w / "agent-sym.jsonl")
    assert sorted(s.sid for s in subagents.list_subs(t) if s.group) == ["agent-wfx"]
    assert subagents.read_work(t, "agent-wfx", "wf_link") is None
    assert subagents.read_work(t, "agent-sym", "wf_e4a7d81a-485") is None


def _wf_journal(w, started, done):
    rows = [{"type": "launched"}]
    rows += [{"type": "started", "agentId": a, "label": a, "phase": "Build"} for a in started]
    rows += [{"type": "result", "agentId": a, "result": "ok"} for a in done]
    (w / "journal.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _tool_lines(n, target="x.py"):
    out = [{"type": "user", "timestamp": "2026-10-11T01:00:00.000Z", "message": {"content": "go"}}]
    for _ in range(n):
        out.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": target}}]}})
    return "\n".join(json.dumps(x) for x in out) + "\n"


def test_workflow_done_by_journal_and_summary(tmp_path):
    t, w = mk_wf(tmp_path)
    for a in ("fin", "run", "stale"):
        (w / f"agent-{a}.jsonl").write_text(_tool_lines(2))
    os.utime(w / "agent-stale.jsonl", (time.time() - 3600,) * 2)
    _wf_journal(w, ["wfx", "fin", "run", "stale", "queued"], ["fin"])
    subs = {s.sid: s for s in subagents.list_subs(t)}
    assert subs["agent-fin"].done and not subs["agent-fin"].running  # result line wins over a fresh mtime
    assert subs["agent-run"].running and not subs["agent-stale"].running and not subs["agent-stale"].done
    sm = subagents.summarize(list(subs.values()), t)
    g = next(g for g in sm.groups if g.gid)
    assert (g.total, g.done) == (5, 1) and sm.running == sm.total - sm.finished
    assert sm.groups[0].gid == "" and sm.groups[1].gid  # Direct first, workflows below
    assert g.label.startswith("workflow ") and g.label.endswith("e4a7d81a")


def test_groups_order_direct_first_then_newest_workflow(tmp_path):
    t, w = mk_wf(tmp_path)
    w2 = w.parent / "wf_bbbbbbbb-111"
    w2.mkdir()
    (w2 / "agent-n.jsonl").write_text(_tool_lines(1))
    os.utime(w / "agent-wfx.jsonl", (time.time() - 1000,) * 2)
    os.utime(tmp_path / "abc" / "subagents" / "agent-aaa.jsonl", (time.time() - 3000,) * 2)
    sm = subagents.summarize(subagents.list_subs(t), t)
    assert [g.gid for g in sm.groups] == ["", "wf_bbbbbbbb-111", "wf_e4a7d81a-485"]


def test_activity_calls_last_masked_incremental(tmp_path):
    t, w = mk_wf(tmp_path)
    f = w / "agent-act.jsonl"
    f.write_text(_tool_lines(3, f"/src/{TOK}.py"))
    s = next(s for s in subagents.list_subs(t) if s.sid == "agent-act")
    assert s.calls == 3 and not s.calls_more and s.last == "Read" and TOK not in s.last_target and s.t0 > 0
    with open(f, "a") as fh:
        fh.write(_tool_lines(2, "/b.py").split("\n", 1)[1])
    s = next(s for s in subagents.list_subs(t) if s.sid == "agent-act")
    assert s.calls == 5 and "b.py" in s.last_target
    assert s.elapsed(time.time()).endswith(("s", "m", "h", "d")) or "h" in s.elapsed(time.time())


def test_activity_bounded_cold_read(tmp_path, monkeypatch):
    t, w = mk_wf(tmp_path)
    monkeypatch.setattr(subagents, "COLD_BYTES", 2000)
    (w / "agent-big.jsonl").write_text(_tool_lines(200))
    s = next(s for s in subagents.list_subs(t) if s.sid == "agent-big")
    assert s.calls_more and 0 < s.calls < 200


def test_journal_symlink_ignored(tmp_path):
    t, w = mk_wf(tmp_path)
    (w / "journal.jsonl").unlink()
    real = tmp_path / "j.jsonl"
    real.write_text('{"type":"result","agentId":"wfx"}\n')
    os.symlink(real, w / "journal.jsonl")
    assert not next(s for s in subagents.list_subs(t) if s.sid == "agent-wfx").done


def test_list_markup_strip_and_etag(env, monkeypatch, tmp_path):
    c = _with_transcript(env, monkeypatch, tmp_path)
    w = tmp_path / "abc" / "subagents" / "workflows" / "wf_e4a7d81a-485"
    w.mkdir(parents=True)
    (w / "agent-wfx.jsonl").write_text(_tool_lines(2))
    (w / "agent-wfy.jsonl").write_text(_tool_lines(1))
    _wf_journal(w, ["wfx", "wfy"], ["wfy"])
    r = c.get("/a/claude-12/subagents")
    assert r.status_code == 200
    for needle in ("Running <strong>", "Finished <strong>", "Total <strong>", "1/2 done", "<progress", "Direct", "2 calls", "Read"):
        assert needle in r.text, needle
    assert "running " in r.text and "finished " in r.text
    assert "workflow e4a7d81a" in r.text and ">wf " not in r.text
    # Direct is open and first; workflow groups are closed by default
    assert r.text.index('id="sa-g-direct" open') < r.text.index('id="sa-g-wf_e4a7d81a-485">')
    # the row's loaded detail is never morphed by a list refresh; running rows carry data-run
    assert 'data-nodiff data-keep' in r.text and 'data-run="1"' in r.text and 'data-run="0"' in r.text
    assert 'id="sa-root" data-run="' in r.text and "data-ended" not in r.text
    tag = r.headers["etag"]
    assert c.get("/a/claude-12/subagents", headers={"If-None-Match": tag}).status_code == 304


def test_running_filter_and_empty_body(env, monkeypatch, tmp_path):
    c = _with_transcript(env, monkeypatch, tmp_path)
    r = c.get("/a/claude-12/subagents?running=1")
    assert r.status_code == 200 and "Explore" in r.text and "running " in r.text
    assert "agent-bbb" not in r.text and "Finished" not in r.text
    assert c.get("/a/claude-12/subagents?running=1", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    os.utime(tmp_path / "abc" / "subagents" / "agent-aaa.jsonl", (time.time() - 3600,) * 2)
    e = c.get("/a/claude-12/subagents?running=1")
    assert e.status_code == 200 and e.text == ""


def _use(tid):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "sleep 500"}}]}}


def _res(tid):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


def test_waiting_on_open_tool_call_is_running(tmp_path):
    t = mk(tmp_path)
    p = tmp_path / "abc" / "subagents" / "agent-aaa.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [_use("t1")]) + "\n")
    os.utime(p, (time.time() - 600,) * 2)  # silent for 10 min: a long Bash run
    a = next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa")
    assert a.running and a.waiting
    with open(p, "a") as f:
        f.write(json.dumps(_res("t1")) + "\n")
    os.utime(p, (time.time() - 600,) * 2)
    a = next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa")
    assert not a.running and not a.waiting  # result came back, then silent: finished
    with open(p, "a") as f:
        f.write(json.dumps(_use("t2")) + "\n")
    os.utime(p, (time.time() - subagents.WAIT_S - 60,) * 2)
    assert not next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa").running  # stale cap


def _replays():
    blocks = [{"type": "tool_use", "id": i, "name": "Bash", "input": {"command": "sleep 500"}} for i in ("t0", "t1")]
    return [{"type": k, "message": {"content": blocks}} for k in ("api-request-blob", "api-request", "api-request-shape")]


def test_request_replays_do_not_reopen_calls_or_inflate_count(tmp_path):
    t = mk(tmp_path)
    p = tmp_path / "abc" / "subagents" / "agent-aaa.jsonl"
    h = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "SubagentHandback", "input": {"message": "done"}}]}}
    p.write_text("\n".join(json.dumps(x) for x in [h, _res("t1")]) + "\n")
    old = time.time() - 600
    os.utime(p, (old,) * 2)
    base = subagents._activity(p, p.stat().st_size)
    assert base[0] == 1 and base[2] == "SubagentHandback" and not base[5]
    with open(p, "a") as f:  # replays appended after the real result (second, incremental read)
        f.write("\n".join(json.dumps(x) for x in _replays()) + "\n")
    os.utime(p, (old,) * 2)
    calls, _more, last, _tgt, _t0, waiting, _tok = subagents._activity(p, p.stat().st_size)
    assert calls == 1 and last == "SubagentHandback" and not waiting
    a = next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa")
    assert not a.waiting and not a.running and a.calls == 1 and a.last == "SubagentHandback"
    subagents._ACT.clear()  # cold read of the whole file gives the same answer
    assert subagents._activity(p, p.stat().st_size)[:3] == (1, False, "SubagentHandback")
    assert not subagents._activity(p, p.stat().st_size)[5]


def test_open_assistant_tool_use_still_waits_despite_replays(tmp_path):
    t = mk(tmp_path)
    p = tmp_path / "abc" / "subagents" / "agent-aaa.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in [_use("t1"), *_replays()]) + "\n")
    os.utime(p, (time.time() - 600,) * 2)
    a = next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa")
    assert a.waiting and a.running and a.calls == 1


def _asst_usage(n, inp=10, out=5, cr=1000, cw=200, ts="2026-10-11T01:00:01.000Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"content": [{"type": "text", "text": f"step {n}"}],
            "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
                      "cache_creation_input_tokens": cw}}}


def test_tokens_from_latest_usage_incremental(tmp_path, monkeypatch):
    t = mk(tmp_path)
    p = tmp_path / "abc" / "subagents" / "agent-aaa.jsonl"
    p.write_text(json.dumps(_asst_usage(1)) + "\n")
    a = next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa")
    assert a.tokens == 1215
    reads = []
    real = subagents._usage_total
    monkeypatch.setattr(subagents, "_usage_total", lambda data: reads.append(len(data)) or real(data))
    with open(p, "a") as f:
        f.write(json.dumps(_res("t9")) + "\n")  # no usage in the appended part: keeps the last total
    assert next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa").tokens == 1215
    with open(p, "a") as f:
        f.write(json.dumps(_asst_usage(2, inp=1, out=2, cr=3000, cw=4)) + "\n")
    assert next(s for s in subagents.list_subs(t) if s.sid == "agent-aaa").tokens == 3007
    first = len(json.dumps(_asst_usage(1))) + 1
    assert reads and all(n < first + 400 for n in reads)  # only the appended bytes, never the whole file


def test_work_steps_tool_ids_status_and_times(tmp_path):
    t = mk(tmp_path)
    p = tmp_path / "abc" / "subagents" / "agent-aaa.jsonl"
    rows = [
        {"type": "user", "timestamp": "2026-10-11T01:00:00.000Z", "message": {"content": "go"}},
        {"type": "assistant", "timestamp": "2026-10-11T01:00:02.000Z", "message": {"content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/a.py"}},
            {"type": "tool_use", "id": "t3", "name": "Grep", "input": {"pattern": "x"}},
            {"type": "tool_use", "name": "Glob", "input": {"pattern": "*.py"}}]}},
        {"type": "user", "timestamp": "2026-10-11T01:00:05.000Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "SECRET RESULT"},
            {"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": "no such file"}]}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    w = subagents.read_work(t, "agent-aaa")
    got = [(s.role, s.label, s.id, s.status) for s in w.steps]
    assert got == [("prompt", "", "", ""), ("text", "", "", ""), ("tool", "Bash", "t1", "ok"),
                   ("tool", "Read", "t2", "err"), ("tool", "Grep", "t3", "run"), ("tool", "Glob", "", "")]
    t0 = w.steps[2].ts
    assert t0 > 0 and w.steps[2].ended - t0 == 3.0 and w.steps[4].ended == 0.0 and w.steps[0].ts == t0 - 2
    assert "SECRET RESULT" not in repr(w) and "no such file" not in repr(w)


def test_tokens_filter_is_registered():
    from pm.web.render import env

    f = env.filters["tokens"]
    assert [f(950), f(1215), f(34_000), f(1_500_000)] == ["950", "1.2k", "34k", "1.5M"]
    assert env.from_string("{{ n | tokens }}").render(n=3007) == "3.0k"


def test_blocks_group_tools_count_names_and_mark_last_reply():
    S = subagents.Step
    steps = [S("prompt", "go"), S("text", "first"), S("tool", "ls", "Bash", "t1", "ok"),
             S("tool", "a.py", "Read", "t2", "run"), S("tool", "pwd", "Bash", "t3", "run"), S("text", "done"),
             S("tool", "x", "", "", "")]
    b = subagents.blocks(steps)
    assert [x.kind for x in b] == ["prompt", "reply", "tools", "reply", "tools"]
    assert b[0].step is steps[0] and b[1].step is steps[1] and not b[1].last and b[3].last
    assert b[2].steps == tuple(steps[2:5]) and b[2].counts == (("Bash", 2), ("Read", 1)) and b[2].running == 2
    assert b[4].counts == (("tool", 1),) and b[4].running == 0  # no label -> "tool"
    assert subagents.Work(subagents.Sub("agent-a"), steps).blocks() == b


def test_blocks_empty_and_no_reply():
    S = subagents.Step
    assert subagents.blocks([]) == []
    b = subagents.blocks([S("prompt", "go"), S("tool", "ls", "Bash")])
    assert [x.kind for x in b] == ["prompt", "tools"] and not any(x.last for x in b)


def test_duration_filter_is_registered():
    from pm.web.render import env

    assert env.from_string("{{ n | duration }}").render(n=-5) == "0s"
    assert env.from_string("{{ n | duration }}").render(n=3725) == "1h 2m"


def test_running_count_claude_only_and_cached(monkeypatch):
    from types import SimpleNamespace as NS

    from pm.agents import subagents as sa

    calls = []
    subs = [sa.Sub("agent-a", running=True), sa.Sub("agent-b"), sa.Sub("agent-c", running=True)]
    monkeypatch.setattr(sa, "list_subs", lambda tr: calls.append(tr) or subs)
    monkeypatch.setattr(sa, "_running_cache", sa.LRU(max_entries=8))
    claude = NS(kind=NS(value="claude"), transcript="/t/a.jsonl")
    assert sa.running_count(claude) == 2
    assert sa.running_count(claude) == 2 and calls == ["/t/a.jsonl"]  # cached
    assert sa.running_count(NS(kind=NS(value="codex"), transcript="/t/b.jsonl")) == 0
    assert sa.running_count(NS(kind=NS(value="claude"), transcript="")) == 0
    monkeypatch.setattr(sa, "RUNNING_TTL_S", 0.0)
    assert sa.running_count(claude) == 2 and len(calls) == 2  # after the TTL it reads again

    def boom(tr):
        raise OSError("x")
    monkeypatch.setattr(sa, "list_subs", boom)
    assert sa.running_count(NS(kind=NS(value="claude"), transcript="/t/c.jsonl")) == 0
