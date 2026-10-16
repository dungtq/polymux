#!/usr/bin/env python3
"""Dev pm instance with throwaway state, token and fixtures (docs/DEV-TESTING-PLAN.md).

    devpm.py start [--port N] [--limits default|warn|red] [--pm-bin P] [--extra-toml F]   prints instance.json
    devpm.py stop [--port N]
    devpm.py status [--port N] [--json]

The instance runs with HOME=<root>/home, so it never sees the real ~/.config/pm token, ~/.claude or
~/.local/state/pm. Agents are fake `claude` processes in a private `tmux -L pmtest-dev-<port>` server.
Auth stays on: log in with the throwaway token in <root>/home/.config/pm/token.

--pm-bin, --extra-toml and --limits are remembered in instance.json: a later `start` without them (uishot, a
stale-code restart) keeps the instance's values; give one explicitly to change it (`--pm-bin ''` = this tree's pm).
A restart only replaces the pm process; the fixture projects, state, token and fake agents stay. `stop` wipes them.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIX = REPO / "tests/fixtures/dev"
REAL_HOME = Path(os.path.expanduser("~")).resolve()
MIN_PORT = 8095
NTFY_OFFSET = 100
BASE = Path(os.environ.get("TMPDIR", "/tmp")) / "pm-dev"
SOCK_PREFIX = "pmtest-dev-"
PROJECT_ENV = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL") if k in os.environ}
OPT_DEFAULTS = {"pm_bin": "", "extra_toml": "", "limits": "default"}  # start options kept in instance.json


def root_of(port: int) -> Path:
    return BASE / str(port)


def guard(port: int, root: Path) -> None:
    """Refuse anything that could touch the real instance."""
    if port < MIN_PORT:
        sys.exit(f"devpm: port {port} refused (dev ports start at {MIN_PORT}; 8093 is the real pm)")
    real_state = (REAL_HOME / ".local/state/pm").resolve()
    for p in (root, root / "state"):
        rp = p.resolve()
        if rp == real_state or real_state in rp.parents or rp in (REAL_HOME, REAL_HOME / ".config/pm"):
            sys.exit(f"devpm: {rp} is the real pm state; refusing")


def tmux(sock: str, *args: str, timeout: int = 10, home: Path | None = None) -> subprocess.CompletedProcess:
    assert sock.startswith(SOCK_PREFIX)
    env = {**PROJECT_ENV, "TMUX": ""}
    if home is not None:  # the tmux server (and every pane) inherits this HOME, never the real one
        env["HOME"] = str(home)
    return subprocess.run(["tmux", "-L", sock, *args], capture_output=True, text=True, timeout=timeout,
                          env=env, check=False)


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def code_stamp() -> float:
    """Newest mtime of the backend code (``*.py``, templates) pm loads once at start."""
    newest = 0.0
    for dp, dn, fn in os.walk(REPO / "src/pm"):
        dn[:] = [d for d in dn if d not in ("__pycache__", "static", "vendor")]
        for f in fn:
            if f.endswith((".py", ".html")):
                try:
                    newest = max(newest, os.stat(os.path.join(dp, f)).st_mtime)
                except OSError:
                    pass
    return newest


def is_stale(cur: dict, stamp: float | None = None) -> bool:
    """A running dev instance older than the backend code serves 404s for routes added since."""
    return (code_stamp() if stamp is None else stamp) > float(cur.get("started_at") or 0)


def alive(pid: int) -> bool:
    if pid <= 0:  # kill(0, 0) would probe our own process group
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def info_of(port: int) -> dict | None:
    f = root_of(port) / "instance.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def sess_name(a: dict) -> str:
    return a.get("tmux_session") or f"dev-{a['key']}"


def start_opts(cur: dict | None, pm_bin: str | None, extra_toml: str | None, limits: str | None) -> dict:
    """Start options: an explicit flag wins (``''`` = the default), else the instance's remembered value."""
    old = cur or {}
    out = {}
    for k, v in (("pm_bin", pm_bin), ("extra_toml", extra_toml), ("limits", limits)):
        if v is None:
            v = old.get(k, OPT_DEFAULTS[k])
        elif k != "limits" and v:
            v = os.path.abspath(os.path.expanduser(v))  # callers run from other cwds
        out[k] = v or OPT_DEFAULTS[k]
    return out


def start_plan(cur: dict | None, opts: dict, *, up: bool, stale: bool, intact: bool) -> str:
    """``reuse`` a healthy, current instance with the same options; ``restart`` only its pm process (fixture
    projects, state, token and fake agents stay); ``fresh`` (stop + seed) when nothing intact is left."""
    same = bool(cur) and all(cur.get(k, d) == opts[k] for k, d in OPT_DEFAULTS.items())
    if cur and up and not stale and same:
        return "reuse"
    return "restart" if cur and intact else "fresh"


