"""Tracker dates follow pm's display offset (``display_utc_offset_h``), not the host's timezone.

The host runs in UTC here and pm displays UTC+7, so 18:30 UTC is already the next day for the user."""

import os
import time
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from pm.core import timefmt
from pm.projects import backlog
from pm.projects.tracker import changelog, featurelist, issues, mdimport

PLUS7 = timezone(timedelta(hours=7))
FROZEN = datetime(2030, 1, 1, 18, 30, tzinfo=UTC)  # 2030-01-02 01:30 at UTC+7, still 2030-01-01 on the host


@pytest.fixture
def host_utc(monkeypatch):
    """Host TZ = UTC, display offset +7, ``timefmt.now`` frozen at FROZEN; all restored after."""
    old_tz, old_h = os.environ.get("TZ"), timefmt.offset_hours()
    os.environ["TZ"] = "UTC"
    time.tzset()
    timefmt.configure(SimpleNamespace(display_utc_offset_h=7.0))
    monkeypatch.setattr(timefmt, "now", lambda: FROZEN.astimezone(PLUS7))
    yield
    if old_tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old_tz
    time.tzset()
    timefmt.configure(SimpleNamespace(display_utc_offset_h=old_h))


def test_timefmt_today_and_day_start(host_utc):
    assert timefmt.today() == date(2030, 1, 2)
    start = timefmt.day_start_epoch(date(2026, 10, 15))
    assert start == datetime(2026, 10, 14, 17, tzinfo=UTC).timestamp()
    assert timefmt.fmt_dt(start) == "2026-10-15 00:00:00"


def test_issue_title_stamp_uses_display_date(tk, host_utc):
    mdimport.import_text(tk, "proj", "backlog", "## Inbox\n")
    it = issues.create_issue(tk, "proj", "late night")
    assert it.title == "late night (2030-01-02)"


def test_file_backlog_add_uses_display_date(host_utc):
    fn, _pre, _tok = backlog.build_op("add", {"text": "x"}, settings=None, path=None, lease_id="")
    assert "- [ ] x (2030-01-02)" in fn("# b\n\n## Inbox\n")


def test_mdimport_title_date_is_display_midnight(host_utc):
    want = datetime(2026, 1, 1, 17, tzinfo=UTC).timestamp()
    assert mdimport.title_date("old (2026-01-02)") == want
    assert timefmt.fmt_dt(mdimport.title_date("old (2026-01-02)")) == "2026-01-02 00:00:00"


def test_changelog_dates(tk, host_utc):
    changelog.import_text(tk, "proj", "# Changelog\n\n## 2026-10-01 (one)\n- a\n")
    assert changelog.entries(tk, "proj")[0]["created_at"] == datetime(2026, 9, 30, 17, tzinfo=UTC).timestamp()
    e = changelog.add(tk, "proj", "new", "- b")
    assert e["heading"].startswith("## 2030-01-02")


def test_feature_audited_default_day(tk, host_utc):
    featurelist.import_text(tk, "proj", "# Features\n\nLast audited: 2026-01-01\n")
    assert featurelist.set_audited(tk, "proj") == "Last audited: 2030-01-02"


def test_pmctl_configures_offset_and_show_has_zone_label(pmctl, cfg, host_utc):
    cfg.write_text(cfg.read_text() + "display_utc_offset_h = 7.0\n")
    timefmt.configure(SimpleNamespace(display_utc_offset_h=0.0))  # pmctl must set it from its settings
    pmctl("import")
    code, out, _ = pmctl("issue", "add", "cli late")
    assert code == 0 and "cli late (2030-01-02)" in out
    code, out, _ = pmctl("issue", "show", "4")
    assert code == 0 and "UTC+7 by" in out
