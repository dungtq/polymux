import json
import threading
import time
from dataclasses import replace

import httpx
import pytest

from pm.notify import feed as ntfy_feed


def line(i, t, tag="/x", **kw):
    d = {"event": "message", "id": i, "time": t, "title": f"T{i}", "message": f"M{i}",
         "tags": [tag], "topic": "agent"}
    d.update(kw)
    return json.dumps(d)


@pytest.fixture
def st(tmp_path, monkeypatch):
    ntfy_feed.reset()
    monkeypatch.setattr(ntfy_feed, "_transport", None)  # undone after the test, whatever serve() set
    dev = tmp_path / "dev"
    (dev / "pm").mkdir(parents=True)
    (dev / "pm" / "sub").mkdir()
    (dev / "other").mkdir()
    from pm.core.config import Settings
    s = Settings(dev_root=dev, extra_projects=(dev / "pm" / "sub",), state_dir=tmp_path)
    yield s
    ntfy_feed.reset()


def serve(body, calls=None):
    def h(req):
        if calls is not None:
            calls.append(str(req.url))
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, text=body)
    ntfy_feed._transport = httpx.MockTransport(h)


def test_parse_filters_sorts():
    body = "\n".join([
        json.dumps({"event": "open", "id": "o", "time": 5}),
        json.dumps({"event": "keepalive", "time": 6}),
        "garbage{", line("a", 10), line("b", 30), line("c", 20),
        json.dumps({"event": "message", "id": "bad", "time": "x"}),
    ])
    items = ntfy_feed.parse(body)
    assert [i.id for i in items] == ["b", "c", "a"]
    assert items[0].title == "T" + "b"


def test_caps_and_mask():
    tok = "ghp_" + "a" * 36
    it = ntfy_feed.parse(line("a", 1, title="t" * 500 + tok, message=f"x {tok} " + "m" * 2000))[0]
    assert len(it.title) <= 200 and len(it.message) <= 1000
    assert tok not in it.message
    it = ntfy_feed.parse(line("a", 1, title=f"x {tok}"))[0]
    assert tok not in it.title
    n = ntfy_feed.MAX_ITEMS + 200
    assert len(ntfy_feed.parse("\n".join(line(str(i), i) for i in range(n)))) == ntfy_feed.MAX_ITEMS


def test_matching_counts_limit(st):
    d = str(st.dev_root)
    serve("\n".join([
        line("1", 1, d + "/pm"), line("2", 2, d + "/pm/src/deep"), line("3", 3, d + "/pm/sub/x"),
        line("4", 4, d + "/other"), line("5", 5, "/elsewhere/z"),
    ]))
    r = ntfy_feed.recent(st)
    assert [i.id for i in r] == ["5", "4", "3", "2", "1"]
    by = {i.id: i.project for i in r}
    assert by["2"] == d + "/pm" and by["3"] == d + "/pm/sub" and by["5"] == "/elsewhere/z"
    c = ntfy_feed.counts_by_project(st)
    assert type(c) is dict
    assert c == {d + "/pm": 2, d + "/pm/sub": 1, d + "/other": 1, "/elsewhere/z": 1}
    assert [i.id for i in ntfy_feed.recent(st, limit=2)] == ["5", "4"]
    assert [i.id for i in ntfy_feed.recent(st, project_path=d + "/pm")] == ["2", "1"]
    assert ntfy_feed.fetch_ok(st)


def test_match_is_on_path_segments_and_memoised(st, monkeypatch):
    d = str(st.dev_root)
    serve("\n".join([line("1", 1, d + "/pmx/a"), line("2", 2, d + "/pm/"), line("3", 3, "/")]))
    calls = []
    real = ntfy_feed._match
    monkeypatch.setattr(ntfy_feed, "_match", lambda i, paths: calls.append(i.id) or real(i, paths))
    by = {i.id: i.project for i in ntfy_feed.recent(st)}
    assert by == {"1": d + "/pmx/a", "2": d + "/pm", "3": "/"}  # "pmx" is not inside "pm"
    assert len(calls) == 3
    ntfy_feed.recent(st)
    ntfy_feed.counts_by_project(st)
    assert len(calls) == 3  # same messages and projects: one match pass
    (st.dev_root / "pmx").mkdir()  # a new project re-matches
    assert {i.id: i.project for i in ntfy_feed.recent(st)}["1"] == d + "/pmx"
    assert len(calls) == 6


