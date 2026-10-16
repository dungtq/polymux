"""Project-scoped VM operations (pm.infra.vmops) against an in-memory fake virsh."""

from dataclasses import replace

import pytest

from infra.fakevirt import FakeVirt
from pm.core.config import Settings
from pm.infra import vmops
from pm.infra.libvirt import VmError

PATS = ("lab-*",)


@pytest.fixture
def st():
    return Settings(virsh_bin="virsh", virsh_write_sg=False, vm_protected=("*eve*",))


@pytest.fixture
def fv():
    return FakeVirt()


def _err(fn, *a, **k) -> int:
    with pytest.raises(VmError) as e:
        fn(*a, **k)
    return e.value.status


def test_scope_refuses_outside_patterns_unknown_and_bad_names(st, fv):
    assert _err(vmops.snapshots, st, "prod", PATS, fv.run) == 404  # exists, not the project's
    assert _err(vmops.snapshots, st, "lab-9", PATS, fv.run) == 404  # matches, not listed
    assert _err(vmops.snapshots, st, "lab-1;rm", PATS, fv.run) == 400
    assert _err(vmops.snapshots, st, "lab-1", (), fv.run) == 404  # no patterns = no VMs
    assert _err(vmops.snapshots, st, "LAB-1", PATS, fv.run) == 404  # case-sensitive like filter_info
    assert not fv.writes


def test_snapshots_list_marks_current_and_reads_ro(st, fv):
    v = vmops.snapshots_view(st, "lab-1", PATS, fv.run)
    assert v["vm"] == "lab-1" and v["protected"] is False and v["manage"] is True
    assert [(s["name"], s["current"], s["state"]) for s in v["snapshots"]] == [("s1", False, "running"),
                                                                              ("s2", True, "running")]
    assert v["snapshots"][0]["created"] == "2026-10-15 10:00:00 +0700"
    assert all(a[1] == "-r" for a, _t in fv.calls)
    assert fv.calls[-2][0][-2:] == ["--", "lab-1"]


def test_snapshot_create_default_name_conflict_and_argv(st, fv):
    msg = vmops.snapshot_create(st, "lab-1", PATS, "", fv.run)
    assert msg.startswith("Snapshot pm-") and fv.writes[-1][:3] == ["snapshot-create-as", "--atomic", "--"]
    assert fv.writes[-1][3] == "lab-1" and fv.writes[-1][4].startswith("pm-")
    assert fv.calls[-1][1] == vmops.SNAP_TIMEOUT_S
    assert _err(vmops.snapshot_create, st, "lab-1", PATS, "s1", fv.run) == 409
    assert _err(vmops.snapshot_create, st, "lab-1", PATS, "-x", fv.run) == 400  # never an option
    assert _err(vmops.snapshot_create, st, "lab-eve", ("lab-*",), "a", fv.run) == 403  # protected


def test_snapshot_revert_and_delete_need_existing_snapshot(st, fv):
    assert _err(vmops.snapshot_revert, st, "lab-1", PATS, "nope", fv.run) == 404
    assert not fv.writes
    assert vmops.snapshot_revert(st, "lab-1", PATS, "s1", fv.run) == "Reverted lab-1 to s1."
    assert fv.writes[-1] == ["snapshot-revert", "--", "lab-1", "s1"]
    assert vmops.snapshot_delete(st, "lab-1", PATS, "s1", fv.run).startswith("Deleted snapshot s1")
    assert fv.writes[-1] == ["snapshot-delete", "--", "lab-1", "s1"] and fv.snaps["lab-1"] == ["s2"]


def test_mutations_need_vm_manage(st, fv):
    off = replace(st, vm_manage=False)
    assert _err(vmops.snapshot_create, off, "lab-1", PATS, "a", fv.run) == 403
    assert _err(vmops.domain_action, off, "start", "lab-2", PATS, "", fv.run) == 403
    assert vmops.snapshots(off, "lab-1", PATS, fv.run)  # reads still work
    assert not fv.writes


def test_domain_actions_keep_libvirt_guards(st, fv):
    assert vmops.domain_action(st, "start", "lab-2", PATS, "", fv.run) == "Started lab-2."
    assert fv.writes[-1] == ["start", "--", "lab-2"]
    assert _err(vmops.domain_action, st, "start", "lab-2", PATS, "", fv.run) == 409  # already running
    assert _err(vmops.domain_action, st, "force-off", "lab-1", PATS, "x", fv.run) == 403  # typed confirm
    assert vmops.domain_action(st, "force-off", "lab-1", PATS, "lab-1", fv.run) == "Forced off lab-1."
    assert _err(vmops.domain_action, st, "delete", "lab-1", PATS, "", fv.run) == 403
    assert vmops.domain_action(st, "delete", "lab-1", PATS, "lab-1", fv.run).startswith("Undefined lab-1")
    assert fv.writes[-1] == ["undefine", "--", "lab-1"] and "lab-1" not in fv.doms
    assert _err(vmops.domain_action, st, "force-off", "lab-eve", PATS, "lab-eve", fv.run) == 403
    assert _err(vmops.domain_action, st, "force-off", "prod", PATS, "prod", fv.run) == 404
    assert _err(vmops.domain_action, st, "reboot", "lab-1", PATS, "", fv.run) == 404
    assert "lab-eve" in fv.doms and "prod" in fv.doms


def test_write_failure_is_502(st, fv):
    fv.fail = True
    assert _err(vmops.snapshot_revert, st, "lab-1", PATS, "s1", fv.run) == 502


def test_console_read_only_info(st, fv):
    c = vmops.console(st, "lab-1", PATS, fv.run)
    assert c == {"vm": "lab-1", "state": "running", "url": "vnc://127.0.0.1:5901", "read_only": True}
    fv.display = "garbage <x>"
    assert vmops.console(st, "lab-1", PATS, fv.run)["url"] == "vnc://127.0.0.1:5901"  # vncdisplay :1
    fv.display, fv.vnc = "", ""
    assert vmops.console(st, "lab-1", PATS, fv.run)["url"] == ""
    assert vmops.console(st, "lab-2", PATS, fv.run) == {"vm": "lab-2", "state": "shut off", "url": "",
                                                        "read_only": True}
    assert all(a[1] == "-r" for a, _t in fv.calls)
    assert not any("--include-password" in a for a, _t in fv.calls)


def test_cached_count_never_lists(monkeypatch):
    from pm.infra import libvirt

    dom = lambda n: libvirt.Domain(n, "running", 1, 1, False, True, False)  # noqa: E731
    monkeypatch.setattr(libvirt, "_cache", None)
    monkeypatch.setattr(libvirt, "collect", lambda *a, **k: pytest.fail("virsh called"))
    assert vmops.cached_count(PATS) is None  # nothing cached yet: unknown, no virsh call
    monkeypatch.setattr(libvirt, "_cache", (0.0, libvirt.VmInfo(0.0, (dom("lab-1"), dom("lab-2"), dom("prod")))))
    assert vmops.cached_count(PATS) == 2 and vmops.cached_count(()) is None
    monkeypatch.setattr(libvirt, "_cache", (0.0, libvirt.VmInfo(0.0, available=False, error="down")))
    assert vmops.cached_count(PATS) is None
