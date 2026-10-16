# pm operations

## Service

systemd user unit `~/.config/systemd/user/pm.service` (source
`deploy/pm.service`), runs `~/dev/pm/.venv/bin/pm`, `Restart=on-failure`.

    systemctl --user status pm
    systemctl --user restart pm
    journalctl --user -u pm -f
    curl -fsS http://127.0.0.1:8093/healthz

After editing the unit: `systemctl --user daemon-reload && systemctl --user restart pm`.

## Reverse proxy

pm speaks plain HTTP on loopback; put a TLS reverse proxy in front. Examples: `deploy/nginx/pm.conf`
(nginx on the host) and `deploy/stack/` (nginx-ui + ntfy in Docker). The proxy needs `proxy_buffering off`,
HTTP/1.1 upgrade headers (SSE and `/term` websocket) and `proxy_read_timeout 1h`.

When nginx runs in a container it cannot reach the host's `127.0.0.1`. Proxy to the docker gateway
(e.g. `http://172.17.0.1:8093`) and add that address to `binds` in `pm.toml`. Do not bind `0.0.0.0`.

## tmux_bin gotcha

The systemd user PATH often lacks a tmux installed by mise, asdf or by hand, so plain `tmux` may resolve to
an older system tmux that cannot talk to the running server (protocol mismatch, empty session list). Pin it:

    tmux_bin = "/path/to/the/tmux/that/runs/your/server"   # `tmux -V` inside a session; `command -v tmux`

Restart pm after a tmux upgrade and update the path.

## State

`~/.local/state/pm/` (`state_dir`, created on first use):

- `audit.jsonl`: append-only audit log; mutations fail closed if it cannot be written
- `pm.db`: SQLite state
- `backups/pm-YYYY-MM-DD.db`: daily copies of `pm.db` (see Backup below)
- `hook_secret`, `passkeys.json`: planned; not created by the current code

Config and secrets live in `~/.config/pm/` (`pm.toml`, `token`, `totp`, all
0600). Never print or commit them.

Backup: copy `~/.config/pm/` and `~/.local/state/pm/` while stopped, or just
`audit.jsonl` and `pm.db` live (SQLite: use `sqlite3 pm.db ".backup f"`).
pm itself writes one online copy of `pm.db` a day to `backups/pm-YYYY-MM-DD.db`
(dir 0700, files 0600; first try ~60 s after start, then every 6 h) and keeps
the newest `db_backup_keep` (pm.toml, default `14`; `0` = off). `pmctl backup`
writes today's copy now (`--force` overwrites it) and prints its path.
Rotate `audit.jsonl` by `systemctl --user stop pm`, move the file aside,
start. Rotate the token by writing a new random value to `token` (0600) and
restarting. To re-provision TOTP, stop pm, remove `totp`, start, repeat below.

## Provision TOTP

Log in, open `/unlock`. While no TOTP exists it shows a provision form
(`POST /unlock/provision`, 409 if already provisioned). The otpauth URI is
shown once; add it to the authenticator. It is never logged or audited. Then
unlock with a 6-digit code at `/unlock` (valid `unlock_ttl_s`, 10 min).

## Claude hook

See [claude-hook.md](claude-hook.md) for the snippet to install alongside the
existing Stop hook (installed by the user through the cfg flow).

## Troubleshooting

| Symptom | Check |
|---|---|
| Page slow | `journalctl --user -u pm`; many sessions/panes polled every `poll_interval_s`; check tmux responds (`tmux ls`); look for stuck git calls |
| Empty tmux list | `tmux_bin` not pinned or wrong version (see above); `tmux_socket` set by mistake |
| 403 on POST | Origin not in `allowed_origins`, missing CSRF token, locked session (visit `/unlock`), or tier above `tier_ceiling` or session protected |
| 502 from nginx | pm down, or not bound on the address nginx proxies to; `ss -ltn | grep 8093` |
| SSE stalls or arrives in bursts | vhost needs `proxy_buffering off`, `proxy_http_version 1.1`, `proxy_read_timeout 1h`; compare with `deploy/nginx/pm.conf` and reload |

## Login, step-up and terminal flags (2026-10-07)

Keys in `~/.config/pm/pm.toml`:

| Key | Default | Meaning |
|---|---|---|
| `require_unlock` | `false` | `false`: login (token or passkey) is the gate; mutations still need CSRF + Origin + tier + audit. `true`: also require a TOTP/passkey unlock before every mutation (the old behaviour; `/unlock*` routes come back) |
| `terminal_enabled` | `false` | Enables the read-only browser terminal at `/term/{sid}` (raw `tmux attach -r`; typed input never reaches tmux; stream is unmasked and never logged) |
| `terminal_max` / `terminal_idle_s` / `terminal_max_s` | `2` / `300` / `1800` | Concurrent terminals, idle timeout, hard cap per connection |

