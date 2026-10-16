"""Git remotes: gitctl validators/_check_args for `remote`, panel routes, guard, audit."""

import json
import re
import subprocess
from dataclasses import replace

import pytest
from helpers import login
from helpers import pages_env as env  # noqa: F401  (fixture)

from pm.core.models import Tier
from pm.git import ctl as gitctl

GH = ("git.example.test",)  # allowed git hosts (the default is none)

H = {"origin": "https://pm.example.test"}
GOOD = ["git@git.example.test:dt/pm.git", "ssh://git@git.example.test/dt/pm.git", "ssh://git@git.example.test:2222/dt/pm",
        "git@GIT.example.test:dt/pm", "git@git.example.test:org/sub/repo.git"]
BAD_URLS = [
    "https://git.example.test/dt/pm.git", "http://git.example.test/dt/pm", "file:///etc/passwd", "ext::sh -c id",
    "-oProxyCommand=id", "--upload-pack=id", "git@git.example.test:dt/pm.git\nfoo", "git@git.example.test:dt/pm.git ",
    " git@git.example.test:dt/pm.git", "git@git.example.test:../etc", "git@git.example.test:dt/../x", "git@git.example.test:-x",
    "ssh://git@git.example.test:99999/dt/pm", "ssh://git@git.example.test:0/dt/pm", "ssh://user@git.example.test/dt/pm",
    "git@-oProxyCommand=x:dt/pm", "ssh://git@git.example.test/dt/pm;id", "git@git.example.test:dt/pm$(id)",
    "git@git.example.test:dt/pm`id`", "git@git.example.test:d t/pm", "git@git.example.test:dt/pm|x", "git@git.example.tést:dt/pm",
    "git@git.example.test:dt/pm\x00", "", "git@:dt/pm", "ssh://git@/dt/pm", "git@git.example.test:", "git@git.example.test:dt/x.lock",
    "git@git.example.test:" + "a" * 500, "ssh://git@git.example.test/dt/pm?x=1", "ssh://git@git.example.test/dt/pm#x",
    "ssh://git@git.example.test:22:/dt/pm", "git@evil.com#git.example.test:a/b", "ssh://git@[::1]/dt/pm", "git@git.example.test:dt/pm x",
]
NOT_ALLOWED_HOST = ["git@github.com:a/b.git", "ssh://git@evil.example/a/b", "git@git.example.test.evil.com:a/b",
                    "git@10.0.0.1:a/b"]


@pytest.mark.parametrize("u", GOOD)
def test_good_urls(u):
    assert gitctl.check_remote_url(u, GH) == u


@pytest.mark.parametrize("u", BAD_URLS)
def test_bad_urls(u):
    with pytest.raises(gitctl.GitDenied):
        gitctl.check_remote_url(u, GH)


@pytest.mark.parametrize("u", NOT_ALLOWED_HOST)
def test_host_allow_list(u):
    with pytest.raises(gitctl.GitDenied, match="allowed"):
        gitctl.check_remote_url(u, GH)
    assert gitctl.check_remote_url(u, ("git.example.test", gitctl.remote_url_host(u))) == u  # configurable


@pytest.mark.parametrize("n", ["gitdev", "origin", "a.b-c_d", "A1", "x" * 32])
def test_good_names(n):
    assert gitctl.check_remote_name(n) == n


@pytest.mark.parametrize("n", ["", "-v", "--upload-pack=x", "a b", "a/b", "a;b", "x" * 33, "a\n", ".x", "a..b",
                               "a.lock", "é", "$(id)", "a:b", "--"])
def test_bad_names(n):
    with pytest.raises(gitctl.GitDenied):
        gitctl.check_remote_name(n)


def test_check_args_remote_forms():
    ok = [["remote"], ["remote", "-v"], ["remote", "get-url", "gitdev"],
          ["remote", "add", "--", "x", "git@git.example.test:a/b"], ["remote", "set-url", "--", "x", "git@git.example.test:a/b"],
          ["remote", "remove", "--", "x"]]
    for a in ok:
        gitctl._check_args(a, GH)
    bad = [["remote", "add", "x", "git@git.example.test:a/b"],  # no --
           ["remote", "add", "--", "x", "https://git.example.test/a/b"],
           ["remote", "add", "--", "x", "git@github.com:a/b"],
           ["remote", "add", "--", "-x", "git@git.example.test:a/b"],
           ["remote", "add", "--", "x", "git@git.example.test:a/b", "--extra"],
           ["remote", "add", "-f", "--", "x", "git@git.example.test:a/b"],
           ["remote", "update"], ["remote", "rename", "a", "b"], ["remote", "prune", "origin"],
           ["remote", "set-head", "origin", "x"], ["remote", "set-branches", "o", "x"],
           ["remote", "remove", "x"], ["remote", "remove", "--", "-v"], ["remote", "remove", "--", "x", "y"],
           ["remote", "get-url", "--all", "x"], ["remote", "-v", "x"], ["remote", "show", "origin"],
           ["remote", "set-url", "--push", "--", "x", "git@git.example.test:a/b"]]
    for a in bad:
        with pytest.raises(gitctl.GitDenied):
            gitctl._check_args(a, GH)


