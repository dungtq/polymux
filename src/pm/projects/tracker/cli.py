"""``pmctl``: the agents' command line for tracker projects.

Writes pm.db directly through the domain code (works while pm is down, no token), appends an audit line
with the agent id as client and re-renders the file into the main work tree. Exit 0 on success, 1 on an
error (message on stderr). Keep imports light: no web stack here.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Sequence

from pm.core.errors import PmError
from pm.projects import backlog
from pm.projects.tracker import cli_cmds, memory, schema


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(1, f"pmctl: {message}\n")


def _common() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--project", default=argparse.SUPPRESS, help="project name (default: from the cwd)")
    p.add_argument("--config", default=argparse.SUPPRESS, help="pm.toml (default ~/.config/pm/pm.toml)")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="JSON output")
    return p


def _status_flags(p: argparse.ArgumentParser, default: str) -> None:
    g = p.add_mutually_exclusive_group()
    for flag in ("open", "done", "all"):
        g.add_argument(f"--{flag}", dest="status", action="store_const", const=flag)
    p.set_defaults(status=default)


def _issue_parser(sub, name: str, common) -> None:
    p = sub.add_parser(name, help="issues = BACKLOG.md items" + (" (alias of issue)" if name != "issue" else ""))
    s = p.add_subparsers(dest="action", required=True, parser_class=_Parser)
    a = s.add_parser("add", parents=[common], help="new issue in the Inbox")
    a.add_argument("title")
    a.add_argument("--body-file", help="body (Markdown) from a file, - = stdin")
    a.add_argument("--image", action="append", default=[], help="image file (repeat, max 10)")
    a.add_argument("--label", action="append", default=[])
    a.add_argument("--section", help="BACKLOG.md heading (default Inbox)")
    _status_flags(s.add_parser("list", parents=[common], help="list issues (default --open)"), "open")
    s.add_parser("show", parents=[common], help="title, body, image paths, notes").add_argument("num", type=int)
    n = s.add_parser("note", parents=[common], help="add a note")
    n.add_argument("num", type=int)
    n.add_argument("text", nargs="?", help="note text (or --body-file)")
    n.add_argument("--body-file")
    d = s.add_parser("done", parents=[common], help="close an issue")
    d.add_argument("num", type=int)
    d.add_argument("--commit", help="commit that fixed it")
    s.add_parser("reopen", parents=[common]).add_argument("num", type=int)
    e = s.add_parser("edit", parents=[common], help="change title and/or body")
    e.add_argument("num", type=int)
    e.add_argument("--title")
    e.add_argument("--body-file")
    i = s.add_parser("image", parents=[common], help="attach an image")
    i.add_argument("num", type=int)
    i.add_argument("file")
    s.add_parser("rm", parents=[common], help="delete an issue").add_argument("num", type=int)
    m = s.add_parser("move", parents=[common], help="move an issue to another project (gets a new number)")
    m.add_argument("num", type=int)
    m.add_argument("--to", required=True, metavar="PROJECT", help="target project (its BACKLOG.md in the tracker)")


def _change_parser(sub, common) -> None:
    p = sub.add_parser("change", help="CHANGELOG.md entries")
    s = p.add_subparsers(dest="action", required=True, parser_class=_Parser)
    a = s.add_parser("add", parents=[common], help="new entry at the top: ## <date> (<title>)")
    a.add_argument("title")
    a.add_argument("--date", help="YYYY-MM-DD (default today)")
    a.add_argument("--body-file", required=True, help="body (Markdown) from a file, - = stdin")
    s.add_parser("list", parents=[common], help="entries, drafts marked").add_argument(
        "--all", action="store_true", help="also abandoned drafts")
    s.add_parser("show", parents=[common]).add_argument("num", type=int)
    e = s.add_parser("edit", parents=[common], help="new title (date kept) and/or body")
    e.add_argument("num", type=int)
    e.add_argument("--title")
    e.add_argument("--body-file")
    s.add_parser("rm", parents=[common], help="delete an entry").add_argument("num", type=int)


def _feature_parser(sub, common) -> None:
    p = sub.add_parser("feature", help="FEATURES.md entries")
    s = p.add_subparsers(dest="action", required=True, parser_class=_Parser)
    a = s.add_parser("set", parents=[common], help="add or update - **NAME** — TEXT in ## AREA")
    a.add_argument("area")
    a.add_argument("name")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--text", help="the line after '- **NAME** — '")
    g.add_argument("--text-file", help="the same from a file, - = stdin")
    r = s.add_parser("rm", parents=[common], help="remove an entry")
    r.add_argument("area")
    r.add_argument("name")
    s.add_parser("list", parents=[common], help="entries by area").add_argument("--area")
    w = s.add_parser("show", parents=[common])
    w.add_argument("area")
    w.add_argument("name")
    s.add_parser("audited", parents=[common], help="set the 'Last audited:' line").add_argument(
        "--date", help="YYYY-MM-DD (default today)")


def _claim_num(text: str) -> int:
    """A claim number as ``12`` or ``c12``."""
    t = text.strip().removeprefix("c")
    if not t.isdigit():
        raise argparse.ArgumentTypeError(f"not a claim number: {text!r}")
    return int(t)


def _claim_parser(sub, common) -> None:
    p = sub.add_parser("claim", help="agent work claims: announce work, close it, see overlaps")
    s = p.add_subparsers(dest="action", required=True, parser_class=_Parser)
    a = s.add_parser("add", parents=[common], help="announce work: a new active claim (prints overlaps)")
    a.add_argument("title")
    a.add_argument("--path", action="append", default=[], metavar="GLOB",
                   help="repo-relative glob (repeat; none = the whole repo)")
    a.add_argument("--issue", type=int, help="backlog issue it works on (gets a note)")
    a.add_argument("--branch", help="default: the cwd's branch")
    a.add_argument("--worktree", metavar="DIR", help="default: the cwd's work tree")
    a.add_argument("--pane", metavar="%ID", help="default: $TMUX_PANE")
    lp = s.add_parser("list", parents=[common], help="active claims (stale marked)")
    lp.add_argument("--all", action="store_true", help="also the last 50 done / dropped")
    lp.add_argument("--mine", action="store_true", help="only this agent's")
    s.add_parser("show", parents=[common]).add_argument("num", type=_claim_num)
    u = s.add_parser("update", parents=[common], help="replace paths and/or title")
    u.add_argument("num", type=_claim_num)
    u.add_argument("--path", action="append", metavar="GLOB")
    u.add_argument("--title")
    d = s.add_parser("done", parents=[common], help="close a claim as done (prints claims to notify)")
    d.add_argument("num", type=_claim_num)
    d.add_argument("--commit", help="commit that did it")
    d.add_argument("--note")
    r = s.add_parser("drop", parents=[common], help="give a claim up")
    r.add_argument("num", type=_claim_num)
    r.add_argument("--note")
    s.add_parser("gc", parents=[common], help="drop stale claims (their pane is gone)")
    m = sub.add_parser("merge-check", parents=[common],
                       help="before merging this branch: base moved, overlapping claims and work trees (exit 0/1/2)")
    m.add_argument("--base", help="branch it merges into (default: the repo's main branch)")


def _memory_body(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("--text", help="body (Markdown)")
    g.add_argument("--body-file", help="body from a file, - = stdin")
    p.add_argument("--title", help="index title (default: the name)")
    p.add_argument("--global", dest="is_global", action="store_true", help="the global memory (type user only)")


def _memory_parser(sub, common) -> None:
    p = sub.add_parser("memory", help="agent memory shared by every agent kind (synced with Claude Code's)")
    s = p.add_subparsers(dest="action", required=True, parser_class=_Parser)
    types = list(memory.TYPES)
    a = s.add_parser("add", parents=[common], help="new memory")
    a.add_argument("name", help="slug: a-z, 0-9, '-', '_'")
    a.add_argument("--type", required=True, choices=types)
    a.add_argument("--description", required=True, help="one line: what it is and when it applies")
    _memory_body(a)
    e = s.add_parser("set", parents=[common], help="replace the given fields (adds the memory when missing)")
    e.add_argument("name")
    e.add_argument("--type", choices=types)
    e.add_argument("--description")
    _memory_body(e)
    for act, hlp in (("show", "description, body, who and when"), ("rm", "delete (its synced files go too)")):
        x = s.add_parser(act, parents=[common], help=hlp)
        x.add_argument("name")
        x.add_argument("--global", dest="is_global", action="store_true", help="the global memory")
    lp = s.add_parser("list", parents=[common], help="this project's memories")
    lp.add_argument("--type", choices=types)
    lp.add_argument("--all", action="store_true", help="also the global memories")
    q = s.add_parser("search", parents=[common], help="name, title, description and body contain QUERY")
    q.add_argument("query")
    q.add_argument("--limit", type=int, default=memory.SEARCH_LIMIT, help=f"at most {memory.SEARCH_LIMIT}")
    s.add_parser("context", parents=[common], help="short index for a session start (project + global)")
    y = s.add_parser("sync", parents=[common], help="two-way sync with Claude Code's memory dirs (all work trees)")
    y.add_argument("--all", action="store_true", help="every project with memories or a Claude memory dir")


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    p = _Parser(prog="pmctl", parents=[common],
                description="pm tracker: issues (BACKLOG.md), TODO.md, CHANGELOG.md, FEATURES.md, agent"
                            " work claims and agent memory in pm.db")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    for name in ("issue", "backlog"):
        _issue_parser(sub, name, common)
    t = sub.add_parser("todo", help="TODO.md tasks")
    ts = t.add_subparsers(dest="action", required=True, parser_class=_Parser)
    ta = ts.add_parser("add", parents=[common])
    ta.add_argument("text")
    ta.add_argument("--section", help="TODO.md heading (default: the last section)")
    for act in ("done", "reopen"):
        ts.add_parser(act, parents=[common]).add_argument("num", type=int)
    _status_flags(ts.add_parser("list", parents=[common], help="list tasks (default --all)"), "all")
    _change_parser(sub, common)
    _feature_parser(sub, common)
    _claim_parser(sub, common)
    _memory_parser(sub, common)
    r = sub.add_parser("render", parents=[common], help="write the tracker files from pm.db")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="exit 1 listing files that differ from the render")
    g.add_argument("--stage", action="store_true", help="render into this work tree and git add (pre-commit)")
    g.add_argument("--merge", nargs=4, metavar=("BASE", "RESULT", "THEIRS", "PATH"),
                   help="merge driver (%%O %%A %%B %%P): take THEIRS' new/ticked items, render PATH into RESULT")
    g.add_argument("--post-commit", action="store_true",
                   help="post-commit / post-merge hook: stamp this branch's drafts (main: land merged ones)")
    i = sub.add_parser("import", parents=[common], help="move the tracker files into pm.db")
    i.add_argument("--replace", action="store_true", help="re-import an imported file")
    i.add_argument("--file", action="append", choices=list(schema.ALL_FILES),
                   help="only this file (repeat; default all, missing ones skipped)")
    h = sub.add_parser("hooks", help="git hooks")
    hs = h.add_subparsers(dest="action", required=True, parser_class=_Parser)
    hs.add_parser("install", parents=[common],
                  help="pre-commit + post-commit + post-merge hooks + merge driver").add_argument(
        "--force", action="store_true", help="replace a foreign hook (kept as <hook>.pmctl-old)")
    b = sub.add_parser("backup", parents=[common], help="copy pm.db to state_dir/backups/pm-<today>.db now")
    b.add_argument("--force", action="store_true", help="overwrite today's copy")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return cli_cmds.dispatch(args)
    except (PmError, backlog.Bad) as e:
        print(f"pmctl: {getattr(e, 'msg', None) or e}", file=sys.stderr)
    except cli_cmds.AuditFailed:
        print("pmctl: audit log unavailable; nothing was changed", file=sys.stderr)
    except (OSError, sqlite3.Error) as e:
        print(f"pmctl: {e.__class__.__name__}: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
