"""Builder: chat on the left, sandboxed preview of the agent's project files on the right.

``GET /a/{id}/builder`` renders ``builder.html`` with these context keys (the UI agent relies on them):
  agent      the Agent (live, or the stand-in of an ended one)
  base       URL prefix of the preview; the page minted it (HMAC-signed, expires), end with "/"
  project    the Project object or None; pname: its name (or the agent's project string) or None
  root_name  folder name of the preview root
  entry      first of index.html, public/index.html, dist/index.html, build/index.html, out/index.html
             that exists under the root, else None (iframe src = base + entry)
  state      "ready" | "empty"
  live       False for an ended agent; with it: the composer keys of chatctx.composer_ctx
  rec        the registry record of an ended agent (the ended composer: Resume and send / Fork and send), else None
  mtime_url  poll URL (JSON {"mtime", "n"}); files_url lists previewable files (JSON {"files": [...]})
  api_url    the API pane's endpoint list (GET) and, + "/send", its dev-server send (POST)
  frame_hosts  settings.builder_frame_hosts: external origins the address bar / a "Preview:" line may load;
             the page's CSP gets ``frame-src 'self' <frame_hosts>`` (render.csp_frames), other pages none

The preview iframe is sandboxed WITHOUT allow-same-origin, so its document has an opaque origin and the
SameSite=Strict session cookie is NOT sent on its sub-resource requests (css, js, images). Files are
therefore served under ``/a/{id}/builder/f/{exp}.{sig}/<path>``, a path prefix signed with the pm session
secret over (agent id, root, expiry) and accepted instead of the cookie for that route only.
``/a/{id}/builder/frame/<path>`` is the same handler behind the normal login (top-level use, tests).

API pane (static/js/builder-api.js): ``GET /a/{id}/builder/api`` returns the endpoints of an OpenAPI file or
mock files under the preview root (projects/apimock.py) plus ``ports`` (setting builder_api_ports, empty = mock
only); ``{"source": null}`` when there is none, ``{"error": ...}`` when a file is refused. Mock replies are
computed in the browser from that JSON. ``POST /a/{id}/builder/api/send`` (JSON, CSRF header) sends one request
to 127.0.0.1:<listed port> (projects/apisend.py); the audit line keeps method, port and path only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from pm.agents import registry
from pm.core import executors
from pm.core.config import frame_origins
from pm.core.errors import NotFound, PmError
from pm.core.models import Tier
from pm.projects import apimock, apisend
from pm.security import sessions_store as sstore
from pm.security.guard import audit_result, mutation_guard
from pm.web import deps
from pm.web.chatctx import composer_ctx, live_or_ended
from pm.web.deps import AUTH, snapshot
from pm.web.render import csp_frames, html

router = APIRouter()

ENTRIES = ("index.html", "public/index.html", "dist/index.html", "build/index.html", "out/index.html")
MAX_BYTES = 5 * 1024 * 1024
MAX_SCAN = 2000
MAX_LIST = 200
SIG_TTL_S = 12 * 3600
SKIP_DIRS = {"node_modules"}
TYPES = {
    "html": "text/html; charset=utf-8", "htm": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8",
    "mjs": "text/javascript; charset=utf-8", "json": "application/json",
    "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "ico": "image/x-icon", "woff2": "font/woff2",
    "txt": "text/plain; charset=utf-8",
}
LIST_EXT = {"html", "htm", "css", "js", "mjs", "svg", "png", "jpg", "jpeg", "gif", "webp", "ico"}
CSP = (
    "sandbox allow-scripts allow-forms allow-popups-to-escape-sandbox; "
    "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'none'; "
    "frame-ancestors 'self'"
)
HEADERS = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
           "Content-Security-Policy": CSP}
NOSTORE = {"Cache-Control": "no-store"}
API_ACTION = "builder.api_send"


# ------------------------------------------------------------------ root + signing
def _root(settings, agent, snap) -> Path | None:
    """Real directory the preview serves: agent cwd, else its project path."""
    cands = [agent.cwd]
    proj = snap.project(agent.project) if agent.project else None
    if proj is not None:
        cands.append(proj.path)
    for c in cands:
        if not c:
            continue
        try:
            r = Path(c).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if r.is_dir() and r != Path("/"):
            return r
    return None


def _sign(settings, agent_id: str, root: Path, exp: int) -> str:
    key = hmac.new(sstore.load_secret(settings.state_dir), b"pm-builder-frame", hashlib.sha256).digest()
    msg = f"{agent_id}\0{root}\0{exp}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def _mint(settings, agent_id: str, root: Path) -> str:
    exp = int(time.time()) + SIG_TTL_S
    return f"{exp}.{_sign(settings, agent_id, root, exp)}"


def _token_ok(settings, agent_id: str, root: Path, token: str) -> bool:
    exp_s, _, sig = token.partition(".")
    if not exp_s.isdigit() or len(exp_s) > 12 or not sig:
        return False
    exp = int(exp_s)
    return exp >= time.time() and hmac.compare_digest(sig, _sign(settings, agent_id, root, exp))


# ------------------------------------------------------------------ safe file access
def _hidden(parts) -> bool:
    return any(p.startswith(".") for p in parts)


def _resolve(root: Path, rel: str) -> Path | None:
    """Real file under root for a URL path (dir -> index.html), else None."""
    if len(rel) > 1024 or "\0" in rel or "\\" in rel:
        return None
    parts = [p for p in rel.split("/") if p != ""]
    if any(p.startswith(".") for p in parts):  # .., ., .git, .env
        return None
    try:
        real = root.joinpath(*parts).resolve(strict=True)
        if real.is_dir():
            real = (real / "index.html").resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not real.is_relative_to(root) or not real.is_file():
        return None
    if _hidden(real.relative_to(root).parts):  # a symlink into .git etc.
        return None
    return real


def _walk(root: Path, cap: int):
    """Yield (relpath, DirEntry stat) of regular files, skipping dot-dirs/node_modules/symlinked dirs."""
    n = 0
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for f in sorted(files):
            if f.startswith("."):
                continue
            p = os.path.join(dirpath, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            yield os.path.relpath(p, root), st
            n += 1
            if n >= cap:
                return


def _entry(root: Path) -> str | None:
    for e in ENTRIES:
        if _resolve(root, e) is not None:
            return e
    return None


def _serve(root: Path, rel: str) -> Response:
    real = _resolve(root, rel)
    if real is None:
        raise HTTPException(404, "not found")
    ctype = TYPES.get(real.suffix.lower().lstrip("."))
    if ctype is None:
        raise HTTPException(404, "not found")
    try:
        with open(real, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except OSError:
        raise HTTPException(404, "not found") from None
    if len(data) > MAX_BYTES:
        raise HTTPException(404, "not found")
    return Response(data, media_type=ctype, headers=HEADERS)


async def _ctx(request: Request, agent_id: str):
    snap = await snapshot(request)
    agent, _ended = live_or_ended(request, snap, agent_id)
    root = _root(deps.settings(request), agent, snap)
    return agent, snap, root


# ------------------------------------------------------------------ routes
@router.get("/a/{agent_id}/builder", dependencies=AUTH)
async def builder_page(request: Request, agent_id: str) -> Response:
    settings = deps.settings(request)
    agent, snap, root = await _ctx(request, agent_id)
    ended = agent.id not in {a.id for a in snap.agents}
    rec = await executors.run_collectors(registry.get, settings, agent.id) if ended else None
    entry = _entry(root) if root else None
    proj = snap.project(agent.project) if agent.project else None
    base = f"/a/{agent.id}/builder/f/{_mint(settings, agent.id, root)}/" if root else ""
    hosts = frame_origins(settings.builder_frame_hosts)
    return html(request, "builder.html", agent=agent, base=base, project=proj, pname=proj.name if proj else agent.project,
                 root_name=root.name if root else "", entry=entry, state="ready" if entry else "empty",
                 mtime_url=f"/a/{agent.id}/builder/mtime", files_url=f"/a/{agent.id}/builder/files",
                 api_url=f"/a/{agent.id}/builder/api",
                 live=not ended, rec=rec,
                 embed=False, frame_hosts=hosts, csp=csp_frames(hosts),
                 **(await composer_ctx(request, agent) if not ended else {}))


@router.get("/a/{agent_id}/builder/frame/{path:path}", dependencies=AUTH)
async def builder_frame(request: Request, agent_id: str, path: str = "") -> Response:
    _agent, _snap, root = await _ctx(request, agent_id)
    if root is None:
        raise HTTPException(404, "not found")
    return _serve(root, path)


@router.get("/a/{agent_id}/builder/f/{token}/{path:path}")
async def builder_signed(request: Request, agent_id: str, token: str, path: str = "") -> Response:
    """Signed prefix instead of the session cookie (sandboxed iframe sub-resources carry no cookie)."""
    _agent, _snap, root = await _ctx(request, agent_id)
    if root is None or not _token_ok(deps.settings(request), _agent.id, root, token):
        raise HTTPException(404, "not found")
    return _serve(root, path)


@router.get("/a/{agent_id}/builder/mtime", dependencies=AUTH)
async def builder_mtime(request: Request, agent_id: str) -> Response:
    _agent, _snap, root = await _ctx(request, agent_id)
    newest = n = 0
    if root is not None:
        for _rel, st in _walk(root, MAX_SCAN):
            newest = max(newest, st.st_mtime_ns)
            n += 1
    return JSONResponse({"mtime": newest, "n": n}, headers={"Cache-Control": "no-store"})


@router.get("/a/{agent_id}/builder/files", dependencies=AUTH)
async def builder_files(request: Request, agent_id: str) -> Response:
    _agent, _snap, root = await _ctx(request, agent_id)
    out: list[str] = []
    if root is not None:
        for rel, _st in _walk(root, MAX_SCAN):
            if rel.rsplit(".", 1)[-1].lower() in LIST_EXT and "." in rel:
                out.append(rel)
                if len(out) >= MAX_LIST:
                    break
    return JSONResponse({"files": out}, headers={"Cache-Control": "no-store"})


@router.get("/a/{agent_id}/builder/api", dependencies=AUTH)
async def builder_api(request: Request, agent_id: str) -> Response:
    _agent, _snap, root = await _ctx(request, agent_id)
    ports = list(apisend.allowed_ports(deps.settings(request)))
    try:
        if root is None:
            raise NotFound("no folder")
        out = dict(await executors.run_collectors(apimock.load_api, root))
    except NotFound:
        out = {"source": None, "endpoints": []}
    except PmError as e:
        out = {"source": None, "endpoints": [], "error": str(e)}
    return JSONResponse({**out, "ports": ports}, headers=NOSTORE)


@router.post("/a/{agent_id}/builder/api/send")
async def builder_api_send(request: Request, agent_id: str,
                           _=Depends(mutation_guard(API_ACTION, Tier.MESSAGE))) -> Response:
    agent, _snap, _root = await _ctx(request, agent_id)
    detail = apisend.audit_detail(None)
    try:
        req = apisend.parse_request(await request.body(), request.headers.get("content-length"))
        detail = apisend.audit_detail(req)
        out = await apisend.send(deps.settings(request), req)  # async httpx: no executor thread held
    except PmError as e:
        result = "denied" if e.status in (400, 403, 413) else "error"
        audit_result(request, API_ACTION, agent.id, result, {**detail, "reason": str(e)[:200]})
        return JSONResponse({"error": str(e)}, status_code=e.status, headers=NOSTORE)
    audit_result(request, API_ACTION, agent.id, "ok", {**detail, "status": out["status"]})
    return JSONResponse(out, headers=NOSTORE)