def test_push_rule_unchanged():
    with pytest.raises(gitctl.GitDenied):
        gitctl._check_args(["push", "origin", "main"])
    gitctl._check_args(["push", "gitdev", "main"])


def _git(*a, cwd):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def _repo(s):
    p = s.dev_root / "alpha"
    _git("init", "-q", "-b", "main", cwd=p)
    return p


def _remotes(p):
    return subprocess.run(["git", "remote", "-v"], cwd=p, capture_output=True, text=True).stdout


def test_gitctl_add_set_remove(env):
    s, _ = env
    p = _repo(s)
    gitctl.remote_add(p, "gitdev", "git@git.example.test:dt/pm.git", allowed_hosts=GH)
    with pytest.raises(gitctl.GitError, match="exists"):
        gitctl.remote_add(p, "gitdev", "git@git.example.test:dt/other.git", allowed_hosts=GH)
    gitctl.remote_set_url(p, "gitdev", "ssh://git@git.example.test/dt/x.git", allowed_hosts=GH)
    assert gitctl.remotes(p) == [gitctl.RemoteInfo("gitdev", "ssh://git@git.example.test/dt/x.git", "ssh://git@git.example.test/dt/x.git")]
    with pytest.raises(gitctl.GitDenied):
        gitctl.remote_set_url(p, "gitdev", "https://git.example.test/x", allowed_hosts=GH)
    with pytest.raises(gitctl.GitError, match="no such"):
        gitctl.remote_remove(p, "nope")
    gitctl.remote_remove(p, "gitdev")
    assert gitctl.remotes(p) == []


def _tok(c):
    return re.search(r'name="csrf" value="([^"]+)"', c.get("/p/alpha").text).group(1)


def _audit(s):
    return [json.loads(x) for x in s.audit_file.read_text().splitlines()]


def _post(c, act, tok, **f):
    return c.post(f"/p/alpha/git/remote/{act}", data={"csrf": tok, **f}, headers=H)


def test_panel_routes_flow(env):
    s, c = env
    p = _repo(s)
    login(s, c)
    page = c.get("/p/alpha").text
    assert 'href="/p/alpha/git/panel"' in page and "Git remotes</h2>" not in page and "Recent commits" not in page
    tok = _tok(c)
    t = c.get("/p/alpha/git/remotes").text
    assert "No remotes." in t and "No pm ssh key yet" in t and "/settings#ssh" in t
    r = _post(c, "add", tok, name="gitdev", url="git@git.example.test:dt/pm.git")
    assert r.status_code == 200 and "Add remote ok" in r.text and "git@git.example.test:dt/pm.git" in r.text
    assert "gitdev\t" in _remotes(p)
    r = _post(c, "set-url", tok, name="gitdev", url="ssh://git@git.example.test/dt/pm2.git")
    assert "Set url ok" in r.text and "dt/pm2.git" in _remotes(p)
    r = _post(c, "add", tok, name="up", url="git@github.com:a/b.git")  # host not allowed
    assert "Refused" in r.text and "up\t" not in _remotes(p)
    r = _post(c, "add", tok, name="up", url="git@git.example.test:dt/pm.git\n--upload-pack=x")
    assert "Refused" in r.text and "up\t" not in _remotes(p)
    r = _post(c, "add", tok, name="-oProxy", url="git@git.example.test:dt/pm.git")
    assert "Refused" in r.text
    r = _post(c, "add", tok, name="gitdev", url="git@git.example.test:dt/pm.git")
    assert "failed" in r.text and "dt/pm2.git" in _remotes(p)
    r = _post(c, "add", tok, name="mirror", url="git@git.example.test:dt/m.git")
    assert "mirror\t" in _remotes(p)
    r = _post(c, "remove", tok, name="mirror")
    assert "Remove remote ok" in r.text and "mirror\t" not in _remotes(p)
    # gitdev removal needs the typed confirm
    r = _post(c, "remove", tok, name="gitdev")
    assert "typed confirmation" in r.text and "gitdev\t" in _remotes(p)
    r = _post(c, "remove", tok, name="gitdev", confirm="nope")
    assert "typed confirmation" in r.text and "gitdev\t" in _remotes(p)
    r = _post(c, "remove", tok, name="gitdev", confirm="gitdev")
    assert "Remove remote ok" in r.text and _remotes(p) == ""
    rows = [(a["action"], a["result"]) for a in _audit(s) if a["action"].startswith("git_remote")]
    assert ("git_remote_add", "ok") in rows and ("git_remote_set_url", "ok") in rows
    assert ("git_remote_remove", "ok") in rows and ("git_remote_remove", "denied") in rows
    text = s.audit_file.read_text()
    assert "dt/pm" not in text and "github" not in text  # no urls in the audit, host only
    ok_add = [a for a in _audit(s) if a["action"] == "git_remote_add" and a["result"] == "ok"][0]
    assert ok_add["detail"] == {"remote": "gitdev", "url_host": "git.example.test"}


