"""Agent chat: the live strip (#chat), the markdown side panel and uploads (the items: chatitems.py); issue
images (``/tracker/img``) are served here too, with the same checks as uploads."""

from __future__ import annotations

import hashlib
import re
import time
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from pm.agents import slash
from pm.chat import stats as chat_stats
from pm.chat import strip as chat_strip
from pm.chat.stats import human as human_tokens
from pm.chat.text import clean_text, tool_family
from pm.core import executors, prefs
from pm.core.errors import PmError
from pm.core.fsutil import resolve_md
from pm.core.markdown_render import render_markdown
from pm.core.models import ChatView, Tier
from pm.core.uploads import IMG_MAX_BYTES, UPLOAD_RE, sniff_image, store_upload
from pm.projects.tracker import issues as tracker_issues
from pm.security.audit import AuditError
from pm.security.guard import audit_result, mutation_guard
from pm.web import chatctx, deps
from pm.web.deps import AUTH, agent_or_404, snapshot
from pm.web.render import env, html

router = APIRouter()
HASH_RE = re.compile(r"^[0-9a-f]{8,64}$")


env.globals["tool_family"] = tool_family

_NUM_OPT = re.compile(r"^\s*[1-3][.)]", re.M)


def quick_answers(ask) -> list[str]:
    """Answer buttons for a prompt without key options: 1-3 for a numbered list, else y/n."""
    return ["1", "2", "3"] if ask is not None and _NUM_OPT.search(ask.text or "") else ["y", "n"]


def _digest(view: ChatView, body: str = "") -> str:
    """Hash of everything that is rendered (message, ask card, state)."""
    return hashlib.sha256((repr(view) + "\0" + body).encode()).hexdigest()[:16]


@router.get("/a/{agent_id}/stats", dependencies=AUTH)
async def agent_stats(request: Request, agent_id: str) -> Response:
    from pm.agents import limits
    from pm.agents.usage import panel_data

    snap = await snapshot(request)
    agent, ended = chatctx.live_or_ended(request, snap, agent_id)
    settings = deps.settings(request)
    st = None if ended else await executors.run_tmux(chat_stats.stats_for, agent, settings)
    usage = None if ended or st is None else await executors.run_tmux(panel_data, agent, settings)
    lim = limits.read_limits(settings.state_dir) if agent.kind.value == "claude" and not ended else []
    resp = html(request, "_stats.html", agent=agent, st=st, u=usage, lim=lim, human=human_tokens,
                 n=lambda v: f"{v:,}", compact_cmd=slash.compact_cmd(agent.kind.value))
    # unchanged fragment: 204 (nodiff.js keeps the DOM as it is), so the panel never flashes
    etag = '"' + hashlib.sha256(resp.body).hexdigest()[:16] + '"'
    if request.headers.get("if-none-match", "") == etag:
        return Response(status_code=204, headers={"ETag": etag, "Cache-Control": "no-store"})
    resp.headers["ETag"] = etag
    return resp


def _is_embed_ref(request: Request) -> bool:
    """The chat partial is polled by htmx from the page; the drawer iframe page has ?embed=1 in HX-Current-URL."""
    from urllib.parse import parse_qs, urlsplit
    cur = request.headers.get("HX-Current-URL", "")
    return "1" in parse_qs(urlsplit(cur).query).get("embed", []) or request.query_params.get("embed") == "1"


# ------------------------------------------------------------------ live strip (#chat)
@router.get("/a/{agent_id}/chat", dependencies=AUTH)
async def agent_chat(request: Request, agent_id: str, h: str | None = None) -> Response:
    """The LIVE strip under the conversation (items, ``/items``), right above the composer: the ask
    card, "working... N tool calls", a tool waiting for more than 10 s with the pane tail, Claude's
    own queued message. Never the reply text: that is in the items."""
    snap = await snapshot(request)
    agent, ended = chatctx.live_or_ended(request, snap, agent_id)
    settings = deps.settings(request)
    view, strip = await executors.run_tmux(chat_strip.read, settings, agent, ended, chatlog=chatctx.get_chatlog())
    running = view.state.value == "running" and not ended
    digest = _digest(view, repr(strip) + repr(running))
    etag = f'"{digest}"'
    seen = request.headers.get("if-none-match", "")
    if seen == etag or (h and HASH_RE.fullmatch(h) and h == digest):
        return Response(status_code=204, headers={"ETag": etag, "Cache-Control": "no-store"})
    resp = html(
        request, "_chat.html", agent=agent, view=view, digest=digest, running=running, ended=ended,
        strip=strip, now=time.time(), embed=_is_embed_ref(request), quick_answers=quick_answers,
    )
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ markdown side panel
MD_MAX_BYTES = 512 * 1024
_NOSTORE = {"Cache-Control": "no-store"}


@router.get("/a/{agent_id}/md", dependencies=AUTH)
async def agent_md(request: Request, agent_id: str, path: str = "") -> Response:
    snap = await snapshot(request)
    agent = chatctx.live_or_ended(request, snap, agent_id)[0]
    real = resolve_md(path, agent.cwd)
    if real is None:
        raise HTTPException(404, "file not found")
    try:
        with open(real, "rb") as f:
            data = f.read(MD_MAX_BYTES + 1)
    except OSError:
        raise HTTPException(404, "file not found") from None
    cut = len(data) > MD_MAX_BYTES
    text = clean_text(data[:MD_MAX_BYTES].decode("utf-8", "replace"), MD_MAX_BYTES)
    body = render_markdown(text, MD_MAX_BYTES)
    note = '<p class="muted small">Truncated.</p>' if cut else ""
    html = (
        f'<div class="mdv" data-path="{escape(str(real), quote=True)}">'
        f'<div class="md">{body}</div>{note}</div>'
    )
    return HTMLResponse(html, headers=_NOSTORE)


