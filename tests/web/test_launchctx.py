"""pm.web.launchctx: the launch form view model carries the launch_redirects hint (``launch_data["hint"]``)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.chat import items as chat_items
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


def _seed(s, agent_id, *rows):
    con = chat_items.connect(s)
    try:
        con.execute("BEGIN IMMEDIATE")
        for i, (kind, text) in enumerate(rows):
            chat_items.add(con, agent_id, "c", f"u{i}", kind, text=text, ts=float(i))
        con.commit()
    finally:
        con.close()


ROWS = (("user", "fix the parser"), ("tool", "bash ls"), ("text", "looking at it"), ("text", "fixed"))


@pytest.mark.anyio
async def test_handoff_sets_the_remembered_prompt(env):  # noqa: F811
    s, _c = env
    snap = services.build_snapshot(s, force=True)
    p = snap.project("alpha")
    aid = snap.agents[0].id
    _seed(s, aid, *ROWS)
    m = await launchctx.form_model(s, snap, p, None, aid)
    pr = m["launch_data"]["remembered"]["prompt"]
    assert pr.startswith("You are taking over") and "User: fix the parser" in pr and "Assistant: fixed" in pr
    assert "bash ls" not in pr
    assert "prompt" not in (await launchctx.form_model(s, snap, p))["launch_data"]["remembered"]


@pytest.mark.anyio
async def test_handoff_bad_or_foreign_id_gives_nothing(env):  # noqa: F811
    s, _c = env
    (s.dev_root / "beta").mkdir()
    snap = services.build_snapshot(s, force=True)
    aid = snap.agents[0].id
    _seed(s, aid, *ROWS)
    assert launchctx.handoff_prompt(s, snap, snap.project("alpha"), aid)
    assert launchctx.handoff_prompt(s, snap, snap.project("beta"), aid) == ""  # not an agent of that project
    for bad in ("", "../x", "claude-zzzzzz", "nope"):
        assert launchctx.handoff_prompt(s, snap, snap.project("alpha"), bad) == ""
    m = await launchctx.form_model(s, snap, snap.project("beta"), None, aid)
    assert "prompt" not in m["launch_data"]["remembered"]


def _prompt_of(text):
    import html as htmlmod
    import json
    import re
    m = re.search(r'id="launch-data" hidden data-json="([^"]*)"', text) or re.search(r'data-l-data="([^"]*)"', text)
    return json.loads(htmlmod.unescape(m.group(1)))["remembered"].get("prompt", "")


def test_launch_routes_take_handoff(env):  # noqa: F811
    from helpers import login
    s, c = env
    login(s, c)
    aid = services.build_snapshot(s, force=True).agents[0].id
    _seed(s, aid, *ROWS)
    r = c.get(f"/launch/form?project=alpha&handoff={aid}")
    assert r.status_code == 200 and "User: fix the parser" in _prompt_of(r.text)
    assert "User: fix the parser" in _prompt_of(c.get(f"/launch/form?handoff={aid}").text)  # project from the agent
    assert _prompt_of(c.get("/launch/form?project=alpha&handoff=../x").text) == ""
    r = c.get(f"/p/alpha/launch?handoff={aid}")
    assert r.status_code == 200 and "User: fix the parser" in _prompt_of(r.text)
    assert _prompt_of(c.get("/p/alpha/launch?handoff=claude-zzzzzz").text) == ""


def test_launch_form_handoff_of_an_ended_record_picks_its_project(env):  # noqa: F811
    from pathlib import Path

    from helpers import login

    from pm.agents import registry
    from pm.chat.sources.past import PastSession
    s, c = env
    login(s, c)
    u = "11111111-2222-3333-4444-555555555555"
    assert registry.import_past(s, [PastSession("claude", u, Path(f"/store/{u}"), "/x", 500.0)], project="alpha") == 1
    aid = registry.mint_id("claude", u)
    _seed(s, aid, *ROWS)
    r = c.get(f"/launch/form?handoff={aid}")  # no project given: the ended record's project is the default
    assert r.status_code == 200 and "User: fix the parser" in _prompt_of(r.text)
