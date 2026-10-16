import html
import json
import re
from pathlib import Path

from helpers import chat_env as env  # noqa: F401  (fixture)
from helpers import login

from pm.web.render import TEMPLATE_DIR


def _remembered(t):
    raw = re.search(r'id="launch-data" hidden data-json="([^"]*)"', t).group(1)
    return json.loads(html.unescape(raw))["remembered"]


def test_review_prefill_page(env):  # noqa: F811
    s, c = env
    login(s, c)
    t = c.get("/p/alpha/launch?review=claude-12").text
    assert "Prompt preview" in t and "DO NOT edit" in t
    r = _remembered(t)
    assert r["kind"] == "claude" and r["mode"] == "plan" and "DO NOT edit" in r["prompt"]
    assert "bypass" not in r["mode"] and not r.get("extra")


def test_review_unknown_agent_is_plain_launch(env):  # noqa: F811
    s, c = env
    login(s, c)
    t = c.get("/p/alpha/launch?review=claude-zzzzzz").text
    assert "DO NOT edit" not in t and "prompt" not in _remembered(t)
    assert c.get("/p/alpha/launch?review=../x").status_code == 200


def test_review_link_markup(env):  # noqa: F811
    s, c = env
    login(s, c)
    t = c.get("/a/claude-12").text
    assert "launch?review=" not in t and ">Review</a>" not in t  # moved to the ntfy rows
    assert 'class="button small bld-link"' in t
    ntfy = (TEMPLATE_DIR / "_ntfy.html").read_text()
    assert "launch?review={{ r.owner.agent_id|urlencode }}" in ntfy and "r.owner and r.proj_name" in ntfy
