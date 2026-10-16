"""Chat message text -> HTML: strips paste wrappers, shows uploaded images as thumbnails.

Only files that exist in ``state_dir/uploads`` under the names ``/a/{id}/image`` writes
(24 hex chars + image extension) become images (``pm.core.uploads``); everything else stays text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pm.core.markdown_render import MAX_CHARS, render_markdown
from pm.core.uploads import upload_name
from pm.security.mask import render_pane

_WRAP = re.compile(r"</?pasted_content\b[^>\n]*>", re.IGNORECASE)


def strip_wrappers(text: str) -> str:
    """Drop ``<pasted_content ..>`` / ``</pasted_content ..>`` tags; the text between stays."""
    return _WRAP.sub("", text or "").strip("\n")


# Messages other agents sent with `tmux-agents` (request / reply / no-reply notice), as that tool pastes them; several
# queued ones can arrive as one user message.
_NOTE = r"(?:\s*\[\d+ older message\(s\) for you[^\]]*\])?"
_NEXT = r"\[tmux-agents (?:request|reply) "
_PEER_REQ = re.compile(r"\[tmux-agents request id=(\S+) from ([^\]\n]+)\]\n(.*?)---\nWhen you have finished,.*?(?:say why\.|\Z)" + _NOTE, re.S)
_PEER_REPLY = re.compile(r"\[tmux-agents reply id=(\S+) from (.+?) status=(\w+)\][ \n]((?:(?!\n" + _NEXT + r").)*)", re.S)
_PEER_NOTICE = re.compile(r"((?:(?!" + _NEXT + r").)*?)\[tmux-agents id=(\S+?)(?: from ([^\]\n]+?))?, no reply expected\]" + _NOTE, re.S)
_PEER_ID = re.compile(r"\b(?:pm id=)?((?:claude|codex|devin|cursor-agent|antigravity)-[0-9a-f]{6})\b")
_PEER_NAME = re.compile(r"name=([^,)]+)")
_PEER_NOTE_END = re.compile(r"\[\d+ older message\(s\) for you[^\]]*\]\s*\Z")
PEER_KINDS = ("claude", "codex", "devin", "cursor-agent", "antigravity")


@dataclass(frozen=True)
class PeerMessage:
    role: str  # request | reply | notice
    sender: str  # the sender's pm agent id when known, else its name / pane
    kind: str  # agent kind of the sender (colour), "other" when unknown
    body: str
    status: str = ""  # reply: done | failed | blocked ...


def _peer(role: str, who: str, body: str, status: str = "") -> PeerMessage:
    pid, name = _PEER_ID.search(who), _PEER_NAME.search(who)
    sender = pid.group(1) if pid else (name.group(1).strip() if name else who.strip())
    kind = next((k for k in PEER_KINDS if pid and pid.group(1).startswith(k + "-")), "other")
    return PeerMessage(role, sender, kind, _PEER_NOTE_END.sub("", body).strip(), status)


def parse_peers(text: str) -> list[PeerMessage] | None:
    """The ``tmux-agents`` messages that make up ``text`` (all of it), in order; None when any part is not one."""
    t = strip_wrappers(text or "").strip()
    if "[tmux-agents " not in t:
        return None
    out: list[PeerMessage] = []
    pos = 0
    while pos < len(t):
        pos += len(t[pos:]) - len(t[pos:].lstrip())
        if pos >= len(t):
            break
        if m := _PEER_REQ.match(t, pos):
            out.append(_peer("request", m.group(2), m.group(3)))
        elif m := _PEER_REPLY.match(t, pos):
            out.append(_peer("reply", m.group(2), m.group(4), m.group(3)))
        elif m := _PEER_NOTICE.match(t, pos):
            out.append(_peer("notice", m.group(3) or "agent", m.group(1)))  # older tool versions sent no "from"
        else:
            return None
        pos = m.end()
    return out or None


def clip_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


_BLOCKS = re.compile(r"[\u2580-\u259f]+")  # banner logo glyphs render as black boxes in a bubble


def clean_text(text: str, limit: int) -> str:
    """Pane/transcript text for a chat bubble: ANSI-stripped, masked, logo glyphs dropped, clipped."""
    return clip_text(_BLOCKS.sub(" ", render_pane(text)), limit)


def split_message(text: str, uploads: Path | None) -> tuple[str, list[str]]:
    """(text without wrapper tags and image-path lines, upload names in order)."""
    names: list[str] = []
    keep: list[str] = []
    fence = False
    for ln in strip_wrappers(text).split("\n"):
        if ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
        n = None if fence else upload_name(ln, uploads)
        if n:
            names.append(n)
        else:
            keep.append(ln)
    return "\n".join(keep).strip("\n"), names


def images_html(names: list[str]) -> str:
    if not names:
        return ""
    items = "".join(
        f'<a class="chat-img" href="/uploads/{n}" target="_blank" rel="noopener noreferrer">'
        f'<img src="/uploads/{n}" alt="Attached image" loading="lazy"></a>'
        for n in dict.fromkeys(names))
    return f'<div class="chat-imgs">{items}</div>'


def render_message(text: str, uploads: Path | None = None, max_chars: int = MAX_CHARS) -> str:
    """Safe HTML for a message body: markdown of the text plus a thumbnail row."""
    body, names = split_message(text, uploads)
    html = render_markdown(body, max_chars, uploads=uploads)
    return html + images_html(names)


def renderer(uploads: Path | None):
    """``md`` callable for templates, bound to the uploads dir."""
    return lambda text, max_chars=MAX_CHARS: render_message(text, uploads, max_chars)


__all__ = ["render_message", "renderer", "split_message", "strip_wrappers"]


# ------------------------------------------------------------------ tool families (chip colours)
_FAM_EXACT = {
    "shell": ("bash", "bashoutput", "killshell", "killbash", "shell", "local_shell", "exec_command",
              "run_terminal_cmd", "run_command", "terminal", "command", "monitor"),
    "read": ("read", "grep", "glob", "ls", "read_file", "list_dir", "view", "view_file", "search",
             "find", "codebase_search", "file_search", "grep_search", "find_by_name", "list_files"),
    "edit": ("edit", "multiedit", "write", "notebookedit", "apply_patch", "edit_file", "write_file",
             "write_to_file", "str_replace", "str_replace_editor", "create_file", "search_replace",
             "delete_file", "replace_file_content", "multi_replace_file_content"),
    "web": ("webfetch", "websearch", "web_search", "fetch", "read_url_content", "search_web", "browser"),
    "agent": ("task", "agent", "todowrite", "todo_write", "skill", "exitplanmode", "enterplanmode",
              "sendmessage", "subagenthandback", "update_plan", "spawn_agent"),
}
_FAM_BY_NAME = {n: fam for fam, names in _FAM_EXACT.items() for n in names}
_FAM_KEYWORDS = (  # first match wins; order matters ("WebSearch" is web, not read)
    ("shell", ("bash", "shell", "terminal", "exec")),
    ("web", ("web", "fetch", "url", "browse", "http")),
    ("agent", ("task", "agent", "todo", "skill", "plan")),
    ("edit", ("edit", "write", "patch", "replace", "create", "delete", "notebook")),
    ("read", ("read", "grep", "glob", "search", "find", "list", "view")),
)


def tool_family(name: str) -> str:
    """Colour family of a tool name: shell, read, edit, web, agent, mcp or other (stable per name)."""
    n = (name or "").strip().lower()
    if not n:
        return "other"
    if n.startswith(("mcp__", "mcp_", "mcp:", "mcp.")):
        return "mcp"
    if n in _FAM_BY_NAME:
        return _FAM_BY_NAME[n]
    for fam, keys in _FAM_KEYWORDS:
        if any(k in n for k in keys):
            return fam
    return "other"
