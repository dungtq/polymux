"""changelog: byte-exact round trip (real files + edge cases), add / edit / rm, absorb, branch takes."""

import pytest

from pm.core.errors import Invalid, NotFound
from pm.projects.tracker import changelog, drafts, schema

from .conftest import DOC_SAMPLES, display_today

TODAY = display_today()
BRANCH = drafts.View(branch="b", main=False, head="a" * 40)
PM = "# Changelog\n\nIntro.\n\n## 2026-10-02 (two)\n- b1\n- b2\n\n## 2026-10-01 (one)\n- a1\n"
DASH = "# Changelog\n\n## 2026-10-02 - Two\n\n- b1\n\n## 2026-10-01 - One\n\n- a1\n"


def _main(tk) -> str:
    return (tk.dev_root / "proj" / "CHANGELOG.md").read_text()


@pytest.mark.parametrize("path", [p for p in DOC_SAMPLES if "CHANGELOG" in p.name], ids=lambda p: p.name)
def test_round_trip_real_files(tk, path):
    data = path.read_bytes()
    res = changelog.import_text(tk, "proj", data.decode())
    assert res["entries"] > 5
    assert changelog.render_text(tk, "proj").encode() == data
    assert (tk.dev_root / "proj" / "CHANGELOG.md").read_bytes() == data


@pytest.mark.parametrize("text", [
    "", "\n", "# C\n", "# C", "## only heading", "## a\r\n- x\r\n\r\n## b\r\n- y\r\n", "## a\n- x",
    "pre\n\n## 2026-10-13 (em — dash ✓)\n### Fixed\n- z\n\n\n", "## same\n- 1\n## same\n- 2\n",
])
def test_round_trip_edge_cases(tk, text):
    changelog.import_text(tk, "proj", text)
    assert changelog.render_text(tk, "proj") == text


def test_numbers_oldest_first_and_import_refused_twice(tk):
    changelog.import_text(tk, "proj", PM)
    assert [(e["num"], e["title"]) for e in changelog.entries(tk, "proj")] == [(2, "2026-10-02 (two)"),
                                                                               (1, "2026-10-01 (one)")]
    with pytest.raises(Invalid):
        changelog.import_text(tk, "proj", PM)


def test_add_on_main_matches_the_file_style(tk):
    changelog.import_text(tk, "proj", PM)
    e = changelog.add(tk, "proj", "  new   thing ", "\n\n- n1\n  wrapped\n\n\n", actor="claude-x")
    assert e["num"] == 3 and e["state"] == "landed" and e["created_by"] == "claude-x"
    want = PM.replace("## 2026-10-02", f"## {TODAY} (new thing)\n- n1\n  wrapped\n\n## 2026-10-02")
    assert _main(tk) == want == changelog.render_text(tk, "proj")
    changelog.add(tk, "proj", "dated", "x", day="2026-09-30")
    assert _main(tk).startswith("# Changelog\n\nIntro.\n\n## 2026-09-30 (dated)\nx\n\n## ")
    with pytest.raises(Invalid):
        changelog.add(tk, "proj", "t", "  \n")
    with pytest.raises(Invalid):
        changelog.add(tk, "proj", "t", "b", day="13.10.2026")


def test_add_dash_style(tk):
    changelog.import_text(tk, "proj", DASH)
    changelog.add(tk, "proj", "Three", "- c1", day="2026-10-03")
    assert _main(tk) == DASH.replace("## 2026-10-02", "## 2026-10-03 - Three\n\n- c1\n\n## 2026-10-02")


def test_add_and_edit_keep_an_em_dash_heading(tk):
    """A file in the ``## 2026-10-07 — Title`` style: a new entry and a retitled one keep the em dash."""
    em = "# Changelog\n\nNewest first.\n\n## 2026-10-07 — Hub mode\n\n- a\n"
    changelog.import_text(tk, "proj", em)
    changelog.add(tk, "proj", "Next", "- b", day="2026-10-08")
    assert _main(tk).startswith("# Changelog\n\nNewest first.\n\n## 2026-10-08 — Next\n\n- b\n\n## 2026-10-07")
    changelog.edit(tk, "proj", 1, title="Hub")
    assert "## 2026-10-07 — Hub\n" in _main(tk)


def test_edit_and_rm(tk):
    changelog.import_text(tk, "proj", PM)
    changelog.edit(tk, "proj", 1, title="first", body="- A")
    assert "## 2026-10-01 (first)\n- A\n\n" in _main(tk)
    changelog.remove(tk, "proj", 2)
    assert "two" not in _main(tk)
    with pytest.raises(NotFound):
        changelog.remove(tk, "proj", 2)
    with pytest.raises(Invalid):
        changelog.edit(tk, "proj", 1)


def test_branch_add_is_a_draft_hidden_on_main(tk):
    changelog.import_text(tk, "proj", PM)
    e = changelog.add(tk, "proj", "on b", "- d", here=BRANCH)
    assert (e["state"], e["branch"]) == ("draft", "b")
    assert _main(tk) == PM == changelog.render_text(tk, "proj")
    assert changelog.render_text(tk, "proj", view=BRANCH).startswith("# Changelog\n\nIntro.\n\n## " + TODAY + " (on b)\n")
    # a landed entry is not changed from a branch; the branch's own draft is
    with pytest.raises(Invalid):
        changelog.edit(tk, "proj", 1, title="x", here=BRANCH)
    changelog.edit(tk, "proj", e["num"], title="on b 2", here=BRANCH)
    other = drafts.View(branch="c", main=False)
    with pytest.raises(Invalid):
        changelog.remove(tk, "proj", e["num"], here=other)


