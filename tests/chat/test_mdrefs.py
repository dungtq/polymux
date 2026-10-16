"""pm.chat.mdrefs: the folders a chat worked in (cd, git -C, --directory, file folders) and the .md paths it named,
read lazily from the chat items once for both."""

from pm.chat import mdrefs
from pm.core.models import ChatItem


def test_cmd_dirs(monkeypatch):
    monkeypatch.setenv("HOME", "/h")
    line = "cd ~/dev/polymux-wt-dt; sed -n 1,12p README.md; ls docs && cat SECURITY.md"
    assert mdrefs.cmd_dirs(line) == ["/h/dev/polymux-wt-dt"]
    assert mdrefs.cmd_dirs("cd /a/b && git -C /c/d status | head; (cd '/e f' && ls)") == ["/a/b", "/c/d", "/e f"]
    assert mdrefs.cmd_dirs("git --no-pager -C $HOME/x log; uv run --directory ${HOME}/y pytest") == ["/h/x", "/h/y"]
    assert mdrefs.cmd_dirs("uv run --directory=/z/w pytest; if true; then cd /q; fi") == ["/z/w", "/q"]
    assert mdrefs.cmd_dirs("cd docs; cd ../x; cd /a/../b; abcd /n; echo cd /m") == []  # relative, '..', not a cd
    assert mdrefs.cmd_dirs("cd /a/b/; cd /a/b") == ["/a/b"]


def test_file_dirs(monkeypatch):
    monkeypatch.setenv("HOME", "/h")
    assert mdrefs.file_dirs("Read /a/b/c.py and ~/d/E.md.") == ["/h/d"]  # text: .md files only
    tool = "sed -n 1p /a/b/c.py ~/src/pm /dev/null https://x.y/a/b.md $HOME/n/o.md /a/../e/f.md"
    assert mdrefs.file_dirs(tool, any_file=True) == ["/a/b", "/h/src/pm", "/dev/null", "/h/n"]


def _items(n_tool_lines):
    return [ChatItem(seq=i + 1, rev=i + 1, uid=f"t{i}", conv="c", kind="tool", turn="u", ts=float(i), name="Bash",
                     line=line) for i, line in enumerate(n_tool_lines)]


def test_chat_paths_newest_first_read_once(monkeypatch):
    page = _items(["cd /old && cat /old/OLD.md", "cd /new; cat x"])
    page.append(ChatItem(seq=9, rev=9, uid="m", conv="c", kind="text", turn="u", ts=9.0, text="See /t/T.md"))
    calls = []

    def list_items(settings, agent_id, **kw):
        calls.append(kw.get("before_seq"))
        return page

    monkeypatch.setattr("pm.chat.items.list_items", list_items)
    cp = mdrefs.ChatPaths(None, "claude-1")
    assert calls == []  # nothing read until iterated
    assert list(cp.mentions()) == ["/t/T.md", "/old/OLD.md"]
    assert list(cp.dirs()) == ["/t", "/new", "/old"]
    assert calls == [None]  # one page (fewer than 1000 items), shared by both


def test_chat_paths_ignore_thinking(monkeypatch):
    page = [ChatItem(seq=1, rev=1, uid="k", conv="c", kind="think", turn="u", ts=1.0, text="maybe /k/K.md"),
            ChatItem(seq=2, rev=2, uid="m", conv="c", kind="text", turn="u", ts=2.0, text="See /t/T.md")]
    monkeypatch.setattr("pm.chat.items.list_items", lambda settings, agent_id, **kw: page)
    cp = mdrefs.ChatPaths(None, "claude-1")
    assert list(cp.mentions()) == ["/t/T.md"] and list(cp.dirs()) == ["/t"]


def test_chat_paths_reads_at_most_two_pages_lazily(monkeypatch):
    full = _items(["cd /p%d" % i for i in range(1000)])
    calls = []

    def list_items(settings, agent_id, **kw):
        calls.append(kw.get("before_seq"))
        return full

    monkeypatch.setattr("pm.chat.items.list_items", list_items)
    cp = mdrefs.ChatPaths(None, "claude-1")
    assert next(cp.dirs()) == "/p999" and calls == [None]
    assert len(list(cp.dirs())) == 1000 and calls == [None, 1]
    list(cp.mentions())
    assert calls == [None, 1]