def test_request_shape(st):
    calls = []
    serve(line("1", 1), calls)
    ntfy_feed.recent(st)
    assert calls and calls[0].startswith(st.ntfy_topic_url + "/json?")
    assert "poll=1" in calls[0] and "since=8760h" in calls[0]


def test_ttl_and_stale_on_error(st):
    st = replace(st, ntfy_cache_s=1000)
    calls = []
    serve(line("1", 1), calls)
    ntfy_feed.recent(st)
    ntfy_feed.recent(st)
    assert len(calls) == 2  # one request per topic, second recent() served from cache
    # expire, then fail: stale items remain, ok False
    ntfy_feed._state["at"] = time.monotonic() - 5000
    serve(httpx.ConnectError("x"))
    assert [i.id for i in ntfy_feed.recent(st)] == ["1"]  # stale served at once
    for _ in range(100):
        if ntfy_feed._state["thread"] is None:
            break
        time.sleep(0.02)
    assert [i.id for i in ntfy_feed.recent(st)] == ["1"]
    assert ntfy_feed.fetch_ok(st) is False


def test_unreachable_never_fetched(st):
    serve(httpx.ConnectError("x"))
    assert ntfy_feed.recent(st) == []
    assert ntfy_feed.counts_by_project(st) == {}
    assert ntfy_feed.fetch_ok(st) is False


def test_single_flight(st):
    st = replace(st, ntfy_cache_s=0)
    gate = threading.Event()
    n = []

    def slow(settings):
        n.append(1)
        gate.wait(5)
        return ntfy_feed.parse(line("1", 1))

    orig = ntfy_feed._fetch
    ntfy_feed._fetch = slow
    try:
        ntfy_feed._state["at"] = time.monotonic() - 10
        ntfy_feed._state["key"] = (ntfy_feed._topics(st), st.ntfy_history)
        ntfy_feed._state["items"] = ntfy_feed.parse(line("0", 0))
        for _ in range(5):
            assert [i.id for i in ntfy_feed.recent(st)] == ["0"]  # stale, no waiting
        assert len(n) == 1
    finally:
        gate.set()
        time.sleep(0.1)
        ntfy_feed._fetch = orig


def test_since_translates_days():
    assert ntfy_feed._since("7d") == "168h"
    assert ntfy_feed._since("12h") == "12h"


def test_both_topics_merged(st):
    calls = []

    def h(req):
        calls.append(str(req.url))
        if "/pm/" in str(req.url):
            return httpx.Response(200, text=line("p", 20, tags=["pm", "idle"], topic="pm"))
        return httpx.Response(200, text="\n".join([line("a", 10), line("b", 30)]))
    ntfy_feed._transport = httpx.MockTransport(h)
    assert [i.id for i in ntfy_feed.recent(st)] == ["b", "p", "a"]
    assert len(calls) == 2 and "/agent/json" in calls[0] and "/pm/json" in calls[1]


def test_one_topic_down_keeps_other(st):
    def h(req):
        if "/pm/" in str(req.url):
            raise httpx.ConnectError("x")
        return httpx.Response(200, text=line("a", 10))
    ntfy_feed._transport = httpx.MockTransport(h)
    assert [i.id for i in ntfy_feed.recent(st)] == ["a"]
    assert ntfy_feed.fetch_ok(st)


def test_restart_uses_disk_copy_without_polling(st):
    now = int(time.time())
    calls = []
    serve(line("1", now - 5, title="hello"), calls)
    ntfy_feed.recent(st)
    assert (st.state_dir / "ntfy-feed.json").stat().st_mode & 0o777 == 0o600
    n = len(calls)
    ntfy_feed.reset()  # simulated restart
    calls.clear()
    assert [i.id for i in ntfy_feed.recent(st)] == ["1"]
    assert calls == [] and n > 0