def test_audit_fails_closed(tk):
    changelog.import_text(tk, "proj", PM)

    def boom(action, detail):
        raise RuntimeError("audit down")
    with pytest.raises(RuntimeError):
        changelog.add(tk, "proj", "never", "x", audit=boom)
    assert len(changelog.entries(tk, "proj")) == 2 and _main(tk) == PM


def test_absorb_hand_edit(tk):
    changelog.import_text(tk, "proj", PM)
    d = changelog.add(tk, "proj", "draft", "- d", here=BRANCH)
    path = tk.dev_root / "proj" / "CHANGELOG.md"
    hand = PM.replace("- b2\n", "- b2 changed\n").replace("## 2026-10-01 (one)\n- a1\n", "")
    hand = hand.replace("Intro.\n\n", "Intro.\n\n## 2026-10-03 (by hand)\n- h\n\n")
    path.write_text(hand)
    changelog.add(tk, "proj", "after", "- y")  # absorbs first
    es = {e["title"]: e for e in changelog.entries(tk, "proj")}
    assert es["2026-10-03 (by hand)"]["state"] == "landed" and es["2026-10-03 (by hand)"]["num"] == d["num"] + 1
    assert es["2026-10-02 (two)"]["body"] == "- b1\n- b2 changed\n\n"
    assert "2026-10-01 (one)" not in es  # landed + missing: removed
    assert es[f"{TODAY} (draft)"]["state"] == "draft"  # drafts are never removed by an absorb
    assert _main(tk) == f"# Changelog\n\nIntro.\n\n## {TODAY} (after)\n- y\n\n" + hand.split("Intro.\n\n")[1]


def test_absorb_skips_conflict_markers(tk):
    changelog.import_text(tk, "proj", PM)
    (tk.dev_root / "proj" / "CHANGELOG.md").write_text("<<<<<<< HEAD\n## x\n=======\n## y\n>>>>>>> b\n")
    changelog.add(tk, "proj", "z", "- z")
    assert [e["num"] for e in changelog.entries(tk, "proj")] == [3, 2, 1]


def test_new_entries_by_occurrence():
    base = "## a\n- 1\n## b\n"
    theirs = "## n\n- new\n## a\n- 1\n## a\n- again\n## b\n"
    assert changelog.new_entries(base, theirs) == [("## n", "- new\n"), ("## a", "- again\n")]
    assert changelog.new_entries(theirs, theirs) == []


def test_take_main_lands_drafts_and_adds_unknown(tk):
    changelog.import_text(tk, "proj", PM)
    d = changelog.add(tk, "proj", "feat", "- f", here=BRANCH)
    theirs = PM.replace("Intro.\n\n", f"Intro.\n\n## 2026-10-05 (old rules)\n- o\n\n{d['heading']}\n- f\n\n")
    res = changelog.take(tk, "proj", PM, theirs, drafts.View())
    assert res == {"landed": 1, "added": 1}
    assert _main(tk) == theirs
    titles = [e["title"] for e in changelog.entries(tk, "proj")]
    assert titles[:2] == ["2026-10-05 (old rules)", f"{TODAY} (feat)"]
    assert all(e["state"] == "landed" for e in changelog.entries(tk, "proj"))
    assert changelog.take(tk, "proj", PM, theirs, drafts.View()) == {"landed": 0, "added": 0}


def test_take_on_a_branch_makes_drafts(tk):
    changelog.import_text(tk, "proj", PM)
    theirs = PM.replace("Intro.\n\n", "Intro.\n\n## 2026-10-05 (hand on b)\n- o\n\n")
    assert changelog.take(tk, "proj", PM, theirs, BRANCH) == {"landed": 0, "added": 1}
    e = changelog.entries(tk, "proj")[0]
    assert (e["state"], e["branch"]) == ("draft", "b")
    assert _main(tk) == PM
    assert changelog.render_text(tk, "proj", view=BRANCH) == theirs


def test_not_imported(tk):
    with pytest.raises(Invalid):
        changelog.add(tk, "proj", "x", "y")
    assert changelog.render_text(tk, "proj") is None
    assert schema.file_name("changelog") == "CHANGELOG.md"


def test_absorb_pairs_repeated_headings_by_occurrence(tk):
    text = "# C\n\n## same\n- first\n\n## same\n- second\n"
    changelog.import_text(tk, "proj", text)
    before = changelog.entries(tk, "proj")
    (tk.dev_root / "proj" / "CHANGELOG.md").write_text(text.replace("- second", "- second changed"))
    changelog.add(tk, "proj", "x", "- x")
    after = changelog.entries(tk, "proj")[1:]
    assert [e["num"] for e in after] == [e["num"] for e in before]
    assert [e["body"] for e in after] == ["- first\n\n", "- second changed\n"]
