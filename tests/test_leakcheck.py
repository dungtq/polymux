"""tools/leakcheck.sh: staged, push and tree scans, the pattern file and hook install."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "leakcheck.sh"
PUBLIC = ROOT / "tools" / "leak-public.txt"
ZERO = "0" * 40


def _env(tmp: Path, patterns: Path | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("PM_PRIVATE_PATTERNS", None)
    env.update(HOME=str(tmp / "home"), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    if patterns is not None:
        env["PM_PRIVATE_PATTERNS"] = str(patterns)
    return env


def _git(repo: Path, env: dict[str, str], *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, timeout=30,
                          check=check)


def _leak(repo: Path, env: dict[str, str], *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([str(TOOL), *args], cwd=repo, env=env, input=stdin, capture_output=True, text=True,
                          timeout=60, check=False)


def _repo(tmp: Path, patterns: str | None = "\\bsecrethost\\b\nsrc:\\binternalbox\\b\npath:(^|/)\\.env$\n"):
    """A fresh repo plus an env whose PM_PRIVATE_PATTERNS points at a tmp pattern file."""
    pat = None
    if patterns is not None:
        pat = tmp / "patterns.txt"
        pat.write_text("# private set\n\n" + patterns)
    env = _env(tmp, pat)
    repo = tmp / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], env=env, check=True, timeout=30)
    return repo, env


def _stage(repo: Path, env: dict[str, str], files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(repo, env, "add", "--", *files)


def test_staged_clean_passes(tmp_path):
    repo, env = _repo(tmp_path)
    _stage(repo, env, {"a.txt": "nothing to see\n"})
    r = _leak(repo, env, "--staged")
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""


def test_staged_hit_blocked_and_masked(tmp_path):
    repo, env = _repo(tmp_path)
    _stage(repo, env, {"a.txt": "one\ntwo\n"})
    _git(repo, env, "commit", "-qm", "base")
    _stage(repo, env, {"a.txt": "one\ntwo\nssh secrethost now\n"})
    r = _leak(repo, env, "--staged")
    assert r.returncode == 1
    assert "leakcheck: a.txt:3: pattern #1: ssh *** now" in r.stderr
    assert "secrethost" not in r.stderr + r.stdout
    assert "private plugin" in r.stderr


def test_staged_path_pattern_blocks_env_file(tmp_path):
    repo, env = _repo(tmp_path)
    _stage(repo, env, {"sub/.env": ""})
    r = _leak(repo, env, "--staged")
    assert r.returncode == 1
    assert "leakcheck: sub***: path pattern #3" in r.stderr


def test_src_pattern_ignored_under_tests(tmp_path):
    repo, env = _repo(tmp_path)
    _stage(repo, env, {"tests/test_x.py": "host = 'internalbox'\n"})
    assert _leak(repo, env, "--staged").returncode == 0
    _stage(repo, env, {"src/x.py": "host = 'internalbox'\n"})
    r = _leak(repo, env, "--staged")
    assert r.returncode == 1
    assert "src/x.py:1: pattern #2" in r.stderr and "tests/" not in r.stderr


def test_missing_pattern_file_warns_and_passes(tmp_path):
    repo, env = _repo(tmp_path, patterns=None)
    _stage(repo, env, {"a.txt": "secrethost\n"})
    r = _leak(repo, env, "--staged")
    assert r.returncode == 0
    assert "no pattern file" in r.stderr and "skipped" in r.stderr
    r = _leak(repo, env, "--tree", "--patterns", str(tmp_path / "nope.txt"))
    assert r.returncode == 2


def _pushed_base(tmp_path):
    repo, env = _repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], env=env, check=True, timeout=30)
    _git(repo, env, "remote", "add", "origin", str(remote))
    _stage(repo, env, {"a.txt": "clean\n"})
    _git(repo, env, "commit", "-qm", "base")
    _git(repo, env, "push", "-q", "origin", "main")
    return repo, env, remote


def _commit(repo: Path, env: dict[str, str], files: dict[str, str]) -> str:
    _stage(repo, env, files)
    _git(repo, env, "commit", "-qm", "c")
    return _git(repo, env, "rev-parse", "HEAD").stdout.strip()


def _push_line(repo: Path, env: dict[str, str], sha: str) -> str:
    remote_sha = _git(repo, env, "rev-parse", "origin/main").stdout.strip()
    return f"refs/heads/main {sha} refs/heads/main {remote_sha}\n"


def test_push_blocks_unpushed_hit(tmp_path):
    repo, env, remote = _pushed_base(tmp_path)
    sha = _commit(repo, env, {"b.txt": "x\nsecrethost\n"})
    r = _leak(repo, env, "--push", "origin", str(remote), stdin=_push_line(repo, env, sha))
    assert r.returncode == 1
    assert f"leakcheck: {sha[:10]} b.txt:2: pattern #1" in r.stderr
    assert "secrethost" not in r.stderr
    # a push to the URL (no remote name) still excludes the remote sha from the range
    r = _leak(repo, env, "--push", str(remote), str(remote), stdin=_push_line(repo, env, sha))
    assert r.returncode == 1
    # deleting a ref scans nothing
    r = _leak(repo, env, "--push", "origin", str(remote), stdin=f"(delete) {ZERO} refs/heads/x {sha}\n")
    assert r.returncode == 0, r.stderr


def test_push_scans_lines_a_merge_adds(tmp_path):
    """A conflict resolution (evil merge) has no plain diff; --cc shows the lines the merge itself adds."""
    repo, env, remote = _pushed_base(tmp_path)
    _commit(repo, env, {"a.txt": "a\nb\nc\n"})
    _git(repo, env, "push", "-q", "origin", "main")
    _git(repo, env, "checkout", "-qb", "x")
    _commit(repo, env, {"a.txt": "a\nX\nc\n"})
    _git(repo, env, "checkout", "-q", "main")
    _commit(repo, env, {"a.txt": "a\nM\nc\n"})
    _git(repo, env, "merge", "-q", "x", check=False)  # conflict
    sha = _commit(repo, env, {"a.txt": "a\nsecrethost in the fix\nc\n"})
    r = _leak(repo, env, "--push", "origin", str(remote), stdin=_push_line(repo, env, sha))
    assert r.returncode == 1
    assert f"leakcheck: {sha[:10]} a.txt:2: pattern #1: *** in the fix" in r.stderr


def test_push_clean_and_already_pushed_hit(tmp_path):
    repo, env, remote = _pushed_base(tmp_path)
    sha = _commit(repo, env, {"b.txt": "fine\n"})
    assert _leak(repo, env, "--push", "origin", str(remote), stdin=_push_line(repo, env, sha)).returncode == 0
    _commit(repo, env, {"c.txt": "secrethost\n"})
    _git(repo, env, "push", "-q", "origin", "main")  # the hit is on the remote now
    sha = _commit(repo, env, {"d.txt": "fine too\n"})
    r = _leak(repo, env, "--push", "origin", str(remote), stdin=_push_line(repo, env, sha))
    assert r.returncode == 0, r.stderr


def test_tree_public_patterns_on_repo_are_green(tmp_path):
    r = _leak(ROOT, _env(tmp_path, None), "--tree", "--patterns", str(PUBLIC))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "leakcheck: OK" in r.stdout


def test_tree_reports_hits_and_skips_pattern_file(tmp_path):
    repo, env = _repo(tmp_path)
    _stage(repo, env, {"pats.txt": "secrethost\n", "a.md": "see secrethost\n", "tests/t.py": "internalbox\n"})
    r = _leak(repo, env, "--tree", "--patterns", str(repo / "pats.txt"))
    assert r.returncode == 1
    # a pattern file inside the repo is public: the pattern may be printed, the file itself is not scanned
    assert "leakcheck: a.md:1: pattern #1 (secrethost): see ***" in r.stderr
    assert "pats.txt:" not in r.stderr


def _install(repo: Path, env: dict[str, str], *extra: str) -> subprocess.CompletedProcess:
    return _leak(repo, env, "--install", *extra)


def test_install_writes_hooks_and_blocks_commit(tmp_path):
    repo, env = _repo(tmp_path)
    r = _install(repo, env)
    assert r.returncode == 0, r.stderr
    hooks = repo / ".git" / "hooks"
    assert "# git hooks chain runner (pre-commit.d)\n" in (hooks / "pre-commit").read_text()
    assert (hooks / "pre-commit.d" / "leakcheck").stat().st_mode & 0o111
    assert (hooks / "leakcheck.sh").stat().st_mode & 0o111
    assert "# leakcheck pre-push hook" in (hooks / "pre-push").read_text()
    _stage(repo, env, {"a.txt": "secrethost\n"})
    c = _git(repo, env, "commit", "-qm", "leak", check=False)
    assert c.returncode != 0
    assert "a.txt:1: pattern #1" in c.stderr and "secrethost" not in c.stderr
    _stage(repo, env, {"a.txt": "clean\n"})
    assert _git(repo, env, "commit", "-qm", "ok", check=False).returncode == 0
    assert _install(repo, env).returncode == 0  # a second install keeps the chain runner


def test_install_keeps_foreign_hooks_unless_forced(tmp_path):
    repo, env = _repo(tmp_path)
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-push").write_text("#!/bin/sh\necho mine\n")
    r = _install(repo, env)
    assert r.returncode == 2
    assert "refused" in r.stderr
    assert (hooks / "pre-push").read_text() == "#!/bin/sh\necho mine\n"
    assert not (hooks / "leakcheck.sh").exists()
    r = _install(repo, env, "--force")
    assert r.returncode == 0, r.stderr
    assert (hooks / "pre-push.leakcheck-old").read_text() == "#!/bin/sh\necho mine\n"
    assert "# leakcheck pre-push hook" in (hooks / "pre-push").read_text()


def test_forced_install_keeps_the_old_pre_commit_running(tmp_path):
    repo, env = _repo(tmp_path)
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\necho old-hook-ran >&2\n")
    (hooks / "pre-commit").chmod(0o755)
    assert _install(repo, env).returncode == 2
    assert _install(repo, env, "--force").returncode == 0
    assert (hooks / "pre-commit.d" / "00-previous").read_text() == "#!/bin/sh\necho old-hook-ran >&2\n"
    _stage(repo, env, {"a.txt": "clean\n"})
    c = _git(repo, env, "commit", "-qm", "ok", check=False)
    assert c.returncode == 0 and "old-hook-ran" in c.stderr


def test_private_values_in_paths_are_masked(tmp_path):
    repo, env = _repo(tmp_path, "\\bsecrethost\\b\npath:secretdir/\n")
    _stage(repo, env, {"secrethost-notes.md": "about secrethost\n", "secretdir/x.txt": "x\n"})
    r = _leak(repo, env, "--staged")
    assert r.returncode == 1
    assert "***-notes.md:1: pattern #1: about ***" in r.stderr
    assert "***x.txt: path pattern #2" in r.stderr
    assert "secrethost" not in r.stderr and "secretdir" not in r.stderr


def test_ignored_pattern_file_in_repo_is_not_shown(tmp_path):
    repo, env = _repo(tmp_path, None)
    (repo / ".gitignore").write_text("priv.txt\n")
    (repo / "priv.txt").write_text("\\bsecrethost\\b\n")
    env["PM_PRIVATE_PATTERNS"] = str(repo / "priv.txt")
    _stage(repo, env, {"a.txt": "secrethost\n"})
    r = _leak(repo, env, "--staged")
    assert r.returncode == 1
    assert "a.txt:1: pattern #1: ***" in r.stderr and "secrethost" not in r.stderr


def test_install_leaves_pm_tracker_hook(tmp_path):
    repo, env = _repo(tmp_path)
    pc = repo / ".git" / "hooks" / "pre-commit"
    pc.write_text("#!/bin/sh\n# pm tracker hook\nexit 0\n")
    r = _install(repo, env)
    assert r.returncode == 0
    assert "pmctl hooks install" in r.stderr
    assert pc.read_text() == "#!/bin/sh\n# pm tracker hook\nexit 0\n"


def test_install_refuses_hookspath_outside_repo(tmp_path):
    repo, env = _repo(tmp_path)
    _git(repo, env, "config", "core.hooksPath", str(tmp_path / "shared-hooks"))
    r = _install(repo, env)
    assert r.returncode == 2
    assert "outside this repo" in r.stderr
    assert not (tmp_path / "shared-hooks").exists()
