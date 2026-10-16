"""pm never uses the browser's native popups. Use the in-page modal (static/js/dialog.js:
pmConfirm / pmPrompt, or hx-confirm / data-confirm, which dialog.js turns into the modal).
Rule: docs/UI-RULES.md, docs/UI-CONDUCT.md, AGENTS.md (Hard rules)."""
import re
from pathlib import Path

from pm.web.render import TEMPLATE_DIR

ROOT = Path(__file__).resolve().parents[1] / "src" / "pm"
POPUP = re.compile(r"(?<![\w.$])(?:window\s*\.\s*)?(?:alert|confirm|prompt)\s*\(|window\s*\.\s*open\s*\(|\bonbeforeunload\b")
# The canvas keeps one addEventListener('beforeunload') guard: the browser owns that text and offers no in-page alternative.


def _files():
    for base in (TEMPLATE_DIR, ROOT / "static"):
        found = [p for p in base.rglob("*") if p.suffix in (".html", ".js")]
        assert found, f"no .html/.js files under {base}: the scan would pass on nothing"
        for p in found:
            rel = p.relative_to(ROOT).as_posix()
            if p.suffix in (".html", ".js") and not rel.startswith("static/vendor/"):
                yield rel, p


def test_no_native_browser_popups():
    bad = []
    for rel, p in _files():
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if POPUP.search(line):
                bad.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not bad, (
        "Native browser popups (alert/confirm/prompt/window.open) are forbidden. Use the in-page modal: "
        "window.pmConfirm({title, body, choices}) / window.pmPrompt({title, label}) from static/js/dialog.js, "
        "or hx-confirm=\"...\" / data-confirm=\"...\" on the form (dialog.js shows the modal).\n" + "\n".join(bad))


def test_dialog_helper_is_loaded_everywhere():
    base = (TEMPLATE_DIR / "base.html").read_text()
    assert "/static/js/dialog.js" in base
    js = (ROOT / "static/js/dialog.js").read_text()
    for needle in ("window.pmDialog", "window.pmConfirm", "window.pmPrompt", "htmx:confirm", "data-confirm"):
        assert needle in js
