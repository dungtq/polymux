"""The library's read-only viewer (pm.agents.inventory.fsread.read_listed) on a tmp home: only listed paths, symlinks
resolved and re-checked, hidden folders only inside listed paths, ~/.agents-git and ~/.cfg never, text masked."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from pm.agents.inventory import fsread
from pm.core.errors import NotFound


def w(p: Path, data: str | bytes) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data)
    return p


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    w(h / ".agents/skills/demo/SKILL.md", "---\nname: demo\n---\n\n# Demo\n")
    w(h / "agents/AGENTS.md", "# canonical\n")
    w(h / ".agents/hooks/stop.sh", "#!/bin/sh\necho \x1b[31mred\x1b[0m\n")
    w(h / ".ssh/id_ed25519", "PRIVATE\n")
    w(h / ".cfg/HEAD", "ref\n")
    w(h / "dev/tool/README.md", "# tool\n")
    w(h / ".agents/hooks/bin.dat", b"\x00\x01binary")
    (h / ".claude/skills").mkdir(parents=True)
    os.symlink(h / ".agents/skills/demo", h / ".claude/skills/demo")  # the usual link into the shared folder
    os.symlink(h / ".ssh", h / ".claude/skills/evil")  # a "skill" that points at ~/.ssh
    os.symlink(h / ".cfg/HEAD", h / ".agents/hooks/cfg.sh")
    os.symlink(h / "dev/tool/README.md", h / ".claude/skills/tool.md")  # a visible target outside the lists
    return h


LISTED = {"~/.agents/skills/demo", "~/.claude/skills/demo", "~/agents/AGENTS.md", "~/.agents/hooks/stop.sh",
          "~/.claude/skills/evil", "~/.agents/hooks/cfg.sh", "~/.claude/skills/tool.md", "~/.agents/hooks/bin.dat"}


def test_listed_paths_skip_globs():
    inv = NS(skills=[NS(path="~/.agents/skills/a")], rules=[NS(path="~/x/*.md"), NS(path="~/agents/AGENTS.md")],
             scripts=[NS(path="")])
    assert fsread.listed_paths(inv) == {"~/.agents/skills/a", "~/agents/AGENTS.md"}


def test_reads_a_skill_folder_a_rule_and_a_linked_skill(home):
    v = fsread.read_listed("~/.agents/skills/demo", LISTED, home)
    assert v.file == "~/.agents/skills/demo/SKILL.md" and "# Demo" in v.text and not v.cut
    assert fsread.read_listed("~/.claude/skills/demo", LISTED, home).file == "~/.agents/skills/demo/SKILL.md"
    assert fsread.read_listed("~/agents/AGENTS.md", LISTED, home).text == "# canonical\n"
    assert fsread.read_listed("~/.claude/skills/tool.md", LISTED, home).file == "~/dev/tool/README.md"


def test_script_text_is_ansi_stripped_and_cut(home):
    assert "\x1b" not in fsread.read_listed("~/.agents/hooks/stop.sh", LISTED, home).text
    v = fsread.read_listed("~/agents/AGENTS.md", LISTED, home, limit=4)
    assert v.cut and v.text == "# ca"


@pytest.mark.parametrize("path", [
    "~/.ssh/id_ed25519",  # not listed
    "~/agents/../.ssh/id_ed25519",  # not listed either
    "~/.claude/skills/evil",  # listed, but a link into a hidden folder no listed path covers (and a folder)
    "~/.agents/hooks/cfg.sh",  # listed, but resolves into ~/.cfg
    "~/.agents/hooks/bin.dat",  # binary
    "/etc/passwd",
    "",
])
def test_refuses(home, path):
    with pytest.raises(NotFound):
        fsread.read_listed(path, LISTED | {"/etc/passwd"}, home)


def test_refuses_a_link_into_ssh_listed_as_a_file(home):
    os.symlink(home / ".ssh/id_ed25519", home / ".agents/hooks/key.sh")
    with pytest.raises(NotFound):
        fsread.read_listed("~/.agents/hooks/key.sh", LISTED | {"~/.agents/hooks/key.sh"}, home)
