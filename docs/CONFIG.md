# pm configuration

pm reads `~/.config/pm/pm.toml` at start. A missing file means every default
below. Restart pm after a change (`systemctl --user restart pm`).

Rules:

- Paths accept `~`.
- Lists are TOML arrays; pm stores them as tuples.
- An unknown top-level key is logged and ignored.
- Source of truth: `src/pm/core/config.py` (`Settings`).

Example:

```toml
tmux_bin = "/usr/bin/tmux"
base_url = "https://pm.example.lan"
binds = ["127.0.0.1", "172.17.0.1"]
git_allowed_hosts = ["git.example.lan", "github.com"]
ntfy_url = "https://ntfy.example.lan/pm"
ntfy_topic_url = "https://ntfy.example.lan/agent"
ntfy_pm_topic_url = "https://ntfy.example.lan/pm"
display_utc_offset_h = 2.0
servers = [
  { name = "nas", host = "192.168.1.5", port = 22, note = "backup box" },
]
```

## Network and URLs

| Key | Default | Meaning |
|---|---|---|
| `binds` | `["127.0.0.1"]` | Addresses pm listens on. Add your Docker bridge gateway if the proxy runs in a container. Never `0.0.0.0` |
| `port` | `8093` | TCP port on every bind address |
| `base_url` | `"http://127.0.0.1:8093"` | Public URL of pm. Used for links, ntfy click URLs and as the passkey relying-party id (its host name) |
| `allowed_origins` | `[base_url]` | Origins accepted on POST requests (empty = `[base_url]`) |

## Projects and git

| Key | Default | Meaning |
|---|---|---|
| `dev_root` | `"~/dev"` | Each folder under it is a project |
| `extra_projects` | `[]` | More project folders outside `dev_root` |
| `git_allowed_hosts` | `[]` | Hosts a project's remotes may point at. Also turns on commit links when the `gitdev` remote is on one of these hosts (`https://<host>/<owner>/<repo>/commit/<sha>`). Empty = remotes panel can add nothing, no links |
| `user_ssh_dir` | `"~/.ssh"` | Listed read-only (public keys only) |
| `project_templates` | `[{name = "empty"}]` | Templates of the New project dialog: `{name, description, files = {"rel/path" = "text"}}`; `{name}` in a file is replaced by the project name. No network |
| `git_remote_template` | `""` | URL of a new project's `gitdev` remote, `{name}` = project name (e.g. `"git@git.example.lan:me/{name}.git"`); the host must be in `git_allowed_hosts`. Empty = the New project dialog skips the remote |

pm's git actions use the remote named `gitdev`.

## Files and state

| Key | Default | Meaning |
|---|---|---|
| `state_dir` | `"~/.local/state/pm"` | `pm.db`, `audit.jsonl`, sessions, `hook_secret`, backups |
| `token_file` | `"~/.config/pm/token"` | Login token (0600, created on first start) |
| `dev_token_file` | unset (= `dev-token` next to `token_file`) | Second full-access login token for scripts and agents (created on first start) |
| `totp_file` | `"~/.config/pm/totp"` | TOTP secret, written when you set up TOTP |
| `db_backup_keep` | `14` | Daily `pm.db` copies kept in `state_dir/backups/`; `0` = off |

## tmux

| Key | Default | Meaning |
|---|---|---|
| `tmux_bin` | `"tmux"` | tmux binary. Use an absolute path under systemd; it must match the running server's version |
| `tmux_socket` | unset | `-L` socket name; for tests only |
| `unprotected_sessions` | `[]` | Globs of tmux sessions pm may change. Every other session is protected. `pm*` is always protected |
| `poll_interval_s` | `2.0` | How often pm polls tmux |
| `list_default` | `10` | Default rows in lists (10, 20, 50 or "all") |

## Access and sessions

| Key | Default | Meaning |
|---|---|---|
| `tier_ceiling` | `2` | Highest action tier allowed: 0 view, 1 safe, 2 message, 3 terminal |
| `require_unlock` | `false` | `true` = every change also needs a recent TOTP or passkey unlock |
| `unlock_ttl_s` | `600` | How long an unlock lasts |
| `cookie_ttl_s` | `34560000` (400 days) | Max session age |
| `idle_ttl_s` | `17280000` (200 days) | Session ends after this much inactivity |
| `client_error_rate_per_min` | `30` | Browser error reports accepted per client per minute |

## Read-only terminal

| Key | Default | Meaning |
|---|---|---|
| `terminal_enabled` | `false` | Enables the browser view of a tmux session (`tmux attach -r`, input never sent) |
| `terminal_max` | `2` | Concurrent terminals |
| `terminal_idle_s` | `300` | Idle timeout |
| `terminal_max_s` | `1800` | Hard cap per connection |

