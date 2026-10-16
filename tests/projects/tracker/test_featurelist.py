"""featurelist / featurestore: byte-exact round trip (real files), Summary line, set / rm / list, revisions,
continuation lines, absorb, branch drafts and landing."""

import pytest

from pm.core.errors import Invalid, NotFound
from pm.projects.tracker import drafts, featurelist, featurestore, schema

from .conftest import DOC_SAMPLES, FIX

BRANCH = drafts.View(branch="b", main=False, head="b" * 40)
F = ("# F\n\nLast audited: 2026-10-01\nSummary: 3 features in 2 areas.\n\n## Chat\n\n"
     "- **Send** — send a message.\n- **Copy** — copy a reply.\n  continued here\n\n## Git\n\n- **Push** — push.\n")


def _main(tk) -> str:
    return (tk.dev_root / "proj" / "FEATURES.md").read_text()


def _revs(tk):
    con = schema.connect(tk)
    try:
        return [dict(r) for r in con.execute("SELECT * FROM tk_feature_rev ORDER BY id")]
    finally:
        con.close()


@pytest.mark.parametrize("path", [p for p in DOC_SAMPLES if "FEATURES" in p.name], ids=lambda p: p.name)
def test_round_trip_real_files(tk, path):
    data = path.read_bytes()
    res = featurelist.import_text(tk, "proj", data.decode())
    assert res["lines"] > 3
    assert featurelist.render_text(tk, "proj").encode() == data
    assert (tk.dev_root / "proj" / "FEATURES.md").read_bytes() == data


def test_pm_file_entries_and_real_summary(tk):
    """A full FEATURES.md sample: every bullet is an entry, and its Summary line is right, so a change keeps it right."""
    text = (FIX / "alpha-FEATURES.md").read_text()
    featurelist.import_text(tk, "proj", text)
    fs = featurelist.features(tk, "proj")
    assert len(fs) == text.count("\n- **") and len({f["area"] for f in fs}) == text.count("\n## ")
    featurelist.set_feature(tk, "proj", "Tools", "New tool", "does things.")
    n, m = len(fs) + 1, len({f["area"] for f in fs})
    assert f"\nSummary: {n} features in {m} areas.\n" in _main(tk)
    assert _main(tk).endswith("- **New tool** — does things.\n")


def test_summary_kept_until_a_change(tk):
    wrong = F.replace("3 features in 2", "9 features in 9")
    featurelist.import_text(tk, "proj", wrong)
    assert _main(tk) == wrong  # import keeps it byte-exact
    featurelist.rm_feature(tk, "proj", "Git", "Push")
    assert "Summary: 2 features in 1 areas.\n" in _main(tk) and "Push" not in _main(tk)


@pytest.mark.parametrize("text", ["", "\n", "# x", "## A\r\n- **N** — t\r\n", "## A\n- **N** — t — more ✓",
                                  "- **pre** — not an entry\n## A\n  indented prose\n- plain bullet\n"])
def test_round_trip_edge_cases(tk, text):
    featurelist.import_text(tk, "proj", text)
    assert featurelist.render_text(tk, "proj") == text


def test_parse_grammar():
    p = featurestore.parse(F)
    assert [(e.area, e.name, e.text, e.extra) for e in p.entries] == [
        ("Chat", "Send", "send a message.", ""), ("Chat", "Copy", "copy a reply.", "  continued here\n"),
        ("Git", "Push", "push.", "")]


def test_set_update_keeps_old_text_and_drops_continuation(tk):
    featurelist.import_text(tk, "proj", F)
    f = featurelist.set_feature(tk, "proj", "Chat", "Copy", "copy any reply.", actor="claude-x")
    assert (f["text"], f["extra"], f["updated_by"]) == ("copy any reply.", "", "claude-x")
    assert "- **Copy** — copy any reply.\n\n## Git" in _main(tk) and "continued" not in _main(tk)
    assert [(r["state"], r["text"], r["extra"]) for r in _revs(tk)] == [("old", "copy a reply.", "  continued here\n")]
    assert "Summary: 3 features in 2 areas." in _main(tk)


def test_set_new_entry_and_new_area(tk):
    featurelist.import_text(tk, "proj", F)
    featurelist.set_feature(tk, "proj", "Chat", "Edit", "edit a message.")
    assert "  continued here\n- **Edit** — edit a message.\n\n## Git" in _main(tk)
    featurelist.set_feature(tk, "proj", "Servers", "Ping", "ping a host.")
    featurelist.set_feature(tk, "proj", "Servers", "Pong", "answer.")
    assert _main(tk).endswith("- **Push** — push.\n\n## Servers\n\n- **Ping** — ping a host.\n- **Pong** — answer.\n")
    assert "Summary: 6 features in 3 areas." in _main(tk)
    assert [f["name"] for f in featurelist.features(tk, "proj", area="servers")] == ["Ping", "Pong"]
    # the next render is the same bytes
    assert featurelist.render_text(tk, "proj") == _main(tk)


