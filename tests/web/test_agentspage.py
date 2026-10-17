"""GET /agents/library/view (routes/agentspage.py): one file the library lists, read-only, as the markdown reader's
fragment; logged-in only; everything the inventory does not list is 404. Tmp home (fixtures of test_agentsedit)."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from .test_agentsedit import ORIGIN, app, client, home, w  # noqa: F401  (pytest fixtures)

URL = "/agents/library/view"


def test_view_a_skill_rendered(client):
    r = client.get(URL, params={"path": "~/.agents/skills/cfg"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.text.startswith('<div class="mdv" data-path="~/.agents/skills/cfg/SKILL.md"><div class="md">')
    assert "<p>body</p>" in r.text and '<code class="language-yaml">name: cfg' in r.text  # frontmatter: no <h2>
    assert "<h2>" not in r.text


def test_view_a_script_is_one_escaped_code_block(client, home):
    from pm.agents.inventory import read as agentsinv

    w(home / ".agents/hooks/stop.sh", "#!/bin/sh\necho '<b>x</b>' ```\n", 0o755)
    w(home / ".claude/settings.json", '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": '
                                      '"~/.agents/hooks/stop.sh"}]}]}}')
    agentsinv.clear_cache()
    r = client.get(URL, params={"path": "~/.agents/hooks/stop.sh"})
    assert r.status_code == 200, r.text
    assert "<pre><code>" in r.text and "&lt;b&gt;x&lt;/b&gt;" in r.text and "<b>x</b>" not in r.text


def test_view_refuses_what_the_library_does_not_list(client, home):
    w(home / ".ssh/id_ed25519", "PRIVATE\n")
    os.symlink(home / ".ssh", home / ".claude/skills/evil")
    for p in ("~/.ssh/id_ed25519", "~/.claude/skills/evil", "~/agents/../.ssh/id_ed25519", "/etc/passwd", ""):
        r = client.get(URL, params={"path": p})
        assert r.status_code == 404 and "PRIVATE" not in r.text, p


def test_view_needs_a_login(app):
    r = TestClient(app, base_url=ORIGIN).get(URL, params={"path": "~/.agents/skills/cfg"}, follow_redirects=False)
    assert r.status_code in (303, 401) and "body" not in r.text
