"""Right-hand doc pane of the project page: rendered project files (CHANGELOG, README, ...) and notices."""

from __future__ import annotations

from fastapi.responses import HTMLResponse, Response

from pm.web.render import get_template

VIEW_TITLES = {"changelog": "CHANGELOG", "readme": "README", "features": "FEATURES", "claude": "CLAUDE.md", "agents": "AGENTS.md"}


def doc_html(title: str, doc=None, error: str = "", **extra) -> str:
    """Right-hand pane body: rendered Markdown (already masked), or a one-line notice."""
    from pm.core.markdown_render import render_markdown
    from pm.projects.projfiles import MAX_CHARS

    html = render_markdown(doc.text, MAX_CHARS) if doc is not None else ""
    return get_template("_project_doc.html").render(title=title, doc=doc, html=html, error=error, **extra)


def doc_fragment(html: str, status: int = 200) -> Response:
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})


def project_file_html(path: str, key: str) -> tuple[str, bool]:
    """(pane HTML, found) for one fixed project file; missing or refused files give a notice."""
    from pm.projects import projfiles

    title = VIEW_TITLES[key]
    try:
        return doc_html(title, projfiles.read_project_file(path, key)), True
    except projfiles.FileRefused as e:
        return doc_html(title, error=f"No {title} here." if e.status == 404 else e.msg), False
