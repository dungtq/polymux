"""Every /static/ stylesheet and script a template links exists; tokens.css loads before the other stylesheets."""

import re
from pathlib import Path

import pytest

from pm.web.render import TEMPLATE_DIR

PM = Path(__file__).resolve().parent.parent / "src/pm"
STATIC = PM / "static"
TEMPLATE_DIRS = [TEMPLATE_DIR, PM / "security/templates"]
LINK = re.compile(r"<link\b[^>]*\bhref=\"(/static/[^\"]+)\"", re.I)
SCRIPT = re.compile(r"<script\b[^>]*\bsrc=\"(/static/[^\"]+)\"", re.I)
STYLESHEET = re.compile(r"<link\b[^>]*\brel=\"stylesheet\"[^>]*>", re.I)


def _templates() -> list[Path]:
    return sorted(p for d in TEMPLATE_DIRS for p in d.rglob("*.html"))


def _refs() -> list[tuple[str, str]]:
    out = []
    for t in _templates():
        text = t.read_text()
        for rx in (LINK, SCRIPT):
            out += [(str(t.relative_to(PM)), m.group(1)) for m in rx.finditer(text)]
    return out


def test_templates_found():
    assert len(_templates()) > 10
    assert any(t.name == "login.html" for t in _templates())
    assert len(_refs()) > 20


@pytest.mark.parametrize(("template", "url"), _refs())
def test_static_ref_resolves(template, url):
    assert "{{" not in url, f"{template}: templated static url {url}"
    rel = url.split("?", 1)[0].split("#", 1)[0].removeprefix("/static/")
    f = (STATIC / rel).resolve()
    assert STATIC.resolve() in f.parents, f"{template}: {url} escapes static/"
    assert f.is_file(), f"{template}: {url} has no file under src/pm/static"


@pytest.mark.skipif(not (STATIC / "css/tokens.css").exists(), reason="no tokens.css")
def test_tokens_css_is_first_stylesheet():
    sheets = [re.search(r'href="([^"]+)"', m.group(0)).group(1)
              for m in STYLESHEET.finditer((TEMPLATE_DIR / "base.html").read_text())]
    assert sheets and sheets[0] == "/static/css/tokens.css", sheets[:3]


def test_projtabs_js_keeps_tab_across_live_refresh():
    from pathlib import Path
    src = Path(__file__).parent.parent / "src/pm"
    js = (src / "static/js/projtabs.js").read_text()
    tabs = (src / "static/js/tabs.js").read_text()  # pm.tabs keeps the tab (data-tabs-key) and re-applies it on refresh
    assert 'data-tabs-key="pm.ptab"' in (TEMPLATE_DIR / "_project_live.html").read_text()
    assert "nodiff:swapped" in tabs and "htmx:afterSettle" in tabs and "pm:tab" in js and "pm.tabs.restore" in js
    assert "vms-changed" in js and "pm:vmload" in js and "fetch(" not in js and "alert(" not in js


def test_theme_and_plugin_static_urls_are_versioned_per_url(tmp_path, monkeypatch):
    from pm.web import assets
    monkeypatch.setattr(assets, "_dirs", {})
    monkeypatch.setattr(assets, "_cache", {})
    dt = tmp_path / "dt"
    (dt / "css").mkdir(parents=True)
    (dt / "css/base.css").write_text("dt{}")
    (tmp_path / "secret.css").write_text("x")
    assets.add_static("/static/t/dt", dt)
    page = '<link href="/static/css/base.css"><link href="/static/t/dt/css/base.css">'
    core, theme = re.findall(r"\?v=([0-9a-f]{10})", assets.version_static(page, STATIC))
    assert core != theme  # same relative path, own hash each (the cache is keyed by the resolved file)
    assert set(assets._cache) == {str(STATIC / "css/base.css"), str(dt / "css/base.css")}
    for bad in ("/static/t/dt/../secret.css", "/static/t/dt/css/../../secret.css", "/static/t/dt/", "/static/../x",
                "/static/t/dt/nope.css", "/static/p/none/a.css"):
        html = f'<script src="{bad}"></script>'
        assert assets.version_static(html, STATIC) == html, bad  # refused or missing: left as it is
    (dt / "link.css").symlink_to(tmp_path / "secret.css")
    assert assets.version_static('<link href="/static/t/dt/link.css">', STATIC) == '<link href="/static/t/dt/link.css">'