def test_git_tab_panel_and_page_layout(env):
    s, c = env
    _repo(s)
    login(s, c)
    page = c.get("/p/alpha").text
    # plugin link rows sit after the docs panel (inside the aside, below the tab body)
    assert page.index('id="pj-body"') < page.index('class="pj-after') < page.index("</aside>", page.index("pj-right"))
    assert 'data-pj="git"' in page and 'data-pj="dirty"' not in page
    tok = _tok(c)
    _post(c, "add", tok, name="mirror", url="git@git.example.test:dt/m.git")
    _post(c, "add", tok, name="gitdev", url="git@git.example.test:dt/pm.git")
    h = c.get("/p/alpha/git/panel").text
    assert 'data-tab="history"' in h and 'data-tab="config"' in h and "data-keep" in h
    assert 'class="tabs pj-subtabs tabs-line" role="tablist" aria-label="Git" data-tabs-key="pm.pjgit.sub"' in h
    assert "Last 0 commits" in h and "No commits." in h
    assert h.index("gitdev") < h.index("mirror") and ">default<" in h  # default remote first, marked
    assert 'data-remote-typed="gitdev"' in h and "Add remote" in h
    assert 'action="/settings/ssh/keygen"' in h and "Generate key" in h and "/settings#ssh" in h
    assert 'data-gr-refresh="/p/alpha/git/remotes"' in h
    assert c.get("/p/nope/git/panel").status_code == 404


def test_panel_shows_managed_pubkey(env):
    s, c = env
    _repo(s)
    login(s, c)
    from pm.infra import sshkeys
    d = sshkeys.ssh_dir(s)
    d.mkdir(parents=True)
    line = "ssh-ed25519 " + "A" * 68 + " pm@h"
    (d / "id_ed25519_pm.pub").write_text(line + "\n")
    t = c.get("/p/alpha/git/remotes").text
    assert line in t and "data-copy-text" in t and 'id="gr-pubkey"' in t and "SHA256:" in t and "Generate key" not in t


def test_remote_guard_rejections(env):
    s, c = env
    p = _repo(s)
    for act in ("add", "set-url", "remove"):
        assert c.post(f"/p/alpha/git/remote/{act}", data={"csrf": "x", "name": "a", "url": "git@git.example.test:a/b"},
                      headers=H).status_code == 401
    login(s, c)
    tok = _tok(c)
    f = {"name": "x", "url": "git@git.example.test:a/b"}
    for act in ("add", "set-url", "remove"):
        u = f"/p/alpha/git/remote/{act}"
        assert c.post(u, data={"csrf": tok, **f}, headers={"origin": "https://evil.example"}).status_code == 403
        assert c.post(u, data={"csrf": "bad", **f}, headers=H).status_code == 403
        assert c.post(u, data=f, headers=H).status_code == 403
    assert c.post("/p/nope/git/remote/add", data={"csrf": tok, **f}, headers=H).status_code == 404
    assert c.get("/p/nope/git/remotes").status_code == 404
    c.app.state.settings = replace(s, tier_ceiling=Tier.VIEW)
    c.app.state.settings.extras.update(s.extras)
    assert c.post("/p/alpha/git/remote/add", data={"csrf": tok, **f}, headers=H).status_code == 403
    assert _remotes(p) == ""


def test_remote_audit_failure_fails_closed(env, monkeypatch):
    s, c = env
    p = _repo(s)
    login(s, c)
    tok = _tok(c)
    from pm.security import audit as audit_mod

    def boom(self, entry):
        raise audit_mod.AuditError("full")

    monkeypatch.setattr(audit_mod.AuditLog, "write", boom)
    r = _post(c, "add", tok, name="x", url="git@git.example.test:a/b")
    assert r.status_code == 503 and _remotes(p) == ""


def test_never_touches_forbidden_repos(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for n in (".cfg", ".agents-git"):
        (tmp_path / n).mkdir()
    with pytest.raises(gitctl.GitDenied):
        gitctl.remote_add(tmp_path / ".cfg", "gitdev", "git@git.example.test:a/b", allowed_hosts=GH)


def test_show_docs_button_sits_in_the_path_row(env):
    s, c = env
    _repo(s)
    login(s, c)
    h = c.get("/p/alpha").text
    row = h[h.index('class="small muted ico-dir pj-pathrow"'):]
    row = row[:row.index("</p>")]
    assert "data-pj-show" in row and h.index("pj-pathrow") < h.index('class="actions"')
