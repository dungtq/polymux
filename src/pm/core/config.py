from __future__ import annotations

import logging
import logging.config
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pm.core.models import Tier

DEFAULT_CONFIG = Path.home() / ".config/pm/pm.toml"
log = logging.getLogger("pm.config")


@dataclass(frozen=True)
class Settings:
    binds: tuple[str, ...] = ("127.0.0.1",)  # add the docker gateway when nginx runs in a container
    port: int = 8093
    base_url: str = "http://127.0.0.1:8093"
    dev_root: Path = Path.home() / "dev"
    extra_projects: tuple[Path, ...] = ()
    state_dir: Path = Path.home() / ".local/state/pm"
    token_file: Path = Path.home() / ".config/pm/token"
    # second login token agents may read (full access, user decision 2026-10-11); revoke by deleting the file + restart
    # default: "dev-token" next to token_file (so tests/dev instances with a tmp token_file never touch ~/.config/pm)
    dev_token_file: Path | None = None
    totp_file: Path = Path.home() / ".config/pm/totp"
    # git remotes panel: URL hosts a project remote may point at;
    # pm.toml `git_allowed_hosts = ["git.example.lan", ...]`; also the hosts commit hashes link to. user_ssh_dir: ~/.ssh, listed read-only (*.pub only).
    git_allowed_hosts: tuple[str, ...] = ()
    # the one remote pm's git actions fetch, pull and push (never forced); a git remote name, checked at app start
    push_remote: str = "origin"
    user_ssh_dir: Path = Path.home() / ".ssh"
    # Claude Code's home (``projects/<encoded cwd>/memory`` = its per-directory memory, synced by `pmctl memory sync`)
    claude_dir: Path = Path.home() / ".claude"
    # named login accounts per agent kind from pm.toml (``[accounts]  claude = ["work"]``, read-only; the UI adds more in
    # state_dir/accounts.json, see pm.core.accounts); every named account gets state_dir/accounts/<kind>/<name>
    accounts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    tmux_bin: str = "tmux"
    tmux_socket: str | None = None  # -L name; tests set a private one
    tier_ceiling: Tier = Tier.MESSAGE
    unprotected_sessions: tuple[str, ...] = ()  # allow-list; everything else protected
    cookie_ttl_s: int = 400 * 86400  # single user; browsers cap cookies at ~400 days
    idle_ttl_s: int = 200 * 86400
    unlock_ttl_s: int = 10 * 60
    allowed_origins: tuple[str, ...] = ()  # empty = (base_url,) (set in __post_init__)
    poll_interval_s: float = 2.0
    # Single-user host: login (token or passkey) is the gate. Set true in pm.toml to
    # require a TOTP/passkey unlock before every mutation again.
    require_unlock: bool = False
    # P7 read-only browser terminal (raw tmux attach stream; never masked, never logged)
    terminal_enabled: bool = False
    terminal_max: int = 2
    terminal_idle_s: int = 300
    terminal_max_s: int = 1800
    # ntfy mirror (Inbox + project pages); topics are read without auth; "" = off
    ntfy_topic_url: str = ""  # agent-written finish messages
    ntfy_pm_topic_url: str = ""  # pm's own alerts (notify.py, alerts.py); "" = skip
    ntfy_inbox_count: int = 10  # rows shown in the Inbox (user can switch 5/10/20/50)
    ntfy_cache_s: int = 60  # also the max age of the on-disk copy a restart reuses
    ntfy_stream: bool = True  # hold an open push stream per topic (ntfy /json); False = poll only
    ntfy_history: str = "365d"  # how far back the mirror reads (ntfy `since=`)
    # list size selector on Inbox / Projects / tmux (10, 20, 50 or "all"); remembered per page in a cookie
    list_default: int = 10
    # server health alerts -> ntfy (alerts.py). Thresholds are CRITICAL levels; a metric must be bad for
    # `alerts_sustain` consecutive samples, is re-notified at most every `alerts_repeat_s`, and a recovery
    # notice is sent when it clears.
    alerts_enabled: bool = True
    alerts_interval_s: int = 60
    alerts_sustain: int = 3
    alerts_repeat_s: int = 3600
    alerts_load_per_cpu: float = 1.0
    alerts_mem_pct: float = 92.0
    alerts_swap_pct: float = 60.0
    alerts_disk_pct: float = 92.0
    alerts_containers: bool = True  # unhealthy / exited containers
    alerts_hosts: bool = True  # hosts in `servers` unreachable
    # Timestamps shown in the GUI use this fixed UTC offset (hours); label is "UTC+7" for 7.0
    display_utc_offset_h: float = 0.0
    # Event-loop watchdog: log the main-thread stack when the loop is stalled this long (s);
    # exit the process (systemd restarts it) when stalled this long (0 disables the exit).
    watchdog_stall_log_s: float = 2.0
    watchdog_exit_s: float = 30.0
    # browser error reports (POST /client-errors): per client per minute
    client_error_rate_per_min: int = 30
    # libvirt VM management (Servers tab). Reads use `virsh -r`; writes go through `sg libvirt -c`
    # when pm's process lacks the libvirt group. vm_protected: fnmatch globs of VM names pm never changes.
    virsh_bin: str = "virsh"
    virsh_uri: str = "qemu:///system"
    virsh_write_sg: bool = True
    vm_pool: str = "images"
    vm_manage: bool = True  # false = list only
    vm_protected: tuple[str, ...] = ()
    # Servers > Resources/Services: stop/kill processes and stop/restart/kill containers
    proc_manage: bool = True  # false = list only
    container_manage: bool = True
    container_protected: tuple[str, ...] = ("nginx*", "ntfy*", "gitea*", "adguard*")  # fnmatch globs
    # ended agent records (permanent /a/<id> pages) are kept this many days; pruned on first poll
    agent_retention_days: int = 90
    # past CLI sessions on disk per project folder (agents/pastsessions.py) are imported as ended
    # records every this many seconds (min 60); 0 = off
    past_sessions_interval_s: int = 600
    # auto-exit of long-idle Claude agents (autoexit.py): seconds idle before exit (0 = off); the
    # settings page can override it. The sweeper wakes every autoexit_interval_s (min 30).
    autoexit_idle_s: int = 86400
    autoexit_interval_s: int = 300
    # pm.toml only: also kill the agent's window after an auto-exit (when it holds only the left-over shell);
    # off = the window stays like any agent window after its agent quits (remain-on-exit)
    autoexit_close_window: bool = False
    # daily online copy of pm.db in state_dir/backups/pm-YYYY-MM-DD.db (pm.core.db.backup): copies kept; 0 = off
    db_backup_keep: int = 14
    # tmux sessions (fnmatch globs) whose idle Claude agents auto-exit may end; `pm*` is always excluded
    autoexit_sessions: tuple[str, ...] = ("*",)
    # inbox: an idle agent is "finished" this many seconds after its last response, then "idle" (min 60)
    idle_after_s: int = 28800
    # dev instance (tools/devpm.py): show a DEV tag in the header; no effect on auth
    dev_banner: bool = False
    # systemd user unit the topbar Reload button restarts; empty disables it (never on a dev instance)
    reload_unit: str = "pm.service"
    # Builder preview: external origins the aside may frame (https://host[:port] only; they go into the
    # builder page's CSP frame-src). builder_hint: add the "[pm builder: ...]" line to Builder-mode messages.
    builder_frame_hosts: tuple[str, ...] = ()
    builder_hint: bool = True
    # Builder API pane: local dev-server ports (127.0.0.1 only) the pane may send test requests to; empty = mock
    # only (default). pm's own port, 8093 and 8094 are always refused (projects/apisend.py).
    builder_api_ports: tuple[int, ...] = ()
    # argv that prints the agent id for tracker writes from `pmctl` when $PM_ACTOR is unset (e.g. ["tmux-agents", "id"]);
    # empty = "agent"
    actor_cmd: tuple[str, ...] = ()
    # New project dialog (projects/newproject.py): templates [{name, description, files = {"rel/path": "text"}}]
    # ("{name}" in a file = the project name). git_remote_template: URL of the new project's push_remote, "{name}"
    # = the project name (e.g. "git@git.example.lan:me/{name}.git"); "" = the remote step is skipped
    project_templates: list = field(default_factory=lambda: [{"name": "empty"}])
    git_remote_template: str = ""
    # Launch form hint: project -> the project its code work usually belongs in (e.g. {pm = "polymux"}); {} = none
    launch_redirects: dict = field(default_factory=dict)
    # UI theme (pm.web.themes): "classic" = today's UI; plugin themes by name; unknown = classic + a warning
    theme: str = "classic"
    # plugins (entry-point group "pm.plugins", pm.web.plugins): false = load none (env PM_NO_PLUGINS=1 too).
    # plugins: the raw [plugins.<name>] tables; each plugin gets its own table (configure hook).
    plugins_enabled: bool = True
    plugins: dict = field(default_factory=dict)
    # --- formerly `[extras]` keys (EXTRAS_FIELDS); pm.toml may set them top-level or under [extras]
    agent_bins: dict = field(default_factory=dict)  # kind -> absolute binary path (default ~/.local/bin/<kind>)
    agent_models: dict = field(default_factory=dict)  # kind -> [extra model ids] for the picker
    allow_bypass: bool = True  # kill switch: anything but True disables every bypass mode
    worktree_root: str | None = None  # agent worktrees; None = <project>-wt next to the project
    snippets: list | dict = field(default_factory=list)  # composer snippets: [(label, text)] or {label: text}
    alerts_host: str = ""  # host name in alerts; "" = this host's short name
    disable_poll: bool = False  # tests/dev: no tmux poller, alerts, inventory warm-up
    watchdog: bool = False  # start the loop watchdog even with disable_poll
    ntfy_ca: str | None = None  # CA file for ntfy TLS; None = system store
    ntfy_debounce_s: float = 30.0  # = notify.DEBOUNCE_S
    ntfy_dedupe_s: float = 600.0  # = notify.DEDUPE_S
    # notify.py's base publish URL (= notify.DEFAULT_URL); separate from ntfy_topic_url / ntfy_pm_topic_url
    ntfy_url: str = ""  # "" = pm sends no ntfy messages
    servers: list = field(default_factory=list)  # extra host checks: [{name, host, port, note}]
    ntfy_ui_url: str = ""  # ntfy web UI link; "" = scheme://host of ntfy_url
    layouts: dict = field(default_factory=dict)  # project -> tmux window layout for "ensure"
    extras: dict = field(default_factory=dict)  # unknown [extras] keys only; nothing reads them
    # the pm.toml path load_settings read (also when it was missing); never settable from pm.toml itself
    config_file: Path | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.allowed_origins:  # not given: the public URL is the one origin POSTs may come from
            object.__setattr__(self, "allowed_origins", (str(self.base_url).rstrip("/"),))

    @property
    def audit_file(self) -> Path:
        return self.state_dir / "audit.jsonl"

    @property
    def client_errors_file(self) -> Path:
        return self.state_dir / "client-errors.jsonl"

    @property
    def prefs_file(self) -> Path:
        return self.state_dir / "prefs.json"

    @property
    def db_file(self) -> Path:
        return self.state_dir / "pm.db"

    @property
    def tracker_dir(self) -> Path:
        """Tracker issue images (``<sha>.<ext>``, hard links or copies of uploads)."""
        return self.state_dir / "tracker"

    @property
    def memory_dir(self) -> Path:
        """Agent memory renders for agents without a memory dir of their own (``<project>.md``)."""
        return self.state_dir / "memory"


