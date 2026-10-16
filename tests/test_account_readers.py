"""An agent started under a named login account is read from that account's folder (chat log, stats, live
view, recap, fork, ``registry.config_dir_of``); without an account nothing changes."""

import dataclasses
import json

import pytest

from pm.agents import fork, recap, registry
from pm.chat import live
from pm.chat import log as chatlog
from pm.chat import stats as chatstats
from pm.chat.sources.claude import encode_cwd
from pm.core.models import Agent, AgentKind, AgentState

REAL_REGISTRY = True
UUID = "11111111-2222-3333-4444-555555555555"
CWD = "/work/proj"


@pytest.fixture
def acct(settings, tmp_path):
    """(settings with a claude account 'work', its folder, an Agent whose record stores that account)."""
    s = dataclasses.replace(settings, accounts={"claude": ("work",), "codex": ("alt",)}, claude_dir=tmp_path / "own")
    cdir = tmp_path / "accounts" / "claude" / "work"
    a = Agent(id="claude-1", kind=AgentKind.CLAUDE, pane_id="%9", session_id="$3", pid=5, cwd=CWD,
              project="proj", state=AgentState.IDLE, ident=UUID, session_uuid=UUID)
    registry.expect_account(s, "claude", "%9", "work")
    a = dataclasses.replace(a, id=registry.assign(s, [a])[0].id)
    return s, cdir, a


def _transcript(cdir):
    d = cdir / "projects" / encode_cwd(CWD)
    d.mkdir(parents=True)
    p = d / f"{UUID}.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "timestamp": "2026-10-08T10:00:00+00:00", "message": {"role": "user", "content": "do it"}},
        {"type": "assistant", "timestamp": "2026-10-08T10:01:00+00:00", "message": {
            "role": "assistant", "model": "claude-x", "content": [{"type": "text", "text": "all done"}],
            "usage": {"input_tokens": 5, "output_tokens": 7}}},
    ]) + "\n")
    return p


def test_config_dir_of(acct, settings):
    s, cdir, a = acct
    assert registry.config_dir_of(s, a.id, a.kind) == cdir
    assert registry.config_dir_of(s, a.id, "claude") == cdir
    assert registry.config_dir_of(s, a.id, AgentKind.DEVIN) is None  # no accounts for that kind
    assert registry.config_dir_of(s, "claude-nope", "claude") is None  # unknown agent: the CLI's own folder
    # the stored account left [accounts]: fall back instead of raising
    assert registry.config_dir_of(dataclasses.replace(s, accounts={}), a.id, "claude") is None


def test_chatlog_store_file_reads_account_dir(acct):
    s, cdir, a = acct
    p = _transcript(cdir)
    assert chatlog.store_file(a) is None  # ~/.claude has nothing
    assert chatlog.store_file(a, registry.config_dir_of(s, a.id, a.kind)) == p


def test_stats_and_live_read_account_dir(acct):
    s, cdir, a = acct
    _transcript(cdir)
    cd = registry.config_dir_of(s, a.id, a.kind)
    assert chatstats.stats_for(a) is None
    assert chatstats.stats_for(a, s, config_dir=cd) is not None
    assert live._claude_info(a)[1] == 0.0
    info, mt = live._claude_info(a, cd)
    assert info.text == "all done" and mt > 0
    assert live.last_user_task(a, cd) == "do it"


def test_recap_reads_account_dir(acct):
    s, cdir, a = acct
    _transcript(cdir)
    assert recap.recap_for(a).empty
    rc = recap.recap_for(a, registry.config_dir_of(s, a.id, a.kind))
    assert not rc.empty and rc.prompt == "do it"


def test_fork_finds_session_file_in_account_dir(acct):
    s, cdir, a = acct
    p = _transcript(cdir)
    cd = registry.config_dir_of(s, a.id, a.kind)
    assert fork._transcript(a, "claude", UUID) is None
    assert fork._transcript(a, "claude", UUID, cd) == p
    assert fork.session_id(a, cd) == UUID  # pid 0: ident, no live lookup


def test_codex_session_file_in_account_dir(acct, tmp_path):
    from pm.chat import sources

    s, _, _ = acct
    day = __import__("datetime").datetime.now()
    d = tmp_path / "accounts" / "codex" / "alt" / "sessions" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    d.mkdir(parents=True)
    f = d / "rollout-x.jsonl"
    f.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": CWD, "id": "x"}}) + "\n")
    home = tmp_path / "emptyhome"
    home.mkdir()
    kw = dict(pid=4242, cwd=CWD, age_s=60, home=home)
    assert sources.session_file("codex", **kw) is None
    assert sources.session_file("codex", config_dir=tmp_path / "accounts" / "codex" / "alt", **kw) == f


def test_snapshot_detects_agents_in_every_account_sessions_dir(settings, tmp_path, monkeypatch):
    from pm.infra import snapshot as services
    s = dataclasses.replace(settings, accounts={"claude": ("work",)}, claude_dir=tmp_path / "own")
    got = {}
    monkeypatch.setattr(services.agents_mod, "detect_agents",
                        lambda panes, **kw: got.update(kw) or [])
    services._build(s, 1.0)
    assert got["sessions_dirs"] == [tmp_path / "own" / "sessions", s.state_dir / "accounts" / "claude" / "work" / "sessions"]
