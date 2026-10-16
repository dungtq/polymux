"""Sort/limit/filter plumbing of the list pages (Inbox, Projects, tmux): column-header sort, Show limit, cookies."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request, Response

from pm.agents import sorting
from pm.web import deps

_PATHS = {"inbox": "/", "projects": "/p", "tmux": "/tmux"}


def sorted_page(request: Request, page: str) -> tuple[str, dict[str, dict], str, bool]:
    key, persist = sorting.choose(
        page, request.query_params.get("sort"), request.cookies.get(sorting.COOKIES[page])
    )
    path = _PATHS[page]
    query = dict(request.query_params)
    cols = sorting.sort_cols(page, key, path, query)
    self_url = path + "?" + urlencode({**{k: v for k, v in query.items() if k != "sort"}, "sort": key})
    return key, cols, self_url, persist


def wants_fragment(request: Request) -> bool:
    """A poll / SSE / filter refresh (HX-Request) that wants only the list, not a boosted full page."""
    return bool(request.headers.get("hx-request")) and not request.headers.get("hx-boosted")


def clean_q(request: Request) -> str:
    """The list filter `?q=`: whitespace folded, max 80 chars (filter.js q() and sorting.fold_q fold the same way)."""
    return " ".join(request.query_params.get("q", "").split())[:80]


def keep(request: Request, *names: str) -> list[tuple[str, str]]:
    """Query params carried through the list forms as hidden inputs."""
    return [(k, v) for k, v in request.query_params.items() if k in names]


def remember(resp: Response, page: str, key: str, persist: bool) -> Response:
    if persist:
        resp.set_cookie(
            sorting.COOKIES[page], key, max_age=365 * 86400, secure=True,
            samesite="lax", path="/",
        )
    return resp


def limit_page(request: Request, page: str, path: str) -> tuple[str, list[dict], bool]:
    key, persist = sorting.choose_limit(
        page, request.query_params.get("n"),
        request.cookies.get(sorting.LIMIT_COOKIES[page]), deps.settings(request).list_default,
    )
    return key, sorting.limit_links(key, path, dict(request.query_params)), persist


def remember_limit(resp: Response, page: str, key: str, persist: bool) -> Response:
    if persist:
        resp.set_cookie(
            sorting.LIMIT_COOKIES[page], key, max_age=365 * 86400, secure=True,
            samesite="lax", path="/",
        )
    return resp


def limit_ctx(links: list[dict], shown: int, total: int) -> dict:
    return {"limit_links": links, "limit_shown": shown, "limit_total": total}
