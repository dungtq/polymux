"""Syntax highlighting (pygments, short ``hl-`` classes) and diff markup for the project Files pane.

Input text is always already ANSI-stripped and masked (``projfiles._clean``); pygments escapes
HTML itself and every fallback path uses ``html.escape``. Never raises.
"""

from __future__ import annotations

import html


def _plain(text: str) -> str:
    return html.escape(text, quote=False)


def highlight(text: str, filename: str) -> tuple[str, str]:
    """(html, language name) for ``text``; lexer picked by filename, plain escaped text on any problem."""
    try:
        from pygments import highlight as _hl
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_for_filename
        from pygments.util import ClassNotFound

        try:
            lexer = get_lexer_for_filename(filename)
        except ClassNotFound:
            return _plain(text), "Text"
        return _hl(text, lexer, HtmlFormatter(nowrap=True, classprefix="hl-")), lexer.name
    except Exception:  # noqa: BLE001 - highlighting must never break the pane
        return _plain(text), "Text"


def diff_html(text: str) -> str:
    """One ``<span>`` per diff line (newline kept inside the span), escaped, classed df-*."""
    out = []
    for ln in text.splitlines():
        if ln.startswith(("diff ", "index ", "--- ", "+++ ", "new file", "deleted file", "similarity", "rename ", "old mode", "new mode", "Binary files")):
            cls = "df-meta"
        elif ln.startswith("@@"):
            cls = "df-hunk"
        elif ln.startswith("+"):
            cls = "df-add"
        elif ln.startswith("-"):
            cls = "df-del"
        else:
            cls = "df-ctx"
        out.append(f'<span class="{cls}">{_plain(ln)}\n</span>')
    return "".join(out)
