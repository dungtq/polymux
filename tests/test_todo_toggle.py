import re

import pytest
from fastapi.testclient import TestClient
from helpers import ORIGIN, audit_text, make_client, make_settings

from pm.core.models import Tier
from pm.projects import projects
from pm.projects import todo as rt

ORIGIN = "https://pm.example.test"
SEED = ("# T\n\n## Phase 1\n\n- [x] done one\n- [ ] open `code` and **bold**\n- [ ] second <b>x</b>\n"
        "\n## Phase 2\n\n- [ ] third\n")


@pytest.fixture
def env(fake_tmux, tmp_path):
    dev = tmp_path / "dev"
    (dev / "pm").mkdir(parents=True)
    (dev / "other").mkdir()
    f = dev / "pm" / "TODO.md"
    f.write_text(SEED)
    s = make_settings(fake_tmux, tmp_path, state="st", tier_ceiling=Tier.SAFE, disable_poll=True)
    c = make_client(s, static=True, base_url=ORIGIN, logged_in=True)
    return c, f, s


class _Tok:
    """Page tokens are gone (item ops apply to the current file); old call sites ignore this."""

    def group(self, _n):
        return "0" * 16


def fields(c, name="pm"):
    html = c.get(f"/p/{name}").text
    return html, re.search(r'name="csrf" value="([^"]+)"', html).group(1), _Tok()


def post(c, csrf, name="pm", origin=ORIGIN, **data):
    h = {"Origin": origin} if origin else {}
    return c.post(f"/p/{name}/todo/toggle", data={"csrf": csrf, **data}, headers=h,
                  follow_redirects=False)


def test_render_groups_and_markdown(env):
    c, f, s = env
    html, _, tok = fields(c)
    assert tok and "Phase 1" in html and "Phase 2" in html
    assert "<code>code</code>" in html and "<strong>bold</strong>" in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "1 done" in html and "3 open" in html and "Show all" not in html


def test_show_all_over_eight(env):
    c, f, s = env
    f.write_text("## A\n" + "".join(f"- [ ] i{n}\n" for n in range(11)))
    html, _, _ = fields(c)
    assert "Show all (11)" in html and "<details" in html


def test_empty_heading_skipped(env):
    c, f, s = env
    f.write_text("## Empty\n- [x] a\n## Has\n- [ ] b\n")
    html, _, _ = fields(c)
    assert "Has" in html and ">Empty<" not in html


def test_toggle_ok(env):
    c, f, s = env
    _, csrf, tok = fields(c)
    r = post(c, csrf, line="5", text="open `code` and **bold**", token=tok.group(1))
    assert r.status_code == 303 and r.headers["location"] == "/p/pm#todo"
    assert "- [x] open `code` and **bold**" in f.read_text()
    assert f.read_text().count("[x]") == 2
    log = s.audit_file.read_text()
    assert "todo_toggle" in log and "open `code`" not in log


def test_toggle_back_and_star_bullet(env):
    c, f, s = env
    f.write_text("* [x] a\n")
    _, csrf, _ = fields(c)
    tok = rt.token_of(f.read_bytes())
    assert post(c, csrf, line="0", text="a", token=tok).status_code == 303
    assert f.read_text() == "* [ ] a\n"


def test_text_mismatch_refused(env):
    c, f, s = env
    _, csrf, tok = fields(c)
    r = post(c, csrf, line="5", text="other", token=tok.group(1))
    assert r.status_code == 409 and f.read_text() == SEED


def test_unrelated_edit_does_not_block_toggle(env):
    c, f, s = env
    _, csrf, tok = fields(c)
    f.write_text("# agent line\n\n" + SEED + "- [ ] new\n")  # lines shifted by an agent
    r = post(c, csrf, line="5", text="open `code` and **bold**")
    assert r.status_code == 303 and "- [x] open `code` and **bold**" in f.read_text()
    assert "new" in f.read_text() and "# agent line" in f.read_text()
    r = post(c, csrf, line="5", text="open `code` and **bold**x")
    assert r.status_code == 409 and "changed or removed" in r.text


def test_external_write_between_read_and_replace_retries_once(env):
    c, f, s = env
    n = {"calls": 0}

    def fn(t):
        n["calls"] += 1
        if n["calls"] == 1:
            f.write_text(SEED + "- [ ] raced\n")  # external writer after our read
        return rt.toggle_line(t, 5, "open `code` and **bold**")

    from pm.projects import backlog as rb
    rb.mutate(s, f, fn, name="TODO.md")
    assert n["calls"] == 2 and "raced" in f.read_text() and f.read_text().count("[x]") == 2
    n["calls"] = -10  # always raced: gives up with 409, file keeps the external content
    import pytest
    def always_raced(t):
        f.write_text(f.read_text() + "x")
        return t + "y"

    with pytest.raises(rb.Bad) as e:
        rb.mutate(s, f, always_raced, name="TODO.md")
    assert e.value.status == 409


