import io
import logging

import pytest

from pm.core.config import setup_logging


@pytest.fixture(autouse=True)
def restore_pm_logger():
    lg = logging.getLogger("pm")
    saved = (lg.handlers[:], lg.level, lg.propagate, lg.disabled)
    yield lg
    lg.handlers[:], lg.level, lg.propagate, lg.disabled = saved


def test_twice_one_handler(restore_pm_logger):
    setup_logging()
    setup_logging()
    lg = restore_pm_logger
    assert len(lg.handlers) == 1
    assert isinstance(lg.handlers[0], logging.StreamHandler)
    assert lg.level == logging.INFO and lg.propagate is False


def test_child_info_reaches_handler(restore_pm_logger):
    setup_logging()
    h = restore_pm_logger.handlers[0]
    buf = io.StringIO()
    old = h.setStream(buf)
    try:
        logging.getLogger("pm.x").info("hello %s", "there")
    finally:
        h.setStream(old)
    assert buf.getvalue() == "INFO pm.x: hello there\n"


def test_level_honoured(restore_pm_logger):
    setup_logging("warning")
    lg = restore_pm_logger
    assert lg.level == logging.WARNING
    h = lg.handlers[0]
    buf = io.StringIO()
    old = h.setStream(buf)
    try:
        logging.getLogger("pm.x").info("dropped")
        logging.getLogger("pm.x").warning("kept")
    finally:
        h.setStream(old)
    assert buf.getvalue() == "WARNING pm.x: kept\n"


def test_existing_loggers_stay_enabled(restore_pm_logger):
    other = logging.getLogger("pm.already.there")
    setup_logging()
    assert other.disabled is False