## Agents

| Key | Default | Meaning |
|---|---|---|
| `idle_after_s` | `28800` | An agent is "finished" this long after its last reply, then "idle" (min 60) |
| `agent_retention_days` | `90` | Days ended-agent records are kept |
| `past_sessions_interval_s` | `600` | Import past CLI sessions found on disk this often; `0` = off (min 60) |
| `autoexit_idle_s` | `86400` | Exit idle Claude agents after this long; `0` = off |
| `autoexit_interval_s` | `300` | Sweeper interval (min 30) |
| `autoexit_sessions` | `["*"]` | tmux session globs where auto-exit may act; `pm*` never |
| `actor_cmd` | `[]` | Command (argv list, no shell, 2 s timeout) that prints the agent id `pmctl` records when `$PM_ACTOR` is unset, e.g. `["tmux-agents", "id"]`. Empty = `agent` |

## Builder

| Key | Default | Meaning |
|---|---|---|
| `builder_frame_hosts` | `[]` | Extra `https://host[:port]` origins the preview may frame. Other values are dropped |
| `builder_hint` | `true` | Add a `[pm builder: ...]` line to Builder messages |
| `builder_api_ports` | `[]` | Local `127.0.0.1` ports the API pane may call. pm's own port is always refused |

## ntfy

| Key | Default | Meaning |
|---|---|---|
| `ntfy_url` | `""` | Where pm publishes its own notices and alerts. Empty = no publishing |
| `ntfy_topic_url` | `""` | Topic of agent-written finish messages, mirrored into the Inbox. Empty = off |
| `ntfy_pm_topic_url` | `""` | pm's own alerts topic, merged into the mirror. Empty = off |
| `ntfy_ui_url` | `""` | Link to the ntfy web UI; empty = scheme and host of `ntfy_url` |
| `ntfy_ca` | unset | CA file for ntfy TLS; unset = system store |
| `ntfy_inbox_count` | `10` | Rows in the Inbox ntfy panel |
| `ntfy_cache_s` | `60` | Mirror cache age |
| `ntfy_stream` | `true` | Keep a push stream open per topic; `false` = poll |
| `ntfy_history` | `"365d"` | How far back the mirror reads |
| `ntfy_debounce_s` | `30.0` | Min gap between two notices for one agent |
| `ntfy_dedupe_s` | `600.0` | The same agent + state is not re-sent within this window |

## Health alerts

Sent to `ntfy_url`. A metric must be bad for `alerts_sustain` samples in a row.

| Key | Default | Meaning |
|---|---|---|
| `alerts_enabled` | `true` | Master switch |
| `alerts_interval_s` | `60` | Sample interval |
| `alerts_sustain` | `3` | Bad (or good) samples before an alert (or recovery) |
| `alerts_repeat_s` | `3600` | Min gap between repeats of one alert |
| `alerts_load_per_cpu` | `1.0` | 5-minute load / CPUs |
| `alerts_mem_pct` | `92.0` | Used memory % |
| `alerts_swap_pct` | `60.0` | Used swap % |
| `alerts_disk_pct` | `92.0` | Used disk % |
| `alerts_containers` | `true` | Alert on unhealthy or exited containers |
| `alerts_hosts` | `true` | Alert on unreachable hosts from `servers` |
| `alerts_host` | `""` | Host name in alerts; empty = this host's short name |

## Servers, containers and VMs

| Key | Default | Meaning |
|---|---|---|
| `servers` | `[]` | Other hosts to check by TCP: `[{name, host, port, note}]` |
| `proc_manage` | `true` | Allow stop / kill of processes; `false` = list only |
| `container_manage` | `true` | Allow stop / restart / kill of containers |
| `container_protected` | `["nginx*", "ntfy*", "gitea*", "adguard*"]` | Container name globs never changed |
| `virsh_bin` | `"virsh"` | libvirt CLI |
| `virsh_uri` | `"qemu:///system"` | libvirt connection |
| `virsh_write_sg` | `true` | Run writes through `sg libvirt` when pm lacks the group |
| `vm_pool` | `"images"` | Storage pool for VM images |
| `vm_manage` | `true` | Allow VM actions; `false` = list only |
| `vm_protected` | `[]` | VM name globs never changed, e.g. `["*prod*"]` |

## Display and process

| Key | Default | Meaning |
|---|---|---|
| `display_utc_offset_h` | `0` | Fixed UTC offset (hours) for times in the UI |
| `dev_banner` | `false` | Show a DEV tag (dev instances) |
| `reload_unit` | `"pm.service"` | systemd user unit the Reload button restarts; `""` hides it |
| `watchdog_stall_log_s` | `2.0` | Log the stack when the event loop stalls this long |
| `watchdog_exit_s` | `30.0` | Exit (systemd restarts pm) after a stall this long; `0` = never |
| `disable_poll` | `false` | No tmux poller, alerts or warm-up (tests, dev) |
| `watchdog` | `false` | Start the watchdog even with `disable_poll` |

