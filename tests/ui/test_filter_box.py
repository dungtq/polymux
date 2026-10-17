"""Unified list filter box on / (Inbox, Ended), /ntfy, /p, /tmux and the project page: live row hiding, in-field clear x,
Esc, phone layout, ?q= restore (Inbox), and the query surviving a list swap (pm:refresh / the 15 s poll).
Name-type text only: a word that is only in a message body matches nothing."""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.ui

TERM = "codex"  # in one live (idle-fold) row's data-q on / and in one ntfy row's title on /ntfy
MSG_WORD = "green"  # only in a ntfy message body ("Fixed the form and added a test. All green.")
PAGES = {
    "/": {"rows": "[data-frow][data-q]", "bar": "#inbox-filter"},
    "/ntfy": {"rows": ".ntfy-row[data-q]", "bar": "#ntfy-tools-all"},
}
# Pages whose rows are all [data-frow] and whose box position is not tied to a Show bar. The term is picked from the
# rows themselves (fixture rows differ per page): the shortest-match token of at least 5 characters.
ROW_PAGES = {
    "/p": {"rows": "[data-frow][data-q]"},
    "/tmux": {"rows": "[data-frow][data-q]"},
    "/p/{project}": {"rows": "[data-frow][data-q]"},
}
PAGES.update(ROW_PAGES)
BASE = ["/", "/ntfy"]  # pages the message-text / refresh / bar-layout tests apply to
INPUT = "input[data-filter]"


def _go(page, url, inst=None):
    page.goto(url.format(project=inst["project"]) if "{project}" in url else url)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector(INPUT)
    page.wait_for_selector(PAGES[url.split("?")[0]]["rows"], state="attached")


def _term(page, url):
    """A filter word for the page: TERM where the page has it (/ and /ntfy), else the rarest token of its rows."""
    if url in BASE:
        return TERM
    return page.eval_on_selector_all(PAGES[url]["rows"], """els => {
      const qs = els.map(e => e.getAttribute('data-q')), n = qs.length; let best = null, bn = n;
      for (const t of new Set(qs.flatMap(q => q.split(' ')))) {
        if (t.length < 5) continue;
        const c = qs.filter(q => q.includes(t)).length;
        if (c > 0 && c < bn) { best = t; bn = c; }
      }
      return best; }""")


def _rows(page, url):
    return page.locator(PAGES[url]["rows"] + ":visible")


def _matching(page, url, term):
    """How many rows (hidden or not) have the term in data-q, and how many rows there are."""
    return page.eval_on_selector_all(
        PAGES[url]["rows"], "(els, t) => [els.filter(e => e.getAttribute('data-q').includes(t)).length, els.length]", term)


def _x(page):
    return page.locator(".fbox-x").first


@pytest.mark.parametrize("width", [1300, 440])
@pytest.mark.parametrize("url", BASE)
def test_typing_filters_rows_and_x_clears(make_page, url, width):
    page = make_page(width=width)
    _go(page, url)
    box = page.locator(INPUT)
    assert box.count() == 1 and box.get_attribute("placeholder") == "Filter..."
    n_match, n_all = _matching(page, url, TERM)
    assert 0 < n_match < n_all
    total = _rows(page, url).count()
    assert total >= n_all - 8  # (rows past the first few of the Ended fold stay folded)
    assert not _x(page).is_visible()  # no text: no x
    box.fill(TERM.upper())  # case-insensitive
    expect(_rows(page, url)).to_have_count(n_match)
    assert _x(page).is_visible()
    assert page.evaluate("() => document.documentElement.hasAttribute('data-pmq')")
    # the x clears, refocuses the input and shows every row again
    _x(page).click()
    assert box.input_value() == "" and page.evaluate("() => document.activeElement.matches('input[data-filter]')")
    expect(_rows(page, url)).to_have_count(total)
    assert not _x(page).is_visible()
    assert not page.evaluate("() => document.documentElement.hasAttribute('data-pmq')")


@pytest.mark.parametrize("url", BASE)
def test_escape_clears(page, url):
    _go(page, url)
    total = _rows(page, url).count()
    box = page.locator(INPUT)
    box.fill(TERM)
    expect(_rows(page, url)).not_to_have_count(total)
    box.press("Escape")
    assert box.input_value() == ""
    expect(_rows(page, url)).to_have_count(total)
    assert not _x(page).is_visible()
    box.press("Escape")  # Esc on an empty box does nothing
    assert box.input_value() == ""


