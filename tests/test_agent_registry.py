"""Permanent agent ids: minted from the session identity, persisted, pane is an attribute."""
import dataclasses
import sqlite3
import time
from pathlib import Path

from helpers import audit_text, make_client, make_settings, mk_client, post, snap_with

from pm.agents import registry
from pm.core.config import Settings
from pm.core.models import Agent, AgentKind, AgentState
from pm.infra import snapshot as services
from pm.tmux.ctl import SEP
from pm.web import render
from pm.web.render import TEMPLATE_DIR
from pm.web.routes.pages import router

REAL_REGISTRY = True


def ag(kind=AgentKind.CLAUDE, pane="%140", ident="sess-a", pid=100, **kw):
    return Agent(id=f"{kind.value}-{pane[1:]}", kind=kind, pane_id=pane, session_id="$1", pid=pid,
                 cwd="/x", state=AgentState.IDLE, state_since=1.0, ident=ident, **kw)


def st(tmp_path):
    return Settings(state_dir=tmp_path / "state")


def one(s, a, now=1000.0):
    return registry.assign(s, [a], now=now)[0]


def test_id_from_session_not_pane_and_survives_restart(tmp_path):
    s = st(tmp_path)
    a = one(s, ag())
    assert a.id.startswith("claude-") and not a.id.split("-", 1)[1].isdigit()
    assert a.id == registry.mint_id("claude", "sess-a")
    assert one(s, ag(), now=1100.0).id == a.id  # next poll / pm restart
    assert one(s, ag(pane="%999", pid=555), now=1200.0).id == a.id  # resume in another pane
    assert registry.get(s, a.id).last_pane == "%999"


def test_other_program_same_pane_gets_other_id_and_old_one_ends(tmp_path):
    s = st(tmp_path)
    c = one(s, ag())
    d = one(s, ag(kind=AgentKind.DEVIN, ident="dev-1", pid=101), now=1100.0)
    assert d.id != c.id
    rec = registry.get(s, c.id)
    assert rec.ended and rec.ended_at == 1100.0 and rec.last_pane == "%140"
    # same program restarted in the same pane = another conversation = another id
    e = one(s, ag(ident="sess-b", pid=102), now=1200.0)
    assert e.id not in (c.id, d.id)
    # the old conversation resumed comes back under its old id
    assert one(s, ag(ident="sess-a", pid=103), now=1300.0).id == c.id
    assert not registry.get(s, c.id).ended


def test_tmux_restart_reusing_pane_ids(tmp_path):
    s = st(tmp_path)
    a1 = one(s, ag(ident="s1"))
    a2 = one(s, ag(ident="s2", pid=7), now=2000.0)  # tmux restarted: %140 is somebody else
    assert a1.id != a2.id and registry.get(s, a1.id).ended and not registry.get(s, a2.id).ended


def test_session_less_agent_is_claimed_when_the_session_shows_up(tmp_path):
    s = st(tmp_path)
    first = one(s, ag(ident=None))
    again = one(s, ag(ident="late"), now=1100.0)
    assert again.id == first.id and registry.get(s, first.id).ident == "late"
    assert one(s, ag(ident=None), now=1200.0).id == first.id


def test_incomplete_round_ends_nothing(tmp_path):
    s = st(tmp_path)
    a = one(s, ag())
    assert registry.assign(s, [], complete=False, now=1100.0) == []
    assert not registry.get(s, a.id).ended
    registry.assign(s, [], complete=True, now=1200.0)
    assert registry.get(s, a.id).ended


def test_two_live_agents_same_session_do_not_share_an_id(tmp_path):
    s = st(tmp_path)
    out = registry.assign(s, [ag(), ag(pane="%141", pid=200)], now=1000.0)
    assert out[0].id != out[1].id


def test_legacy_pane_id_maps_to_newest_record(tmp_path):
    s = st(tmp_path)
    old = one(s, ag(ident="s1"))
    new = one(s, ag(ident="s2", pid=9), now=2000.0)
    assert registry.by_legacy(s, "claude-140").id == new.id
    assert registry.by_legacy(s, "devin-140") is None
    snap = services.Snapshot(ts=1, agents=(new,))
    assert registry.resolve(s, snap, "claude-140").redirect == new.id
    assert registry.resolve(s, snap, old.id).ended.id == old.id
    assert registry.resolve(s, snap, old.id).now_in_pane.id == new.id
    assert registry.resolve(s, snap, "claude-777") is None


def test_legacy_without_record_falls_back_to_pane_agent(tmp_path):
    s = st(tmp_path)
    other = ag(kind=AgentKind.DEVIN, ident=None)
    other = dataclasses.replace(other, id="devin-abc")
    snap = services.Snapshot(ts=1, agents=(other,))
    assert registry.resolve(s, snap, "claude-140").redirect == "devin-abc"