## Agent launch and composer

| Key | Default | Meaning |
|---|---|---|
| `agent_bins` | `{}` | Agent kind to absolute binary, e.g. `{ claude = "/usr/local/bin/claude" }`. Default `~/.local/bin/<kind>` |
| `agent_models` | `{}` | Agent kind to extra model ids for the picker |
| `allow_bypass` | `true` | Kill switch: anything but `true` disables every no-prompt (bypass) mode |
| `worktree_root` | unset | Where agent worktrees go; unset = `<project>-wt` next to the project |
| `snippets` | `[]` | Composer snippets: `[[label, text], ...]` or `{label = text}` |
| `layouts` | `{}` | Project to tmux window layout used by "ensure" |

## Theme and plugins

| Key | Default | Meaning |
|---|---|---|
| `theme` | `"classic"` | Default UI theme for browsers that have not picked one. `classic` is built in; `dt` ships with pm as a first-party plugin; other plugins can add more. An unknown name logs a warning and uses `classic`. A theme that overrides `_settings_panel.html` must keep its `plugin_sections` line, or plugin Settings sections disappear |
| `plugins_enabled` | `true` | Load installed plugins (entry-point group `pm.plugins`, which includes pm's own `dt` theme). `false` loads none. The environment variable `PM_NO_PLUGINS=1` skips discovery of installed plugins (the test suite sets it; `disable_poll` does not, so a dev instance loads installed plugins). Plugin background loops run only when polling is on (not `disable_poll`) |
| `[plugins.<name>]` | none | Settings for one plugin, handed to it as a table. pm does not read them itself; a value that is not a table is logged and dropped |

Two themes ship with pm:

- `classic`: the built-in UI and the fallback for every page.
- `dt`: a keyboard-driven shell (one sidebar, session tabs, a chat aside on any page, one search dialog). It is
  pm's own plugin (`pm.web.dt`, registered by pm's `pm.plugins` entry point), so it loads like any plugin. Its
  docs: `docs/themes/dt/README.md`.

Each browser picks its theme; the first match wins:

1. `?ui=<name>` on any URL (e.g. `?ui=dt`, `?ui=classic`). A known name is also remembered in the cookie `pm_ui`.
2. The cookie `pm_ui`, set from Settings › UI theme (shown when more than one theme is loaded). Each browser keeps
   its own.
3. `theme` in pm.toml.

A name that is not loaded is ignored at each step. If `dt` fails to load, or `plugins_enabled = false` (or
`PM_NO_PLUGINS=1`), `dt` is not loaded, so `?ui=dt`, a `pm_ui=dt` cookie and `theme = "dt"` all fall back to
`classic` without an error page.

Other plugins are separate Python packages installed into pm's environment. A plugin can add pages, nav entries,
Settings sections, background loops, themes, templates and static files (served at `/static/p/<name>/`).
A plugin can also add external links (called on each page view; the first plugin with a URL wins):

- `project_links(project)` returns `(title, [ExtLink(label, url, desc, icon)])` or `None`: one row of
  link badges below the project page's docs panel (`desc` = tooltip, `icon` = `files` or `plans` shows an icon).
- `agent_cwd_url(agent, project)` returns a URL or `None`: the agent page's working-directory link (`project` is `None` outside a project).
- `agent_transcript_url(agent)` returns a URL or `None`: the chat's Full Transcript link.

Only `http://` and `https://` URLs are used; other URLs, wrong return types and hooks that raise are logged and
ignored. Without a plugin these links are not shown.

A plugin that fails to load is logged and skipped; pm still starts. Its POST routes pass the same login,
Origin, CSRF, tier and audit checks as pm's own; a plugin with an unchecked POST route is not loaded.

```toml
theme = "classic"
[plugins.example]
refresh_s = 60
```

## `[extras]`

Older configs put these keys under an `[extras]` table: `agent_bins`,
`agent_models`, `allow_bypass`, `worktree_root`, `snippets`, `alerts_host`,
`disable_poll`, `watchdog`, `ntfy_ca`, `ntfy_debounce_s`, `ntfy_dedupe_s`,
`ntfy_url`, `servers`, `ntfy_ui_url`, `layouts`.

pm still reads them there. A top-level key wins over the same key in
`[extras]`. Unknown keys under `[extras]` are logged and kept, but nothing
reads them. New configs should set every key at the top level.
