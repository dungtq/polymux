# pm edge stack (Docker Compose)

An optional HTTPS front for pm on one Linux host. It runs three containers:

| Service | Image | What it does |
|---|---|---|
| `cert-manager` | built from `./cert-manager` (Alpine 3.20) | Keeps a private root CA and one leaf certificate (for example `*.example.lan`). Renews the leaf before it expires. |
| `nginx-ui` | `uozi/nginx-ui:v2.5.6` | nginx on ports 80 and 443 for the pm and ntfy sites, plus the nginx-ui admin panel on port 9000. |
| `ntfy` | `binwiederhier/ntfy:v2.28.0` | Push notifications for pm. It has no host port. nginx reaches it as `http://ntfy:80`. |

pm is not part of the stack. It runs on the host as a systemd user unit on port 8093.
nginx reaches it through `host.docker.internal`, which points to the Docker bridge gateway.

```
browser ──https──▶ nginx-ui :443 ──▶ host.docker.internal:8093  (pm on the host)
                                 └─▶ ntfy:80                     (container)
```

## Files

| Path | Purpose |
|---|---|
| `compose.yaml` | The three services. |
| `env.example` | Every setting, with comments. Copy it to `.env` (`.env` stays out of git). |
| `cert-manager/` | CA and leaf script, health check, Dockerfile. |
| `nginx/nginx-ui.conf` | Shared maps (WebSocket upgrade) and catch-all servers. |
| `nginx/sites/*.conf.template` | The pm and ntfy sites, with `${PM_HOST}`, `${NTFY_HOST}`, `${PM_PORT}` placeholders. |
| `ntfy/server.yml` | ntfy settings (behind a proxy, message cache). |
| `install-sites.sh` | Fills in the templates from `.env` and installs them into nginx-ui. |
| `data/` | Created at first start: CA, leaf, nginx-ui and ntfy data. Keep it out of git. |

## Prerequisites

- Linux with Docker Engine and the Compose v2 plugin (`docker compose version`).
- `envsubst` on the host (package `gettext-base` on Debian/Ubuntu, `gettext` elsewhere).
- pm installed and running as a systemd user service (see [docs/INSTALL.md](../../docs/INSTALL.md)).
- Run every command below from this directory (`deploy/stack/`), or copy it somewhere of your own
  first (for example `~/pm-stack`) so `data/` lives outside the clone.
- Ports 80 and 443 free on the host.

## Setup

1. Copy and edit the settings.

   ```bash
   cp env.example .env
   $EDITOR .env
   ```

   Set at least `PM_HOST`, `NTFY_HOST` and `CERT_DOMAINS`. The wildcard in `CERT_DOMAINS`
   must cover both host names. Add the server's LAN IP to `CERT_IPS` if you open the panel by IP.

2. Start the stack.

   ```bash
   docker compose up -d --build
   ```

   cert-manager creates the CA and the leaf first. nginx-ui starts when the leaf is ready
   (up to two minutes on the first start, RSA 4096 key generation).

3. Install the pm and ntfy sites into nginx.

   ```bash
   ./install-sites.sh            # both sites
   ./install-sites.sh --print    # only show the rendered files
   ```

   The script copies the files into `/etc/nginx/sites-available` in the container, links them
   in `sites-enabled`, runs `nginx -t` and reloads nginx. Run it again after you change
   `PM_HOST`, `NTFY_HOST` or `PM_PORT`. If `nginx -t` fails, it disables the new sites and stops.
   The sites also show up in the nginx-ui panel, where you can edit them.

4. Trust the root CA on every client.

   The root certificate is `data/pki/ca/ca.crt` (a copy is `data/pki/ca/root-ca.crt`).
   Clients can also download it with `curl -o ca.crt http://<server>/root-ca.crt`.

   - Linux (Debian/Ubuntu): `sudo cp ca.crt /usr/local/share/ca-certificates/pm-stack.crt && sudo update-ca-certificates`
   - macOS: open it in Keychain Access, add it to System, set "Always Trust" for SSL.
   - Windows: `certmgr.msc` → Trusted Root Certification Authorities → Import.
   - Firefox keeps its own store: Settings → Certificates → Import.
   - iOS: install the profile, then enable it in Settings → General → About → Certificate Trust Settings.

   Never copy `data/pki/ca/ca.key` off the server.

5. Make the names resolve to the server, in DNS or in each client's `/etc/hosts`:

   ```
   192.0.2.10  pm.example.lan
   192.0.2.10  ntfy.example.lan
   ```

   Replace `192.0.2.10` with the server's address.

