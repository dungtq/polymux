"""store: add/done/edit/delete, placement like backlog.add_item, numbers, audit (fail closed), concurrency."""

import threading

import pytest

from pm.core.errors import Invalid, NotFound
from pm.projects import backlog
from pm.projects.tracker import mdgen, mdimport, schema, store

from .conftest import display_today

TODAY = display_today()


def _imp(tk, text, file="backlog"):
    mdimport.import_text(tk, "proj", file, text)


def _text(tk, file="backlog"):
    return mdgen.render_text(tk, "proj", file)


@pytest.mark.parametrize("text", [
    "",
    "# pm backlog\n\nintro\n",
    "# b\n\n## Inbox\n",
    "# b\n\n## Inbox\n\n- [ ] one\n",
    "# b\n\n## Inbox\n- [ ] one\n## Later\n- [ ] two\n",
    "# b\n\n## Inbox\n\n- [ ] one\n\n\n## Later\n\n- [ ] two",
    "## Inbox\n\n- [ ] a\nsome text after\n\n## Next\n",
    "no newline at end",
])
def test_add_matches_backlog_add_item(tk, text):
    _imp(tk, text)
    it = store.add_item(tk, "proj", "backlog", "  new   item ")
    assert it.title == f"new item ({TODAY})" and it.status == "open" and it.created_by == "user"
    assert _text(tk) == backlog.add_item(text, "new item", TODAY)


def test_add_many_keeps_order_and_numbers(tk):
    _imp(tk, "## Inbox\n\n- [ ] a\n\n## Done\n- [x] z\n")
    nums = [store.add_item(tk, "proj", "backlog", f"n{i}").num for i in range(5)]
    assert nums == [3, 4, 5, 6, 7]
    text = _text(tk)
    expected = "## Inbox\n\n- [ ] a\n" + "".join(f"- [ ] n{i} ({TODAY})\n" for i in range(5)) + "\n## Done\n- [x] z\n"
    assert text == expected


def test_positions_renumber_when_tight(tk):
    _imp(tk, "## Inbox\n- [ ] a\n## Next\n")
    con = schema.connect(tk)
    con.execute("UPDATE tk_line SET pos = 2.0000000001 WHERE text = '## Next'")
    con.close()
    store.add_item(tk, "proj", "backlog", "b")
    assert _text(tk) == f"## Inbox\n- [ ] a\n- [ ] b ({TODAY})\n\n## Next\n"


def test_todo_add_section_existing_missing_and_default(tk):
    _imp(tk, "# T\n\n## P1 one\n- [x] a\n\n## P2 two\n- [ ] b\n", "todo")
    store.add_item(tk, "proj", "todo", "in p1", section="p1 ONE")
    store.add_item(tk, "proj", "todo", "last section")
    store.add_item(tk, "proj", "todo", "fresh", section="P9 tracker")
    assert _text(tk, "todo") == (
        f"# T\n\n## P1 one\n- [x] a\n- [ ] in p1 ({TODAY})\n\n## P2 two\n- [ ] b\n- [ ] last section ({TODAY})\n"
        f"\n## P9 tracker\n\n- [ ] fresh ({TODAY})\n")


def test_todo_add_without_headings_goes_after_last_line(tk):
    _imp(tk, "- [ ] a\n\n", "todo")
    store.add_item(tk, "proj", "todo", "b")
    assert _text(tk, "todo") == f"- [ ] a\n- [ ] b ({TODAY})\n\n"


def test_backlog_section_option(tk):
    _imp(tk, "## Inbox\n- [ ] a\n## Ideas\n- [ ] i\n")
    store.add_item(tk, "proj", "backlog", "idea", section="ideas")
    assert _text(tk).endswith(f"## Ideas\n- [ ] i\n- [ ] idea ({TODAY})\n")