EXTRAS_FIELDS = (
    "agent_bins", "agent_models", "allow_bypass", "worktree_root", "snippets", "alerts_host", "disable_poll",
    "watchdog", "ntfy_ca", "ntfy_debounce_s", "ntfy_dedupe_s", "ntfy_url", "servers", "ntfy_ui_url",
    "layouts",
)


def _convert(key: str, val):
    if key in {"dev_root", "state_dir", "token_file", "dev_token_file", "totp_file", "user_ssh_dir",
               "claude_dir"}:
        return Path(val).expanduser()
    if key == "extra_projects":
        return tuple(Path(v).expanduser() for v in val)
    if key in {"unprotected_sessions", "autoexit_sessions", "allowed_origins", "binds", "vm_protected", "container_protected",
               "git_allowed_hosts", "builder_frame_hosts", "actor_cmd"}:
        return tuple(val)
    if key == "builder_api_ports":
        return tuple(v for v in val if isinstance(v, int) and not isinstance(v, bool))
    if key == "tier_ceiling":
        return Tier(int(val))
    if key == "accounts":
        return _account_names(val)
    if key == "plugins":
        return _plugin_tables(val)
    if key == "launch_redirects":
        return _redirect_map(val)
    return val


ACCOUNT_KINDS = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}  # kind -> env var that moves its login
ACCOUNT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


