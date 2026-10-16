"""Server-side Markdown for chat bubbles. Input must already be masked.

Raw HTML is escaped, link schemes are allow-listed, images never load remote
content (rendered as plain links), and work is capped. The return value is the
only string the templates may mark ``|safe``.
"""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt

from pm.core.uploads import upload_name

MAX_CHARS = 20000
MAX_NESTING = 20
_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
_OK_SCHEMES = {"http", "https", "mailto"}
_CTRL_RE = re.compile(r"[\x00-\x20\x7f-\x9f]")


def _safe_url(url: str) -> bool:
    u = _CTRL_RE.sub("", html.unescape(url or ""))
    if not u or u.startswith("//") or "\\" in u[:2]:
        return False
    m = _SCHEME_RE.match(u)
    if m:
        return m.group(1).lower() in _OK_SCHEMES
    # no scheme: must not hide one behind a colon before any path/query char
    head = re.split(r"[/?#]", u, maxsplit=1)[0]
    return ":" not in head


def _build() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True})
    md.enable("table").enable("strikethrough")
    md.options["maxNesting"] = MAX_NESTING
    md.validateLink = _safe_url  # type: ignore[method-assign]

    def link_open(self, tokens, idx, options, env):
        tok = tokens[idx]
        href = tok.attrGet("href") or ""
        local = upload_name(href, env.get("uploads")) if href.startswith("/") else None
        if local:  # [shot](/tmp/claude-1000/x.png): open in the in-page viewer (imgview.js)
            tok.attrSet("href", f"/uploads/{local}")
            tok.attrSet("class", "chat-img")
            return self.renderToken(tokens, idx, options, env)
        if not _safe_url(href):
            tok.attrSet("href", "#")
        elif _SCHEME_RE.match(href):
            tok.attrSet("target", "_blank")
            tok.attrSet("rel", "noopener noreferrer nofollow")
        return self.renderToken(tokens, idx, options, env)

    def image(self, tokens, idx, options, env):
        tok = tokens[idx]
        src = tok.attrGet("src") or ""
        alt = self.renderInlineAsText(tok.children or [], options, env) or src
        label = html.escape(alt or "image", quote=True)
        if not _safe_url(src):
            return label
        name = upload_name(src, env.get("uploads"))
        if name:
            return (f'<a class="chat-img" href="/uploads/{name}" target="_blank" rel="noopener noreferrer">'
                    f'<img src="/uploads/{name}" alt="{label}" loading="lazy"></a>')
        s = html.escape(src, quote=True)
        return (f'<a href="{s}" target="_blank" rel="noopener noreferrer nofollow">'
                f"{label}</a>")

    plain_code = md.renderer.rules["code_inline"]

    def code_inline(self, tokens, idx, options, env):
        """`/abs/path/shot.png` that is an image we can serve: clickable, opens the viewer."""
        out = plain_code(tokens, idx, options, env)
        c = tokens[idx].content.strip()
        name = upload_name(c, env.get("uploads")) if c.startswith("/") else None
        return f'<a class="chat-img" href="/uploads/{name}">{out}</a>' if name else out

    def table_open(self, tokens, idx, options, env):
        return '<div class="md-table"><table>\n'

    def table_close(self, tokens, idx, options, env):
        return "</table></div>\n"

    md.add_render_rule("link_open", link_open)
    md.add_render_rule("image", image)
    md.add_render_rule("code_inline", code_inline)
    md.add_render_rule("table_open", table_open)
    md.add_render_rule("table_close", table_close)
    return md


_MD = _build()


def render_markdown(text: str, max_chars: int = MAX_CHARS, uploads=None) -> str:
    """Render masked ``text`` to safe HTML (empty string for empty input)."""
    if not text:
        return ""
    text = text[:max_chars]
    try:
        return _MD.render(text, {"uploads": uploads})
    except Exception:  # never fail the page; fall back to escaped text
        return "<p>" + html.escape(text).replace("\n", "<br>\n") + "</p>"
