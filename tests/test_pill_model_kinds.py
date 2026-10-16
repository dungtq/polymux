"""Model pill for every agent kind: the model is read from each kind's own session store (fake files under a tmp HOME)."""
import json
import sqlite3

from pm.agents import head as agenthead


def _v(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _ld(f, b):
    return _v(f << 3 | 2) + _v(len(b)) + b


def _vi(f, n):
    return _v(f << 3) + _v(n)


def test_model_short_non_claude_ids():
    assert agenthead.model_short("SWE-2 Medium") == "swe-2-medium"
    assert agenthead.model_short("gpt-6.1-sol") == "gpt-6-1-sol"
    assert agenthead.model_short("claude-sonnet-5-5-20261001") == "sonnet-5-5"


def test_codex_devin_cursor_antigravity_claude(tmp_path):
    h = tmp_path
    # codex: newest turn_context wins
    r = h / ".codex/sessions/2026/10/06/rollout-x.jsonl"
    r.parent.mkdir(parents=True)
    r.write_text("\n".join(json.dumps(e) for e in [
        {"type": "turn_context", "payload": {"model": "gpt-old"}},
        {"type": "response_item", "payload": {"model": "ignored"}},
        {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}}]) + "\n")
    assert agenthead.record_model(str(r), h) == "gpt-6.1-sol"
    # devin: sessions.db model, fallback to the ATIF agent.model_name
    d = h / ".local/share/devin/cli"
    (d / "transcripts").mkdir(parents=True)
    t = d / "transcripts" / "calm-fox.json"
    t.write_text('{"agent": {"name": "devin", "model_name": "SWE-2 Medium", "tool_definitions": []}}')
    assert agenthead.record_model(str(t), h) == "SWE-2 Medium"
    con = sqlite3.connect(d / "sessions.db")
    con.execute("CREATE TABLE sessions (id TEXT, model TEXT, main_chain_id TEXT)")
    con.commit()
    con.close()
    t2 = d / "transcripts" / "calm-fox.json"
    assert t2 == t
    t2 = d / "transcripts" / "wise-owl.json"
    t2.write_text(t.read_text())
    con = sqlite3.connect(d / "sessions.db")
    con.execute("INSERT INTO sessions VALUES ('wise-owl','swe-2-max','')")
    con.commit()
    con.close()
    assert agenthead.record_model(str(t2), h) == "swe-2-max"
    # cursor: meta.lastUsedModel (hex JSON)
    c = h / ".cursor/chats/abc/uuid/store.db"
    c.parent.mkdir(parents=True)
    con = sqlite3.connect(c)
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('0', ?)", (json.dumps({"lastUsedModel": "grok-4.6"}).encode().hex(),))
    con.commit()
    con.close()
    assert agenthead.record_model(str(c), h) == "grok-4.6"
    # antigravity: gen_metadata.1.19 = model
    a = h / ".gemini/antigravity-cli/conversations/u.db"
    a.parent.mkdir(parents=True)
    con = sqlite3.connect(a)
    con.execute("CREATE TABLE gen_metadata (idx INTEGER, size INTEGER, data BLOB)")
    blob = _ld(1, _ld(19, b"gemini-3.8-flash") + _ld(4, _vi(2, 5) + _vi(3, 7)))
    con.execute("INSERT INTO gen_metadata VALUES (0, ?, ?)", (len(blob), blob))
    con.commit()
    con.close()
    assert agenthead.record_model(str(a), h) == "gemini-3.8-flash"
    # claude still works; foreign paths are not read
    cl = h / ".claude/projects/p/s.jsonl"
    cl.parent.mkdir(parents=True)
    cl.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-5-5-20261001"}}) + "\n")
    assert agenthead.record_model(str(cl), h) == "claude-sonnet-5-5-20261001"
    other = tmp_path / "elsewhere.jsonl"
    other.write_text(r.read_text())
    assert agenthead.record_model(str(other), h) == ""
    assert agenthead.record_model("", h) == ""


def test_list_pill_stays_transcript_only(tmp_path):
    """Sidebar/search rows use pill_model (no /proc read); only the agent page uses live_model."""
    assert agenthead.pill_model("") == ""
    assert agenthead.pill_model(str(tmp_path / "missing.jsonl")) == ""