def _account_names(val) -> dict[str, tuple[str, ...]]:
    """``[accounts]`` kind -> list of names; unknown kinds, bad names and non-lists are logged and dropped."""
    if not isinstance(val, dict):
        log.warning("config: [accounts] is not a table, ignored")
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for kind, names in val.items():
        if kind not in ACCOUNT_KINDS or not isinstance(names, list):
            log.warning("config: [accounts] %r ignored (kinds: %s; value must be a list)", kind, ", ".join(ACCOUNT_KINDS))
            continue
        good = [n for n in names if isinstance(n, str) and ACCOUNT_NAME_RE.fullmatch(n)]
        if len(good) != len(names):
            log.warning("config: [accounts] %s: names must match %s, some ignored", kind, ACCOUNT_NAME_RE.pattern)
        out[kind] = tuple(dict.fromkeys(good))
    return out


def _plugin_tables(val) -> dict:
    """``[plugins.<name>]`` tables; anything that is not a table is logged and dropped."""
    if not isinstance(val, dict):
        log.warning("config: [plugins] is not a table, ignored")
        return {}
    out = {}
    for name, cfg in val.items():
        if isinstance(cfg, dict):
            out[name] = dict(cfg)
        else:
            log.warning("config: [plugins.%s] is not a table, ignored", name)
    return out