def test_title_validation(tk):
    _imp(tk, "")
    with pytest.raises(Invalid):
        store.add_item(tk, "proj", "backlog", "  \n ")
    with pytest.raises(Invalid):
        store.add_item(tk, "proj", "backlog", "x" * (store.MAX_ITEM + 1))
    assert store.add_item(tk, "proj", "backlog", "a\nb").title.startswith("a b (")
    with pytest.raises(Invalid):
        store.add_item(tk, "proj", "backlog", "ok", labels=["bad label"])


def test_not_imported_is_refused_and_file_untouched(tk):
    f = tk.dev_root / "proj" / "BACKLOG.md"
    f.write_text("- [ ] mine\n")
    with pytest.raises(Invalid):
        store.add_item(tk, "proj", "backlog", "x")
    assert f.read_text() == "- [ ] mine\n"
    with pytest.raises(Invalid):
        store.add_item(tk, "proj", "nope", "x")


def test_done_reopen_edit_delete(tk):
    _imp(tk, "- [ ] a\n- [X] b\n")
    it = store.set_done(tk, "proj", "backlog", 1, True, actor="claude-x", commit="abc1234")
    assert it.done and it.closed_by == "claude-x" and it.commit == "abc1234" and it.closed_at > 0
    assert _text(tk) == "- [x] a\n- [X] b\n"
    it = store.set_done(tk, "proj", "backlog", 2, False)
    assert not it.done and it.closed_by == "" and _text(tk) == "- [x] a\n- [ ] b\n"
    with pytest.raises(Invalid):
        store.set_done(tk, "proj", "backlog", 1, True, commit="not hex!")
    it = store.edit_item(tk, "proj", "backlog", 1, title="  a2  ", body="line 1\r\nline 2\n")
    assert it.title == "a2" and it.body_md == "line 1\nline 2" and it.has_more
    assert _text(tk) == "- [x] a2 [#1]\n- [ ] b\n"
    store.edit_item(tk, "proj", "backlog", 1, body="")
    assert _text(tk) == "- [x] a2\n- [ ] b\n"
    store.delete_item(tk, "proj", "backlog", 1)
    assert _text(tk) == "- [ ] b\n"
    with pytest.raises(NotFound):
        store.get_item(tk, "proj", "backlog", 1)


def test_numbers_are_not_reused_after_delete(tk):
    _imp(tk, "- [ ] a\n- [ ] b\n")
    store.delete_item(tk, "proj", "backlog", 2)
    assert store.add_item(tk, "proj", "backlog", "c").num == 3


def test_mutation_rewrites_main_tree_file(tk):
    f = tk.dev_root / "proj" / "BACKLOG.md"
    f.write_text("## Inbox\n")
    mdimport.import_project(tk, "proj", tk.dev_root / "proj")
    store.add_item(tk, "proj", "backlog", "x")
    assert f.read_text() == f"## Inbox\n\n- [ ] x ({TODAY})\n"


def test_audit_runs_before_commit_and_fails_closed(tk):
    _imp(tk, "- [ ] a\n")
    seen = []
    store.add_item(tk, "proj", "backlog", "secret words", audit=lambda a, d: seen.append((a, d)))
    action, detail = seen[0]
    assert action == "tracker_add" and detail["project"] == "proj" and detail["num"] == 2
    assert "title_sha256" in detail and "secret" not in repr(detail)

    def boom(a, d):
        raise RuntimeError("audit down")
    before = _text(tk)
    with pytest.raises(RuntimeError):
        store.add_item(tk, "proj", "backlog", "never", audit=boom)
    with pytest.raises(RuntimeError):
        store.set_done(tk, "proj", "backlog", 1, True, audit=boom)
    assert _text(tk) == before and [i.num for i in store.list_items(tk, "proj", "backlog")] == [1, 2]
    assert not store.get_item(tk, "proj", "backlog", 1).done


def test_list_items_status_filter(tk):
    _imp(tk, "- [ ] a\n- [x] b\n")
    assert [i.title for i in store.list_items(tk, "proj", "backlog", "done")] == ["b"]
    with pytest.raises(Invalid):
        store.list_items(tk, "proj", "backlog", "bogus")
    assert store.list_items(tk, "other", "backlog") == []


