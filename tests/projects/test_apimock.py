"""projects.apimock: spec discovery in fixed places, OpenAPI 3 / Swagger 2 / YAML / mocks parsing, caps, refusals."""
import json

import pytest

from pm.core.errors import Invalid, NotFound
from pm.projects import apimock

SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Pets"},
    "paths": {
        "/pets": {
            "get": {"summary": "List pets",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "example": 5}}],
                    "responses": {"200": {"description": "OK", "content": {"application/json": {
                        "schema": {"type": "array", "items": {"$ref": "#/components/schemas/Pet"}}}}}}},
            "post": {"requestBody": {"content": {"application/json": {"example": {"name": "Rex"}}}},
                     "responses": {"201": {"description": "Created", "content": {"application/json": {
                         "examples": {"one": {"value": {"id": 7, "name": "Rex"}}}}}},
                                   "400": {"description": "Bad"}}},
        },
        "/pets/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer", "example": 1}}],
            "delete": {"responses": {"204": {"description": "Gone"}}},
        },
    },
    "components": {"schemas": {"Pet": {"type": "object", "properties": {
        "id": {"type": "integer"}, "name": {"type": "string", "example": "Tom"},
        "self": {"$ref": "#/components/schemas/Pet"}}}}},
}


@pytest.fixture(autouse=True)
def _nocache():
    apimock._cache.clear()


def _eps(out):
    return {(e["method"], e["path"]): e for e in out["endpoints"]}


def test_openapi_json(tmp_path):
    (tmp_path / "openapi.json").write_text(json.dumps(SPEC))
    out = apimock.load_api(tmp_path)
    assert out["source"] == "openapi.json" and out["kind"] == "openapi" and out["title"] == "Pets"
    eps = _eps(out)
    assert set(eps) == {("GET", "/pets"), ("POST", "/pets"), ("DELETE", "/pets/{id}")}
    get = eps[("GET", "/pets")]
    assert get["summary"] == "List pets" and get["params"] == [
        {"name": "limit", "in": "query", "required": False, "example": 5}]
    body = get["responses"][0]["body"]
    assert get["responses"][0]["status"] == "200" and body[0]["name"] == "Tom" and body[0]["id"] == 0
    post = eps[("POST", "/pets")]
    assert post["request"] == {"name": "Rex"}
    assert [r["status"] for r in post["responses"]] == ["201", "400"]
    assert post["responses"][0]["body"] == {"id": 7, "name": "Rex"} and post["responses"][1]["body"] is None
    assert eps[("DELETE", "/pets/{id}")]["params"][0]["example"] == 1  # path-level parameter


def test_yaml_in_api_folder_and_swagger(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "openapi.yaml").write_text(
        "openapi: 3.1.0\ninfo: {title: Y}\npaths:\n  /a:\n    get:\n      responses:\n"
        "        200:\n          description: ok\n          content:\n            application/json:\n"
        "              example: {when: 2026-01-02}\n")
    out = apimock.load_api(tmp_path)
    r = out["endpoints"][0]["responses"][0]
    assert out["source"] == "api/openapi.yaml" and r["status"] == "200" and r["body"] == {"when": "2026-01-02"}
    json.dumps(out)  # plain JSON (dates, int keys converted)
    apimock._cache.clear()
    (tmp_path / "swagger.json").write_text(json.dumps({
        "swagger": "2.0", "paths": {"/u": {"post": {
            "parameters": [{"in": "body", "name": "b", "schema": {"type": "object", "properties": {"n": {"type": "string"}}}}],
            "responses": {"200": {"description": "ok", "examples": {"application/json": {"ok": True}}}}}}}}))
    out = apimock.load_api(tmp_path)
    e = out["endpoints"][0]
    assert out["kind"] == "swagger" and e["request"] == {"n": "string"} and e["params"] == []
    assert e["responses"][0]["body"] == {"ok": True}


def test_yaml_is_safe_load_only(tmp_path):
    (tmp_path / "openapi.yml").write_text("!!python/object/apply:os.system ['true']\n")
    with pytest.raises(Invalid):
        apimock.load_api(tmp_path)


