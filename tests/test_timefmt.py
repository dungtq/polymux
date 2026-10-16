import re
from types import SimpleNamespace

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.core import timefmt
from pm.web.render import env as _env

E0 = 1791358325  # 2026-10-07 14:32:05 UTC+7 (07:32:05 UTC)


@pytest.fixture(autouse=True)
def _reset():
    timefmt.configure(SimpleNamespace(display_utc_offset_h=7.0))
    yield
    timefmt.configure(SimpleNamespace(display_utc_offset_h=7.0))


def test_fmt_basic():
    assert timefmt.fmt_dt(E0) == "2026-10-07 14:32:05"
    assert timefmt.fmt_date(E0) == "2026-10-07"
    assert timefmt.fmt_time(E0) == "14:32:05"
    assert timefmt.fmt_full(E0) == "2026-10-07 14:32:05 UTC+7"


def test_day_rollover_at_17_utc():
    e = 1791392400  # 2026-10-07 17:00:00 UTC
    assert timefmt.fmt_dt(e) == "2026-10-08 00:00:00"
    assert timefmt.fmt_dt(e - 1) == "2026-10-07 23:59:59"


@pytest.mark.parametrize("bad", [None, 0, -5, "x", float("nan"), float("inf"), 1e30, [], True])
def test_bad_input(bad):
    for f in (timefmt.fmt_dt, timefmt.fmt_date, timefmt.fmt_time, timefmt.fmt_full):
        assert f(bad) == "\u2013"


def test_iso():
    assert timefmt.iso(E0) == "2026-10-07T14:32:05+07:00"
    for bad in (None, 0, -5, "x", float("nan"), 1e30, True):
        assert timefmt.iso(bad) is None


def test_offsets():
    timefmt.configure(SimpleNamespace(display_utc_offset_h=5.5))
    assert timefmt.label() == "UTC+5:30" and timefmt.fmt_dt(E0) == "2026-10-07 13:02:05"
    timefmt.configure(SimpleNamespace(display_utc_offset_h=-3))
    assert timefmt.label() == "UTC-3" and timefmt.fmt_dt(E0) == "2026-10-07 04:32:05"
    assert timefmt.offset_attr() == "-3"


def test_filters_registered():
    for n in ("ts", "dt", "date", "time", "ago"):
        assert n in _env.filters
    assert _env.filters["ts"](E0) == "2026-10-07 14:32:05 UTC+7"
    out = str(_env.filters["ago"](E0))
    assert 'title="2026-10-07 14:32:05 UTC+7"' in out and "ago" in out


def test_no_clock_in_pages(env):  # noqa: F811
    s, c = env
    login(s, c)
    for path in ("/", "/p", "/tmux", "/servers", "/audit"):
        t = c.get(path).text
        assert 'id="clock"' not in t and "/static/js/clock.js" not in t, path
        assert not re.search(r"<script(?![^>]*\bsrc=)", t) and "<style" not in t


def test_relative_times_have_title(env):  # noqa: F811
    s, c = env
    login(s, c)
    t = c.get("/").text
    assert re.search(r'<span title="1970-01-01 07:00:01 UTC\+7">', t) or "UTC+7\">" in t
    assert "active <span" not in t


def test_audit_format(env):  # noqa: F811
    from pm.core.models import AuditEntry
    from pm.security.audit import AuditLog
    s, c = env
    login(s, c)
    AuditLog(s.audit_file).write(AuditEntry(float(E0), "1.2.3.4", "tmux.new", "$1", "ok", {}))
    t = c.get("/audit").text
    assert "2026-10-07 14:32:05" in t and "UTC+7" in t


DUR_CASES = [0, 0.9, 5, 59, 60, 61, 300, 3599, 3600, 3661, 7380, 86399, 86400, 90000, 200000, 10 ** 7]


def test_duration_is_the_one_formatter_for_recap_and_subagents():
    """recap.human_duration delegates to timefmt.duration; subagents' private copy is gone."""
    import inspect

    from pm.agents import recap, subagents
    for s in DUR_CASES:
        assert timefmt.duration(s) == recap.human_duration(s), s
    assert not hasattr(subagents, "fmt_dur")
    assert "timefmt.duration(" in inspect.getsource(subagents)


def test_duration_pinned_and_negative():
    assert [timefmt.duration(s) for s in (5, 300, 3661, 90000)] == ["5s", "5m", "1h 1m", "1d 1h"]
    assert timefmt.duration(90000, days_hours=False) == "1d"
    assert timefmt.duration(-5) == "0s" and timefmt.duration(-5, days_hours=False) == "0s"


# --- periods_of: cumulative ranges in the display tz (UTC+7), ISO week starts Monday ---------------
def _ep(y, mo, d, h=0, mi=0, s=0):
    from datetime import datetime, timedelta, timezone
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone(timedelta(hours=7))).timestamp()


def test_periods_midnight_boundary():
    now = _ep(2026, 10, 7, 14, 32)  # Wednesday
    assert timefmt.periods_of(_ep(2026, 10, 7, 0, 0, 0), now) == ("today", "week", "month", "year", "all")
    assert timefmt.periods_of(_ep(2026, 10, 6, 23, 59, 59), now) == ("week", "month", "year", "all")


