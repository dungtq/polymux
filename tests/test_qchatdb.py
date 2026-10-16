"""Quick chat history DB: schema, masking, titles, search, prune, export (tmp state_dir only)."""
import os
import stat
import time

import pytest

from pm.qchat import db


def mk(settings, **kw):
    return db.create_conv(settings, **{"profile": "none", "folder": "", "model": "", "mcp": [], "skills": [], **kw})


def test_open_db_private_wal(settings):
    con = db.open_db(settings)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    con.close()
    assert stat.S_IMODE(os.stat(settings.state_dir / "qchat.db").st_mode) == 0o600
    assert not (settings.state_dir / "pm.db").exists()


def test_conv_roundtrip_and_title(settings):
    cid = mk(settings, profile="read", folder="pm", model="opus", mcp=["a"], skills=["s"])
    c = db.get_conv(settings, cid)
    assert c["title"] == "New chat" and c["msgs"] == 0 and c["mcp"] == ["a"] and c["archived"] is False
    db.add_message(settings, cid, "user", "x" * 100)
    db.add_message(settings, cid, "assistant", "hi", html="<p>hi</p>", cost=0.01, model="opus",
                   tools=[{"id": "1", "name": "Read", "fam": "read", "line": "f", "text": "", "err": False}])
    db.add_message(settings, cid, "user", "second")
    c = db.get_conv(settings, cid)
    assert c["title"] == "x" * 60 and c["msgs"] == 3
    m = db.messages(settings, cid)
    assert [x["role"] for x in m] == ["user", "assistant", "user"]
    assert m[1]["tools"][0]["name"] == "Read" and m[1]["cost"] == 0.01 and m[1]["html"] == "<p>hi</p>"
    with pytest.raises(ValueError):
        db.add_message(settings, cid, "system", "x")


def test_text_is_masked(settings):
    cid = mk(settings)
    secret = "sk-ant-api03-" + "A1b2C3d4" * 6
    db.add_message(settings, cid, "user", f"key {secret}", html=f"<p>{secret}</p>",
                   tools=[{"name": "Bash", "line": f"echo {secret}"}])
    blob = db.messages(settings, cid)[0]
    assert secret not in blob["text"] and secret not in blob["html"] and secret not in blob["tools"][0]["line"]
    assert secret not in db.get_conv(settings, cid)["title"]
    assert secret not in db.export_md(settings, cid)


def test_opts_session_rename_delete(settings):
    cid = mk(settings, profile="none")
    db.set_conv_opts(settings, cid, profile="full", model="haiku", mcp=["m"], skills=[])
    db.set_session(settings, cid, "11111111-2222-3333-4444-555555555555")
    c = db.get_conv(settings, cid)
    assert (c["profile"], c["model"], c["mcp"]) == ("full", "haiku", ["m"]) and c["folder"] == ""
    assert c["archived"] is True  # no such claude session file
    assert db.rename_conv(settings, cid, "  New   name ") and db.get_conv(settings, cid)["title"] == "New name"
    assert not db.rename_conv(settings, cid, "  ") and not db.rename_conv(settings, 999, "x")
    assert db.delete_conv(settings, cid) and not db.delete_conv(settings, cid) and db.get_conv(settings, cid) is None


def test_list_search_stats_delete_all(settings):
    a, b = mk(settings), mk(settings)
    db.add_message(settings, a, "user", "about kubernetes pods")
    db.add_message(settings, b, "user", "100% sure")
    db.add_message(settings, b, "assistant", "the answer is 42")
    assert [c["id"] for c in db.list_convs(settings)] == [b, a]
    assert [c["id"] for c in db.list_convs(settings, q="kuber")] == [a]
    assert db.list_convs(settings, q="42") == []  # q matches title only
    r = db.search(settings, "answer")
    assert [x["id"] for x in r] == [b] and "answer" in r[0]["snippet"]
    assert [x["id"] for x in db.search(settings, "kubernetes")] == [a]
    assert [x["id"] for x in db.search(settings, "%")] == [b]  # literal %, not wildcard
    assert db.search(settings, "") == []
    s = db.stats(settings)
    assert s["convs"] == 2 and s["msgs"] == 3 and s["size"] > 0 and s["path"].endswith("qchat.db")
    assert db.delete_all(settings) == 2 and db.stats(settings)["msgs"] == 0


def test_prune(settings):
    ids = [mk(settings) for _ in range(5)]
    for i in ids:
        db.add_message(settings, i, "user", "m")
    assert db.prune(settings, 0, 0) == 0
    assert db.prune(settings, 3, 0) == 2
    assert sorted(c["id"] for c in db.list_convs(settings)) == ids[2:]
    con = db.open_db(settings)
    con.execute("UPDATE convs SET updated=? WHERE id=?", (time.time() - 40 * 86400, ids[2]))
    con.commit()
    con.close()
    assert db.prune(settings, 0, 30) == 1
    assert db.stats(settings)["msgs"] == 2  # messages cascade


def test_export_md(settings):
    cid = mk(settings)
    db.add_message(settings, cid, "user", "hello")
    db.add_message(settings, cid, "assistant", "world")
    assert db.export_md(settings, cid) == "# hello\n\n**You**\n\nhello\n\n**Claude**\n\nworld\n"
    assert db.export_md(settings, 999) is None


def test_migrates_v1_db_adds_agent_column(settings):
    import sqlite3
    p = settings.state_dir / "qchat.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript("CREATE TABLE convs(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT "
                      "'New chat', session TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', profile TEXT "
                      "NOT NULL DEFAULT 'none', folder TEXT NOT NULL DEFAULT '', mcp TEXT NOT NULL DEFAULT '[]', "
                      "skills TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL);"
                      "INSERT INTO convs(title,created,updated) VALUES('old',1,1); PRAGMA user_version=1;")
    con.commit()
    con.close()
    assert db.get_conv(settings, 1)["agent"] == ""
    con = db.open_db(settings)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    con.close()
    db.open_db(settings).close()  # a second open does not try to add it again
    cid = mk(settings, agent="claude-abc123", title="btw: dev-pm-1-claude")
    assert db.get_conv(settings, cid)["agent"] == "claude-abc123"


def test_set_title_is_kept_by_first_message(settings):
    cid = mk(settings, agent="claude-abc123", title="btw: dev-pm-1-claude")
    db.add_message(settings, cid, "user", "what is it doing?")
    assert db.get_conv(settings, cid)["title"] == "btw: dev-pm-1-claude"
    cid2 = mk(settings)
    assert db.rename_conv(settings, cid2, "Mine")
    db.add_message(settings, cid2, "user", "first")
    assert db.get_conv(settings, cid2)["title"] == "Mine"
    cid3 = mk(settings)
    db.add_message(settings, cid3, "user", "first")
    assert db.get_conv(settings, cid3)["title"] == "first" and db.get_conv(settings, cid3)["agent"] == ""
