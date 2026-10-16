"""Hand-off prompt for "Switch agent": the chat's user messages and assistant text replies as one prompt.

Pure (no IO). Tool calls, command output, thinking and turn ends are left out. The first user message (the task) and
the last assistant reply (where the work stands) are kept whole, up to a share of the budget; the other items are
dropped oldest first (the newest that fit stay, the first one that does not is clipped) and a marker says where.
"""

from __future__ import annotations

from collections.abc import Iterable

from pm.chat.text import clip_text
from pm.core.models import ChatItem

HANDOFF_MAX = 8000  # the launch prompt textareas have maxlength 8000
PREAMBLE = ("You are taking over work started by another coding agent. "
            "Conversation so far (tool calls and command output omitted):")
MARKER = "[... earlier messages omitted ...]"
SEP = "\n\n"
MIN_CLIP = 80  # a clipped item shorter than this body is dropped instead
_ROLES = {"user": "User: ", "text": "Assistant: "}


def build(items: Iterable[ChatItem], *, limit: int = HANDOFF_MAX) -> str:
    """The hand-off prompt for ``items`` (conversation order), at most ``limit`` chars; "" when no turn is left."""
    turns = []
    for it in items:
        role, body = _ROLES.get(it.kind), (it.text or "").replace("\x00", "").strip()
        if role and body:
            turns.append((it.kind, role, body))
    if not turns:
        return ""
    head = PREAMBLE + SEP
    full = [role + body for _k, role, body in turns]
    if len(head) + len(SEP.join(full)) <= limit:
        return head + SEP.join(full)
    avail = max(limit - len(head), 0)
    first = next((i for i, t in enumerate(turns) if t[0] == "user"), -1)
    last = next((i for i in range(len(turns) - 1, -1, -1) if turns[i][0] == "text"), -1)
    cap = avail * 2 // 5  # the two protected items together take at most 80%, the rest is for the marker and newer items
    out: dict[int, str] = {}
    left = avail - 2 * (len(MARKER) + len(SEP))  # room for up to two markers
    for i in (first, last):
        if i >= 0:
            out[i] = clip_text(full[i], cap)
            left -= len(out[i]) + len(SEP)
    for i in range(len(turns) - 1, -1, -1):
        if i in out:
            continue
        if len(full[i]) + len(SEP) <= left:
            out[i] = full[i]
            left -= len(full[i]) + len(SEP)
            continue
        room = left - len(SEP) - len(turns[i][1])
        if room >= MIN_CLIP:
            out[i] = turns[i][1] + clip_text(turns[i][2], room)
        break
    parts, gap = [], False
    for i in range(len(turns)):
        if i in out:
            parts.append(out[i])
            gap = False
        elif not gap:
            parts.append(MARKER)
            gap = True
    return clip_text(head + SEP.join(parts), limit) if avail else head[:limit].strip()
