"""v7-only "all devices" preferences of the dt theme: Settings › Display and Chat colours (V7-ISO-9, README O5).

One JSON file ``state_dir/v7-prefs.json``::

    {"rev": 3, "prefs": {"run_status": "plain", "col_k_claude": "#c96442", "kind_edge": false}}

Only keys in ``SCHEMA`` are kept, each checked against its kind (an enum, a bool, or a ``#rrggbb`` colour); a missing
key means the theme's default. ``update`` takes a partial change (an empty value = back to the default) and raises
``Invalid`` on an unknown key or a bad value; ``apply`` runs it under the file lock (``pm.core.filelock``) and writes
only when something changed, bumping ``rev`` so other browsers re-read. The launch defaults and ntfy switches are not
here: they are the core's prefs (``/settings/prefs``, V7-ISO-8). Per-browser looks (theme, fonts, sizes) are in the
browser's ``pm.v7.*`` storage (js/head.js). No framework imports.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pm.core import filelock
from pm.core.cache import LRU
from pm.core.config import Settings
from pm.core.errors import Invalid
from pm.core.fsutil import JsonStore

FILE = "v7-prefs.json"
LOCK = "v7-prefs.lock"
COLOUR_RE = re.compile(r"#[0-9a-f]{6}")
BOOL = "bool"
COLOUR = "colour"

# colour keys -> the token they override on <html> (DESIGN.md §3.1 "User-set colours"); js/head.js has the same map.
# The source pills' colours (--src-you, --src-ntfy) are not here: tokens.css has no such tokens yet.
COLOURS = {
    "col_dot_run": "--dot-run", "col_dot_wait": "--dot-wait", "col_dot_fail": "--dot-fail",
    "col_dot_idle": "--dot-idle", "col_dot_end": "--dot-end", "col_badge_un": "--badge-un",
    "col_badge_need": "--badge-need",
    "col_user_bg": "--bub-user-bg", "col_user_bd": "--bub-user-bd", "col_msg_bg": "--bub-asst-bg",
    "col_k_claude": "--k-claude", "col_k_codex": "--k-codex", "col_k_devin": "--k-devin",
    "col_k_cursor_agent": "--k-cursor-agent", "col_k_antigravity": "--k-antigravity",
    "col_tool_shell": "--tool-shell", "col_tool_read": "--tool-read", "col_tool_edit": "--tool-edit",
    "col_tool_web": "--tool-web", "col_tool_agent": "--tool-agent", "col_tool_mcp": "--tool-mcp",
}
PILL_STYLES = ("soft", "border", "grey")
SCHEMA: dict[str, object] = {
    "run_status": ("playful", "plain", "off"),  # Display › Running status (README O4)
    "src_you": PILL_STYLES, "src_ntfy": PILL_STYLES,  # Display › message source pills
    "model_pill": BOOL,  # Display › Show the model pill
    "msg_bg": ("default", "page", "tint", "custom"),  # Chat colours › agent message background
    "kind_edge": BOOL, "kind_pill": BOOL,  # Chat colours › left edge / model pill in the agent colour
    "tool_pill": ("border", "fill", "both"),  # Chat colours › tool call pill style
    "peek_open": ("new", "same"),  # Display › Project quick look: links open in a new browser tab or this one
    **{k: COLOUR for k in COLOURS},
}
DEFAULTS = {"run_status": "playful", "src_you": "soft", "src_ntfy": "soft", "model_pill": True, "msg_bg": "default",
            "kind_edge": True, "kind_pill": True, "tool_pill": "border", "peek_open": "new"}
MAX_KEYS = len(SCHEMA)


@dataclass(frozen=True)
class Prefs:
    rev: int = 0
    values: Mapping[str, object] = field(default_factory=dict)  # only stored (non-default) keys

    def get(self, key: str):
        """The stored value, else the default (None for a colour: the theme's token)."""
        return self.values.get(key, DEFAULTS.get(key))


EMPTY = Prefs()


def check(key: str, value) -> object:
    """``value`` for ``key`` after checking it (form strings "1"/"0"/"true"/"false" for bools, colours lower-cased).
    Raises ``Invalid`` on an unknown key or a bad value."""
    kind = SCHEMA.get(key) if isinstance(key, str) else None
    if kind is None:
        raise Invalid("unknown setting")
    if kind == BOOL:
        if isinstance(value, bool):
            return value
        if value in ("1", "true", "on"):
            return True
        if value in ("0", "false", "off"):
            return False
        raise Invalid(f"{key}: on or off")
    if kind == COLOUR:
        v = value.strip().lower() if isinstance(value, str) else ""
        if not COLOUR_RE.fullmatch(v):
            raise Invalid(f"{key}: a colour like #1a7f37")
        return v
    if value not in kind:  # an enum tuple
        raise Invalid(f"{key}: one of {', '.join(kind)}")
    return value


def from_json(d) -> Prefs:
    """``Prefs`` from the file's object; unknown keys and bad values are dropped (a broken file = defaults)."""
    if not isinstance(d, dict):
        return EMPTY
    rev = d.get("rev")
    vals: dict[str, object] = {}
    raw = d.get("prefs")
    for k, v in (raw.items() if isinstance(raw, dict) else ()):
        try:
            vals[k] = check(k, v)
        except Invalid:
            continue
    return Prefs(rev if isinstance(rev, int) and rev >= 0 else 0, vals)


def to_json(p: Prefs) -> dict:
    return {"rev": p.rev, "prefs": dict(sorted(p.values.items()))}


def update(p: Prefs, change: Mapping[str, object]) -> Prefs:
    """``p`` with ``change`` applied (rev untouched): a value equal to the default or "" drops the key. Raises
    ``Invalid`` on an unknown key, a bad value, or too many keys at once."""
    if not change or len(change) > MAX_KEYS:
        raise Invalid("nothing to save" if not change else "too many settings")
    vals = dict(p.values)
    for k, v in change.items():
        if v in ("", None):
            if k not in SCHEMA:
                raise Invalid("unknown setting")
            vals.pop(k, None)
            continue
        v = check(k, v)
        if k in DEFAULTS and DEFAULTS[k] == v:
            vals.pop(k, None)
        else:
            vals[k] = v
    return Prefs(p.rev, vals)


# ------------------------------------------------------------------ file IO
def path(settings: Settings) -> Path:
    return Path(settings.state_dir) / FILE


def _store(settings: Settings) -> JsonStore:
    return JsonStore(path(settings), dict, validate=lambda d: isinstance(d, dict))


def load(settings: Settings) -> Prefs:
    """The stored prefs (EMPTY when the file is missing or broken)."""
    return from_json(_store(settings).load())


_cache = LRU(max_bytes=256 * 1024, max_entries=8)  # str(path) -> ((mtime_ns, size), Prefs)


def read_cached(settings: Settings) -> Prefs:
    """``load`` for the render path: re-read only when the file's (mtime_ns, size) changed."""
    p = path(settings)
    try:
        s = os.stat(p)
    except OSError:
        return EMPTY
    key = (s.st_mtime_ns, s.st_size)
    hit = _cache.get(str(p))
    if hit is not None and hit[0] == key:
        return hit[1]
    got = load(settings)
    _cache.put(str(p), (key, got))
    return got


def apply(settings: Settings, change: Mapping[str, object]) -> Prefs:
    """Under the lock: load, ``update``, bump ``rev`` and write when a value changed. Returns the stored prefs.
    Raises ``Invalid`` from ``update``, ``OSError`` when the lock is busy."""
    f = path(settings)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with filelock.locked(f.parent / LOCK):
            old = load(settings)
            new = update(old, change)
            if dict(new.values) == dict(old.values):
                return old
            new = Prefs(old.rev + 1, new.values)
            _store(settings).save(to_json(new))
    except filelock.LockTimeout as e:
        raise OSError("prefs are busy") from e
    return new


def as_json(p: Prefs) -> dict:
    """The GET /dt/prefs body: rev and the stored (non-default) values; js/head.js applies them."""
    return {"rev": p.rev, "prefs": dict(p.values)}
