"""Out-of-the-box (host-neutral) behaviour: ntfy and commit links off, globs for VM protection.

The test run uses helpers.SITE as Settings defaults, so these tests pass NEUTRAL_DEFAULTS explicitly."""
import socket

import httpx
from helpers import NEUTRAL_DEFAULTS

from pm.core.config import Settings
from pm.infra import libvirt
from pm.notify import ntfy
from pm.security.unlock import Unlocker


def neutral(**kw) -> Settings:
    return Settings(**{**NEUTRAL_DEFAULTS, **kw})


def test_ntfy_off_sends_nothing():
    calls = []
    t = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    assert ntfy.publish(neutral(), "t", "m", transport=t) is False and calls == []


def test_vm_protected_globs():
    assert not libvirt.is_protected("eve-ng", neutral())
    s = neutral(vm_protected=("*eve*", "prod-?"))
    assert libvirt.is_protected("EVE-NG-x", s) and libvirt.is_protected("prod-1", s)
    assert not libvirt.is_protected("prod-12", s) and not libvirt.is_protected("alpha", s)


def test_totp_account_is_hostname(tmp_path):
    assert Unlocker(tmp_path / "totp", 60).account == f"pm@{socket.gethostname()}"
    assert Unlocker(tmp_path / "totp", 60, account="me@x").account == "me@x"
