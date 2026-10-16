# Install pm

pm runs on a Linux host as your normal user, as a systemd user unit. It binds
to loopback and is reached through a TLS reverse proxy.

## 1. Requirements

| Tool | Min version | Required | Without it |
|---|---|---|---|
| Linux | recent kernel with `/proc` | yes | pm does not run (host metrics, process list and the terminal use `/proc` and pty) |
| uv | 0.12 | yes | no way to install the Python deps |
| Python | 3.13 | yes | uv installs it for you if missing |
| tmux | 3.x, **same version as the running tmux server** | yes | no sessions, no agents (an older/newer client shows an empty list) |
| git | 2.x | yes | no git status, history or actions |
| Docker Engine + Compose v2 | | no | Stacks tab and container list hidden |
| libvirt `virsh` + `qemu-img` | | no | VMs tab hidden. Writes run through `sg libvirt` when pm's process lacks the `libvirt` group |
| OpenSSH client (`ssh`, `ssh-keygen`) | | no | pm's own SSH key (Settings > SSH) and git over ssh |
| ntfy server | | no | no push notifications, no ntfy mirror in the Inbox |
| `claude`, `codex`, `devin`, `cursor-agent` | | no | that agent kind cannot be launched or resumed |
| `tmux-agents` (`deploy/tmux-agents/`) | | no | agent ids for `pmctl` and agent-to-agent messages unavailable |
| Playwright | | dev only | UI test suite (`pytest -m ui`) |

pm never needs root. It never binds `0.0.0.0`.

## 2. Install uv

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

Other methods: <https://docs.astral.sh/uv/getting-started/installation/>.

## 3. Clone and sync

```sh
git clone https://github.com/dungtq/polymux.git ~/polymux
cd ~/polymux
uv sync
```

`uv sync` creates `.venv/` with the `pm` and `pmctl` commands.

## 4. Config

pm reads `~/.config/pm/pm.toml`. A missing file means all defaults.

```sh
mkdir -p ~/.config/pm
cp -n deploy/pm.toml.example ~/.config/pm/pm.toml
chmod 600 ~/.config/pm/pm.toml
```

The keys you will most likely set:

| Key | Why |
|---|---|
| `tmux_bin` | Absolute path to the tmux that matches your running server (see below) |
| `base_url` | The public `https://` URL of pm behind the proxy. Passkeys use its host name |
| `binds` | Add the Docker bridge gateway if your proxy runs in a container |
| `dev_root`, `extra_projects` | Where your projects live |
| `git_allowed_hosts` | Git hosts the remotes panel may point at; also enables commit links |
| `ntfy_url`, `ntfy_topic_url`, `ntfy_pm_topic_url` | Your ntfy server (empty = off) |

All keys: [CONFIG.md](CONFIG.md).

### The tmux_bin gotcha

The tmux client and the tmux server must be the same version. A systemd user
unit gets a short `PATH`. If you installed tmux with a version manager (mise,
asdf, Homebrew on Linux, nix) your shell finds that tmux, but the unit finds
the distro's `/usr/bin/tmux`. The two speak different protocols, and pm shows
an empty session list.

Check:

```sh
command -v tmux
tmux -V                                # client
tmux display-message -p '#{version}'   # running server
```

Then pin the absolute path in `pm.toml`:

```toml
tmux_bin = "/home/you/.local/share/mise/installs/tmux/3.5a/tmux"
```

Update it and restart pm after every tmux upgrade.

### git remote name

pm's git panel fetches, pulls and pushes the remote named `gitdev`. In each
project, add your push remote under that name:

```sh
git remote add gitdev git@git.example.lan:you/project.git
```

Commit hashes link to `https://<host>/<owner>/<repo>/commit/<sha>` when that
remote's host is in `git_allowed_hosts`.

## 5. Login token

On first start pm creates two files, each with a random 32-byte URL-safe
value, mode 0600, if they do not exist:

| File | Use |
|---|---|
| `~/.config/pm/token` | Your login token |
| `~/.config/pm/dev-token` | A second full-access token for agents or scripts that drive the UI (for example Playwright checks). Delete it to stop accepting it; pm creates a new value on the next start |

You may also create `token` yourself before the first start. Never commit or
print either file. After the first login you can register a passkey in
Settings > Access and sign in with it.