Passkey login: sign in with the token once, open `/settings`, register a
passkey, then use "Sign in with passkey" on `/login`.

Terminal notes: sessions matching `pm*` are never attachable. Tickets
(`/term/ticket`, single use, 30 s) appear in the URL query of the WebSocket
request, so nginx and uvicorn access logs contain them; they are useless
after use. When pm is the only client of a session its window takes the
browser's size (see `docs/P7-spike-findings.md`); with other clients attached
their size wins. The CSP is set per response by the app, not by nginx
(login and `/term` need inline styles; every other page does not).

## ntfy mirror

The Inbox and project pages mirror two ntfy topics, `agent` (agent-written finish messages) and `pm` (pm's own alerts), merged by time (read-only, no auth,
cached in memory, refreshed in the background). Keys in `~/.config/pm/pm.toml`:

| Key | Default | Meaning |
|---|---|---|
| `ntfy_topic_url` | `""` (e.g. `https://ntfy.example.lan/agent`) | Agent topic, read with `GET {url}/json?poll=1&since=<ntfy_history>` |
| `ntfy_pm_topic_url` | `""` (e.g. `https://ntfy.example.lan/pm`) | pm's own alerts topic, read the same way and merged in (`""` = skip) |
| `ntfy_inbox_count` | `10` | Rows in the Inbox panel (chips 5/10/20/50 override; remembered in cookie `pm_ntfy_n_inbox`) |
| `ntfy_stream` | `true` | Keep an open push stream per ntfy topic (no polling while connected); `false` = polling only |
| `ntfy_cache_s` | `60` | Cache TTL in seconds (kept in `state_dir/ntfy-feed.json` too, so a restart does not re-poll); stale data is served while refreshing, and a refresh only fetches what is new |
| `ntfy_history` | `365d` | How far back to read |

A message belongs to the project whose directory is the longest prefix of its
tag (the absolute workspace path). Click links are shown only when https on
the host of `base_url`.

Each message is one collapsed row (time, project/tag chip, title); click it
to see the message, timestamp, all tags and the link. "Expand all / Collapse
all" and the expanded state across the 15 s refresh live in
`static/js/ntfy.js` (no inline script, CSP unchanged).

## Alerts

`notify/alerts.py` samples this host every `alerts_interval_s` (60) and publishes
ntfy notices (topic `ntfy_url`, default `""` = off, never `agent`; click link
`{base_url}/servers`). Set `alerts_enabled = false` to turn it off; it is also
skipped when `disable_poll` is set.

| Key | Default | Meaning |
|---|---|---|
| `alerts_sustain` | `3` | Consecutive bad samples before the first alert, and good samples before the recovery notice |
| `alerts_repeat_s` | `3600` | Minimum gap between repeat alerts for one still-bad metric |
| `alerts_load_per_cpu` | `1.0` | 5-minute load average divided by CPUs |
| `alerts_mem_pct` / `alerts_swap_pct` | `92` / `60` | Used memory (from MemAvailable) / swap |
| `alerts_disk_pct` | `92` | Used space on `/`, `/home`, the dev root, `/var/lib/docker` (one per device); inodes alert at 95% |
| `alerts_containers` | `true` | Unhealthy, restarting, dead or non-zero-exit containers |
| `alerts_hosts` | `true` | Hosts from the Servers page unreachable (TCP check) |

Metric keys: `load`, `mem`, `swap`, `disk:<mount>`, `inode:<mount>`,
`container:<name>`, `host:<name>`, `tmux` (tmux server unreachable). A metric
clears only once it is 3 points (load: 15%) below its trigger for `alerts_sustain`
samples. At most 5 notices per cycle, then one "N more alerts" summary.
Priority 4 for alerts (tags project path, `warning`), 3 for recovery
(`white_check_mark`). Messages hold the metric, value and limit only.

State: `~/.local/state/pm/alerts.json` (0600), `{"version": 1, "alerted":
{"<key>": <unix time of last notice>}}`. After a restart those keys do not
re-alert until `alerts_repeat_s` has passed; a metric that recovered meanwhile
sends its recovery notice. Delete the file to reset. Wiring: `alerts.start(app)`
and `await alerts.stop(app)` in the app lifespan.
