"""Launch form view model, shared by the launch page (``GET /p/<name>/launch``) and the theme partial
``GET /launch/form`` (``_launch_form.html``; every theme renders the same data its own way).

``launch_data`` is also the JSON the launch form script reads (``#launch-data``): remembered choices, prompt
templates, worktree hints, slash commands, permission modes (one-line help, efforts) and models per kind.
"""

from __future__ import annotations

from urllib.parse import quote

from pm.agents import catalog, slash
from pm.agents import launch as launch_mod
from pm.core import executors, prefs
from pm.core.models import AgentKind


def kinds() -> list[str]:
    """Agent kinds a launch form offers (every kind but ``other``)."""
    return [k.value for k in AgentKind if k is not AgentKind.OTHER]


def remembered(st, project) -> dict:
    """Launch form defaults: the project's last launch (or the global one)."""
    saved = prefs.load(st.prefs_file)
    d = saved["launch"]
    return dict(saved["projects"].get(project.name) or {
        k: v for k, v in (("kind", d["agent"]), ("model_choice", d["model"]), ("mode", d["mode"])) if v})


async def todo_rows(st, snap, project) -> list[tuple]:
    """(index, item, dispatch prompt, in progress) per open TODO item of the project."""
    items = project.todo.open_items if project.todo else ()
    rows = []
    for i, item in enumerate(items):
        key = launch_mod.item_key(item)
        rows.append((i, item, launch_mod.dispatch_prompt(item),
                     await launch_mod.in_progress(st, project.name, key, snap)))
    return rows


async def launch_data(st, project, kind_list: list[str], remembered_: dict) -> dict:
    """JSON for the launch form script: remembered choices, templates, worktree hints, slash, modes, models."""
    hints = await executors.run_collectors(launch_mod.worktree_hints, st, project)
    return {
        "remembered": remembered_,
        "templates": [{"name": n, "text": t} for n, t in launch_mod.TEMPLATES],
        "worktree": hints,
        "slash": slash.launch_catalog(),
        "modes": launch_mod.form_spec(st),
        "models": {k: catalog.families(rows) for k, rows in (await executors.run_collectors(
            catalog.all_models, st, kind_list)).items()},
    }


async def form_model(st, snap, project, todo: int | None = None) -> dict:
    """Theme-neutral view model of one project's launch form.

    Keys: ``project`` (Project), ``base`` (``/p/<name>``; the form posts to ``base + "/launch"``, a TODO item to
    ``base + "/dispatch"``), ``projects`` (names, for the project picker), ``running`` (Agents already in the
    project), ``kinds``, ``launch_data`` (see ``launch_data``; ``modes[kind]`` has ``modes`` [{key, help, bypass}],
    ``extra`` and ``efforts``), ``bypass_allowed``, ``todo`` [{index, text, prompt, busy}] (``busy`` = the agent id it
    is dispatched to, else ""), ``todo_selected`` (the open, not busy item ``todo`` names, else None; its dispatch
    prompt is the remembered prompt).
    """
    kind_list = kinds()
    rem = remembered(st, project)
    rows = await todo_rows(st, snap, project)
    selected = todo if todo is not None and 0 <= todo < len(rows) and not rows[todo][3] else None
    if selected is not None:
        rem["prompt"] = rows[selected][2]
    return {
        "project": project,
        "base": "/p/" + quote(project.name),
        "projects": sorted(p.name for p in snap.projects),
        "running": [a for a in snap.agents if a.project == project.name],
        "kinds": kind_list,
        "launch_data": await launch_data(st, project, kind_list, rem),
        "bypass_allowed": launch_mod.bypass_enabled(st),
        "todo": [{"index": i, "text": item, "prompt": prompt, "busy": busy or ""} for i, item, prompt, busy in rows],
        "todo_selected": selected,
    }