def test_item_to_dict_is_json_ready(tk):
    _imp(tk, "- [ ] a\n")
    d = store.get_item(tk, "proj", "backlog", 1).to_dict()
    assert d["done"] is False and d["has_more"] is False and d["labels"] == [] and d["num"] == 1


def test_concurrent_adds_get_unique_numbers(tk):
    _imp(tk, "## Inbox\n")
    errors = []

    def worker(k):
        try:
            for i in range(10):
                store.add_item(tk, "proj", "backlog", f"w{k}-{i}")
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    items = store.list_items(tk, "proj", "backlog")
    assert sorted(i.num for i in items) == list(range(1, 21))
    assert len({i.pos for i in items}) == 20
    text = _text(tk)
    assert sum(1 for ln in text.splitlines() if ln.startswith("- [ ] w")) == 20


def test_two_connections_serialise_with_begin_immediate(tk):
    _imp(tk, "")
    a, b = schema.connect(tk), schema.connect(tk)
    try:
        a.execute("BEGIN IMMEDIATE")
        b.execute("PRAGMA busy_timeout = 50")
        import sqlite3

        with pytest.raises(sqlite3.OperationalError):
            b.execute("BEGIN IMMEDIATE")
        store.insert_item(a, "proj", "backlog", "first")
        a.execute("COMMIT")
        with schema.tx(b):
            store.insert_item(b, "proj", "backlog", "second")
    finally:
        a.close()
        b.close()
    assert [i.num for i in store.list_items(tk, "proj", "backlog")] == [1, 2]


def test_toggle_flips_in_one_call(tk):
    _imp(tk, "## Inbox\n\n- [ ] one\n")
    seen = []
    it = store.toggle(tk, "proj", "backlog", 1, actor="u1", audit=lambda a, d: seen.append((a, d)))
    assert it.done and it.closed_by == "u1" and "- [x] one" in _text(tk)
    assert not store.toggle(tk, "proj", "backlog", 1).done and "- [ ] one" in _text(tk)
    assert seen == [("tracker_toggle", {"project": "proj", "file": "backlog", "num": 1, "done": 1})]
    with pytest.raises(NotFound):
        store.toggle(tk, "proj", "backlog", 9)


def test_toggle_audit_failure_rolls_back(tk):
    _imp(tk, "## Inbox\n\n- [ ] one\n")

    def boom(a, d):
        raise RuntimeError("audit down")
    with pytest.raises(RuntimeError):
        store.toggle(tk, "proj", "backlog", 1, audit=boom)
    assert not store.get_item(tk, "proj", "backlog", 1).done and "- [ ] one" in _text(tk)


@pytest.mark.parametrize("title,short", [
    ("fix it (2026-10-13)", "fix it"),
    ("fix it  (2026-10-13)", "fix it"),
    ("fix it (2026-10-13) later", "fix it (2026-10-13) later"),
    ("fix it (13.10.2026)", "fix it (13.10.2026)"),
    ("(2026-10-13)", "(2026-10-13)"),
    ("plain", "plain"),
])
def test_short_title(title, short):
    assert store.short_title(title) == short


def test_item_title_short_keeps_stored_title(tk):
    _imp(tk, "## Inbox\n")
    it = store.add_item(tk, "proj", "backlog", "new one")
    assert it.title == f"new one ({TODAY})" and it.title_short == "new one"
    assert f"- [ ] new one ({TODAY})" in _text(tk)


# ------------------------------------------------------------------ move between projects
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\0" * 40


def _two(tk):
    """``proj`` and ``other`` (both under the tmp dev root) with their BACKLOG.md in the tracker."""
    (tk.dev_root / "other").mkdir()
    (tk.dev_root / "proj" / "BACKLOG.md").write_text("## Inbox\n\n- [ ] a\n- [ ] b\n")
    (tk.dev_root / "other" / "BACKLOG.md").write_text("## Inbox\n\n- [ ] x\n")
    for name in ("proj", "other"):
        mdimport.import_project(tk, name, tk.dev_root / name, files=("backlog",))