def test_mocks_file_and_folder_merge_with_spec(tmp_path):
    (tmp_path / "openapi.json").write_text(json.dumps(SPEC))
    (tmp_path / "mocks.json").write_text(json.dumps({
        "GET /pets": [{"id": 1, "name": "Real"}],
        "GET /health": {"status": 200, "headers": {"X-A": "1"}, "body": {"ok": True}},
        "nonsense": 1, "FETCH /x": 1, "GET nopath": 1}))
    (tmp_path / "mocks").mkdir()
    (tmp_path / "mocks" / "users.json").write_text(json.dumps([
        {"method": "post", "path": "/users", "request": {"n": 1},
         "responses": {"201": {"body": {"id": 9}}, "409": "taken"}}]))
    (tmp_path / "mocks" / ".hidden.json").write_text(json.dumps({"GET /secret": 1}))
    (tmp_path / "mocks" / "notes.txt").write_text("x")
    eps = _eps(apimock.load_api(tmp_path))
    assert ("GET", "/secret") not in eps and ("GET", "/x") not in eps
    assert eps[("GET", "/pets")]["responses"][0]["body"] == [{"id": 1, "name": "Real"}]  # mock wins
    assert eps[("GET", "/pets")]["summary"] == "List pets"
    h = eps[("GET", "/health")]["responses"][0]
    assert h == {"status": "200", "desc": "", "headers": {"X-A": "1"}, "body": {"ok": True}}
    u = eps[("POST", "/users")]
    assert u["request"] == {"n": 1} and [(r["status"], r["body"]) for r in u["responses"]] == [
        ("201", {"id": 9}), ("409", "taken")]


def test_mocks_only(tmp_path):
    (tmp_path / "mocks").mkdir()
    (tmp_path / "mocks" / "a.json").write_text(json.dumps({"method": "GET", "path": "/a", "body": [1]}))
    out = apimock.load_api(tmp_path)
    assert out["kind"] == "mocks" and out["source"] == "mocks/a.json"
    assert out["endpoints"][0]["responses"][0]["body"] == [1]


def test_nothing_found(tmp_path):
    (tmp_path / "index.html").write_text("x")
    with pytest.raises(NotFound):
        apimock.load_api(tmp_path)


def test_caps(tmp_path, monkeypatch):
    (tmp_path / "openapi.json").write_bytes(b" " * (apimock.MAX_FILE + 1))
    with pytest.raises(Invalid, match="larger"):
        apimock.load_api(tmp_path)
    (tmp_path / "openapi.json").write_text("[" * 200 + "]" * 200)
    with pytest.raises(Invalid):
        apimock.load_api(tmp_path)
    (tmp_path / "openapi.json").write_text("{not json")
    with pytest.raises(Invalid, match="not valid JSON"):
        apimock.load_api(tmp_path)
    (tmp_path / "openapi.json").write_text(json.dumps({"openapi": "3", "paths": {
        f"/p{i}": {"get": {"responses": {}}} for i in range(apimock.MAX_ENDPOINTS + 20)}}))
    out = apimock.load_api(tmp_path)
    assert len(out["endpoints"]) == apimock.MAX_ENDPOINTS and out["truncated"]
    (tmp_path / "openapi.json").write_text(json.dumps({"openapi": "3", "paths": {"/big": {"get": {"responses": {
        "200": {"content": {"application/json": {"example": "x" * (apimock.MAX_EXAMPLE + 1)}}}}}}}}))
    body = apimock.load_api(tmp_path)["endpoints"][0]["responses"][0]["body"]
    assert isinstance(body, str) and "not shown" in body


def test_yaml_alias_bomb_refused(tmp_path):
    lines = ["a: &a [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 9):
        lines.append(f"{chr(97 + i)}: &{chr(97 + i)} [*{chr(96 + i)}, *{chr(96 + i)}, *{chr(96 + i)}, *{chr(96 + i)},"
                     f" *{chr(96 + i)}, *{chr(96 + i)}, *{chr(96 + i)}, *{chr(96 + i)}, *{chr(96 + i)}]")
    (tmp_path / "openapi.yaml").write_text("\n".join(lines) + "\n")
    with pytest.raises(Invalid, match="too deep or too large"):
        apimock.load_api(tmp_path)


def test_symlinks_and_escape_refused(tmp_path):
    proj, out = tmp_path / "proj", tmp_path / "out"
    proj.mkdir()
    out.mkdir()
    (out / "openapi.json").write_text(json.dumps(SPEC))
    (proj / "openapi.json").symlink_to(out / "openapi.json")
    (proj / "api").symlink_to(out, target_is_directory=True)
    (proj / "mocks").symlink_to(out, target_is_directory=True)
    (out / "x.json").write_text(json.dumps({"GET /x": 1}))
    with pytest.raises(NotFound):
        apimock.load_api(proj)
    fifo = tmp_path / "fifo"
    fifo.mkdir()
    import os
    os.mkfifo(fifo / "openapi.json")
    with pytest.raises(NotFound):  # not a regular file: skipped, no hang
        apimock.load_api(fifo)


def test_ref_loop_and_cache(tmp_path):
    spec = {"openapi": "3", "paths": {"/l": {"get": {"responses": {"200": {"$ref": "#/x"}}}}}, "x": {"$ref": "#/x"}}
    (tmp_path / "openapi.json").write_text(json.dumps(spec))
    out = apimock.load_api(tmp_path)
    assert out["endpoints"][0]["responses"] == [{"status": "200", "desc": "", "headers": {}, "body": None}]
    assert apimock.load_api(tmp_path) is out  # cached on (path, mtime, size)
