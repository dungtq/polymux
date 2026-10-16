"""Builder API pane: send one test request to the project's own dev server on 127.0.0.1:<port>.

Off by default: only ports listed in the setting ``builder_api_ports`` are reachable, never pm's own port
(``settings.port``, 8093) or the mock server's (8094). The host is always 127.0.0.1 and the URL is rebuilt
from a checked path (one leading "/", no scheme, "@", "//", backslash, "#", whitespace or control chars)
and re-checked after parsing. Redirects are not followed, proxies from the environment are ignored, the
reply is capped at ``MAX_REPLY`` bytes. pm's own credentials never leave: Cookie, Authorization and
X-CSRF-Token (and hop-by-hop headers) are dropped from the request, Set-Cookie from the reply, and the reply
body and header values go through ``security.mask``.
"""

from __future__ import annotations

import json
import re
import time

import httpx

from pm.core.errors import Denied, Invalid, PmError
from pm.security import mask

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
RESERVED_PORTS = frozenset({8093, 8094})  # pm and the mock server
TIMEOUT_S = 10.0
MAX_PATH = 2048
MAX_BODY = 256 * 1024
MAX_REQUEST = MAX_BODY + 16 * 1024  # the whole JSON request (body + path, query, headers)
MAX_REPLY = 512 * 1024
MAX_HEADERS = 30
DROP_REQ = frozenset({"cookie", "authorization", "x-csrf-token", "host", "content-length", "transfer-encoding",
                      "connection", "keep-alive", "upgrade", "te", "trailer", "proxy-authorization",
                      "proxy-connection"})
DROP_REPLY = frozenset({"set-cookie", "set-cookie2"})
_TOKEN = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")
_BAD_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\#@]")
_BAD_QUERY_CHARS = re.compile(r"[\x00-\x20\x7f\\#]")  # "@" is harmless after "?" (the URL is re-checked)
_transport = None  # tests only: an httpx transport


def allowed_ports(settings) -> tuple[int, ...]:
    """The ports of ``builder_api_ports`` the pane may send to (valid, not reserved, not pm's own)."""
    out: list[int] = []
    for p in settings.builder_api_ports or ():
        if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535 \
                and p not in RESERVED_PORTS and p != settings.port and p not in out:
            out.append(p)
    return tuple(out)


def _port(settings, raw) -> int:
    if isinstance(raw, str) and raw.isdigit() and len(raw) <= 5:
        raw = int(raw)
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise Invalid("port must be a number")
    if raw not in allowed_ports(settings):
        raise Denied("port not enabled (setting builder_api_ports)")
    return raw


def _path(raw, query) -> tuple[str, str]:
    if not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//") or len(raw) > MAX_PATH:
        raise Invalid("path must start with a single /")
    if _BAD_URL_CHARS.search(raw) or "://" in raw or "?" in raw:
        raise Invalid("path has characters that are not allowed")
    query = query if isinstance(query, str) else ""
    query = query.removeprefix("?")
    if len(query) > MAX_PATH or _BAD_QUERY_CHARS.search(query):
        raise Invalid("query has characters that are not allowed")
    return raw, query


def _headers(raw) -> dict[str, str]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict) or len(raw) > MAX_HEADERS:
        raise Invalid(f"headers: at most {MAX_HEADERS} name/value pairs")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not _TOKEN.match(k) or not isinstance(v, str) or len(v) > 4096 \
                or re.search(r"[\x00-\x08\x0a-\x1f\x7f]", v):
            raise Invalid("headers: bad name or value")
        if k.lower() not in DROP_REQ and not k.lower().startswith("proxy-"):
            out[k] = v
    return out


def build_url(port: int, path: str, query: str) -> httpx.URL:
    """http://127.0.0.1:<port><path>[?query], refused unless it parses back to exactly that host and port."""
    url = httpx.URL(f"http://127.0.0.1:{port}{path}" + (f"?{query}" if query else ""))
    if url.scheme != "http" or url.host != "127.0.0.1" or url.port != port or url.userinfo:
        raise Invalid("URL does not point at 127.0.0.1")
    return url


def parse_request(raw: bytes, content_length: str | None) -> dict | None:
    """The JSON request of the send route, or None when it is not JSON. Invalid (status 413) when the
    Content-Length or the actual size is over MAX_REQUEST."""
    if content_length is not None and (not content_length.isdigit() or int(content_length) > MAX_REQUEST):
        raise Invalid("request too large", status=413)
    if len(raw) > MAX_REQUEST:
        raise Invalid("request too large", status=413)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def audit_detail(req) -> dict[str, str | int]:
    """What the audit line keeps: method, port and path (never the query, headers or body)."""
    req = req if isinstance(req, dict) else {}
    path = req.get("path") if isinstance(req.get("path"), str) else ""
    return {"method": str(req.get("method", ""))[:10], "port": str(req.get("port", ""))[:6], "path": path[:200]}


async def send(settings, req) -> dict:
    """Send ``req`` = {"port", "method", "path", "query", "headers", "body"} and return
    {"status", "reason", "headers": [[name, value]], "body", "truncated", "ms"}.
    Invalid / Denied for refused input, PmError(status=502/504) when the server cannot be reached or is slow.
    Async (httpx.AsyncClient on the event loop): a slow dev server never holds a shared executor thread."""
    if not isinstance(req, dict):
        raise Invalid("expected a JSON object")
    port = _port(settings, req.get("port"))
    method = str(req.get("method", "")).upper()
    if method not in METHODS:
        raise Invalid("method not allowed")
    path, query = _path(req.get("path"), req.get("query"))
    headers = _headers(req.get("headers"))
    body = req.get("body") if isinstance(req.get("body"), str) else ""
    if len(body.encode()) > MAX_BODY:
        raise Invalid(f"body is larger than {MAX_BODY // 1024} KB")
    url = build_url(port, path, query)
    kw: dict = {"timeout": TIMEOUT_S, "follow_redirects": False, "trust_env": False}
    if _transport is not None:
        kw["transport"] = _transport
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(**kw) as c, c.stream(method, url, headers=headers,
                                                          content=body.encode() if body else None) as r:
            data, cut = await _read_capped(r)
            status, reason, rh = r.status_code, r.reason_phrase, r.headers.multi_items()
    except httpx.TimeoutException:
        raise PmError(f"no reply from 127.0.0.1:{port} within {TIMEOUT_S:g} s", status=504) from None
    except httpx.HTTPError as e:
        raise PmError(f"could not reach 127.0.0.1:{port} ({e.__class__.__name__})", status=502) from None
    return {"status": status, "reason": reason, "truncated": cut, "ms": int((time.monotonic() - t0) * 1000),
            "headers": [[k, mask(v)] for k, v in rh if k.lower() not in DROP_REPLY][:100],
            "body": mask(data.decode("utf-8", "replace"))}


async def _read_capped(r: httpx.Response) -> tuple[bytes, bool]:
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_REPLY:
            return bytes(buf[:MAX_REPLY]), True
    return bytes(buf), False
