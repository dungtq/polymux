"""The ⋯ menu items of dt rows and headers (PARTS.md §9): one builder for the Inbox, the Notifications list and
the session tab, so every menu has the same keys and the same safety rules (CONDUCT V7-CODE-6). Items are plain dicts;
``parts/_menu.html`` puts them into the trigger's ``data-menu`` JSON and ``js/parts.js`` runs them.

    menu(item("open", "Open chat", "side-r", key="Enter", event="i:open"), SEP, copy_id(aid))

``item`` checks the contract: one action (none when disabled), known keys only, and a typed confirm whose question
names what is typed (V7-SEC-6: the label names the object, and the user types that same name).
"""

from __future__ import annotations

SEP = {"sep": True}
ACTIONS = ("href", "click", "det", "copy", "event", "post")
POST_OPTS = ("vals", "confirm", "typed", "undo", "body", "ok", "done", "refresh")


def item(id: str, label: str, icon: str = "", *, key: str = "", note: str = "", danger: bool = False,
         disabled: bool = False, why: str = "", **action) -> dict:
    """One menu item. ``action``: exactly one of href, click, det, copy, event, post (post may add vals, confirm,
    typed, undo, body, ok, done, refresh; href / event may add confirm); a disabled item has no action and says
    ``why``. ``undo`` replaces the typed confirm's "This cannot be undone." (say what is kept, e.g. a backup)."""
    bad = set(action) - set(ACTIONS) - set(POST_OPTS)
    if bad:
        raise TypeError(f"menu item {id}: unknown keys {sorted(bad)}")
    acts = [k for k in ACTIONS if action.get(k) not in (None, "", False)]
    if disabled:
        if acts:
            raise ValueError(f"menu item {id}: disabled items have no action")
    elif len(acts) != 1:
        raise ValueError(f"menu item {id}: needs exactly one action, got {acts}")
    typed = action.get("typed")
    if typed and typed not in (action.get("confirm") or ""):
        raise ValueError(f"menu item {id}: the confirm question must name the typed value {typed!r}")
    out: dict = {"id": id, "label": label}
    for k, v in (("icon", icon), ("key", key), ("note", note), ("danger", danger), ("disabled", disabled),
                 ("why", why)):
        if v:
            out[k] = v
    out.update({k: v for k, v in action.items() if v not in (None, "", False)})
    return out


def copy_id(agent_id: str) -> dict:
    return item("copy", "Copy agent id", "copy", copy=agent_id)


def details() -> dict:
    """Details (X): toggles the row's details; the same item and icon in every list."""
    return item("det", "Details", "down", key="X", det=True)


def gated(id: str, label: str, icon: str = "", *, why: str = "", need: bool = True, need_why: str = "",
          danger: bool = False, **action) -> dict:
    """An action item that is disabled with a reason when ``why`` (a page-wide reason: control off, protected) is set
    or ``need`` (the row's state allows it) is false; ``why`` wins over ``need_why``."""
    if why or not need:
        return item(id, label, icon, danger=danger, disabled=True, why=why or need_why)
    return item(id, label, icon, danger=danger, **action)


def menu(*parts) -> list[dict]:
    """The items in order: lists are flattened, None dropped, separators never lead, trail or repeat."""
    flat: list[dict] = []
    for p in parts:
        for x in (p if isinstance(p, list | tuple) else (p,)):
            if x is None or (x.get("sep") and (not flat or flat[-1].get("sep"))):
                continue
            flat.append(x)
    while flat and flat[-1].get("sep"):
        flat.pop()
    return flat