def test_not_an_item_and_bad_form(env):
    c, f, s = env
    _, csrf, tok = fields(c)
    assert post(c, csrf, line="0", text="T", token=tok.group(1)).status_code == 409  # not an item
    assert post(c, csrf, line="x", text="a", token=tok.group(1)).status_code == 400
    assert post(c, csrf, line="5").status_code == 400  # no text
    assert f.read_text() == SEED


def test_guards(env):
    c, f, s = env
    _, csrf, tok = fields(c)
    d = dict(line="5", text="open `code` and **bold**", token=tok.group(1))
    assert post(c, "bad", **d).status_code in (400, 403)
    assert post(c, csrf, origin="https://evil.example", **d).status_code in (400, 403)
    assert post(c, csrf, origin=None, **d).status_code in (400, 403)
    assert post(c, csrf, name="nope", **d).status_code == 404
    assert post(c, csrf, name="other", **d).status_code in (400, 404, 409)
    assert f.read_text() == SEED
    anon = TestClient(c.app, base_url="https://pm.example.test")
    assert anon.post("/p/pm/todo/toggle", data=d, headers={"Origin": ORIGIN},
                     follow_redirects=False).status_code in (303, 401, 403)
    assert f.read_text() == SEED


def test_symlink_refused(env, tmp_path):
    c, f, s = env
    _, csrf, tok = fields(c)
    real = tmp_path / "real.md"
    real.write_text("- [ ] a\n")
    f.unlink()
    f.symlink_to(real)
    r = post(c, csrf, line="0", text="a", token=tok.group(1))
    assert r.status_code == 400 and real.read_text() == "- [ ] a\n"
    assert "refused" in c.get("/p/pm").text


def test_size_cap(env):
    c, f, s = env
    big = "- [ ] a\n" + "x" * (256 * 1024)
    f.write_text(big)
    _, csrf, tok = fields(c)
    r = post(c, csrf, line="0", text="a", token=tok.group(1))
    assert r.status_code == 413 and f.read_text() == big


def test_numbers_match_parse_todo(env):
    c, f, s = env
    t = projects.parse_todo(f.parent)
    sm = rt.todo_summary(s, "pm")
    assert (sm["done_n"], sm["total"], sm["open_n"]) == (t.done, t.total, len(t.open_items))


def test_todo_lock_file_in_state_dir_not_project(env):
    c, f, s = env
    _, csrf, _ = fields(c)
    assert post(c, csrf, line="5", text="open `code` and **bold**").status_code == 303
    assert list((s.state_dir / "locks").glob("*.lock"))
    assert not [p for p in f.parent.iterdir() if p.name.endswith(".lock")]


def test_tracker_project_toggles_in_db_file_mode_untouched(env):
    """A project imported into the tracker toggles by item number in pm.db; the others keep line + text."""
    from pm.projects.tracker import mdimport, store

    c, f, s = env
    other = f.parent.parent / "other" / "TODO.md"
    other.write_text(SEED)
    mdimport.import_project(s, "other", other.parent)
    html, csrf, _ = fields(c, "other")
    assert 'name="line" value="2"' in html.split('id="todo-card"')[1]  # item numbers, not line indexes
    r = post(c, csrf, name="other", line="2", text="open `code` and **bold**")
    assert r.status_code == 303 and r.headers["location"] == "/p/other#todo"
    assert store.get_item(s, "other", "todo", 2).done and "- [x] open `code`" in other.read_text()
    assert f.read_text() == SEED


def _add(c, csrf, name, origin=ORIGIN, **data):
    h = {"Origin": origin} if origin else {}
    return c.post(f"/p/{name}/todo/add", data={"csrf": csrf, **data}, headers=h, follow_redirects=False)


def test_todo_add_tracker_project(env):
    from pm.projects.tracker import mdimport, store

    c, f, s = env
    other = f.parent.parent / "other" / "TODO.md"
    other.write_text(SEED)
    mdimport.import_project(s, "other", other.parent)
    _, csrf, _ = fields(c, "other")
    r = _add(c, csrf, "other", text="  brand new task ", section="Phase 2")
    assert r.status_code == 303 and r.headers["location"] == "/p/other#todo"
    items = [it for it in store.list_items(s, "other", "todo") if it.title.startswith("brand new task")]
    assert items and not items[0].done and "- [ ] brand new task" in other.read_text()
    assert "todo_add" in audit_text(s) and "brand new task" not in audit_text(s)


def test_todo_add_refuses_file_mode_bad_form_and_no_origin(env):
    c, f, s = env
    _, csrf, _ = fields(c)
    assert _add(c, csrf, "pm", text="x").status_code == 409
    assert f.read_text() == SEED
    assert _add(c, csrf, "pm", text="   ").status_code == 400
    assert _add(c, csrf, "pm", text="a\nb").status_code == 400
    assert _add(c, csrf, "nope", text="x").status_code == 404
    assert _add(c, csrf, "pm", origin=None, text="x").status_code in (400, 403)