def test_periods_week_starts_monday():
    now = _ep(2026, 10, 7, 14, 32)  # Monday is 2026-10-05
    assert "week" in timefmt.periods_of(_ep(2026, 10, 5, 0, 0, 0), now)
    assert "week" not in timefmt.periods_of(_ep(2026, 10, 4, 23, 59, 59), now)
    mon = _ep(2026, 10, 5, 9)  # on a Monday "week" and "today" coincide
    assert timefmt.periods_of(mon, mon)[:2] == ("today", "week")


def test_periods_last_week():
    now = _ep(2026, 10, 7, 14, 32)  # Wednesday; this Monday 2026-10-05, previous Monday 2026-09-28
    assert "last_week" in timefmt.periods_of(_ep(2026, 9, 28, 0, 0, 0), now)
    assert "last_week" not in timefmt.periods_of(_ep(2026, 9, 27, 23, 59, 59), now)
    assert "last_week" in timefmt.periods_of(_ep(2026, 10, 4, 23, 59, 59), now)
    p = timefmt.periods_of(_ep(2026, 10, 5, 0, 0, 0), now)  # this Monday: week, never last_week
    assert "week" in p and "last_week" not in p
    mon = _ep(2026, 10, 5, 9)  # on a Monday the previous Sunday evening is last week
    assert timefmt.periods_of(_ep(2026, 10, 4, 23, 59), mon)[:1] == ("last_week",)
    # year rollover: now Fri 2027-01-01, Mon 2026-12-21 is last week; the week of 2026-12-28 is the current one
    fri = _ep(2027, 1, 1, 10)
    p = timefmt.periods_of(_ep(2026, 12, 28, 0, 0, 0), fri)
    assert "week" in p and "last_week" not in p
    p = timefmt.periods_of(_ep(2026, 12, 21, 12), fri)
    assert "last_week" in p and "year" not in p
    # last week inside the current month carries both
    assert timefmt.periods_of(_ep(2026, 10, 1, 12), _ep(2026, 10, 8, 12))[:2] == ("last_week", "month")


def test_periods_month_rollover_and_last_month():
    now = _ep(2026, 10, 7, 14)
    assert "month" in timefmt.periods_of(_ep(2026, 10, 1, 0, 0, 0), now)
    p = timefmt.periods_of(_ep(2026, 9, 30, 23, 59, 59), now)
    assert "month" not in p and "last_month" in p and "year" in p
    assert "last_month" not in timefmt.periods_of(_ep(2026, 8, 31, 12), now)
    # week reaching into the previous month: in "week", not in "month"
    p = timefmt.periods_of(_ep(2026, 9, 30, 10), _ep(2026, 10, 1, 10))
    assert "week" in p and "month" not in p and "last_month" in p


def test_periods_year_rollover():
    now = _ep(2027, 1, 5, 10)
    dec = timefmt.periods_of(_ep(2026, 12, 31, 23, 59, 59), now)
    assert dec == ("last_week", "last_month", "all")  # previous year: last week and month, but not this year
    assert timefmt.periods_of(_ep(2027, 1, 1, 0, 0, 0), now)[-2:] == ("year", "all")
    assert timefmt.periods_of(_ep(2025, 6, 1), now) == ("all",)


def test_periods_bad_input_and_default_now():
    assert timefmt.periods_of(None) == ("all",) and timefmt.periods_of(0) == ("all",) and timefmt.periods_of("x") == ("all",)
    import time
    assert timefmt.periods_of(time.time())[0] == "today"


def test_elapsed():
    got = [timefmt.elapsed(x) for x in (None, -1, 0.04, 4.2, 65, 3725)]
    assert got == ["", "", "0.0s", "4.2s", "1m 05s", "1h 02m"]


def test_usage_day_and_servers_stamp_follow_display_offset_not_host_tz():
    import os
    import time

    from pm.agents import usage
    from pm.web.routes import servers as servers_routes

    e = 1791392400  # 2026-10-07 17:00:00 UTC = 2026-10-08 00:00:00 UTC+7
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        assert usage._day(e) == "2026-10-08" and servers_routes._stamp(SimpleNamespace(ts=e)) == "00:00:00"
        timefmt.configure(SimpleNamespace(display_utc_offset_h=-3))
        assert usage._day(e) == "2026-10-07" and servers_routes._stamp(SimpleNamespace(ts=e)) == "14:00:00"
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_day_of_uses_the_display_tz():
    now = _ep(2026, 10, 7, 0, 30)  # 00:30 UTC+7 = 17:30 UTC the day before
    assert timefmt.day_of(_ep(2026, 10, 7, 0, 0, 0), now) == "today"
    assert timefmt.day_of(_ep(2026, 10, 6, 23, 59, 59), now) == "yesterday"
    assert timefmt.day_of(_ep(2026, 10, 6, 0, 0, 0), now) == "yesterday"
    assert timefmt.day_of(_ep(2026, 10, 5, 23, 59, 59), now) == "earlier"
    assert timefmt.day_of(now + 3600, now) == "today"  # clock slack: a future instant is today
    for bad in (None, 0, -5, "x", True):
        assert timefmt.day_of(bad, now) == "earlier"
    assert timefmt.day_of(__import__("time").time()) == "today"
