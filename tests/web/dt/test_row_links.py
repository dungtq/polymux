"""dt table rows with a link (tb.row href=...) give their name cell the same link as the row's own link
(tb.name(..., href=..., row_link=true), PARTS.md §6.4, §7 "Mouse"), so ⌘ / Ctrl / Shift and middle click open the row
in a new tab or window (#247): a div row cannot be opened that way."""

from __future__ import annotations

import re

from pm.web import themes

TPL = themes.HERE / "dt" / "templates"
# rows whose ↵ opens something in the page (project.js: the markdown reader, a skill sheet); not converted yet
NOT_YET = {"_project_skills.html", "project/_plans.html"}


def _rows_with_href():
    for f in sorted(TPL.rglob("*.html")):
        rel = f.relative_to(TPL).as_posix()
        if rel == "parts/_table.html":
            continue
        s = f.read_text(encoding="utf-8")
        for m in re.finditer(r"tb\.row\(", s):
            end = s.find("%}", m.start())
            if "href=" not in s[m.start():end]:
                continue
            nxt = s.find("tb.row(", m.end())
            name = re.search(r"tb\.name\([^%]*", s[end:nxt if nxt >= 0 else len(s)])
            yield rel, name.group(0) if name else None


def test_rows_with_a_link_make_their_name_the_row_link():
    seen = set()
    for rel, name in _rows_with_href():
        seen.add(rel)
        if rel in NOT_YET or name is None:
            continue
        assert "href=" in name and "row_link=true" in name, (rel, name)
    assert {"_projects_list.html", "_agents_installed.html", "parts/_agentrow.html", "_agents_usage.html"} <= seen

