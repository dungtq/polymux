"""Temp git repos for the pm.git tests: plain ``git`` calls (never through pm), commits, a diverged branch,
a commit on the bare gitdev remote."""

import subprocess


def g(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check, capture_output=True, text=True)


def head(r):
    return g(r, "rev-parse", "HEAD").stdout.strip()


def commit_file(r, name, text, msg):
    (r / name).write_text(text)
    g(r, "add", "--", name)
    g(r, "commit", "-q", "-m", msg)


def diverge(repo, feat_text="1\n2\n3\nfeat\n", main_text=None):
    g(repo, "checkout", "-q", "-b", "feat")
    commit_file(repo, "a", feat_text, "feat")
    g(repo, "checkout", "-q", "main")
    if main_text is not None:
        commit_file(repo, "a", main_text, "main")
    else:
        commit_file(repo, "m", "m", "main")


def remote_commit(tmp_path, name, text):
    o = tmp_path / "other"
    if not o.exists():
        g(tmp_path, "clone", "-q", str(tmp_path / "bare.git"), str(o))
        g(o, "config", "user.name", "Bob")
        g(o, "config", "user.email", "bob@example.test")
    commit_file(o, name, text, f"remote {name}")
    g(o, "push", "-q", "origin", "main")
