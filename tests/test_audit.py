import json
import os
import stat

import pytest

from pm.core.models import AuditEntry
from pm.security.audit import AuditError, AuditLog, message_detail, session_tag
from pm.security.sessions_store import sid_hash


def test_write_tail_perms(tmp_path):
    log = AuditLog(tmp_path / "st" / "audit.jsonl")
    for i in range(5):
        log.write(AuditEntry(float(i), "c", "a", f"t{i}", "ok"))
    assert [e["target"] for e in log.tail(3)] == ["t2", "t3", "t4"]
    assert stat.S_IMODE(log.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(log.path.parent.stat().st_mode) == 0o700


def test_tail_missing(tmp_path):
    assert AuditLog(tmp_path / "x.jsonl").tail() == []


def test_message_detail_no_text():
    d = message_detail("secret hello")
    assert d["len"] == 12 and len(d["sha256"]) == 64
    assert "secret" not in json.dumps(d)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores perms")
def test_write_failure_raises(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(AuditError):
            AuditLog(d / "audit.jsonl").write(AuditEntry(0, "c", "a", "t", "ok"))
    finally:
        d.chmod(0o700)


def test_session_tag_is_short_and_never_the_id(tmp_path):
    sid = "raw-session-id-value"
    t = session_tag(sid)
    assert len(t) == 8 and sid not in t and sid_hash(sid).startswith(t)  # the sessions file's hash, cut short
    assert session_tag("") == session_tag(None) == ""
    log = AuditLog(tmp_path / "audit.jsonl")
    log.write(AuditEntry(1.0, "c", "a", "t", "ok", {}, t))
    log.write(AuditEntry(2.0, "c", "a", "t", "ok"))  # no session: the old line shape, no key
    a, b = log.tail()
    assert a["session"] == t and "session" not in b and sid not in log.path.read_text()