@pytest.mark.parametrize("url", BASE)
def test_enter_does_not_reload_the_page(page, url):
    _go(page, url)
    page.evaluate("() => { window.__same = 1; }")
    box = page.locator(INPUT)
    box.fill(TERM)
    box.press("Enter")
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__same === 1") and box.input_value() == TERM


@pytest.mark.parametrize("url", BASE)
def test_message_text_never_matches(page, url):
    _go(page, url)
    assert page.locator(f"{PAGES[url]['rows']}[data-q*='{MSG_WORD}']").count() == 0
    page.locator(INPUT).fill(MSG_WORD)
    expect(_rows(page, url)).to_have_count(0)
    page.locator(INPUT).fill("")
    expect(_rows(page, url).first).to_be_visible()


def test_inbox_server_filter_updates_url_counts_and_ended(page):
    _go(page, "/")
    assert "Ended (" in page.inner_text("body")
    page.locator(INPUT).fill(TERM)
    page.wait_for_function("t => location.search.includes('q=' + t)", arg=TERM)
    page.wait_for_function("() => !document.body.innerText.includes('Ended (')")  # no ended row matches
    # sort links and the bottom Show form carry q
    assert all(f"q={TERM}" in h for h in page.eval_on_selector_all("a.hcell[href*='sort=']", "e => e.map(a => a.href)"))
    page.locator(INPUT).fill("")
    page.wait_for_function("() => !location.search.includes('q=')")
    expect(page.locator("#ended-sessions")).to_have_count(1)


def test_inbox_q_is_restored_on_reload_and_on_direct_link(page):
    _go(page, "/")
    total = _rows(page, "/").count()
    page.locator(INPUT).fill(TERM)
    page.wait_for_function("t => location.search.includes('q=' + t)", arg=TERM)
    page.reload()
    page.wait_for_load_state("networkidle")
    box = page.locator(INPUT)
    assert box.input_value() == TERM and _x(page).is_visible()
    n_match = _matching(page, "/", TERM)[0]
    assert 0 < n_match and _rows(page, "/").count() == n_match < total
    page.goto("/?q=" + TERM.upper())  # a shared link
    page.wait_for_load_state("networkidle")
    assert page.locator(INPUT).input_value() == TERM.upper() and _rows(page, "/").count() == n_match


@pytest.mark.parametrize("url", BASE)
def test_query_survives_a_pm_refresh_swap(page, url):
    _go(page, url)
    box = page.locator(INPUT)
    box.fill(TERM)
    n_match = _matching(page, url, TERM)[0]
    expect(_rows(page, url)).to_have_count(n_match)
    page.wait_for_timeout(1000)  # the Inbox sends its own debounced refresh pings after typing
    # the Inbox list listens for pm:refresh on itself, the ntfy panel on document
    target = "document.getElementById('inbox')" if url == "/" else "document.body"
    seen = []
    page.on("response", lambda r: seen.append(r) if r.request.headers.get("hx-request") == "true"
            and ("/ntfy/panel" in r.url or "q=" in r.url) else None)
    for _ in range(6):  # nodiff.js skips a refresh while its previous fetch is still running: ping again
        page.evaluate(f"() => htmx.trigger({target}, 'pm:refresh')")
        page.wait_for_timeout(700)
        if seen:
            break
    assert seen, "no list refresh request after pm:refresh"
    page.wait_for_timeout(500)
    assert box.input_value() == TERM and _x(page).is_visible()
    expect(_rows(page, url)).to_have_count(n_match)
    assert page.evaluate("() => document.querySelectorAll('input[data-filter]').length") == 1


def test_query_survives_the_15s_ntfy_poll(page):
    _go(page, "/ntfy")
    page.locator(INPUT).fill(TERM)
    n_match = _matching(page, "/ntfy", TERM)[0]
    expect(_rows(page, "/ntfy")).to_have_count(n_match)
    with page.expect_response(lambda r: "/ntfy/panel" in r.url, timeout=20000):
        pass  # the section polls every 15 s
    page.wait_for_timeout(300)
    assert page.locator(INPUT).input_value() == TERM
    expect(_rows(page, "/ntfy")).to_have_count(n_match)