def test_retention_prunes_only_old_ended(tmp_path):
    s = dataclasses.replace(st(tmp_path), agent_retention_days=90)
    a = one(s, ag(ident="old"), now=1000.0)
    b = one(s, ag(ident="live", pane="%2", pid=2), now=1000.0)
    registry.assign(s, [b], now=1010.0)  # a ended at 1010
    assert registry.prune(s, now=1010.0 + 89 * 86400) == 0
    assert registry.prune(s, now=1010.0 + 91 * 86400) == 1
    assert registry.get(s, a.id) is None and registry.get(s, b.id) is not None


def test_db_failure_keeps_detected_ids(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    s = Settings(state_dir=f / "sub")  # cannot be created
    a = ag()
    assert registry.assign(s, [a]) == [a]


# ---------------------------------------------------------------- pages
def _line(pane, cwd):
    return SEP.join(["$1", "s", "1", "@1", "0", "w", "1", pane, "4242", "claude", cwd, "1", "t", "0"]) + "\n"


def _client(fake_tmux, tmp_path, monkeypatch, found):
    dev = tmp_path / "dev"
    (dev / "alpha").mkdir(parents=True, exist_ok=True)
    s = make_settings(fake_tmux, tmp_path)
    monkeypatch.setenv("FAKE_TMUX_OUT", _line("%140", str(dev / "alpha")))
    monkeypatch.setattr(services.agents_mod, "detect_agents", lambda panes: list(found))
    c = make_client(s, router, static=True, logged_in=True)
    return s, c, c.app


def test_ended_page_is_read_only_and_links_to_the_pane_reuser(fake_tmux, tmp_path, monkeypatch):
    found = [ag(ident="s1")]
    s, c, _ = _client(fake_tmux, tmp_path, monkeypatch, found)
    old_id = c.get("/a/claude-140", follow_redirects=False).headers["location"].split("/a/")[1]
    assert c.get(f"/a/{old_id}").status_code == 200  # running
    found[:] = [ag(kind=AgentKind.DEVIN, ident="d1", pid=300)]
    services.build_and_publish(s)
    new_id = services.published(s).agents[0].id
    assert new_id.startswith("devin-")
    r = c.get(f"/a/{old_id}", follow_redirects=False)
    assert r.status_code == 200
    t = r.text
    assert "ended" in t and f'href="/a/{new_id}"' in t and "Now in this pane" in t
    for forbidden in ("/message", "/key", "/interrupt", "/stop", "msgbar", "/pane"):
        assert forbidden not in t.replace("/a/" + old_id + "/chat", "").replace("/settings/panel", "")
    assert c.get(f"/a/{old_id}/chat").status_code == 200
    assert "v5-shell has-side" in t and 'id="v5side"' in t  # ended page keeps the left sidebar
    assert 'action="/a/' + old_id + '/resume"' not in t  # no project for this record: no Resume button
    # md links in an ended chat need the split container + panel (chat.js open() bails without them)
    assert f'id="chatsplit" data-agent="{old_id}"' in t and 'id="mdpane"' in t and "data-md-full" in t
    assert c.get(f"/a/{old_id}/md", params={"path": "nope.md"}).status_code == 404  # served, not 500
    # old pane based bookmark of the ended agent resolves to the newest record of that pane+kind
    assert c.get("/a/devin-140", follow_redirects=False).headers["location"] == f"/a/{new_id}"
    assert c.get("/a/claude-140", follow_redirects=False).headers["location"] == f"/a/{old_id}"
    assert c.get("/a/claude-999", follow_redirects=False).status_code == 404


# ---------------------------------------------------------------- mutation safety
def _mut(tmp_path, monkeypatch, agent):
    from pm.core.config import Settings as S
    stt = S(state_dir=tmp_path / "st", token_file=tmp_path / "tok", totp_file=tmp_path / "totp",
            dev_root=tmp_path / "dev", extra_projects=(), tmux_socket="pmtest-fake")
    app, c, sess, tok, stt = mk_client(stt)
    registry.assign(stt, [agent], now=time.time())
    live = registry.assign(stt, [agent], now=time.time())[0]
    snap_with(monkeypatch, [live])
    return c, tok, stt, live


def test_mutations_refuse_on_session_mismatch(tmp_path, monkeypatch):
    c, tok, stt, a = _mut(tmp_path, monkeypatch, ag(pid=424242))
    monkeypatch.setattr("pm.web.agentctl.live_session_id", lambda pid: "someone-else")
    for path, data in (("message", {"text": "hi"}), ("key", {"key": "y"}), ("interrupt", {}),
                       ("stop", {}), ("snippet", {"index": "0"})):
        r = post(c, tok, f"/a/{a.id}/{path}", **data)
        assert r.status_code == 409, path
    log = audit_text(stt)
    assert log.count("session mismatch") == 5 and "denied" in log


def test_mutation_on_ended_or_legacy_id_is_409_audited(tmp_path, monkeypatch):
    c, tok, stt, a = _mut(tmp_path, monkeypatch, ag(pid=424242))
    registry.assign(stt, [], now=time.time() + 5)  # ended
    snap_with(monkeypatch, [])
    r = post(c, tok, f"/a/{a.id}/message", text="hi")
    assert r.status_code == 409 and "agent ended" in audit_text(stt)
    r = post(c, tok, "/a/claude-140/message", text="hi")
    assert r.status_code == 409 and "stale pane id" in audit_text(stt)
    assert post(c, tok, "/a/claude-777/message", text="hi").status_code == 404


def test_missed_poll_does_not_change_the_id_of_a_session_less_agent(tmp_path):
    s = st(tmp_path)
    a = one(s, ag(kind=AgentKind.DEVIN, ident=None, pid=55))
    registry.assign(s, [], now=1100.0)  # detector flapped
    assert registry.get(s, a.id).ended
    b = one(s, ag(kind=AgentKind.DEVIN, ident=None, pid=55), now=1200.0)
    assert b.id == a.id and not registry.get(s, a.id).ended


def test_ended_page_resume_button_only_for_claude_with_project():
    from pathlib import Path as P

    from jinja2 import Environment, FileSystemLoader
    e = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    e.filters.update(ts=str, dt=str, urlencode=str, ago=str, agent_q=render.agent_q, model_short=str, placeholder=lambda a: ("", ""))
    e.globals["agent_name"] = lambda a: a.label
    e.globals["tz_label"] = lambda: "UTC+7"
    class K: value = "claude"
    class A: id = "claude-1e2ee8"; label = "x"; window_name = None; kind = K
    class R:
        cwd = "/x"; project = "pm"; kind = "claude"; resume_id = "11111111-2222-3333-4444-555555555555"; ended_at = 1; last_seen = 1; first_seen = 1; last_pane = "%1"
    html = e.get_template("agent_ended.html").render(csrf="t", agent=A, rec=R, project=None, now_in_pane=None, resumable=True)
    assert 'action="/a/claude-1e2ee8/resume"' in html and ">Resume</button>" in html
    assert 'data-composer="ended"' in html and "msgbar" not in html  # the real ended composer, never the live one
    assert "ended-meta" in html and ">Directory<" in html and "Not running. Read-only record" in html and "Use Resume to continue" not in html
    R.project = ""
    assert "/resume" not in e.get_template("agent_ended.html").render(csrf="t", agent=A, rec=R, project=None, now_in_pane=None)


# ---------------------------------------------------------------- past sessions (import_past)
PAST_U = "11111111-2222-3333-4444-555555555555"


def _past(kind="claude", ident=PAST_U, m=500.0, cwd="/x"):
    from pm.chat.sources.past import PastSession
    return PastSession(kind, ident, Path(f"/store/{ident}"), cwd, m)


def test_import_past_inserts_ended_rows_once(tmp_path):
    s = st(tmp_path)
    assert registry.import_past(s, [_past(), _past("codex", "rollout-2026-10-01T10-00-00-" + PAST_U, 400.0)],
                                project="alpha") == 2
    rec = registry.get(s, registry.mint_id("claude", PAST_U))
    assert rec.ended and rec.ended_at == rec.last_seen == 500.0 and rec.pid == 0 and rec.last_pane == ""
    assert rec.project == "alpha" and rec.transcript == f"/store/{PAST_U}" and rec.resume_id == PAST_U
    assert registry.import_past(s, [_past()]) == 0
    assert registry.ended_sessions(s, project="alpha")[1] == 2


def test_import_past_skips_known_and_unresumable_idents(tmp_path):
    s = st(tmp_path)
    one(s, ag(kind=AgentKind.CODEX, ident=PAST_U))  # pm saw the bare uuid form live
    assert registry.import_past(s, [_past("codex", "rollout-2026-10-01T10-00-00-" + PAST_U)]) == 0
    assert registry.import_past(s, [_past("claude", "not-a-uuid"), _past("antigravity", "x")]) == 0


def test_import_past_id_clash_gets_a_random_id(tmp_path):
    s = st(tmp_path)
    taken = one(s, ag(ident="other"))
    con = sqlite3.connect(s.db_file)
    con.execute("UPDATE agent_records SET id=? WHERE id=?", (registry.mint_id("claude", PAST_U), taken.id))
    con.commit()
    con.close()
    assert registry.import_past(s, [_past()]) == 1
    rows = registry.ended_sessions(s)[0]
    assert [r.ident for r in rows] == [PAST_U] and rows[0].id != registry.mint_id("claude", PAST_U)


def test_import_past_db_failure_is_minus_one(tmp_path):
    f = tmp_path / "file"
    f.write_text("x")
    assert registry.import_past(Settings(state_dir=f / "sub"), [_past()]) == -1