def test_stale_refresh_is_incremental_and_merges(st):
    now = int(time.time())
    calls = []
    serve(line("1", now - 100), calls)
    ntfy_feed.recent(st)
    ntfy_feed._state["at"] = time.monotonic() - 5000
    calls.clear()
    serve(line("2", now - 1), calls)
    ntfy_feed.recent(st)
    for _ in range(100):
        if ntfy_feed._state["thread"] is None:
            break
        time.sleep(0.02)
    assert calls and all("since=168h" not in c for c in calls)
    assert sorted(i.id for i in ntfy_feed.recent(st)) == ["1", "2"]


def test_disk_copy_ignored_when_topics_change_or_corrupt(st):
    now = int(time.time())
    calls = []
    serve(line("1", now - 5), calls)
    ntfy_feed.recent(st)
    ntfy_feed.reset()
    calls.clear()
    ntfy_feed.recent(replace(st, ntfy_pm_topic_url=""))
    assert calls
    ntfy_feed.reset()
    (st.state_dir / "ntfy-feed.json").write_text("{nope")
    calls.clear()
    ntfy_feed.recent(st)
    assert calls and "since=8760h" in calls[0]


# ---- push stream
import asyncio  # noqa: E402


class _App:
    class state:  # noqa: N801
        hub = None


def _run(coro):
    return asyncio.run(coro)


def test_stream_pushes_items_and_polling_stops(st, monkeypatch):
    now = int(time.time())
    calls = []
    serve(line("1", now - 2) + "\n" + json.dumps({"event": "keepalive", "time": now}), calls)
    published = []

    class Hub:
        def publish(self, t, d):
            published.append(t)

    app = _App()
    app.state = type("S", (), {"hub": Hub(), "settings": st})()
    monkeypatch.setattr(ntfy_feed, "FLUSH_S", 0.01)

    async def go():
        tasks = ntfy_feed.start(app, st)
        await asyncio.sleep(0.3)
        live = set(ntfy_feed._state["live"])
        await ntfy_feed.stop(app)
        return tasks, live

    _, _ = _run(go())
    assert [i.id for i in ntfy_feed._state["items"]] == ["1"]
    assert all("poll=1" not in c for c in calls) and calls
    assert published == ["inbox"]
    assert (st.state_dir / "ntfy-feed.json").exists()


def test_snapshot_skips_poll_while_every_topic_is_live(st):
    calls = []
    serve(line("1", 1), calls)
    topics = ntfy_feed._topics(st)
    with ntfy_feed._lock:
        ntfy_feed._ensure_locked(st)
        ntfy_feed._state["live"].update(topics)
    assert ntfy_feed.recent(st) == []
    assert calls == []
    with ntfy_feed._lock:
        ntfy_feed._state["live"].discard(topics[0])
    ntfy_feed.recent(st)
    assert calls  # one topic not pushing: polling is the fallback


def test_stream_reconnects_after_failure(st, monkeypatch):
    n = []

    def h(req):
        n.append(str(req.url))
        if len(n) == 1:
            raise httpx.ConnectError("x")
        return httpx.Response(200, text=line("9", int(time.time())))
    ntfy_feed._transport = httpx.MockTransport(h)

    async def nosleep(_):
        await asyncio.sleep(0)
    monkeypatch.setattr(ntfy_feed, "_sleep", nosleep)
    monkeypatch.setattr(ntfy_feed, "FLUSH_S", 0.0)
    app = _App()
    app.state = type("S", (), {"hub": None})()

    async def go():
        ntfy_feed.start(app, st)
        await asyncio.sleep(0.2)
        await ntfy_feed.stop(app)
    _run(go())
    assert len(n) >= 2 and [i.id for i in ntfy_feed._state["items"]] == ["9"]