# ------------------------------------------------------------------ image upload
_IMG_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp"})
_BINARY_OK = frozenset({"zip", "gz", "tar"})
_HARD_BLOCK = frozenset({"exe", "dll", "bat", "cmd", "com", "msi", "scr", "sh", "bash", "zsh",
                         "ps1", "vbs", "jar", "app", "dmg", "apk"})


def sniff_file(data: bytes, filename: str = "", exts=None) -> str | None:
    """Extension to store under, or None to reject.

    Images need matching magic bytes and a listed type, a PDF needs %PDF- magic, other listed
    extensions need clean UTF-8 text (zip/gz/tar may be binary). Executables and names with a
    path separator are always refused."""
    allowed = set(prefs.COMPOSER_DEFAULTS["exts"] if exts is None else exts) - _HARD_BLOCK
    if "/" in filename or "\\" in filename or "\0" in filename:
        return None
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix in _HARD_BLOCK:
        return None
    ext = sniff_image(data)
    if ext:
        return ext if ext in allowed or (ext == "jpg" and "jpeg" in allowed) else None
    if data.startswith(b"%PDF-"):
        return "pdf" if "pdf" in allowed else None
    if suffix in _IMG_EXTS or suffix == "pdf" or suffix not in allowed:
        return None
    if suffix in _BINARY_OK:
        return suffix
    if b"\0" in data[:8192]:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return suffix


def _err(msg: str, status: int) -> HTMLResponse:
    return HTMLResponse(f'<p class="flash err">{escape(msg)}</p>', status_code=status, headers=_NOSTORE)


@router.post("/a/{agent_id}/image", response_class=HTMLResponse)
async def agent_image(request: Request, agent_id: str,
                      _=Depends(mutation_guard("agent_image", Tier.MESSAGE))):
    """Store an uploaded image under state_dir/uploads; the page inserts the path into the message."""
    snap = await snapshot(request)
    agent = agent_or_404(snap, agent_id)
    return await _store_upload(request, "agent_image", agent.id)


@router.post("/p/{name}/image", response_class=HTMLResponse)
async def project_image(request: Request, name: str,
                        _=Depends(mutation_guard("project_image", Tier.MESSAGE))):
    """Same upload for the launch page prompt box (no agent yet)."""
    snap = await snapshot(request)
    if snap.project(name) is None:
        raise HTTPException(404, "unknown project")
    return await _store_upload(request, "project_image", name)


async def _store_upload(request: Request, action: str, target: str) -> HTMLResponse:
    try:
        if int(request.headers.get("content-length", "0")) > IMG_MAX_BYTES + 65536:
            audit_result(request, action, target, "denied", {"why": "size"})
            return _err("File too large (max 10 MB).", 413)
    except ValueError:
        pass
    form = await request.form()
    up = form.get("image")
    if up is None or isinstance(up, str):
        return _err("No image.", 400)
    data = await up.read(IMG_MAX_BYTES + 1)
    if len(data) > IMG_MAX_BYTES:
        audit_result(request, action, target, "denied", {"why": "size"})
        return _err("File too large (max 10 MB).", 413)
    allowed = prefs.composer_prefs(deps.settings(request))["exts"]
    ext = sniff_file(data, getattr(up, "filename", "") or "", allowed)
    if ext is None:
        audit_result(request, action, target, "denied", {"why": "type"})
        return _err("Allowed file types: " + ", ".join(sorted(set(allowed) - _HARD_BLOCK)) + ".", 415)
    sha = hashlib.sha256(data).hexdigest()
    dest = store_upload(deps.settings(request), data, ext)
    try:
        audit_result(request, action, target, "ok",
                     {"len": len(data), "sha256": sha, "type": ext})
    except AuditError:
        raise HTTPException(503, "audit unavailable") from None
    return HTMLResponse(
        f'<p class="flash ok" data-image-path="{escape(str(dest), quote=True)}">File attached.</p>',
        headers=_NOSTORE,
    )


_IMG_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


@router.get("/uploads/{name}", dependencies=AUTH)
async def upload_file(request: Request, name: str) -> Response:
    """Serve one stored image from state_dir/uploads: strict name, magic bytes must match, image types only."""
    if not UPLOAD_RE.fullmatch(name):
        raise HTTPException(404, "not found")
    return image_response(deps.settings(request).state_dir / "uploads" / name)


@router.get("/tracker/img/{name}", dependencies=AUTH)
async def tracker_image(request: Request, name: str) -> Response:
    """An issue image (docs/TRACKER-PLAN.md): strict ``<24hex>.<ext>`` name, same checks and headers as uploads."""
    try:
        path = await executors.run_collectors(tracker_issues.image_path, deps.settings(request), name)
    except PmError:
        raise HTTPException(404, "not found") from None
    return await executors.run_collectors(image_response, path)


def image_response(path) -> Response:
    """One stored image (uploads, tracker images): no symlink, magic bytes must match, image types only,
    sandboxed and never sniffed by the browser; anything else is 404. The caller checks the name."""
    try:
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "not found")
        with open(path, "rb") as f:
            data = f.read(IMG_MAX_BYTES + 1)
    except OSError:
        raise HTTPException(404, "not found") from None
    ext = sniff_image(data) if len(data) <= IMG_MAX_BYTES else None
    if ext is None:
        raise HTTPException(404, "not found")
    return Response(data, media_type=_IMG_TYPES[ext], headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline"})
