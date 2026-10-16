"""Phase 3 move: ``__file__``-relative paths in moved modules still point at the right place."""
from pathlib import Path

import pm
from pm.infra import servers_store
from pm.notify import reloadneed

SRC_PM = Path(pm.__file__).resolve().parent
REPO = SRC_PM.parents[1]


def test_reloadneed_watches_the_pm_package():
    assert reloadneed.PKG == SRC_PM
    assert (reloadneed.PKG / "static").is_dir() and (reloadneed.PKG / "web/themes/classic/templates").is_dir()


def test_askpass_helper_is_the_repo_tools_script():
    assert servers_store.ASKPASS == REPO / "tools" / "pm-askpass"
    assert servers_store.ASKPASS.is_file()


def test_templates_and_static_dirs_resolve():
    from pm.web import render
    assert render.HERE == SRC_PM
    assert render.TEMPLATE_DIR == SRC_PM / "web/themes/classic/templates"
    assert (render.TEMPLATE_DIR / "base.html").is_file()


def test_flat_modules_are_gone():
    assert not (SRC_PM / "chat_sources").exists()
    flat = sorted(p.name for p in SRC_PM.glob("*.py") if p.name != "__init__.py")
    assert flat == []
