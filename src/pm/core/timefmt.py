"""Display time: one fixed UTC offset (config display_utc_offset_h, default 0 = UTC).

Pure helpers; bad input (None, 0, garbage, out of range) gives the dash, never raises.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

DASH = "–"
_hours: float = 0.0
_tz = timezone(timedelta(hours=0))


def configure(settings) -> None:
    global _hours, _tz
    h = float(getattr(settings, "display_utc_offset_h", 0.0))
    _hours, _tz = h, timezone(timedelta(hours=h))


def offset_hours() -> float:
    return _hours


def offset_minutes() -> int:
    return round(_hours * 60)


def offset_attr() -> str:
    """Offset for the data-offset attribute: '7', '5.5', '-3'."""
    return f"{_hours:g}"


def label() -> str:
    m = offset_minutes()
    sign = "+" if m >= 0 else "-"
    h, mm = divmod(abs(m), 60)
    return f"UTC{sign}{h}" + (f":{mm:02d}" if mm else "")


def _dt(epoch) -> datetime | None:
    if isinstance(epoch, bool) or not isinstance(epoch, int | float) or not epoch or epoch < 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, _tz)
    except (OverflowError, OSError, ValueError):
        return None


def _fmt(epoch, pattern: str, suffix: bool = False) -> str:
    d = _dt(epoch)
    if d is None:
        return DASH
    return d.strftime(pattern) + (f" {label()}" if suffix else "")


def fmt_dt(epoch) -> str:
    return _fmt(epoch, "%Y-%m-%d %H:%M:%S")


def fmt_date(epoch) -> str:
    return _fmt(epoch, "%Y-%m-%d")


def fmt_time(epoch) -> str:
    return _fmt(epoch, "%H:%M:%S")


def fmt_full(epoch) -> str:
    return _fmt(epoch, "%Y-%m-%d %H:%M:%S", True)


def duration(sec: float, *, days_hours: bool = True) -> str:
    """'45s', '5m', '2h 3m', '1d 4h' ('1d' with days_hours=False). Negative counts as 0."""
    n = max(0, int(sec))
    if n < 60:
        return f"{n}s"
    if n < 3600:
        return f"{n // 60}m"
    if n < 86400:
        return f"{n // 3600}h {n % 3600 // 60}m"
    return f"{n // 86400}d {n % 86400 // 3600}h" if days_hours else f"{n // 86400}d"


def elapsed(sec: float | None) -> str:
    """Elapsed time of a tool call, finer than ``duration``: '4.2s', '1m 05s', '2h 03m'; '' when
    unknown (None or negative)."""
    if sec is None or sec < 0:
        return ""
    if sec < 60:
        return f"{sec:.1f}s"
    n = int(sec)
    return f"{n // 60}m {n % 60:02d}s" if n < 3600 else f"{n // 3600}h {n // 60 % 60:02d}m"


def now() -> datetime:
    return datetime.now(UTC).astimezone(_tz)


def tz() -> timezone:
    """The display timezone (fixed offset)."""
    return _tz


def today() -> date:
    """Today's date in the display timezone (not the host's)."""
    return now().date()


def day_start_epoch(d: date) -> float:
    """Epoch of 00:00 of ``d`` in the display timezone."""
    return datetime(d.year, d.month, d.day, tzinfo=_tz).timestamp()


def clock_text() -> str:
    """Top-bar clock, no seconds: 'Wed 07 Oct 14:32 UTC+7'."""
    return f"{now().strftime('%a %d %b %H:%M')} {label()}"


def clock_title() -> str:
    return f"{now().strftime('%A %d %B %Y %H:%M')} {label()}"


def periods_of(epoch, now=None) -> tuple[str, ...]:
    """Display-tz ranges an instant falls in, cumulative by containment: 'today' (since 00:00), 'week' (since
    Monday 00:00), 'last_week' (the previous
    Monday-to-Sunday week only), 'month' (since the 1st), 'last_month' (the previous calendar month only), 'year'
    (since Jan 1) and always 'all'. Bad input gives ('all',). ``now`` is an epoch (default: the current time)."""
    d = _dt(epoch)
    n = _dt(now) if now else datetime.now(UTC).astimezone(_tz)
    if d is None or n is None:
        return ("all",)
    day = n.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    this_monday = day - timedelta(days=n.weekday())
    last = (month - timedelta(days=1)).replace(day=1)
    out = [name for name, ok in (
        ("today", d >= day), ("week", d >= this_monday),
        ("last_week", this_monday - timedelta(days=7) <= d < this_monday), ("month", d >= month),
        ("last_month", last <= d < month), ("year", d >= day.replace(month=1, day=1))) if ok]
    return (*out, "all")


def day_of(epoch, now=None) -> str:
    """'today' | 'yesterday' | 'earlier' by display-tz calendar day (a future instant counts as today). Bad input
    gives 'earlier'. ``now`` is an epoch (default: the current time)."""
    d = _dt(epoch)
    n = _dt(now) if now else datetime.now(UTC).astimezone(_tz)
    if d is None or n is None:
        return "earlier"
    days = (n.date() - d.date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else "earlier"