6. Set up the nginx-ui panel (optional). It listens on `https://127.0.0.1:9000` by default.
   From another machine use an SSH tunnel (`ssh -L 9000:127.0.0.1:9000 server`), or set
   `NGINX_UI_PANEL_BIND` in `.env`. Open `/install` and use the one-time password from
   `sudo cat data/nginx-ui/.install_secret`. Do not use nginx-ui's own "self-signed" certificate
   for the panel; cert-manager owns it.

## pm settings (`~/.config/pm/pm.toml`)

pm must know its public URL and must listen where nginx can reach it.

```toml
base_url = "https://pm.example.lan"
allowed_origins = ["https://pm.example.lan"]

# Loopback plus the Docker bridge gateway (docker0). Never 0.0.0.0.
binds = ["127.0.0.1", "172.17.0.1"]

ntfy_url = "https://ntfy.example.lan/pm"
ntfy_topic_url = "https://ntfy.example.lan/agent"
ntfy_pm_topic_url = "https://ntfy.example.lan/pm"
```

Restart pm after the change: `systemctl --user restart pm`.

Notes on `binds`:

- `host.docker.internal:host-gateway` resolves to the `docker0` address. It is `172.17.0.1`
  unless your Docker daemon sets another `bip`. Check it with
  `ip -4 addr show docker0`.
- To use the stack network's own gateway instead, read it with
  `docker network inspect pm-edge -f '{{(index .IPAM.Config 0).Gateway}}'`, put that address in
  `binds`, and replace `host-gateway` in `compose.yaml` with the same address.
- The address must exist when pm starts. If pm starts before Docker, restart pm once Docker is up.
- A host firewall (ufw, firewalld) may block container-to-host traffic. Allow the Docker
  subnet to reach `PM_PORT` on the gateway address only.
- Other containers on the host can reach pm on that address too. pm still requires a login.

pm's own clients (for example the finish hook of your coding agents) must also trust the root CA
to post to `https://NTFY_HOST`.

## ntfy

- Topics are open to anyone who can reach `NTFY_HOST`. To require logins, enable `auth-file` and
  `auth-default-access` in `ntfy/server.yml`, then add users with
  `docker compose exec ntfy ntfy user add <name>`.
- `base-url` comes from `NTFY_HOST` (the compose file sets `NTFY_BASE_URL`).
- Messages are cached for 24 hours in `data/ntfy/cache.db`.

## Certificates

- `CA_MODE=generate` creates the CA once. `CA_MODE=import` uses your own `ca.crt` and `ca.key`,
  placed in `data/pki/ca/` before the first start.
- The leaf is reissued when it is missing, expires within `CERT_RENEW_BEFORE_DAYS`, or when
  `CERT_DOMAINS` / `CERT_IPS` change (`docker compose up -d` after editing `.env`).
- After a reissue cert-manager restarts nginx-ui, so the panel and nginx load the new files.
  For that it mounts `/var/run/docker.sock`. This gives the container full Docker API access.
  If you do not want that, remove the line in `compose.yaml` and run
  `docker compose restart nginx-ui` after a renewal. The cert-manager log says when.
- Optional Pushover messages on renewal and failure: set `PUSHOVER_TOKEN` and `PUSHOVER_USER`.

## Root-owned data

The containers run as root, so `data/pki`, `data/nginx-ui` and `data/ntfy` are owned by root.
Use `sudo` to read or delete them. Backup and sync tools running as your user may fail on them;
exclude `data/` there or back it up as root. `data/pki/ca/ca.key` is mode 400 and must stay private.

## Without this stack

The stack is optional. Any reverse proxy on the host works if it:

- terminates TLS for `PM_HOST`,
- proxies to `http://127.0.0.1:8093` (then `binds = ["127.0.0.1"]` is enough),
- sends `Host`, `X-Forwarded-Proto` and `X-Forwarded-Host`,
- passes WebSocket upgrades (`/term`) and does not buffer responses (SSE),
- uses HTTP/2, so many open tabs do not exhaust the browser's connection limit.

`nginx/sites/pm.conf.template` is a complete example for plain nginx on the host: replace
`host.docker.internal` with `127.0.0.1` and point `ssl_certificate` at your files.
A minimal Caddy example (Caddy proxies WebSockets and streams SSE by default):

```
pm.example.lan {
    tls internal
    reverse_proxy 127.0.0.1:8093
}
```

ntfy can also run on its own (`docs.ntfy.sh`); point the `ntfy_*` URLs in pm.toml at it.

## Day to day

```bash
docker compose ps
docker compose logs -f cert-manager
docker compose pull && docker compose up -d --build   # after changing image tags
docker compose down                                    # data/ stays
```

If you change `HTTPS_PORT` from 443, the sites still redirect to port 443; edit the templates and
use the port in `base_url` and `allowed_origins`.
