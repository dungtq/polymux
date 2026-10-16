"""pm.core.db: the shared pm.db helper (moved from pm.chat.db): schema once, WAL, 0600, agent locks."""

import os
import stat

from pm.core import db

SCHEMA = ("CREATE TABLE IF NOT EXISTS t_demo (id INTEGER PRIMARY KEY, v TEXT)",)


def test_connect_creates_db_private_and_wal(settings):
    con = db.connect(settings, SCHEMA)
    try:
        con.execute("INSERT INTO t_demo (v) VALUES ('x')")
        con.commit()
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        con.close()
    assert stat.S_IMODE(os.stat(settings.db_file).st_mode) == 0o600
    assert str(settings.db_file) in db.inited(SCHEMA)


def test_schema_and_migrate_run_once_per_file(settings):
    calls = []
    schema = ("CREATE TABLE IF NOT EXISTS t_once (id INTEGER PRIMARY KEY)",)
    for _ in range(3):
        db.connect(settings, schema, calls.append).close()
    assert len(calls) == 1
    db.inited(schema).discard(str(settings.db_file))
    db.connect(settings, schema, calls.append).close()
    assert len(calls) == 2


def test_agent_lock_is_shared_per_agent():
    assert db.agent_lock("a") is db.agent_lock("a")
    assert db.agent_lock("a") is not db.agent_lock("b")


def test_chat_db_module_is_gone():
    import importlib.util

    assert importlib.util.find_spec("pm.chat.db") is None


def _seed(settings, rows=3):
    con = db.connect(settings, SCHEMA)
    con.executemany("INSERT INTO t_demo (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
    con.commit()
    return con


def test_backup_is_a_readable_private_copy(settings):
    import sqlite3
    from datetime import datetime

    live = _seed(settings)  # an open WAL connection: the copy is online
    try:
        path = db.backup(settings, now=datetime(2026, 10, 13, 9))
    finally:
        live.close()
    assert path == settings.state_dir / "backups" / "pm-2026-10-13.db"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    con = sqlite3.connect(path)
    try:
        assert [r[0] for r in con.execute("SELECT v FROM t_demo ORDER BY id")] == ["r0", "r1", "r2"]
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        con.close()
    assert [p.name for p in path.parent.iterdir()] == ["pm-2026-10-13.db"]  # no tmp left


def test_backup_skips_same_day_unless_forced(settings):
    import sqlite3
    from datetime import datetime

    _seed(settings).close()
    day = datetime(2026, 10, 13, 9)
    path = db.backup(settings, now=day)
    _seed(settings, 1).close()
    assert db.backup(settings, now=day.replace(hour=23)) is None
    count = "SELECT count(*) FROM t_demo"
    assert sqlite3.connect(path).execute(count).fetchone()[0] == 3
    assert db.backup(settings, now=day, force=True) == path
    assert sqlite3.connect(path).execute(count).fetchone()[0] == 4


def test_backup_retention_prunes_oldest(settings):
    from datetime import datetime

    _seed(settings).close()
    folder = settings.state_dir / "backups"
    for d in range(1, 6):
        db.backup(settings, keep=3, now=datetime(2026, 10, d))
    (folder / "notes.txt").write_text("kept")
    db.backup(settings, keep=3, now=datetime(2026, 10, 6))
    assert sorted(p.name for p in folder.iterdir()) == [
        "notes.txt", "pm-2026-10-04.db", "pm-2026-10-05.db", "pm-2026-10-06.db"]


def test_backup_off_or_no_db(settings):
    assert db.backup(settings) is None  # no pm.db yet
    _seed(settings).close()
    assert db.backup(settings, keep=0) is None
    assert not (settings.state_dir / "backups").exists()
    assert db.backup(settings) is not None  # default keep = settings.db_backup_keep (14)


def test_backup_loop_runs_and_survives_errors(settings, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    calls = []

    def fake(s):
        calls.append(s)
        raise OSError("disk full")

    monkeypatch.setattr(db, "backup", fake)
    monkeypatch.setattr(db, "BACKUP_FIRST_DELAY_S", 0)
    monkeypatch.setattr(db, "BACKUP_INTERVAL_S", 0.01)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))

    async def run():
        db.start_backup(app)
        await asyncio.sleep(0.1)
        await db.stop_backup(app)

    asyncio.run(run())
    assert len(calls) >= 2 and app.state.db_backup_task is None