def test_move_item_new_number_notes_images_history_and_both_files(tk, tmp_path):
    from pm.projects.tracker import issues
    _two(tk)
    img = tmp_path / "s.png"
    img.write_bytes(PNG_BYTES)
    issues.attach_image(tk, "proj", 2, img, any_path=True)
    issues.add_note(tk, "proj", 2, "first note", actor="claude-a")
    store.edit_item(tk, "proj", "backlog", 2, body="the body")
    before = store.set_done(tk, "proj", "backlog", 2, True, actor="claude-b", commit="abc1234")
    calls = []
    it = store.move_item(tk, "proj", "other", 2, actor="claude-c", audit=lambda a, d: calls.append((a, d)))
    assert it.project == "other" and it.num == 2 and it.title == "b" and it.body_md == "the body"
    assert it.done and it.closed_by == "claude-b" and it.commit == "abc1234" and it.created_at == before.created_at
    assert it.n_img == 1 and it.n_notes == 2
    d = issues.detail(tk, "other", 2)
    assert [n["body_md"] for n in d["notes"]] == ["first note", "Moved from proj #2."]
    assert d["notes"][1]["author"] == "claude-c" and d["images"][0]["path"].endswith(".png")
    assert calls == [("tracker_move", {"project": "proj", "file": "backlog", "num": 2, "to": "other",
                                       "new_num": 2, "by": "claude-c"})]
    with pytest.raises(NotFound):
        store.get_item(tk, "proj", "backlog", 2)
    assert (tk.dev_root / "proj" / "BACKLOG.md").read_text() == "## Inbox\n\n- [ ] a\n"
    assert (tk.dev_root / "other" / "BACKLOG.md").read_text() == "## Inbox\n\n- [ ] x\n- [x] b [#2]\n"
    assert store.add_item(tk, "proj", "backlog", "c").num == 3  # the moved number is never reused
    con = schema.connect(tk)
    try:
        assert con.execute("SELECT COUNT(*) FROM tk_note n LEFT JOIN tk_item i ON i.id = n.item_id"
                           " WHERE i.id IS NULL").fetchone()[0] == 0  # no orphaned notes / images
        assert con.execute("SELECT COUNT(*) FROM tk_attach a LEFT JOIN tk_item i ON i.id = a.item_id"
                           " WHERE i.id IS NULL").fetchone()[0] == 0
    finally:
        con.close()


def test_move_item_refusals_change_nothing(tk):
    _two(tk)
    (tk.dev_root / "plain").mkdir()
    snap = lambda: ((tk.dev_root / "proj" / "BACKLOG.md").read_text(),  # noqa: E731
                    (tk.dev_root / "other" / "BACKLOG.md").read_text())
    start = snap()
    with pytest.raises(Invalid, match="already in that project"):
        store.move_item(tk, "proj", "proj", 1)
    with pytest.raises(Invalid, match="not in the tracker"):
        store.move_item(tk, "proj", "plain", 1)
    with pytest.raises(NotFound):
        store.move_item(tk, "proj", "other", 99)

    def boom(action, detail):
        raise RuntimeError("audit down")
    with pytest.raises(RuntimeError):
        store.move_item(tk, "proj", "other", 1, audit=boom)  # fail closed: rolled back
    assert snap() == start and store.get_item(tk, "proj", "backlog", 1).title == "a"
    assert [i.num for i in store.list_items(tk, "other", "backlog")] == [1]


def test_move_item_takes_a_hand_edit_of_the_target_first(tk):
    _two(tk)
    (tk.dev_root / "other" / "BACKLOG.md").write_text("## Inbox\n\n- [ ] x\n- [ ] typed by hand\n")
    it = store.move_item(tk, "proj", "other", 1)
    assert it.num == 3
    assert (tk.dev_root / "other" / "BACKLOG.md").read_text() == (
        "## Inbox\n\n- [ ] x\n- [ ] typed by hand\n- [ ] a [#3]\n")  # the "Moved from" note adds the ref
