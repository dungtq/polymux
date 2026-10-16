"""Backlog lists/counts only main checkouts, not linked git worktrees."""


from helpers import ORIGIN, make_client, make_settings

from pm.core.models import Tier
from pm.projects import projects

MD = "## Inbox\n\n- [ ] a1\n- [ ] a2\n"


def test_is_linked_worktree(tmp_path):
    (tmp_path / "main" / ".git").mkdir(parents=True)
    assert not projects.is_linked_worktree(tmp_path / "main")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {tmp_path}/main/.git/worktrees/wt\n")
    assert projects.is_linked_worktree(wt)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".git").write_text(f"gitdir: {tmp_path}/main/.git/modules/sub\n")
    assert not projects.is_linked_worktree(sub)
    assert not projects.is_linked_worktree(tmp_path / "nogit")


def test_backlog_skips_worktrees(fake_tmux, tmp_path):
    dev = tmp_path / "dev"
    (dev / "main" / ".git").mkdir(parents=True)
    (dev / "main-wt-x").mkdir()
    (dev / "main-wt-x" / ".git").write_text(f"gitdir: {dev}/main/.git/worktrees/x\n")
    for d in ("main", "main-wt-x"):
        (dev / d / "BACKLOG.md").write_text(MD)
    s = make_settings(fake_tmux, tmp_path, state="st", tier_ceiling=Tier.SAFE, disable_poll=True)
    c = make_client(s, base_url=ORIGIN)
    c.post("/login", data={"token": s.token_file.read_text().strip()}, follow_redirects=False)
    html = c.get("/backlog").text
    assert "2 open in 1 project" in html
    assert 'value="main"' in html and "main-wt-x" not in html
    # Projects list is unchanged (worktree still a project)
    assert [p.name for p in projects.discover(s)] == ["main", "main-wt-x"]
