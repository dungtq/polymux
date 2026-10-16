"""memory: tk_memory add/get/list/search/update/remove, name/type/description checks, global memories (type user
only), audit details without texts, fail closed. Runs on a tmp pm.db."""

import pytest

from pm.core.errors import Conflict, Invalid, NotFound
from pm.projects.tracker import memory


class Audit:
    def __init__(self, fail: bool = False):
        self.lines, self.fail = [], fail

    def __call__(self, action, detail):
        if self.fail:
            raise RuntimeError("audit down")
        self.lines.append((action, detail))


def _add(tk, name="build-rules", type="feedback", description="Run the checks first", **kw):
    kw.setdefault("actor", "claude-a")
    return memory.add(tk, "proj", name, type=type, description=description, **kw)


def test_add_get_list_and_shape(tk):
    au = Audit()
    m = _add(tk, body="\n\nUse uv.\n\n", title="Build rules", audit=au)
    assert (m.project, m.name, m.type, m.title, m.body_md, m.scope, m.file) == (
        "proj", "build-rules", "feedback", "Build rules", "Use uv.", "project", "build-rules.md")
    assert m.meta == {"metadata": {"node_type": "memory", "type": "feedback"}}  # Claude Code's layout
    assert list(m.to_dict()) == ["name", "project", "scope", "type", "title", "description", "body_md", "meta",
                                 "source", "created_by", "updated_by", "created_at", "updated_at"]
    action, detail = au.lines[-1]
    assert action == "tracker_memory_add" and detail["project"] == "proj" and detail["name"] == "build-rules"
    assert "Run the checks" not in str(detail) and detail["description_len"] == len("Run the checks first")
    assert memory.get(tk, "proj", "build-rules").id == m.id
    _add(tk, "b", type="project")
    assert [x.name for x in memory.list_memories(tk, "proj")] == ["build-rules", "b"]
    assert [x.name for x in memory.list_memories(tk, "proj", type="project")] == ["b"]
    with pytest.raises(Conflict):
        _add(tk)
    with pytest.raises(NotFound):
        memory.get(tk, "proj", "nope")


@pytest.mark.parametrize("name", ["", "Upper", "-x", "a b", "a/b", "x" * 65, "../x", "ä"])
def test_bad_names(tk, name):
    with pytest.raises(Invalid):
        _add(tk, name)


def test_type_and_description_checks(tk):
    with pytest.raises(Invalid):
        _add(tk, type="note")
    with pytest.raises(Invalid):
        _add(tk, description="  ")
    with pytest.raises(Invalid):
        _add(tk, description="x" * (memory.MAX_DESC + 1))
    assert _add(tk, description="two\n  lines").description == "two lines"
    assert _add(tk, "kept", description=" a\nb ", collapse=False).description == "a\nb"  # imports keep it


def test_global_is_user_only_and_listed_after_project(tk):
    with pytest.raises(Invalid):
        _add(tk, "g", type="feedback", is_global=True)
    g = _add(tk, "who", type="user", description="dt, single user host", is_global=True)
    assert (g.project, g.scope) == ("", "global")
    p = _add(tk, "who", type="user", description="project copy")
    assert memory.get(tk, "proj", "who").id == p.id  # the project's wins
    assert memory.get(tk, "proj", "who", is_global=True).id == g.id
    assert memory.get(tk, "other", "who").id == g.id  # global seen from any project
    assert [(m.name, m.scope) for m in memory.list_memories(tk, "proj", include_global=True)] == [
        ("who", "project"), ("who", "global")]
    with pytest.raises(Invalid):
        memory.update(tk, "proj", "who", type="feedback", is_global=True, actor="a")
    assert memory.projects_with_memories(tk) == ["proj"]


def test_update_replaces_given_fields_only(tk):
    m = _add(tk, body="old body", title="T", hook="index text")
    assert m.hook == "index text"
    u = memory.update(tk, "proj", "build-rules", body="new body", actor="codex-b")
    assert (u.body_md, u.description, u.title, u.type, u.updated_by, u.created_by, u.hook) == (
        "new body", m.description, "T", "feedback", "codex-b", "claude-a", "index text")
    u = memory.update(tk, "proj", "build-rules", type="reference", description="new", actor="codex-b")
    assert u.type == "reference" and u.meta["metadata"]["type"] == "reference"
    assert u.hook == ""  # a new description replaces the old index text
    with pytest.raises(Invalid):
        memory.update(tk, "proj", "build-rules", actor="x")
    with pytest.raises(NotFound):
        memory.update(tk, "proj", "nope", body="x", actor="x")


def test_with_type_keeps_the_files_layout():
    assert memory.with_type({"type": "user", "x": 1}, "project") == {"type": "project", "x": 1}
    assert memory.with_type({"metadata": {"a": 1, "type": "user", "b": 2}}, "project") == {
        "metadata": {"a": 1, "type": "project", "b": 2}}
    assert memory.with_type({"metadata": {"a": 1}}, "user") == {"metadata": {"a": 1, "type": "user"}}
    assert memory.with_type(None, "user") == {"metadata": {"node_type": "memory", "type": "user"}}


def test_remove(tk):
    _add(tk)
    au = Audit()
    assert memory.remove(tk, "proj", "build-rules", audit=au).name == "build-rules"
    assert au.lines[-1][0] == "tracker_memory_rm"
    with pytest.raises(NotFound):
        memory.remove(tk, "proj", "build-rules")


def test_search_bounded_and_escaped(tk):
    _add(tk, "a", description="uses 100% of the CPU")
    _add(tk, "b", description="plain", body="the body has needle_x in it")
    for i in range(memory.SEARCH_LIMIT + 5):
        _add(tk, f"n{i}", description="many needle")
    assert [m.name for m in memory.search(tk, "proj", "100%")] == ["a"]
    assert [m.name for m in memory.search(tk, "proj", "needle_x")] == ["b"]  # "_" is not a wildcard
    assert len(memory.search(tk, "proj", "NEEDLE")) == memory.SEARCH_LIMIT
    assert len(memory.search(tk, "proj", "needle", limit=3)) == 3
    with pytest.raises(Invalid):
        memory.search(tk, "proj", " ")
    with pytest.raises(Invalid):
        memory.search(tk, "proj", "x" * (memory.MAX_QUERY + 1))


def test_audit_failure_rolls_back(tk):
    with pytest.raises(RuntimeError):
        _add(tk, audit=Audit(fail=True))
    assert memory.find(tk, "proj", "build-rules") is None
    _add(tk)
    with pytest.raises(RuntimeError):
        memory.update(tk, "proj", "build-rules", body="x", actor="a", audit=Audit(fail=True))
    assert memory.get(tk, "proj", "build-rules").body_md == ""
    with pytest.raises(RuntimeError):
        memory.remove(tk, "proj", "build-rules", audit=Audit(fail=True))
    assert memory.find(tk, "proj", "build-rules") is not None
