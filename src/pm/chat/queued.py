"""Claude Code's queued message: typed while the agent was busy, not processed yet.

The pane shows the message as a highlighted "❯ ..." block followed by the hint line
"ctrl+x ctrl+s to send now". Pure and bounded; callers pass already masked pane lines.
"""
from __future__ import annotations

import re

SCAN_LINES = 40
MAX_BLOCK_LINES = 60
_HINT = re.compile(r"ctrl\+x ctrl\+s to send now", re.I)
_BOX = "│┃║▌▐ \t"


def find_queued(lines: list[str]) -> str:
    """The queued message text above the send-now hint, or "" when there is none."""
    tail = lines[-SCAN_LINES:]
    hint = next((i for i in range(len(tail) - 1, -1, -1) if _HINT.search(tail[i])), -1)
    if hint < 0:
        return ""
    block: list[str] = []
    i = hint - 1
    while i >= 0 and len(block) < MAX_BLOCK_LINES:
        s = tail[i].strip(_BOX)
        if not s:
            return ""  # blank before the prompt marker: not a queued block
        if s.startswith("❯"):
            block.append(s[1:].strip())
            return " ".join(reversed(block)).strip()
        block.append(s)
        i -= 1
    return ""