@pytest.mark.parametrize("url", BASE)
def test_phone_box_is_full_width_on_its_own_line_above_show(make_page, url):
    page = make_page(width=440)
    _go(page, url)
    box = page.locator(INPUT).bounding_box()
    show = page.locator(f"{PAGES[url]['bar']} select").first.bounding_box()
    main = page.locator("main").first.bounding_box()
    assert box["width"] >= main["width"] - 40  # full width of the content column
    assert show["y"] >= box["y"] + box["height"] - 1  # Show wraps below the box, never beside it
    # the filter box is the first control under the title row
    head = page.locator(".inbox-head").first.bounding_box()
    assert box["y"] >= head["y"] + head["height"] - 1
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


def test_desktop_box_shares_the_row_with_show(page):
    for url in BASE:
        _go(page, url)
        box = page.locator(INPUT).bounding_box()
        show = page.locator(f"{PAGES[url]['bar']} select").first.bounding_box()
        assert abs(box["y"] - show["y"]) < 4 and show["x"] > box["x"] + box["width"] - 1


@pytest.mark.parametrize("url", BASE)
def test_touch_clear_target_is_at_least_40px(browser, inst, url):
    ctx = browser.new_context(viewport={"width": 440, "height": 900}, has_touch=True, is_mobile=True, base_url=inst["url"])
    try:
        page = ctx.new_page()
        page.set_default_timeout(15000)
        page.goto("/login")
        page.fill("#token", inst["_token"])
        page.press("#token", "Enter")
        page.wait_for_load_state("networkidle")
        _go(page, url)
        assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        page.locator(INPUT).fill(TERM)
        b = _x(page).bounding_box()
        assert b["width"] >= 40 and b["height"] >= 40, b
        _x(page).tap()
        assert page.locator(INPUT).input_value() == "" and not _x(page).is_visible()
    finally:
        ctx.close()


def test_mouse_phone_clear_target_is_still_a_full_height_button(make_page):
    page = make_page(width=440)
    _go(page, "/ntfy")
    page.locator(INPUT).fill(TERM)
    inp, x = page.locator(INPUT).bounding_box(), _x(page).bounding_box()
    assert x["height"] >= inp["height"] - 1 and x["width"] >= 32
    assert abs((x["x"] + x["width"]) - (inp["x"] + inp["width"])) < 2  # inside the field, at its right edge


# ---------------------------------------------------------------- Projects, tmux and the project page
def _visible(page, url):
    """Visible filterable rows right now (no waiting): what the user sees the instant after a keystroke."""
    return page.eval_on_selector_all(PAGES[url]["rows"], "els => els.filter(e => e.offsetParent !== null).length")


@pytest.mark.parametrize("width", [1300, 440])
@pytest.mark.parametrize("url", list(ROW_PAGES))
def test_row_pages_hide_instantly_and_clear(make_page, inst, url, width):
    page = make_page(width=width)
    _go(page, url, inst)
    box = page.locator(INPUT)
    assert box.count() == 1 and box.get_attribute("placeholder") == "Filter..."
    term = _term(page, url)
    assert term, "no filter word found in the page's rows"
    n_match, n_all = _matching(page, url, term)
    assert 0 < n_match < n_all
    total = _visible(page, url)
    assert total >= 1 and not _x(page).is_visible()
    box.fill(term.upper())  # case-insensitive
    assert _visible(page, url) == n_match  # no waiting: hidden by the adopted sheet on the keystroke itself
    assert _x(page).is_visible() and page.evaluate("() => document.documentElement.hasAttribute('data-pmq')")
    page.wait_for_timeout(900)  # a live (server ?q=) page re-renders; the hidden rows must stay gone
    assert box.input_value() == term.upper()
    expect(page.locator(PAGES[url]["rows"] + ":visible")).to_have_count(n_match)
    _x(page).click()
    assert box.input_value() == "" and page.evaluate("() => document.activeElement.matches('input[data-filter]')")
    assert not _x(page).is_visible()  # (a live page gets its other rows back from the server a moment later)
    assert not page.evaluate("() => document.documentElement.hasAttribute('data-pmq')")
    expect(page.locator(PAGES[url]["rows"] + ":visible")).to_have_count(total)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("url", list(ROW_PAGES))
def test_row_pages_escape_clears_and_enter_stays(page, inst, url):
    _go(page, url, inst)
    term, box = _term(page, url), page.locator(INPUT)
    total = _visible(page, url)
    page.evaluate("() => { window.__same = 1; }")
    box.fill(term)
    box.press("Enter")  # blurs; no submit, no navigation
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__same === 1") and box.input_value() == term
    box.focus()
    box.press("Escape")
    assert box.input_value() == ""
    expect(page.locator(PAGES[url]["rows"] + ":visible")).to_have_count(total)


