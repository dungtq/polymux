import json
from dataclasses import replace
from pathlib import Path

import pytest

from pm.core.config import Settings
from pm.infra import libvirt

FAKE = Path(__file__).parent / "bin" / "virsh"


@pytest.fixture
def vs(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"alpha": "shut off", "beta": "running", "eve-ng-x": "running"}))
    monkeypatch.setenv("FAKE_VIRSH_STATE", str(state))
    monkeypatch.setenv("FAKE_VIRSH_LOG", str(tmp_path / "log"))
    libvirt.invalidate()
    s = Settings(virsh_bin=str(FAKE), virsh_write_sg=False, state_dir=tmp_path)
    s.extras["log"] = tmp_path / "log"
    s.extras["state"] = state
    return s


def calls(s):
    p = s.extras["log"]
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def writes(s):
    return [c for c in calls(s) if c[:1] == ["-c"]]


def test_collect(vs):
    i = libvirt.collect(vs)
    assert i.available and [d.name for d in i.domains] == ["alpha", "beta", "eve-ng-x"]
    a = i.domains[0]
    assert (a.state, a.vcpus, a.mem_kib) == ("shut off", 2, 2097152)
    assert a.disks[0].capacity == 21474836480 and a.disks[0].allocation == 1073741824
    assert i.domains[2].protected and not a.protected
    assert i.pool.capacity == 100 << 30 and i.pool.available == 60 << 30
    v = {x.name: x for x in i.volumes}
    assert v["alpha.qcow2"].used_by == ("alpha",) and not v["alpha.qcow2"].deletable
    assert v["orphan.qcow2"].deletable
    assert not v["base.qcow2"].deletable and "backing of alpha.qcow2" in v["base.qcow2"].used_by
    assert not v["templates"].deletable
    assert not writes(vs)  # listing is read-only (-r)


def test_missing_virsh(vs):
    i = libvirt.collect(replace(vs, virsh_bin="/nonexistent/virsh"))
    assert not i.available


def test_start_ok_and_state_check(vs):
    assert "Started" in libvirt.domain_action(vs, "start", "alpha")
    assert writes(vs)[-1] == ["-c", "qemu:///system", "start", "--", "alpha"]
    with pytest.raises(libvirt.VmError) as e:  # now running
        libvirt.domain_action(vs, "start", "alpha")
    assert e.value.status == 409


def test_shutdown(vs):
    libvirt.domain_action(vs, "shutdown", "beta")
    assert writes(vs)[-1][2:] == ["shutdown", "--", "beta"]


@pytest.mark.parametrize("name,status", [("nope", 404), ("a b", 400), ("-x", 400), ("a;rm", 400),
                                         ("", 400), ("eve-ng-x", 403)])
def test_bad_names_and_protected(vs, name, status):
    with pytest.raises(libvirt.VmError) as e:
        libvirt.domain_action(vs, "shutdown", name)
    assert e.value.status == status and not writes(vs)


def test_configured_protected(vs):
    with pytest.raises(libvirt.VmError) as e:
        libvirt.domain_action(replace(vs, vm_protected=("beta",)), "shutdown", "beta")
    assert e.value.status == 403 and not writes(vs)


def test_destroy_undefine_need_confirm(vs):
    with pytest.raises(libvirt.VmError) as e:
        libvirt.domain_action(vs, "destroy", "beta", "wrong")
    assert e.value.status == 403 and not writes(vs)
    libvirt.domain_action(vs, "destroy", "beta", "beta")
    assert writes(vs)[-1][2:] == ["destroy", "--", "beta"]
    with pytest.raises(libvirt.VmError):
        libvirt.domain_action(vs, "undefine", "beta", "")
    libvirt.domain_action(vs, "undefine", "beta", "beta")


def test_unknown_action_and_disabled(vs):
    with pytest.raises(libvirt.VmError):
        libvirt.domain_action(vs, "reboot", "alpha")
    with pytest.raises(libvirt.VmError) as e:
        libvirt.domain_action(replace(vs, vm_manage=False), "start", "alpha")
    assert e.value.status == 403


def test_virsh_failure(vs, monkeypatch):
    monkeypatch.setenv("FAKE_VIRSH_FAIL", "1")
    with pytest.raises(libvirt.VmError) as e:
        libvirt.domain_action(vs, "start", "alpha")
    assert e.value.status == 502


def test_image_delete(vs):
    with pytest.raises(libvirt.VmError) as e:
        libvirt.image_delete(vs, "alpha.qcow2", "alpha.qcow2")  # attached
    assert e.value.status == 409
    with pytest.raises(libvirt.VmError) as e:
        libvirt.image_delete(vs, "base.qcow2", "base.qcow2")  # backing file
    assert e.value.status == 409
    with pytest.raises(libvirt.VmError) as e:
        libvirt.image_delete(vs, "templates", "templates")
    assert e.value.status == 403
    with pytest.raises(libvirt.VmError) as e:
        libvirt.image_delete(vs, "orphan.qcow2", "nope")
    assert e.value.status == 403
    with pytest.raises(libvirt.VmError) as e:
        libvirt.image_delete(vs, "../etc", "../etc")
    assert e.value.status == 400
    assert not writes(vs)
    libvirt.image_delete(vs, "orphan.qcow2", "orphan.qcow2")
    assert writes(vs)[-1][2:] == ["vol-delete", "--pool", "images", "--", "orphan.qcow2"]


def test_sg_wrapper(vs, monkeypatch):
    monkeypatch.setattr(libvirt, "_in_libvirt_group", lambda: False)
    argv = libvirt.virsh_rw(replace(vs, virsh_write_sg=True, virsh_bin="virsh"), "start", "--", "a b")
    assert argv[:3] == ["sg", "libvirt", "-c"] and argv[3] == "virsh -c qemu:///system start -- 'a b'"
    monkeypatch.setattr(libvirt, "_in_libvirt_group", lambda: True)
    assert libvirt.virsh_rw(replace(vs, virsh_write_sg=True), "start")[0] != "sg"
