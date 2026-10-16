"""notify/clienterrors.py: stack, browser family, csp kind and the grouped view."""
from pm.notify import clienterrors as ce
from pm.security.mask import MASK

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"


def test_clean_stack_keeps_lines_and_line_col_drops_origin_query_and_secrets():
    raw = ("TypeError: x is null\n    at stick (https://pm.example.test/static/js/pane.js?v=ab12:84:17)\n\n"
           f"    at poll (https://pm.example.test/a/x?token={TOKEN}#f)\n")
    st = ce.clean_stack(raw)
    assert st.splitlines() == ["TypeError: x is null", "at stick (/static/js/pane.js:84:17)", "at poll (/a/x)"]
    assert TOKEN not in ce.clean_stack(f"Error: key {TOKEN}") and MASK in ce.clean_stack(f"Error: key {TOKEN}")
    assert len(ce.clean_stack("at f (/x.js:1:2)\n" * 400)) == ce.STACK_MAX
    assert ce.clean_msg("a\nb  c") == "a b c"  # the message stays one line
    assert ce.clean_msg(f"leaked {TOKEN}") == f"leaked {MASK}"  # masking runs (it was silently skipped before)


def test_sanitize_stack_ua_and_csp():
    e = ce.sanitize({"kind": "js", "msg": "boom", "stack": "Error: boom\n at f (/x.js:1:2)"}, "1.2.3.4", ua=UA)
    assert e["stack"] == "Error: boom\nat f (/x.js:1:2)" and e["ua"] == "Chrome · Linux"
    assert UA not in str(e)  # only the family is stored
    bare = ce.sanitize({"kind": "fetch", "msg": "HTTP 502"}, "1.2.3.4")
    assert "stack" not in bare and "ua" not in bare  # old entry shape when there is nothing to add
    csp = ce.sanitize({"kind": "csp", "msg": "Refused img-src: https://avatars.example.test", "url": "/p/web"}, "c")
    assert csp["kind"] == "csp" and "avatars.example.test" in csp["msg"]


def test_grouped_rows_counts_and_paging(tmp_path, monkeypatch):
    log = ce.ClientErrorLog(tmp_path / "ce.jsonl")
    for i, (k, msg, page) in enumerate([("js", "boom", "/a"), ("fetch", "HTTP 502", "/a"), ("js", "boom", "/b"),
                                        ("js", "boom", "/a"), ("csp", "Refused img-src", "/p/web")]):
        log.append({"ts": 10 + i, "kind": k, "msg": msg, "page": page, "url": "/u", "ua": "Chrome · Linux",
                    **({"stack": "at f (/x.js:1:2)"} if i == 2 else {})})
    g = log.grouped()
    assert [r["msg"] for r in g["rows"]] == ["Refused img-src", "boom", "HTTP 502"] and g["total"] == 3
    boom = g["rows"][1]
    assert boom["count"] == 3 and boom["pages"] == ["/a", "/b"] and (boom["first"], boom["last"]) == (10, 13)
    assert boom["stack"] == "at f (/x.js:1:2)" and boom["ua"] == "Chrome · Linux" and boom["sample"]["ts"] == 13
    assert g["counts"] == {"all": 5, "js": 3, "promise": 0, "htmx": 0, "fetch": 1, "csp": 1}
    js = log.grouped(kind="js")
    assert js["total"] == 1 and js["counts"]["all"] == 5  # counts ignore the kind filter
    assert log.grouped(text="web")["counts"]["all"] == 1 and log.grouped(text="WEB")["total"] == 1
    monkeypatch.setattr(ce, "PAGE_SIZE", 2)
    p2 = log.grouped(page=2)
    assert [r["msg"] for r in p2["rows"]] == ["HTTP 502"] and not p2["has_older"] and log.grouped()["has_older"]
    assert log.page(1, "js")["total"] == 3  # the raw view is unchanged