def test_rm_show_and_errors(tk):
    featurelist.import_text(tk, "proj", F)
    assert featurelist.features(tk, "proj", area="Git", name="Push")[0]["text"] == "push."
    featurelist.rm_feature(tk, "proj", "Git", "Push")
    with pytest.raises(NotFound):
        featurelist.rm_feature(tk, "proj", "Git", "Push")
    with pytest.raises(NotFound):
        featurelist.features(tk, "proj", area="Git", name="Push")
    featurelist.set_feature(tk, "proj", "Git", "Push", "push again.")  # back, at the end of its area
    assert _main(tk).endswith("## Git\n\n- **Push** — push again.\n")
    for bad in (("", "x", "t"), ("A", "a**b", "t"), ("A", "N", "two\nlines"), ("A", "N", "  ")):
        with pytest.raises(Invalid):
            featurelist.set_feature(tk, "proj", *bad)


def test_audited(tk):
    featurelist.import_text(tk, "proj", F)
    assert featurelist.set_audited(tk, "proj", "2026-10-13") == "Last audited: 2026-10-13"
    assert "Last audited: 2026-10-13\nSummary: 3 features in 2 areas.\n" in _main(tk)


def test_absorb_hand_edit(tk):
    featurelist.import_text(tk, "proj", F)
    (tk.dev_root / "proj" / "FEATURES.md").write_text(
        F.replace("send a message.", "send a message fast.").replace("- **Push** — push.\n", "- **Pull** — pull.\n"))
    featurelist.set_feature(tk, "proj", "Git", "Fetch", "fetch.")
    names = [(f["name"], f["text"]) for f in featurelist.features(tk, "proj")]
    assert names == [("Send", "send a message fast."), ("Copy", "copy a reply."), ("Pull", "pull."),
                     ("Fetch", "fetch.")]
    assert [r["text"] for r in _revs(tk)] == ["send a message."]


def test_branch_drafts(tk):
    featurelist.import_text(tk, "proj", F)
    featurelist.set_feature(tk, "proj", "Chat", "Send", "send faster.", here=BRANCH)
    featurelist.set_feature(tk, "proj", "Chat", "New", "brand new.", here=BRANCH)
    featurelist.rm_feature(tk, "proj", "Git", "Push", here=BRANCH)
    assert _main(tk) == F == featurelist.render_text(tk, "proj")  # main: no drafts
    b = featurelist.render_text(tk, "proj", view=BRANCH)
    assert "- **Send** — send faster.\n" in b and "- **New** — brand new.\n" in b and "Push" not in b
    assert "Summary: 3 features in 1 areas." in b
    assert [f["name"] for f in featurelist.features(tk, "proj", here=BRANCH)] == ["Send", "Copy", "New"]
    # one draft per (entry, branch): a second set replaces it
    featurelist.set_feature(tk, "proj", "Chat", "Send", "send fastest.", here=BRANCH)
    assert sum(r["state"] == "draft" for r in _revs(tk)) == 3


def test_take_main_lands_matching_drafts(tk):
    featurelist.import_text(tk, "proj", F)
    featurelist.set_feature(tk, "proj", "Chat", "Send", "send faster.", here=BRANCH)
    theirs = F.replace("send a message.", "send faster.").replace("- **Push** — push.\n", "- **Push** — push.\n"
                                                                    "- **Tag** — tag.\n")
    assert featurelist.take(tk, "proj", F, theirs, drafts.View()) == {"landed": 1, "taken": 1}
    assert _main(tk) == theirs.replace("3 features", "4 features")
    assert {r["state"] for r in _revs(tk)} == {"landed", "old"}


def test_take_main_wins_when_both_changed(tk):
    featurelist.import_text(tk, "proj", F)
    featurelist.set_feature(tk, "proj", "Git", "Push", "push on main.")
    theirs = F.replace("push.", "push on branch.")
    assert featurelist.take(tk, "proj", F, theirs, drafts.View()) == {"landed": 0, "taken": 0}
    assert "push on main." in _main(tk)


def test_take_on_a_branch_makes_drafts(tk):
    featurelist.import_text(tk, "proj", F)
    theirs = F.replace("copy a reply.", "copy a reply, by hand.")
    assert featurelist.take(tk, "proj", F, theirs, BRANCH) == {"landed": 0, "taken": 1}
    assert _main(tk) == F
    assert featurelist.render_text(tk, "proj", view=BRANCH) == theirs  # the continuation line goes along
    assert featurelist.take(tk, "proj", F, theirs, BRANCH)["taken"] == 0  # the branch view has it already
