"""Builder page: split chat | sandboxed preview, toolbar, path guard, divider, narrow tabs."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.ui


def _until(page, fn, timeout=8000):
    """Poll page.evaluate(fn): wait_for_function re-evaluates strings with eval, which the page CSP forbids."""
    waited = 0
    while not page.evaluate(fn):
        assert waited < timeout, fn
        page.wait_for_timeout(100)
        waited += 100


def _go(page, inst, key="running"):
    page.goto(f"/a/{inst['agents'][key]}/builder")
    page.wait_for_selector("#builder-frame:not([hidden])")
    return page.locator("#builder-frame")


def test_renders_ready(make_page, inst):
    page = make_page(width=1300, theme="light")
    frame = _go(page, inst)
    assert "/builder/f/" in frame.get_attribute("src")
    assert page.locator("#bld-st .tx").inner_text() in ("Loading", "Loaded")
    _until(page, "() => document.getElementById('bld-sttx').textContent === 'Loaded'")
    assert page.locator("#bld-path").input_value() == "/index.html"
    assert page.frame_locator("#builder-frame").locator("h1").inner_text() == "fixproj preview"
    page.locator("#bld-files summary").click()
    page.wait_for_selector("#bld-list a")
    assert "index.html" in page.locator("#bld-list a").all_inner_texts()
    assert page.locator("#bld-list a[aria-current]").get_attribute("data-path") == "index.html"
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
    assert page._pm_natives == []


def test_path_field_guard(make_page, inst):
    page = make_page(width=1300, theme="dark")
    frame = _go(page, inst)
    src = frame.get_attribute("src")
    for bad in ("../x.html", "a/../../x", "https://evil.example/", "//evil.example/x", "javascript:alert(1)", "a\\b"):
        page.fill("#bld-path", bad)
        page.press("#bld-path", "Enter")
        assert frame.get_attribute("src") == src, bad
        assert page.locator("#bld-path").get_attribute("aria-invalid") == "true"
    page.fill("#bld-path", "/style.css")
    page.press("#bld-path", "Enter")
    assert frame.get_attribute("src").endswith("/style.css")
    assert page.locator("#bld-path").get_attribute("aria-invalid") is None
    assert page.locator("#bld-open").get_attribute("href").endswith("/style.css")


def test_sandbox_by_origin(make_page, inst):
    """Local pages keep the strict sandbox; an allowed external origin may use its own origin (sprites, fonts)."""
    page = make_page(width=1300, theme="light")
    page.route("https://mock.example.test/**", lambda r: r.fulfill(
        status=200, content_type="text/html",
        body="<!doctype html><title>x</title><svg width=8 height=8><use href='i.svg#a'/></svg>"))
    _go(page, inst)
    sb = lambda: page.locator("#builder-frame").get_attribute("sandbox")  # noqa: E731
    assert "allow-same-origin" not in sb()
    page.fill("#bld-path", "https://mock.example.test/pm/x.html")
    page.press("#bld-path", "Enter")
    _until(page, "() => document.getElementById('builder-frame').src.startsWith('https://mock.example.test/')")
    assert "allow-same-origin" in sb() and "allow-scripts" in sb()
    _until(page, "() => document.getElementById('bld-sttx').textContent === 'Loaded'")
    page.fill("#bld-path", "https://" + page.url.split("/")[2] + "/x.html")  # pm's own origin is refused
    page.press("#bld-path", "Enter")
    assert page.locator("#bld-path").get_attribute("aria-invalid") == "true"
    page.fill("#bld-path", "/index.html")
    page.press("#bld-path", "Enter")
    _until(page, "() => document.getElementById('builder-frame').src.includes('/builder/f/')")
    assert "allow-same-origin" not in sb()


def test_screen_size_presets_and_reload(make_page, inst):
    page = make_page(width=1700, theme="light")
    frame = _go(page, inst)
    _until(page, "() => document.getElementById('bld-sttx').textContent === 'Loaded'")
    box = lambda: frame.evaluate("e => [e.offsetWidth, e.offsetHeight, e.getBoundingClientRect().width]")  # noqa: E731
    fit = box()[2]
    assert page.locator("#bld-zoom").is_hidden()
    page.select_option("#bld-size", "ip17pm")  # iPhone 17 Pro Max: 440x956 CSS px, scaled to fit the stage
    w, h, shown = box()
    assert (w, h) == (440, 956) and shown <= 440 and shown < fit
    assert page.locator("#bld-zoom option[value=fit]").text_content().startswith("Fit ")
    assert page.locator("#bld-zoom").input_value() == "fit" and page.locator("#bld-zoom").is_visible()
    stage = page.locator("#bld-stage").bounding_box()
    assert frame.bounding_box()["y"] + frame.bounding_box()["height"] <= stage["y"] + stage["height"] + 1  # fully visible
    for key, size in (("mbp14", (1512, 982)), ("dell27", (2560, 1440)), ("dell34h", (1720, 1440)),
                      ("dell34t", (2293, 1440)), ("tab", (768, 1024)), ("ip17p", (402, 874)),
                      ("ipair", (420, 912)), ("ipse", (375, 667))):
        page.select_option("#bld-size", key)
        assert tuple(box()[:2]) == size, key
        assert box()[2] <= stage["width"], key  # never wider than the stage
    page.select_option("#bld-size", "dell27")
    assert box()[2] < 2560 and page.evaluate("localStorage.getItem('pm-builder-device')") == "dell27"
    page.reload()
    page.wait_for_selector("#builder-frame:not([hidden])")
    assert page.locator("#bld-size").input_value() == "dell27"  # remembered
    page.select_option("#bld-size", "fit")
    assert round(box()[2]) == round(fit) and page.locator("#bld-zoom").is_hidden()
    page.evaluate("document.getElementById('builder-frame').src = 'about:blank'")
    page.click("#bld-reload")
    _until(page, "() => document.getElementById('builder-frame').src.includes('/builder/f/')")
    _until(page, "() => document.getElementById('bld-sttx').textContent === 'Loaded'")


def test_zoom_like_device_mode(make_page, inst):
    page = make_page(width=1500, theme="light")
    frame = _go(page, inst)
    _until(page, "() => document.getElementById('bld-sttx').textContent === 'Loaded'")
    page.select_option("#bld-size", "dell27")
    shown = lambda: frame.evaluate("e => e.getBoundingClientRect().width")  # noqa: E731
    fit = shown()
    assert 2560 * 0.2 < fit < 2560  # fitted
    page.select_option("#bld-zoom", "100")  # 100%: real size, the stage scrolls
    assert round(shown()) == 2560
    assert page.evaluate("(() => { const s = document.getElementById('bld-stage'); return s.scrollWidth > s.clientWidth; })()")
    assert page.evaluate("localStorage.getItem('pm-builder-zoom')") == "100"
    page.select_option("#bld-zoom", "50")
    assert round(shown()) == 1280 and frame.evaluate("e => e.offsetWidth") == 2560
    page.reload()
    page.wait_for_selector("#builder-frame:not([hidden])")
    assert page.locator("#bld-zoom").input_value() == "50"  # remembered
    box = page.locator("#bld-stage").bounding_box()  # ctrl + wheel over the page steps 50 -> 75 -> 100, and back down
    page.mouse.move(box["x"] + 200, box["y"] + 200)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -100)
    _until(page, "() => document.getElementById('bld-zoom').value === '75'")
    page.wait_for_timeout(120)
    page.mouse.wheel(0, 100)
    page.keyboard.up("Control")
    _until(page, "() => document.getElementById('bld-zoom').value === '50'")
    page.select_option("#bld-size", "fit")
    assert page.locator("#bld-zoom").is_hidden()  # Fit pane has no zoom


def test_source_dropdown_files_and_url(make_page, inst):
    page = make_page(width=1500, theme="light")
    page.route("https://mock.example.test/**", lambda r: r.fulfill(
        status=200, content_type="text/html", body="<!doctype html><title>x</title><h1>mock</h1>"))
    frame = _go(page, inst)
    assert page.locator("#bld-src").input_value() == "files" and page.locator("#bld-files").is_visible()
    assert page.locator("#bld-hosts option").first.get_attribute("value") == "https://mock.example.test/"
    page.select_option("#bld-src", "url")
    assert page.locator("#bld-files").is_hidden() and page.locator("#bld-path").get_attribute("list") == "bld-hosts"
    assert page.locator("#bld-path").input_value() == ""  # nothing remembered yet: ready to type
    page.fill("#bld-path", "mock.example.test/pm/x.html")  # a bare host means https
    page.press("#bld-path", "Enter")
    _until(page, "() => document.getElementById('builder-frame').src === 'https://mock.example.test/pm/x.html'")
    page.fill("#bld-path", "evil.example/x")
    page.press("#bld-path", "Enter")
    assert page.locator("#bld-path").get_attribute("aria-invalid") == "true"
    assert frame.get_attribute("src") is not None
    page.select_option("#bld-src", "files")  # back to the project page
    _until(page, "() => document.getElementById('builder-frame').src.includes('/builder/f/')")
    assert page.locator("#bld-files").is_visible() and page.locator("#bld-path").get_attribute("list") is None
    page.select_option("#bld-src", "url")  # the last URL comes back
    _until(page, "() => document.getElementById('builder-frame').src === 'https://mock.example.test/pm/x.html'")
    assert page.locator("#bld-src").input_value() == "url"
    page.reload()  # reopened on the URL
    _until(page, "() => document.getElementById('bld-src').value === 'url'")


def test_old_device_value_maps_to_a_preset(make_page, inst):
    page = make_page(width=1700, theme="light")
    page.add_init_script("localStorage.setItem('pm-builder-device', 'phone')")
    _go(page, inst)
    assert page.locator("#bld-size").input_value() == "ip17pm"


def test_preview_line_link_opens_in_the_aside(make_page, inst):
    page = make_page(width=1700, theme="light")
    page.route("https://mock.example.test/**", lambda r: r.fulfill(
        status=200, content_type="text/html", body="<!doctype html><title>x</title><h1>mock</h1>"))
    _go(page, inst)
    url = "https://mock.example.test/pm/v5/pages/s-app.html?page=Settings"
    page.evaluate("""u => {
      const d = document.createElement('div'); d.className = 'chat-text';
      d.textContent = 'Preview: ' + u; document.getElementById('bld-msgs').appendChild(d);
      const p = document.createElement('div'); p.className = 'chat-text'; p.textContent = 'Preview: the page below';
      document.getElementById('bld-msgs').appendChild(p);
      document.dispatchEvent(new CustomEvent('pm:ci-update', { bubbles: true }));
    }""", url)
    link = page.locator("#bld-msgs a.bld-plink")
    assert link.count() == 1 and link.get_attribute("href") == url  # prose after "Preview:" is not linked
    link.click()
    _until(page, "() => document.getElementById('builder-frame').src.startsWith('https://mock.example.test/')")
    assert page.locator("#bld-path").input_value() == url
    assert len(page.context.pages) == 1  # no new tab
    with page.context.expect_page() as np:  # ctrl-click keeps the normal link behaviour
        link.click(modifiers=["Control"])
    np.value.close()


def test_divider_keyboard_and_persist(make_page, inst):
    page = make_page(width=1300, theme="light")
    _go(page, inst)
    div = page.locator(".bld-div")
    div.focus()
    page.keyboard.press("ArrowRight")
    assert div.get_attribute("aria-valuenow") == "40"
    assert page.evaluate("localStorage.getItem('pm-builder-left')") == "40"
    page.reload()
    page.wait_for_selector("#builder-frame:not([hidden])")
    assert page.locator(".bld-div").get_attribute("aria-valuenow") == "40"
    box = page.locator(".bld-div").bounding_box()
    page.mouse.move(box["x"] + 4, box["y"] + 100)
    page.mouse.down()
    page.mouse.move(400, box["y"] + 100, steps=4)
    page.mouse.up()
    assert int(page.locator(".bld-div").get_attribute("aria-valuenow")) < 40


def test_reloads_when_files_change(make_page, inst):
    page = make_page(width=1300, theme="light")
    _go(page, inst)
    _until(page, "() => document.getElementById('bld-sttx').textContent === 'Loaded'")
    page.wait_for_timeout(2600)  # baseline poll taken
    page.evaluate("window.__n = 0; document.getElementById('builder-frame').addEventListener('load', () => window.__n++)")
    f = Path(inst["root"]) / "projects/fixproj/style.css"
    old = f.read_text()
    try:
        f.write_text(old + "\n/* touched */\n")
        _until(page, "() => window.__n >= 1")
    finally:
        f.write_text(old)


def test_narrow_tabs_no_overflow(make_page, inst):
    page = make_page(width=375, theme="dark")
    page.goto(f"/a/{inst['agents']['running']}/builder")
    assert page.locator(".bld-chat").is_visible() and not page.locator(".bld-prev").is_visible()
    assert not page.locator(".bld-div").is_visible()
    tab = page.locator(".bld-tabs .tab[for=bt-chat]")
    shadow = tab.evaluate("e => getComputedStyle(e).boxShadow")  # shared .tab look (tabs.css): blue underline shown
    assert "inset" in shadow and shadow.startswith(page.evaluate(
        "() => { const d = document.createElement('i'); d.style.color = 'var(--accent)'; document.body.append(d);"
        " const c = getComputedStyle(d).color; d.remove(); return c; }"))
    assert page.locator(".bld-tabs").evaluate("e => e.getBoundingClientRect().height") >= tab.evaluate("e => e.getBoundingClientRect().height")
    page.click("label[for=bt-prev]")
    assert page.locator(".bld-prev").is_visible() and not page.locator(".bld-chat").is_visible()
    assert page.locator("label[for=bt-prev]").get_attribute("aria-selected") == "true"
    assert tab.evaluate("e => getComputedStyle(e).boxShadow") == "none"
    assert page.locator("#bld-st .tx").evaluate("e => e.getBoundingClientRect().width") <= 2  # sr-only
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")


def test_agent_page_has_builder_link(make_page, inst):
    page = make_page(width=1300, theme="light")
    page.goto(f"/a/{inst['agents']['running']}")
    link = page.locator(".agent-actions a", has_text="Builder")
    assert link.get_attribute("href").endswith("/builder")
