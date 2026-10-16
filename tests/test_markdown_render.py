import re

from pm.core.markdown_render import MAX_CHARS
from pm.core.markdown_render import render_markdown as r


def test_basic_features():
    out = r("# H1\n\ntext **b** *i* ~~s~~ `c`\n\n> quote\n\n1. a\n2. b\n\n- x\n  - nested\n")
    for frag in ("<h1>H1</h1>", "<strong>b</strong>", "<em>i</em>", "<s>s</s>",
                 "<code>c</code>", "<blockquote>", "<ol>", "<ul>\n<li>nested</li>"):
        assert frag in out


def test_table_wrapped():
    out = r("|a|b|\n|-|-|\n|1|2|\n")
    assert '<div class="md-table"><table>' in out and "<td>2</td>" in out


def test_code_fence_and_breaks():
    out = r("```py\nprint('<x>')\n```\nl1\nl2")
    assert '<pre><code class="language-py">' in out and "&lt;x&gt;" in out
    assert "<br" in r("l1\nl2")


def test_xss():
    for bad in ("<script>alert(1)</script>", '<img src=x onerror=alert(1)>',
                "<iframe src=//e></iframe>", "<a href=\"javascript:alert(1)\">x</a>"):
        out = r(bad)
        assert "<script" not in out and "<img" not in out and "<iframe" not in out
        assert "<a " not in out
    for bad in ("[x](javascript:alert(1))", "[x](JaVaScRiPt:alert(1))", "[x](data:text/html,a)",
                "[x](vbscript:a)", "<javascript:alert(1)>", "[x]\n\n[x]: javascript:alert(1)",
                "[x](//evil.com)", "[x](java&#x09;script:alert(1))"):
        out = r(bad)
        assert not re.search(r'href="[^"]*(?:script|data:|//evil)', out, re.I), (bad, out)
    assert "&amp;lt;" in r("&lt;b&gt;") or "&lt;" in r("&lt;b&gt;")
    assert "<b>" not in r("&lt;b&gt;")


def test_link_attrs():
    out = r("[a](https://e.com) [m](mailto:a@b.c) [rel](/x/y?z=1) <http://auto.io>")
    assert out.count('rel="noopener noreferrer nofollow"') == 3
    assert out.count('target="_blank"') == 3
    assert '<a href="/x/y?z=1">rel</a>' in out
    assert "http://auto.io" in out
    ref = r("[r]\n\n[r]: https://ref.io")
    assert 'href="https://ref.io"' in ref and "nofollow" in ref


def test_images_neutralised():
    out = r("![alt](https://evil.com/p.png) ![x](javascript:1)")
    assert "<img" not in out
    assert '<a href="https://evil.com/p.png"' in out and ">alt</a>" in out


def test_cap_and_nesting():
    out = r("a" * (MAX_CHARS + 5000))
    assert out.count("a") == MAX_CHARS
    deep = r("> " * 200 + "x")
    assert deep.count("<blockquote>") <= 30
    assert r("") == ""
    r("[" * 5000 + "]" * 5000)


def test_image_paths_in_code_and_links_open_the_viewer(tmp_path, monkeypatch):
    from pm.core import uploads as uploads_mod
    shots = tmp_path / "shots"
    shots.mkdir()
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
    (shots / "a.png").write_bytes(png)
    (shots / "note.txt").write_text("x")
    monkeypatch.setattr(uploads_mod, "AUTO_ROOTS", (tmp_path,))
    up = tmp_path / "uploads"
    out = r(f"see `{shots}/a.png` and [shot]({shots}/a.png) and `{shots}/note.txt` and `{shots}/gone.png`",
                          uploads=up)
    assert out.count('class="chat-img"') == 2 and out.count('href="/uploads/') == 2
    assert f"<code>{shots}/a.png</code>" in out and "gone.png</code></p>" in out.replace("\n", "")
    assert f'href="{shots}' not in out
    assert 'class="chat-img"' not in r("`/etc/passwd.png`", uploads=up)