## 6. Run it once

```sh
uv run pm
curl -fsS http://127.0.0.1:8093/healthz
```

Expected: `{"ok":true,...}`. Open <http://127.0.0.1:8093> on the same host
and log in.

The session cookie is `Secure`. Browsers accept it over plain `http://` only
on `127.0.0.1` and `localhost`. From any other machine you need HTTPS
(step 8).

## 7. systemd user unit

```sh
mkdir -p ~/.config/systemd/user
cp deploy/pm.service ~/.config/systemd/user/pm.service
# edit WorkingDirectory and ExecStart to point at your clone, e.g. %h/polymux and %h/polymux/.venv/bin/pm
systemctl --user daemon-reload
systemctl --user enable --now pm
systemctl --user status pm
journalctl --user -u pm -f
```

To keep pm running when you are logged out (and to start it at boot):

```sh
sudo loginctl enable-linger "$USER"
```

The topbar Reload button restarts the unit named in `reload_unit`
(default `pm.service`). Set it to `""` to hide the button.

## 8. Reverse proxy (TLS)

Put pm behind a TLS proxy and set `base_url` to its URL. The proxy must:

- pass `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`,
- support WebSocket upgrade (terminal),
- not buffer responses (`proxy_buffering off`, `proxy_http_version 1.1`,
  `proxy_read_timeout 1h`) so server-sent events stream.

Examples:

- nginx vhost: [`deploy/nginx/pm.conf`](../deploy/nginx/pm.conf).
- A full Docker stack with nginx-ui, a private CA and ntfy:
  [`deploy/stack/README.md`](../deploy/stack/README.md).

If nginx runs in a container it cannot reach the host's `127.0.0.1`. Bind pm
also on the Docker bridge gateway and proxy to that address:

```toml
binds = ["127.0.0.1", "172.17.0.1"]   # your bridge gateway: docker network inspect bridge
base_url = "https://pm.example.lan"
```

`allowed_origins` defaults to `[base_url]`. Set it only if you reach pm under
more than one origin.

## 9. Claude Code hook (optional)

Without it pm polls tmux every 2 s. With it, Claude Code reports
waiting / finished / running at once. pm creates the secret
`~/.local/state/pm/hook_secret` (0600) on first start. Install the hook
snippet from [claude-hook.md](claude-hook.md) into `~/.claude/settings.json`
yourself; pm never edits it.

## 10. tmux-agents and tmux config (optional)

`deploy/tmux-agents/` holds a small script that gives each agent pane an id
(`tmux-agents id`) and lets agents message each other. pm and `pmctl` use it
to record who made a change.

```sh
install -m 755 deploy/tmux-agents/tmux-agents ~/.local/bin/tmux-agents
```

Details: [`deploy/tmux-agents/README.md`](../deploy/tmux-agents/README.md).
To use it, set `actor_cmd = ["tmux-agents", "id"]` in `pm.toml` (an absolute path if it is not on the
unit's `PATH`); without it `pmctl` uses `$PM_ACTOR`, else `agent`.

`deploy/tmux/` has the author's `tmux.conf`. It is optional; pm works with
any tmux config.

## 11. Verify

```sh
systemctl --user is-active pm
curl -fsS http://127.0.0.1:8093/healthz
curl -sI https://pm.example.lan/login | head -1        # 200 through the proxy
curl -si -X POST http://127.0.0.1:8093/hooks/claude | head -1  # 401 without the secret
```

Then log in, open Projects and tmux, and check that your sessions appear. An
empty tmux list almost always means `tmux_bin` is wrong.

## Upgrade

```sh
cd ~/polymux
git pull
uv sync
systemctl --user restart pm
```

Read [CHANGELOG.md](../CHANGELOG.md) for config changes first. pm keeps a
daily copy of its database in `~/.local/state/pm/backups/`.

## Uninstall

```sh
systemctl --user disable --now pm
rm ~/.config/systemd/user/pm.service
systemctl --user daemon-reload
rm -rf ~/polymux   # the clone
rm -rf ~/.config/pm              # config, token, dev-token, totp
rm -rf ~/.local/state/pm         # pm.db, audit log, sessions, backups
```

Also remove the proxy vhost and the Claude Code hook entries if you added them.
