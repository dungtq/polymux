"""Project settings routes: panel context, saves, links, guard and audit."""

import json
import re

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.projects import settings as pset
from pm.web.routes import projectsettings as routes

H = {"origin": "https://pm.example.test"}
CTX: dict = {}


class _Tpl:
    def render(self, **ctx):
        CTX.clear()
        CTX.update(ctx)
        return f"PANEL flash={ctx['flash']!r} hidden={sorted(ctx['ps'].hidden)} linked={ctx['linked']}"


def _get_template(name):
    return _Tpl() if name == "_project_settings.html" else routes_get_template(name)


routes_get_template = routes.get_template


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(routes, "get_template", _get_template)


def _tok(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/p/alpha").text).group(1)


def _audit(s):
    return [json.loads(x) for x in s.audit_file.read_text().splitlines()]


def _beta(s):
    (s.dev_root / "beta").mkdir()


def test_panel_context(env):
    s, c = env
    _beta(s)
    assert c.get("/p/alpha/settings/panel", follow_redirects=False).status_code in (303, 401, 403)
    login(s, c)
    r = c.get("/p/alpha/settings/panel")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert CTX["choices"] == ["beta"] and CTX["linked"] == [] and CTX["base"] == "/p/alpha"
    assert CTX["components"] == pset.COMPONENTS and CTX["pubkey"] == "" and CTX["csrf"]
    assert "git_html" in CTX and c.get("/p/nope/settings/panel").status_code == 404


def test_save_settings_and_audit(env):
    s, c = env
    login(s, c)
    tok = _tok(c)
    show = [x for x in pset.COMPONENTS if x not in ("todo", "git")]
    r = c.post("/p/alpha/settings", data={"csrf": tok, "show": show, "default_branch": "main"}, headers=H)
    assert r.status_code == 200 and "Saved." in r.text
    got = pset.load(s, "alpha")
    assert got.hidden == {"todo", "git"} and got.default_branch == "main"
    row = [a for a in _audit(s) if a["action"] == "project_settings" and a["result"] == "ok"][-1]
    assert row["detail"] == {"hidden": 2, "branch_set": 1, "vms": 0} and str(s.dev_root) not in json.dumps(row)
    r = c.post("/p/alpha/settings", data={"csrf": tok, "show": show, "vms": "lab-*\nweb?"}, headers=H)
    assert "Saved." in r.text and pset.load(s, "alpha").vms == ("lab-*", "web?")
    row = [a for a in _audit(s) if a["action"] == "project_settings" and a["result"] == "ok"][-1]
    assert row["detail"]["vms"] == 2 and "lab-" not in json.dumps(row)
    r = c.post("/p/alpha/settings", data={"csrf": tok, "show": show, "vms": "bad name"}, headers=H)
    assert "Refused" in r.text and pset.load(s, "alpha").vms == ("lab-*", "web?")
    r = c.post("/p/alpha/settings", data={"csrf": tok, "show": show, "default_branch": "../x"}, headers=H)
    assert "Refused" in r.text
    assert any(a["result"] == "denied" for a in _audit(s))


def test_save_links(env):
    s, c = env
    _beta(s)
    login(s, c)
    tok = _tok(c)
    r = c.post("/p/alpha/settings/links", data={"csrf": tok, "link": ["beta"]}, headers=H)
    assert "linked=['beta']" in r.text and pset.load(s, "alpha").links == ("beta",)
    r = c.post("/p/alpha/settings/links", data={"csrf": tok, "link": ["alpha"]}, headers=H)
    assert "Refused" in r.text and pset.load(s, "alpha").links == ("beta",)
    assert [a["detail"] for a in _audit(s) if a["action"] == "project_links" and a["result"] == "ok"] == [{"links": 1}]


def test_import_workspace(env):
    s, c = env
    _beta(s)
    (s.dev_root / "gamma").mkdir()
    login(s, c)
    tok = _tok(c)
    c.get("/p/alpha/settings/panel")
    assert CTX["workspaces"] == []  # no workspace file: the panel hides the import block
    (s.dev_root / "m.code-workspace").write_text(json.dumps(
        {"folders": [{"path": "alpha"}, {"path": "beta"}, {"path": "elsewhere"}, {"uri": "vscode-remote://h/x"}]}))
    c.get("/p/alpha/settings/panel")
    assert CTX["workspaces"] == [(CTX["workspaces"][0][0], "m.code-workspace (dev)")]
    wid = CTX["workspaces"][0][0]
    c.post("/p/alpha/settings/links", data={"csrf": tok, "link": ["gamma"]}, headers=H)
    r = c.post("/p/alpha/settings/links/import", data={"csrf": tok, "workspace": wid}, headers=H)
    assert r.status_code == 200
    assert CTX["flash"] == "Linked 1 project(s). 1 folder(s) are not pm projects."
    assert pset.load(s, "alpha").links == ("gamma", "beta")
    row = [a for a in _audit(s) if a["action"] == "project_links" and a["result"] == "ok"][-1]
    assert row["detail"] == {"linked": 1, "skipped": 1} and str(s.dev_root) not in json.dumps(row)
    c.post("/p/alpha/settings/links/import", data={"csrf": tok, "workspace": wid}, headers=H)
    assert CTX["flash"].startswith("Linked 0 project(s).") and pset.load(s, "alpha").links == ("gamma", "beta")


def test_import_workspace_refused(env):
    s, c = env
    _beta(s)
    (s.dev_root / "m.code-workspace").write_text(json.dumps({"folders": [{"path": "beta"}]}))
    (s.dev_root.parent / "x.code-workspace").write_text(json.dumps({"folders": [{"path": "dev/beta"}]}))
    login(s, c)
    tok = _tok(c)
    for bad in ("../x.code-workspace", str(s.dev_root.parent / "x.code-workspace"), "", "0123456789ab", "m.code-workspace"):
        r = c.post("/p/alpha/settings/links/import", data={"csrf": tok, "workspace": bad}, headers=H)
        assert r.status_code == 200 and CTX["flash"].startswith("Refused"), bad
    assert pset.load(s, "alpha").links == ()
    denied = [a for a in _audit(s) if a["action"] == "project_links" and a["result"] == "denied"]
    assert len(denied) == 5 and "x.code-workspace" not in json.dumps(denied)


def test_import_workspace_guard(env):
    s, c = env
    u = "/p/alpha/settings/links/import"
    assert c.post(u, data={}, headers=H).status_code in (401, 403, 303)
    login(s, c)
    tok = _tok(c)
    assert c.post(u, data={"workspace": "x"}, headers=H).status_code == 403
    assert c.post(u, data={"csrf": "bad"}, headers=H).status_code == 403
    assert c.post(u, data={"csrf": tok}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/p/nope/settings/links/import", data={"csrf": tok}, headers=H).status_code == 404
    assert pset.load(s, "alpha") == pset.ProjectSettings()


def test_oserror_flash(env, monkeypatch):
    s, c = env
    login(s, c)
    tok = _tok(c)

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(pset, "save_hidden", boom)
    r = c.post("/p/alpha/settings", data={"csrf": tok}, headers=H)
    assert "could not save settings" in r.text


def test_guard_and_fail_closed(env, monkeypatch):
    s, c = env
    u = "/p/alpha/settings"
    assert c.post(u, data={}, headers=H).status_code in (401, 403, 303)
    login(s, c)
    tok = _tok(c)
    assert c.post(u, data={"show": "git"}, headers=H).status_code == 403
    assert c.post(u, data={"csrf": "bad"}, headers=H).status_code == 403
    assert c.post(u, data={"csrf": tok}, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/p/nope/settings", data={"csrf": tok}, headers=H).status_code == 404
    assert pset.load(s, "alpha") == pset.ProjectSettings()
    from pm.security import audit as audit_mod

    def boom(self, entry):
        raise audit_mod.AuditError("full")

    monkeypatch.setattr(audit_mod.AuditLog, "write", boom)
    assert c.post(u, data={"csrf": tok}, headers=H).status_code == 503


async def _tabs(path, hidden):
    from pm.web.routes import pages

    return await pages._project_tabs(str(path), frozenset(hidden))


@pytest.mark.asyncio
async def test_hidden_docs_not_default_tab(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# Log\nchanged-marker\n")
    (tmp_path / "README.md").write_text("# Read\nreadme-marker\n")
    t = await _tabs(tmp_path, [])
    assert t["first_key"] == "changelog" and "changed-marker" in t["first_html"]
    t = await _tabs(tmp_path, ["changelog"])
    assert t["first_key"] == "readme" and "changed-marker" not in t["first_html"]
    t = await _tabs(tmp_path, ["changelog", "readme"])
    assert t["first_key"] == "" and t["first_html"] == ""


def test_save_accounts_route(env):
    import dataclasses

    s, c = env
    s2 = dataclasses.replace(s, accounts={"claude": ("work",)})
    c.app.state.settings = s2
    login(s, c)
    tok = _tok(c)
    c.get("/p/alpha/settings/panel")
    assert CTX["account_kinds"] == [("claude", ("work",), "", "")]
    r = c.post("/p/alpha/settings", data={"csrf": tok, "account_claude": "work"}, headers=H)
    assert "Saved." in r.text and pset.load(s2, "alpha").account("claude") == "work"
    row = [a for a in _audit(s) if a["action"] == "project_settings" and a["result"] == "ok"][-1]
    assert row["detail"]["accounts"] == "claude=work" and str(s.dev_root) not in json.dumps(row)
    r = c.post("/p/alpha/settings", data={"csrf": tok, "account_claude": "-"}, headers=H)
    assert "Saved." in r.text and pset.load(s2, "alpha").account("claude") == "-"
    c.post("/p/alpha/settings", data={"csrf": tok, "account_claude": "work"}, headers=H)
    r = c.post("/p/alpha/settings", data={"csrf": tok, "account_claude": "evil"}, headers=H)
    assert "Refused" in r.text and pset.load(s2, "alpha").account("claude") == "work"
    r = c.post("/p/alpha/settings", data={"csrf": tok, "account_codex": "x"}, headers=H)
    assert "Refused" in r.text
    r = c.post("/p/alpha/settings", data={"csrf": tok}, headers=H)  # no account field: kept
    assert pset.load(s2, "alpha").account("claude") == "work"
    r = c.post("/p/alpha/settings", data={"csrf": tok, "account_claude": ""}, headers=H)
    assert pset.load(s2, "alpha").account("claude") == ""


def test_panel_ui_added_account_and_global_default(env):
    from pm.core import accounts
    s, c = env
    login(s, c)
    accounts.add(s, "codex", "ui")
    accounts.set_default(s, "codex", "ui")
    tok = _tok(c)
    c.get("/p/alpha/settings/panel")
    assert CTX["account_kinds"] == [("codex", ("ui",), "", "ui")]
    r = c.post("/p/alpha/settings", data={"csrf": tok, "account_codex": "ui"}, headers=H)
    assert "Saved." in r.text and pset.load(s, "alpha").account("codex") == "ui"


def test_panel_no_accounts_context(env):
    s, c = env
    login(s, c)
    c.get("/p/alpha/settings/panel")
    assert CTX["account_kinds"] == []


@pytest.mark.parametrize("theme", ["classic", "dt"])
def test_fragment_login_account(theme):
    import jinja2

    from pm.web import themes

    e = jinja2.Environment(loader=jinja2.FileSystemLoader(themes.HERE / theme / "templates"), autoescape=True)
    e.globals["dt_project"] = lambda k: None
    ctx = dict(project=None, csrf="t", base="/p/a", components=pset.COMPONENTS, choices=[], linked=[],
               pubkey="", git_html="", flash="")
    tpl = e.get_template("_project_settings.html")
    off = tpl.render(ps=pset.ProjectSettings(), account_kinds=[], **ctx)
    assert "Login account" not in off and "account_claude" not in off
    on = tpl.render(ps=pset.ProjectSettings(accounts=(("claude", "work"),)),
                    account_kinds=[("claude", ("work", "alt"), "work", "alt")], **ctx)
    assert "Login account" in on and 'name="account_claude"' in on and "account_codex" not in on
    assert on.index("Global default (alt)") < on.index(">work<") and 'value="work" selected' in on and 'value="-"' in on
    assert "a running agent keeps its own" in on