def is_intact(port: int, cur: dict) -> bool:
    """The seeded dir, token and fake-agent tmux server are still there (a pm-only restart can reuse them)."""
    root = root_of(port)
    return ((root / "pm.base.toml").is_file() and Path(cur.get("token_file") or root / "-").is_file()
            and tmux(f"{SOCK_PREFIX}{port}", "list-sessions", home=root / "home").returncode == 0)


def write_toml(root: Path, extra_toml: str) -> None:
    """pm.toml = the seeded base + the --extra-toml file (e.g. a [plugins.<name>] table), re-read every start."""
    text = (root / "pm.base.toml").read_text()
    if extra_toml:
        text += "\n" + Path(extra_toml).read_text()
    (root / "home/.config/pm/pm.toml").write_text(text)


def write_limits(state: Path, limits: str) -> None:
    lim = json.loads((FIX / "limits" / f"{limits}.json").read_text())
    now = time.time()
    for w in lim.values():
        if isinstance(w, dict) and "resets_in_s" in w:
            w["resets_at"] = int(now + w.pop("resets_in_s"))
    (state / "claude-limits.json").write_text(json.dumps(lim))


def seed(port: int, limits: str) -> dict:
    root = root_of(port)
    home, state, projects = root / "home", root / "state", root / "projects"
    sock = f"{SOCK_PREFIX}{port}"
    url = f"http://127.0.0.1:{port}"
    for d in (home / ".config/pm", home / ".claude/sessions", home / ".claude/projects", state, projects):
        d.mkdir(parents=True, exist_ok=True)
    # fixture skills (global, agents + claude) so the project page skills table has rows
    for src, name, desc in (
        ("agents", "ntfy", "Publish and subscribe on the local ntfy server. Use when sending a notification, finishing a CLI or Cursor agent task, or when the user mentions ntfy, ntfy publish, ntfy subscribe, or topic lab/agent."),
        ("agents", "dev-stacks", "When deploying a new Docker or local web stack under ~/dev, update the .stignore for root-owned data directories."),
        ("claude", "short", "Tiny."),
        ("claude", "macos-config", "Pull Linux home configs onto a Mac with macOS-config-sync.sh. Use whenever the user mentions macOS, a Mac, Darwin, Homebrew fish, Ghostty/Kitty/WezTerm on the Mac."),
    ):
        sd = home / f".{src}/skills" / name
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n")
    token = home / ".config/pm/token"
    token.write_text(secrets.token_urlsafe(32) + "\n")
    token.chmod(0o600)

    man = json.loads((FIX / "manifest.json").read_text())
    proj = man["project"]
    for pdir in sorted(d for d in (FIX / "projects").iterdir() if d.is_dir()):
        shutil.copytree(pdir, projects / pdir.name, dirs_exist_ok=True)
        git = ["git", "-C", str(projects / pdir.name)]
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                    ["-c", "user.name=dev", "-c", "user.email=dev@example.invalid", "commit", "-qm", "fixture"]):
            subprocess.run(git + cmd, check=True, capture_output=True, timeout=20, env=PROJECT_ENV)

    # `claude` in the dev HOME is the fake agent, so Resume and Launch start it
    fake = REPO / "tools/devfakeagent.sh"
    (home / ".local/bin").mkdir(parents=True, exist_ok=True)
    link = home / ".local/bin/claude"
    link.unlink(missing_ok=True)
    link.symlink_to(fake)

    write_limits(state, limits)
    now = time.time()

    toml = f"""binds = ["127.0.0.1"]
port = {port}
base_url = "{url}"
dev_root = "{projects}"
extra_projects = []
state_dir = "{state}"
token_file = "{token}"
totp_file = "{home}/.config/pm/totp"
user_ssh_dir = "{home}/.ssh"
tmux_socket = "{sock}"
allowed_origins = ["{url}"]
unprotected_sessions = [{", ".join(f'"{sess_name(a)}"' for a in man["agents"] if not a.get("protected"))}]
ntfy_topic_url = "http://127.0.0.1:{port + NTFY_OFFSET}/agent"
ntfy_pm_topic_url = ""
alerts_enabled = false
autoexit_idle_s = 0
vm_manage = false
virsh_bin = "/bin/false"
dev_banner = true
"""
    (root / "pm.base.toml").write_text(toml)
    (home / ".config/pm/pm.toml").write_text(toml)

    # records for ended agents, transcripts for all
    sys.path.insert(0, str(REPO / "src"))
    from pm.chat.sources.claude import encode_cwd
    from pm.core.config import Settings

    settings = Settings(state_dir=state)
    from pm.agents import registry

    ended: dict[str, str] = {}
    exit_info: dict[str, dict] = {}
    cons = registry._connect(settings)
    try:
        for a in man["agents"]:
            cwd = a["cwd"].replace("{PROJECTS}", str(projects))
            a["cwd"] = cwd
            aproj = a.get("project", proj)
            tpath = ""
            if a.get("transcript") and a.get("uuid"):
                d = home / ".claude/projects" / encode_cwd(cwd)
                d.mkdir(parents=True, exist_ok=True)
                dst = d / f"{a['uuid']}.jsonl"
                shutil.copyfile(FIX / a["transcript"], dst)
                tpath = str(dst)
            if not a["live"]:
                aid = registry.mint_id("claude", a.get("uuid"))
                ended[a["key"]] = aid
                t = now - 3600
                cons.execute(
                    "INSERT OR REPLACE INTO agent_records (id, kind, ident, pid, first_seen, last_seen, "
                    "last_pane, project, cwd, label, transcript, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, "claude", a.get("uuid"), 0, t - 600, t, "%99", aproj, cwd, a.get("label", ""), tpath, t))
                kind = a.get("autoexit")
                if kind:  # info rows as written by pm.agents.autoexit (auto) / record_manual_exit (manual)
                    from pm.agents import head as agenthead

                    model = "" if kind == "auto_nomodel" else agenthead.model_from_transcript(tpath)
                    row = {"ts": t, "cwd": cwd, "kind": "claude", "session": a.get("uuid") or "",
                           "model": model, "project": aproj, "window": "agent-claude"}
                    if kind == "manual":
                        row["manual"] = True
                    else:
                        row.update(tmux_session=sess_name(a), idle_s=90000)
                    exit_info[aid] = row
        cons.commit()
    finally:
        cons.close()
    if exit_info:
        (state / "autoexit.json").write_text(json.dumps({"info": exit_info}, indent=1))

    # live agents: fake `claude` processes (tools/devfakeagent.sh) in a private tmux server
    for a in man["agents"]:
        if not a["live"]:
            continue
        sess = sess_name(a)
        shell = (f"HOME={shlex.quote(str(home))} DEVFAKE_SESSION={shlex.quote(a['uuid'] or '')} "
                 f"exec -a claude bash {shlex.quote(str(fake))} {a['status']}")
        r = tmux(sock, "new-session", "-d", "-s", sess, "-n", a.get("window", "agent-claude"), "-x", "120", "-y", "30",
                 "-c", a["cwd"], "bash", "-c", shell, home=home)
        if r.returncode:
            sys.exit(f"devpm: tmux failed: {r.stderr.strip()}")
        pid = int(tmux(sock, "display-message", "-p", "-t", sess + ":", "#{pane_pid}", home=home).stdout.strip())
        for _ in range(50):  # wait for the fake agent to write its session file
            if (home / ".claude/sessions" / f"{pid}.json").exists():
                break
            time.sleep(0.1)
        else:
            sys.exit(f"devpm: fake agent {a['key']} did not start")
    # one extra live fake agent per other kind (argv0 decides the kind) so per-kind colours can be eyeballed
    cwd0 = str(projects / proj)
    for argv0 in ("codex", "devin", "cursor-agent", "agy"):
        shell = f"HOME={shlex.quote(str(home))} exec -a {argv0} bash {shlex.quote(str(fake))} idle"
        tmux(sock, "new-session", "-d", "-s", f"kind-{argv0}", "-n", f"agent-{argv0}", "-x", "120", "-y", "30",
             "-c", cwd0 if Path(cwd0).is_dir() else str(projects), "bash", "-c", shell, home=home)
    return {"url": url, "port": port, "root": str(root), "token_file": str(token), "state_dir": str(state),
            "socket": sock, "project": proj, "ended": ended, "manifest": man["agents"]}


def resolve_ids(info: dict) -> dict:
    """Agent ids after the first poll: registry rows by session id (live ones are minted by pm)."""
    db = Path(info["state_dir"]) / "pm.db"
    idents = {a["key"]: a.get("uuid") for a in info.pop("manifest")}
    ids = dict(info.pop("ended"))
    for _ in range(40):
        try:
            con = sqlite3.connect(db, timeout=2)
            rows = dict((i, a) for a, i in con.execute("SELECT id, ident FROM agent_records WHERE ident IS NOT NULL"))
            con.close()
        except sqlite3.Error:
            rows = {}
        for k, u in idents.items():
            if u and u in rows and k not in ids:
                ids[k] = rows[u]
        if all(k in ids for k, u in idents.items() if u):
            break
        time.sleep(0.5)
    info["agents"] = ids
    return info


def ntfy_alive(root: Path) -> int:
    """Pid of the instance's running devntfy, else 0."""
    try:
        npid = int((root / "ntfy.pid").read_text())
        return npid if alive(npid) and b"devntfy" in Path(f"/proc/{npid}/cmdline").read_bytes() else 0
    except (OSError, ValueError):
        return 0


def stop_pid(pid: int) -> None:
    if not alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not alive(pid):
                break
            time.sleep(0.2)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def cmd_start(port: int, limits: str | None = None, pm_bin: str | None = None, extra_toml: str | None = None) -> int:
    root = root_of(port)
    guard(port, root)
    cur = info_of(port)
    opts = start_opts(cur, pm_bin, extra_toml, limits)
    up = bool(cur and alive(cur.get("pid", 0)) and healthy(cur["url"]))
    plan = start_plan(cur, opts, up=up, stale=bool(cur) and is_stale(cur), intact=bool(cur) and is_intact(port, cur))
    if plan == "reuse":
        print(json.dumps(cur))
        return 0
    if plan == "restart":  # new code or options: replace only the pm process, keep the seeded data
        stop_pid(cur.get("pid", 0))
        info = dict(cur)
        if cur.get("limits", OPT_DEFAULTS["limits"]) != opts["limits"]:
            write_limits(root / "state", opts["limits"])
    else:
        cmd_stop(port, quiet=True)
        info = seed(port, opts["limits"])
    write_toml(root, opts["extra_toml"])
    info.update(opts)
    log = open(root / "pm.log", "ab")
    if not ntfy_alive(root):
        nt = subprocess.Popen([sys.executable, str(REPO / "tools/devntfy.py"), str(port + NTFY_OFFSET), "--project",
                               str(root / "projects/fixproj")], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                              start_new_session=True)
        (root / "ntfy.pid").write_text(str(nt.pid))
    home = root / "home"
    started_at = time.time()
    env = {**PROJECT_ENV, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "TMUX": ""}
    p = subprocess.Popen([opts["pm_bin"] or str(REPO / ".venv/bin/pm")], cwd=REPO, env=env, stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    info["pid"] = p.pid
    info["started_at"] = started_at
    info["ntfy_url"] = f"http://127.0.0.1:{port + NTFY_OFFSET}"
    for _ in range(60):
        if healthy(info["url"]):
            break
        if p.poll() is not None:
            sys.exit(f"devpm: pm exited early, see {root / 'pm.log'}")
        time.sleep(0.5)
    else:
        sys.exit("devpm: pm did not become healthy")
    if plan == "fresh":
        info = resolve_ids(info)
    (root / "instance.json").write_text(json.dumps(info))
    print(json.dumps(info))
    return 0


def cmd_stop(port: int, quiet: bool = False) -> int:
    root = root_of(port)
    guard(port, root)
    info = info_of(port)
    if info:
        stop_pid(info.get("pid", 0))
    npid = ntfy_alive(root)
    if npid:
        try:
            os.kill(npid, signal.SIGTERM)
        except OSError:
            pass
    tmux(f"{SOCK_PREFIX}{port}", "kill-server")
    if root.exists() and root.parent == BASE:
        shutil.rmtree(root, ignore_errors=True)
    if not quiet:
        print(f"stopped dev instance on {port}")
    return 0


def cmd_status(port: int, as_json: bool) -> int:
    info = info_of(port)
    up = bool(info and alive(info.get("pid", 0)) and healthy(info["url"]))
    if as_json:
        print(json.dumps({**(info or {}), "running": up}))
    else:
        print(f"{'running' if up else 'stopped'} {info['url'] if info else ''}".strip())
    return 0 if up else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("start", "stop", "status"))
    ap.add_argument("--port", type=int, default=MIN_PORT)
    ap.add_argument("--limits", choices=("default", "warn", "red"), default=None,
                    help="limits fixture (default: the instance's, else default)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pm-bin", default=None, help="pm executable to run (e.g. a venv with plugins installed); "
                    "remembered by the instance, '' = this tree's .venv/bin/pm")
    ap.add_argument("--extra-toml", default=None, help="file appended to the dev pm.toml (plugin tables); "
                    "remembered by the instance, '' = none")
    a = ap.parse_args()
    if a.cmd == "start":
        return cmd_start(a.port, a.limits, a.pm_bin, a.extra_toml)
    if a.cmd == "stop":
        return cmd_stop(a.port)
    return cmd_status(a.port, a.json)


if __name__ == "__main__":
    sys.exit(main())
