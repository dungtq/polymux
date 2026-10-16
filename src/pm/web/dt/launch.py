"""The dt Launch dialog's view model (docs/themes/dt/PRD.md §6.12, LAYOUT.md §5 "Launch", DESIGN.md §8 key N).

``dt_launch()`` is a Jinja global (``template_globals()``) read by ``_launch_form.html``, the dt version of the core
partial ``GET /launch/form?project=&todo=``. It reads the route's context (``launchctx.form_model``: ``project``,
``base``, ``projects``, ``running``, ``kinds``, ``launch_data``, ``bypass_allowed``, ``todo``, ``todo_selected``) and
returns a ``LaunchView``: the agent cards (kind colour; the core has no per-kind login state, so none is shown), the
running agents of the project, the remembered choices as one line, the TODO rows, the prompt to prefill and the other
project to offer ("Launch in <hint>", ``launch_data.hint`` from ``launch_redirects``). The model list, efforts,
permission modes and worktree hints stay in ``launch_data`` (a JSON attribute read by js/launch.js). No IO.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from jinja2 import pass_context

log = logging.getLogger(__name__)

KIND_LABELS = {"claude": "Claude Code", "codex": "Codex", "devin": "Devin", "cursor-agent": "Cursor",
               "antigravity": "Antigravity"}
STATES = ("running", "waiting", "failed", "idle", "unknown")


@dataclass(frozen=True)
class Card:
    kind: str  # agent kind word (radio value, data-kind)
    label: str  # product name
    cur: bool  # the preselected kind (remembered, else the first)


@dataclass(frozen=True)
class Running:
    id: str
    kind: str
    state: str  # running | waiting | failed | idle | unknown
    title: str  # tmux window name ("" when none)


@dataclass(frozen=True)
class TodoRow:
    index: int  # the item's index in the project's open TODO list (POST /p/<n>/dispatch index)
    text: str
    prompt: str  # the dispatch prompt (what "Use" puts into the prompt box)
    busy: str  # the agent id it is dispatched to, "" when free


@dataclass(frozen=True)
class LaunchView:
    project: str
    base: str  # /p/<quoted name>
    path: str  # the project directory (where the agent runs without a worktree)
    projects: tuple[str, ...]
    cards: tuple[Card, ...]
    kind: str  # preselected kind ("" when the core offers none)
    running: tuple[Running, ...]
    remembered: str  # "claude · opus · plan" ("" when nothing is remembered yet)
    todo: tuple[TodoRow, ...]
    todo_free: int  # open items not in progress
    todo_sel: int | None  # the item ?todo= preselected (its prompt is the prefill)
    prompt: str  # prefill of the prompt box
    data: Mapping  # launch_data for js/launch.js (remembered, templates, worktree, slash, modes, models)
    bypass_allowed: bool
    hint: str = ""  # launch_data.hint (launch_redirects): another listed project to offer "Launch in <hint>"


EMPTY = LaunchView("", "", "", (), (), "", (), "", (), 0, None, "", {}, False)


def _get(obj, key: str, default=None):
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def cards(kinds: Sequence[str], remembered: Mapping) -> tuple[Card, ...]:
    want = str(remembered.get("kind") or "")
    cur = want if want in kinds else (kinds[0] if kinds else "")
    return tuple(Card(k, KIND_LABELS.get(k, k), k == cur) for k in kinds)


def running(agents: Sequence) -> tuple[Running, ...]:
    out = []
    for a in agents or ():
        kind, state = _get(a, "kind"), _get(a, "state")
        kind = str(getattr(kind, "value", kind) or "other")
        state = str(getattr(state, "value", state) or "unknown")
        out.append(Running(str(_get(a, "id", "")), kind, state if state in STATES else "unknown",
                           str(_get(a, "window_name", "") or "")))
    return tuple(out)


def remembered_line(rem: Mapping) -> str:
    """The remembered launch as one line: kind · model · mode (+ worktree branch); "" when nothing is stored."""
    if not rem or not rem.get("kind"):
        return ""
    model = str(rem.get("variant") or rem.get("model_choice") or "")
    if model == "custom":
        model = str(rem.get("model") or "custom")
    parts = [str(rem["kind"]), model or "default model", str(rem.get("mode") or "default mode")]
    if rem.get("effort"):
        parts.append(str(rem["effort"]))
    if rem.get("branch"):
        parts.append("branch " + str(rem["branch"]))
    return " · ".join(parts)


def todo_rows(items: Sequence) -> tuple[TodoRow, ...]:
    return tuple(TodoRow(int(_get(t, "index", 0)), str(_get(t, "text", "")), str(_get(t, "prompt", "")),
                         str(_get(t, "busy", "") or "")) for t in items or ())


def build(ctx: Mapping) -> LaunchView:
    proj = ctx.get("project")
    data = ctx.get("launch_data") or {}
    rem = (data.get("remembered") or {}) if isinstance(data, Mapping) else {}
    kinds = [str(k) for k in ctx.get("kinds") or ()]
    rows = todo_rows(ctx.get("todo") or ())
    sel = ctx.get("todo_selected")
    sel = sel if isinstance(sel, int) and any(r.index == sel and not r.busy for r in rows) else None
    name, projects = str(_get(proj, "name", "") or ""), tuple(str(p) for p in ctx.get("projects") or ())
    hint = str(data.get("hint") or "") if isinstance(data, Mapping) else ""
    return LaunchView(
        project=name, base=str(ctx.get("base") or ""),
        path=str(_get(proj, "path", "") or ""), projects=projects,
        cards=cards(kinds, rem), kind=next((c.kind for c in cards(kinds, rem) if c.cur), ""),
        running=running(ctx.get("running") or ()), remembered=remembered_line(rem), todo=rows,
        todo_free=sum(1 for r in rows if not r.busy), todo_sel=sel, prompt=str(rem.get("prompt") or ""),
        data=data if isinstance(data, Mapping) else {}, bypass_allowed=bool(ctx.get("bypass_allowed")),
        hint=hint if hint in projects and hint != name else "")


@pass_context
def dt_launch(ctx) -> LaunchView:
    """The Launch dialog's view model from the GET /launch/form context; EMPTY (and a log line) when it is broken."""
    try:
        return build(ctx)
    except (TypeError, ValueError, AttributeError) as e:
        log.warning("dt launch view model: %s", e)
        return EMPTY
