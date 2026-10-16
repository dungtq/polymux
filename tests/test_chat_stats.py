import json

from pm.chat.stats import human, session_stats


def _w(path, *entries):
    with open(path, "a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _asst(mid, i, o, cw=0, cr=0, side=False):
    return {"type": "assistant", "isSidechain": side, "message": {"id": mid, "role": "assistant", "content": [{"type": "text", "text": "x"}],
            "usage": {"input_tokens": i, "output_tokens": o, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}}}


def _user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def test_totals_dedupe_sidechain_and_incremental(tmp_path):
    p = tmp_path / "s.jsonl"
    _w(p, _user("hello"), _asst("m1", 10, 5, 100, 1000), _asst("m1", 10, 40, 100, 1000),  # same reply, final output 40
       _asst("m2", 99, 99, side=True),  # sub-agent: excluded
       {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "r"}]}},
       _user("<system-reminder>x</system-reminder>"))
    s = session_stats(p)
    assert (s.turns, s.input, s.output, s.cache_write, s.cache_read) == (1, 10, 40, 100, 1000)
    assert s.total == 150
    _w(p, _user("again"), _asst("m3", 1, 2))
    with open(p, "a") as f:
        f.write('{"partial"')  # unfinished last line is left for next time
    s = session_stats(p)
    assert s.turns == 2 and s.total == 150 + 3
    assert session_stats(tmp_path / "missing.jsonl") is None


def test_human():
    assert human(950) == "950" and human(1234) == "1.2k" and human(34000) == "34k" and human(2_500_000) == "2.5M"


def test_context_level(tmp_path):
    p = tmp_path / "c.jsonl"
    _w(p, _user("hi"), _asst("m1", 5, 5, 20_000, 100_000))
    s = session_stats(p)
    assert s.ctx == 120_005 and s.window == 200_000 and s.pct == 60 and s.level == "warn"
    _w(p, _asst("m2", 5, 5, 0, 170_000))
    assert session_stats(p).level == "red"
    _w(p, _asst("m3", 5, 5, 0, 300_000))
    assert session_stats(p).window == 1_000_000 and session_stats(p).level == "ok"


def test_ctx_pct(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    from pm.chat import stats
    from pm.chat.sources.common import Stats

    claude, cursor = NS(kind=NS(value="claude")), NS(kind=NS(value="cursor-agent"))
    monkeypatch.setattr(stats, "stats_for", lambda a, settings=None: Stats(ctx=120_005))
    assert stats.ctx_pct(claude) == 60
    assert stats.ctx_pct(cursor) is None  # its numbers come from a pane read: skipped
    monkeypatch.setattr(stats, "stats_for", lambda a, settings=None: Stats())
    assert stats.ctx_pct(claude) is None  # no reply yet: unknown, not 0
    monkeypatch.setattr(stats, "stats_for", lambda a, settings=None: Stats(pct_hint=37))
    assert stats.ctx_pct(claude) == 37
    monkeypatch.setattr(stats, "stats_for", lambda a, settings=None: None)
    assert stats.ctx_pct(claude) is None

    def boom(a, settings=None):
        raise OSError("x")
    monkeypatch.setattr(stats, "stats_for", boom)
    assert stats.ctx_pct(claude) is None