@pytest.mark.parametrize("url", list(ROW_PAGES))
def test_row_pages_phone_box_is_full_width_above_the_rows(make_page, inst, url):
    page = make_page(width=440)
    _go(page, url, inst)
    box = page.locator(INPUT).bounding_box()
    main = page.locator("main").first.bounding_box()
    assert box["width"] >= main["width"] - 40  # full width of the content column
    first = page.locator(PAGES[url]["rows"] + ":visible").first.bounding_box()
    assert first["y"] >= box["y"] + box["height"] - 1  # the box sits above the list, not over it
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


# ---------------------------------------------------------------- sidebar box, Audit, Agents
def test_sidebar_box_has_its_own_query(page, inst):
    page.goto(f"/a/{inst['agents']['running']}")
    page.wait_for_load_state("networkidle")
    box = page.locator("#v5side input[data-filter-scope]")
    rows = page.locator("#v5side .vs-row[data-q]")
    n = rows.count()
    assert n >= 2 and box.get_attribute("placeholder") == "Filter..."
    term = page.eval_on_selector_all("#v5side .vs-row[data-q]", """els => {
      const qs = els.map(e => e.getAttribute('data-q'));
      for (const t of new Set(qs.flatMap(q => q.split(' ')))) {
        const c = qs.filter(q => q.includes(t)).length;
        if (t.length >= 5 && c > 0 && c < qs.length) return t;
      }
      return null; }""")
    assert term
    want = page.eval_on_selector_all("#v5side .vs-row[data-q]", "(els, t) => els.filter(e => e.getAttribute('data-q').includes(t)).length", term)
    box.fill(term.upper())
    expect(page.locator("#v5side .vs-row[data-q]:visible")).to_have_count(want)
    assert not page.evaluate("() => document.documentElement.hasAttribute('data-pmq')")  # not the page's query
    assert "q=" not in page.url
    box.fill("zzzz-no-such-row")
    expect(page.locator("#v5side [data-fnone]:visible")).to_have_count(1)
    box.press("Escape")
    assert box.input_value() == ""
    expect(page.locator("#v5side .vs-row[data-q]:visible")).to_have_count(n)
    expect(page.locator("#v5side [data-fnone]:visible")).to_have_count(0)


def test_audit_box_filters_log_and_client_errors_live(page):
    page.goto("/audit")
    page.wait_for_load_state("networkidle")
    st = page.evaluate("""() => window.pm.fetch('/client-errors', {method: 'POST', csrf: 'header',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({kind: 'fetch', method: 'GET', url: '/', page: '/', msg: 'Failed to fetch'})}).then(r => r.status)""")
    assert st == 204  # one report, so the Client errors list has a row the query must hide
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#ce-list")
    boxes = page.locator("input[data-filter]")
    assert boxes.count() == 2  # the log's box and the Client errors box: one query
    assert page.locator("main button", has_text="Filter").count() == 0
    rows = page.locator("#aud-list table.aud tbody tr:not(.aud-empty)")
    assert rows.count() >= 1
    boxes.first.fill("zzzz-nothing")
    expect(page.locator("#aud-list .aud-empty")).to_have_count(1)
    assert boxes.nth(1).input_value() == "zzzz-nothing"
    assert "/audit?" in page.url and "q=zzzz-nothing" in page.url  # only the /audit form owns the address bar
    expect(page.locator("#ce-list .aud-empty")).to_have_count(1)
    page.locator(".aud-filter .fbox-x").first.click()
    expect(rows.first).to_be_visible()
    assert "q=" not in page.url


def test_agents_library_box_is_live(page):
    page.goto("/agents?t=library")
    page.wait_for_load_state("networkidle")
    box = page.locator("input[data-filter]")
    assert box.count() == 1
    rows = page.locator("#ag-lib .lrow.arow-l")
    n = rows.count()
    assert n >= 2
    name = rows.first.locator(".c-nm strong").inner_text()
    page.evaluate("() => { window.__same = 1; }")
    box.fill(name)
    page.wait_for_function("(n) => document.querySelectorAll('#ag-lib .lrow.arow-l').length < n", arg=n)
    assert page.evaluate("() => window.__same === 1") and f"q={name}" in page.url  # no reload, URL follows
    box.press("Escape")
    expect(rows).to_have_count(n)
