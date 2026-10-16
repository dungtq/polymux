"""pm.web.launchctx: the launch form view model carries the launch_redirects hint (``launch_data["hint"]``)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.infra import snapshot as services
from pm.web import launchctx


def _snap(s):
    (s.dev_root / "beta").mkdir()
    return services.build_snapshot(s, force=True)


@pytest.mark.anyio
async def test_launch_data_hint_from_known_projects(env):  # noqa: F811
    s, _c = env
    snap = _snap(s)
    p = snap.project("alpha")
    assert (await launchctx.launch_data(s, p, ["claude"], {}, ["alpha", "beta"]))["hint"] == ""  # empty map
    s = replace(s, launch_redirects={"alpha": "beta"})
    assert (await launchctx.launch_data(s, p, ["claude"], {}, ["alpha", "beta"]))["hint"] == "beta"
    assert (await launchctx.launch_data(s, p, ["claude"], {}))["hint"] == ""  # no known projects given


@pytest.mark.anyio
async def test_form_model_hint(env):  # noqa: F811
    s, _c = env
    snap = _snap(s)
    p = snap.project("alpha")
    assert (await launchctx.form_model(s, snap, p))["launch_data"]["hint"] == ""
    m = await launchctx.form_model(replace(s, launch_redirects={"alpha": "beta"}), snap, p)
    assert m["projects"] == ["alpha", "beta"] and m["launch_data"]["hint"] == "beta"
    m = await launchctx.form_model(replace(s, launch_redirects={"alpha": "nope"}), snap, p)
    assert m["launch_data"]["hint"] == ""  # not a known project