def _redirect_map(val) -> dict:
    """``launch_redirects``: keep the ``project = "other project"`` pairs; anything else is logged and dropped."""
    if not isinstance(val, dict):
        log.warning("config: launch_redirects is not a table, ignored")
        return {}
    out = {}
    for src, dst in val.items():
        if isinstance(src, str) and isinstance(dst, str) and src and dst and src != dst:
            out[src] = dst
        else:
            log.warning("config: launch_redirects entry %r = %r ignored", src, dst)
    return out


_ORIGIN_RE = re.compile(r"https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*(?::\d{1,5})?")


def frame_origins(hosts) -> tuple[str, ...]:
    """Keep the entries that are a bare ``https://host[:port]`` origin (lower-cased, one trailing "/" allowed).
    Anything else (path, space, ``;``, quote, ``*``, other scheme) is dropped: the value goes into a CSP header."""
    if isinstance(hosts, str):
        hosts = (hosts,)
    out: list[str] = []
    for h in hosts or ():
        o = h.strip().lower().removesuffix("/") if isinstance(h, str) else ""
        if _ORIGIN_RE.fullmatch(o):
            out += [] if o in out else [o]
        else:
            log.warning("config: builder_frame_hosts entry %r is not an https origin, ignored", h)
    return tuple(out)


def load_settings(path: Path | None = None) -> Settings:
    """pm.toml -> Settings. Typed keys under ``[extras]`` are promoted (a top-level key wins);
    unknown keys are logged and, under ``[extras]``, kept in ``Settings.extras``."""
    path = path or DEFAULT_CONFIG
    if not path.exists():
        return Settings(config_file=path)
    raw = tomllib.loads(path.read_text())
    fields = Settings.__dataclass_fields__
    kw: dict = {}
    extras: dict = {}
    for key, val in raw.items():
        if key == "extras":
            if isinstance(val, dict):
                extras = dict(val)
            else:
                log.warning("config: [extras] is not a table, ignored")
        elif key in fields and key != "config_file":
            kw[key] = _convert(key, val)
        else:
            log.warning("config: unknown key %r ignored", key)
    for key in list(extras):
        if key in EXTRAS_FIELDS:
            val = extras.pop(key)
            kw.setdefault(key, val)
        else:
            log.warning("config: unknown extras key %r", key)
    if "base_url" in kw:
        kw.setdefault("allowed_origins", (str(kw["base_url"]).rstrip("/"),))
    if "builder_frame_hosts" in kw:
        kw["builder_frame_hosts"] = frame_origins(raw["builder_frame_hosts"])
    return Settings(**kw, extras=extras, config_file=path)


def setup_logging(level: str = "INFO") -> None:
    """Send pm.* records to stderr (journald adds timestamps). Idempotent: dictConfig resets pm's handlers."""
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"pm": {"format": "%(levelname)s %(name)s: %(message)s"}},
        "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "pm", "stream": "ext://sys.stderr"}},
        "loggers": {"pm": {"level": level.upper(), "handlers": ["stderr"], "propagate": False}},
    })
