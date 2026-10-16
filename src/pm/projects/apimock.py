"""Builder API pane: endpoints of an API the agent builds, read from an OpenAPI/Swagger file or mock files.

``load_api(root)`` looks only in fixed places under the preview root (``SPEC_FILES``, ``MOCK_FILE``,
``MOCK_DIR/*.json``), opens every file ``O_NOFOLLOW|O_NONBLOCK`` (no symlink leaf, no FIFO hang) after
``safe_join`` (no ``..``, no symlink escape), caps the file size, the nesting depth and node count of the
parsed document (YAML aliases cannot blow up the walk), the number of endpoints and the size of each example.
YAML goes through ``yaml.safe_load`` only. The result is cached per (path, mtime, size) of the files read.

Result (JSON-ready)::

    {"source": "openapi.json", "kind": "openapi" | "swagger" | "mocks", "title": str,
     "endpoints": [{"method", "path", "summary",
                    "params": [{"name", "in", "required", "example"}],
                    "request": example | None,
                    "responses": [{"status": "200", "desc": str, "headers": {..}, "body": example | None}]}],
     "truncated": bool}

A mock file maps ``"METHOD /path"`` to a response ``{"status", "headers", "body"}`` (or to the body itself),
or is a list of ``{"method", "path", "summary", "request", "status", "headers", "body"}`` (``"responses"``
instead of status/body gives several). Mock entries override the spec's examples for the same method + path.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import yaml

from pm.core.cache import LRU
from pm.core.errors import Invalid, NotFound, PmError
from pm.core.fsutil import safe_join

SPEC_FILES = ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json",
              "api/openapi.json", "api/openapi.yaml", "api/openapi.yml",
              "docs/openapi.json", "docs/openapi.yaml", "docs/openapi.yml")
MOCK_FILE = "mocks.json"
MOCK_DIR = "mocks"
MAX_FILE = 1024 * 1024
MAX_MOCK_FILES = 50
MAX_TOTAL = 4 * 1024 * 1024  # bytes read per load (spec + mocks)
MAX_ENDPOINTS = 500
MAX_DEPTH = 64
MAX_NODES = 200_000
MAX_EXAMPLE = 32 * 1024  # serialized chars per example; larger ones are replaced by a note
GEN_DEPTH = 8  # schema -> sample generation
GEN_BUDGET = 2000  # schema nodes visited per generated sample
METHODS = ("get", "put", "post", "delete", "patch", "head", "options")
_MOCK_KEY = re.compile(r"^\s*([A-Za-z]+)\s+(/\S*)\s*$")
_cache = LRU(max_bytes=16 * 1024 * 1024, max_entries=32)


# ------------------------------------------------------------------ files
def _read(root: Path, rel: str, budget: list) -> bytes | None:
    """Bytes of a regular file at root/rel, None when missing; Invalid when refused or too big."""
    try:
        p = safe_join(root, rel)
    except PmError:
        return None
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
            return None
        data = fh.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise Invalid(f"{rel} is larger than {MAX_FILE // 1024} KB")
    budget[0] -= len(data)
    if budget[0] < 0:
        raise Invalid(f"mock files are larger than {MAX_TOTAL // (1024 * 1024)} MB together")
    return data


def _mock_names(root: Path) -> list[str]:
    try:
        d = safe_join(root, MOCK_DIR)
        if not stat.S_ISDIR(os.lstat(d).st_mode):
            return []
        with os.scandir(d) as it:
            names = sorted(e.name for e in it if e.name.endswith(".json") and not e.name.startswith(".")
                           and e.is_file(follow_symlinks=False))
    except (OSError, PmError):
        return []
    return [f"{MOCK_DIR}/{n}" for n in names[:MAX_MOCK_FILES]]


def _stamp(root: Path, rel: str):
    """(rel, mtime_ns, size) of a regular, non-symlink file inside root, else None."""
    try:
        st = os.lstat(safe_join(root, rel))
    except (OSError, PmError):
        return None
    return (rel, st.st_mtime_ns, st.st_size) if stat.S_ISREG(st.st_mode) else None


def _parse(rel: str, data: bytes):
    text = data.decode("utf-8", "replace")
    try:
        obj = yaml.safe_load(text) if rel.endswith((".yaml", ".yml")) else json.loads(text)
    except (ValueError, yaml.YAMLError, RecursionError):
        raise Invalid(f"{rel}: not valid {'YAML' if rel.endswith(('.yaml', '.yml')) else 'JSON'}") from None
    _check_shape(rel, obj)
    if rel.endswith((".yaml", ".yml")):  # dates -> text, int keys -> str: plain JSON from here on
        try:
            obj = json.loads(json.dumps(obj, default=str))
        except (TypeError, ValueError):
            raise Invalid(f"{rel}: keys must be text") from None
    return obj


def _check_shape(rel: str, obj) -> None:
    """Refuse documents nested deeper than MAX_DEPTH or with more than MAX_NODES nodes (counted per visit,
    so shared YAML aliases count every time they are reached)."""
    stack, n = [(obj, 0)], 0
    while stack:
        o, d = stack.pop()
        n += 1
        if d > MAX_DEPTH or n > MAX_NODES:
            raise Invalid(f"{rel}: nested too deep or too large")
        if isinstance(o, dict):
            stack.extend((v, d + 1) for v in o.values())
        elif isinstance(o, list):
            stack.extend((v, d + 1) for v in o)


# ------------------------------------------------------------------ examples
def _cap(v):
    """The example itself, or a short note when its JSON is larger than MAX_EXAMPLE chars."""
    try:
        s = json.dumps(v, default=str)
    except (TypeError, ValueError):
        return str(v)[:200]
    return v if len(s) <= MAX_EXAMPLE else f"(example larger than {MAX_EXAMPLE // 1024} KB, not shown)"


def _ref(doc: dict, node, depth: int = 0):
    """Follow local ``$ref`` pointers (``#/a/b``); anything else (or a loop) gives {}."""
    while isinstance(node, dict) and isinstance(node.get("$ref"), str):
        ref = node["$ref"]
        if not ref.startswith("#/") or depth > 16:
            return {}
        node, depth = doc, depth + 1
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                return {}
    return node


def _sample(doc: dict, schema, depth: int = 0, budget: list | None = None):
    """A small example value built from a JSON schema (example, default, enum[0], else by type)."""
    budget = budget if budget is not None else [GEN_BUDGET]
    budget[0] -= 1
    schema = _ref(doc, schema)
    if not isinstance(schema, dict) or depth > GEN_DEPTH or budget[0] <= 0:
        return None
    for k in ("example", "default"):
        if k in schema:
            return schema[k]
    if isinstance(schema.get("examples"), list) and schema["examples"]:
        return schema["examples"][0]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    for k in ("oneOf", "anyOf"):
        if isinstance(schema.get(k), list) and schema[k]:
            return _sample(doc, schema[k][0], depth + 1, budget)
    if isinstance(schema.get("allOf"), list):
        out: dict = {}
        for s in schema["allOf"]:
            v = _sample(doc, s, depth + 1, budget)
            if isinstance(v, dict):
                out.update(v)
        return out
    t = schema.get("type")
    t = t[0] if isinstance(t, list) and t else t
    if t == "object" or isinstance(schema.get("properties"), dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        return {str(k): _sample(doc, v, depth + 1, budget) for k, v in list(props.items())[:50]}
    if t == "array":
        return [_sample(doc, schema.get("items"), depth + 1, budget)]
    return {"integer": 0, "number": 0, "boolean": True, "null": None}.get(
        t, {"date-time": "2026-01-01T00:00:00Z", "date": "2026-01-01", "email": "user@example.com",
            "uuid": "00000000-0000-0000-0000-000000000000"}.get(schema.get("format"), "string"))


def _media_example(doc: dict, content) -> object:
    """Example of the JSON (else first) media type of an OpenAPI 3 ``content`` map."""
    content = content if isinstance(content, dict) else {}
    mt = next((v for k, v in content.items() if "json" in str(k)), None) or next(iter(content.values()), None)
    mt = _ref(doc, mt)
    if not isinstance(mt, dict):
        return None
    if "example" in mt:
        return mt["example"]
    exs = mt.get("examples")
    if isinstance(exs, dict) and exs:
        first = _ref(doc, next(iter(exs.values())))
        if isinstance(first, dict) and "value" in first:
            return first["value"]
    return _sample(doc, mt.get("schema")) if "schema" in mt else None


# ------------------------------------------------------------------ OpenAPI / Swagger
def _param(doc: dict, p) -> dict | None:
    p = _ref(doc, p)
    if not isinstance(p, dict) or not isinstance(p.get("name"), str):
        return None
    ex = p.get("example", p.get("x-example"))
    if ex is None and isinstance(p.get("schema"), dict):
        ex = _sample(doc, p["schema"])
    elif ex is None and p.get("type"):
        ex = _sample(doc, p)
    return {"name": p["name"][:200], "in": str(p.get("in", ""))[:20], "required": bool(p.get("required")),
            "example": _cap(ex)}


def _responses(doc: dict, op: dict, swagger: bool) -> list[dict]:
    out = []
    resps = op.get("responses") if isinstance(op.get("responses"), dict) else {}
    for code, r in list(resps.items())[:20]:
        r = _ref(doc, r)
        if not isinstance(r, dict):
            continue
        if swagger:
            exs = r.get("examples") if isinstance(r.get("examples"), dict) else {}
            body = next((v for k, v in exs.items() if "json" in str(k)), None)
            if body is None and "schema" in r:
                body = _sample(doc, r["schema"])
        else:
            body = _media_example(doc, r.get("content"))
        out.append({"status": str(code)[:8], "desc": str(r.get("description") or "")[:200], "headers": {},
                    "body": _cap(body)})
    return out


def _request(doc: dict, op: dict, params: list, swagger: bool):
    if swagger:
        for p in params:
            p = _ref(doc, p)
            if isinstance(p, dict) and p.get("in") == "body":
                return _cap(_sample(doc, p.get("schema")))
        return None
    rb = _ref(doc, op.get("requestBody"))
    return _cap(_media_example(doc, rb.get("content"))) if isinstance(rb, dict) else None


def _openapi(doc: dict) -> tuple[list[dict], bool]:
    swagger = "swagger" in doc and "openapi" not in doc
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        raise Invalid("spec has no paths")
    eps: list[dict] = []
    for path, item in paths.items():
        item = _ref(doc, item)
        if not isinstance(path, str) or not path.startswith("/") or not isinstance(item, dict):
            continue
        shared = item.get("parameters") if isinstance(item.get("parameters"), list) else []
        for m in METHODS:
            op = item.get(m)
            if not isinstance(op, dict):
                continue
            if len(eps) >= MAX_ENDPOINTS:
                return eps, True
            raw = shared + (op.get("parameters") if isinstance(op.get("parameters"), list) else [])
            params = [q for q in (_param(doc, p) for p in raw) if q and q["in"] != "body"]
            eps.append({"method": m.upper(), "path": path[:500],
                        "summary": str(op.get("summary") or op.get("operationId") or "")[:200],
                        "params": params, "request": _request(doc, op, raw, swagger),
                        "responses": _responses(doc, op, swagger)})
    return eps, False


# ------------------------------------------------------------------ mocks
def _mock_resp(v) -> dict:
    if isinstance(v, dict) and ("body" in v or "status" in v):
        hdrs = v.get("headers") if isinstance(v.get("headers"), dict) else {}
        return {"status": str(v.get("status", 200))[:8], "desc": str(v.get("desc") or "")[:200],
                "headers": {str(k)[:100]: str(x)[:500] for k, x in list(hdrs.items())[:30]},
                "body": _cap(v.get("body"))}
    return {"status": "200", "desc": "", "headers": {}, "body": _cap(v)}


def _mock_entry(method: str, path: str, v) -> dict | None:
    if method.lower() not in METHODS or not path.startswith("/"):
        return None
    if not (isinstance(v, dict) and v.keys() & {"body", "status", "responses"}):
        v = {"body": v}  # the value is the response body itself
    rs = v.get("responses")
    if isinstance(rs, dict):
        resps = [_mock_resp({**(r if isinstance(r, dict) and ("body" in r or "headers" in r) else {"body": r}),
                             "status": code}) for code, r in list(rs.items())[:20]]
    elif isinstance(rs, list):
        resps = [_mock_resp(r) for r in rs[:20]]
    else:
        resps = [_mock_resp(v)]
    return {"method": method.upper(), "path": path[:500], "summary": str(v.get("summary") or "")[:200],
            "params": [], "request": _cap(v.get("request")), "responses": resps}


def _mocks(obj) -> list[dict]:
    out: list[dict] = []
    if isinstance(obj, dict) and not ({"method", "path"} <= obj.keys()):
        for k, v in obj.items():
            m = _MOCK_KEY.match(str(k))
            e = _mock_entry(m.group(1), m.group(2), v) if m else None
            out += [e] if e else []
    else:
        for v in obj if isinstance(obj, list) else [obj]:
            if isinstance(v, dict) and isinstance(v.get("method"), str) and isinstance(v.get("path"), str):
                e = _mock_entry(v["method"], v["path"], v)
                out += [e] if e else []
    return out


# ------------------------------------------------------------------ entry point
def _files(root: Path) -> tuple[str | None, list[str]]:
    spec = next((r for r in SPEC_FILES if _stamp(root, r)), None)
    mocks = ([MOCK_FILE] if _stamp(root, MOCK_FILE) else []) + _mock_names(root)
    return spec, mocks


def load_api(root) -> dict:
    """Endpoints of the API under ``root`` (see module doc). NotFound: no spec or mock file; Invalid: a file
    is refused (too big, bad JSON/YAML, too deep, no paths)."""
    root = Path(root)
    spec, mocks = _files(root)
    if spec is None and not mocks:
        raise NotFound("no OpenAPI file or mocks")
    key = (str(root), tuple(_stamp(root, r) for r in ([spec] if spec else []) + mocks))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    out = _build(root, spec, mocks)
    _cache.put(key, out)
    return out


def _build(root: Path, spec: str | None, mocks: list[str]) -> dict:
    eps: list[dict] = []
    truncated, title, kind, budget = False, "", "mocks", [MAX_TOTAL]
    if spec:
        data = _read(root, spec, budget)
        doc = _parse(spec, data) if data is not None else None
        if not isinstance(doc, dict):
            raise Invalid(f"{spec}: not an OpenAPI document")
        eps, truncated = _openapi(doc)
        info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
        title = str(info.get("title") or "")[:200]
        kind = "swagger" if "swagger" in doc and "openapi" not in doc else "openapi"
    index = {(e["method"], e["path"]): e for e in eps}
    for rel in mocks:
        data = _read(root, rel, budget)
        for m in _mocks(_parse(rel, data)) if data is not None else []:
            old = index.get((m["method"], m["path"]))
            if old is not None:
                old["responses"] = m["responses"]
                old["request"] = m["request"] if m["request"] is not None else old["request"]
                old["summary"] = old["summary"] or m["summary"]
            elif len(eps) < MAX_ENDPOINTS:
                eps.append(m)
                index[(m["method"], m["path"])] = m
            else:
                truncated = True
    src = spec or (mocks[0] if len(mocks) == 1 else f"{len(mocks)} mock files")
    return {"source": src, "kind": kind, "title": title, "endpoints": eps, "truncated": truncated}