def test_history_change_keeps_saved_items_and_refetches(st):
    now = int(time.time())
    serve(line("1", now - 5))
    ntfy_feed.recent(st)
    ntfy_feed.reset()
    calls = []
    gate = threading.Event()

    def h(req):
        calls.append(str(req.url))
        gate.wait(2)
        return httpx.Response(200, text=line("2", now - 1))
    ntfy_feed._transport = httpx.MockTransport(h)
    st2 = replace(st, ntfy_history="30d")
    assert [i.id for i in ntfy_feed.recent(st2)] == ["1"]  # shown at once, not blanked
    gate.set()
    for _ in range(100):
        if ntfy_feed._state["thread"] is None:
            break
        time.sleep(0.02)
    assert calls and "since=720h" in calls[0]
    assert sorted(i.id for i in ntfy_feed.recent(st2)) == ["1", "2"]


def _stream_with(st, monkeypatch, on_new):
    now = int(time.time())
    serve(line("1", now - 2, title="claude-abc123 - done") + "\n" + line("2", now - 1))
    published = []

    class Hub:
        def publish(self, t, d):
            published.append((t, d))

    app = _App()
    app.state = type("S", (), {"hub": Hub(), "settings": st})()
    monkeypatch.setattr(ntfy_feed, "FLUSH_S", 0.01)

    async def go():
        ntfy_feed.start(app, st, on_new=on_new)
        await asyncio.sleep(0.3)
        await ntfy_feed.stop(app)

    _run(go())
    return published


def test_ingest_returns_the_new_items(st):
    a, b = ntfy_feed._parse_line(line("1", 5)), ntfy_feed._parse_line(line("2", 6))
    assert ntfy_feed._ingest(st, "t", [a]) == [a]
    assert ntfy_feed._ingest(st, "t", [a, b]) == [b]
    assert ntfy_feed._ingest(st, "t", []) == []


def test_flush_publishes_the_on_new_payloads(st, monkeypatch):
    got = []

    async def on_new(items):
        got.extend(i.id for i in items)
        return [{"agent": "claude-abc123", "ntfy": 1, "unread": 1, "last": None}]

    published = _stream_with(st, monkeypatch, on_new)
    assert sorted(got) == ["1", "2"]  # each new item once, per topic stream
    assert {t for t, _ in published} == {"inbox"}
    assert all(d.get("agent") == "claude-abc123" and d["ntfy"] == 1 for _, d in published)


def test_flush_falls_back_to_the_plain_event(st, monkeypatch):
    async def none(items):
        return []

    async def boom(items):
        raise RuntimeError("x")

    for cb in (none, boom, None):
        ntfy_feed.reset()
        (st.state_dir / "ntfy-feed.json").unlink(missing_ok=True)  # else the saved copy makes nothing new
        published = _stream_with(st, monkeypatch, cb)
        assert published and all(p == ("inbox", {"ntfy": 1}) for p in published)


def test_flush_runs_again_for_items_that_arrive_meanwhile(st, monkeypatch):
    """No new flush starts while one runs: items pushed during on_new get a second round, not the next message."""
    a, b = ntfy_feed._parse_line(line("1", 5)), ntfy_feed._parse_line(line("2", 6))
    fresh = [a]
    rounds, published = [], []

    async def on_new(items):
        rounds.append([i.id for i in items])
        if len(rounds) == 1:
            fresh.append(b)  # pushed while this round waits on on_new
        return [{"agent": "x", "ntfy": 1, "n": len(rounds)}]

    class Hub:
        def publish(self, t, d):
            published.append(d["n"])

    app = _App()
    app.state = type("S", (), {"hub": Hub(), "settings": st})()
    app.state.ntfy_on_new = on_new  # on the instance: a class attribute would bind as a method
    monkeypatch.setattr(ntfy_feed, "FLUSH_S", 0.0)
    with ntfy_feed._lock:
        ntfy_feed._ensure_locked(st)
    _run(ntfy_feed._flush(app, st, fresh))
    assert rounds == [["1"], ["2"]] and published == [1, 2] and fresh == []
