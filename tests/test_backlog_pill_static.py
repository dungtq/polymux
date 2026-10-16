"""backlog.js swap() refreshes the project tab pills (Backlog, TODO), not only the Open/Done segment counts."""
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

PM = Path(__file__).resolve().parent.parent / "src/pm"


def test_swap_updates_tab_pills():
    js = (PM / "static/js/backlog.js").read_text()
    assert "pj-p-backlog" in js and "pj-p-todo" in js
    assert '.pj-tab[data-pj-pane="' in js and ".pj-n" in js
    tpl = (TEMPLATE_DIR / "project.html").read_text()
    assert 'data-pj-pane="pj-p-backlog"' in tpl and "pj-n" in tpl
    assert len(js.splitlines()) < 500
