# Security

pm can send text to coding agents, kill tmux windows, stop containers and
change VMs. Treat access to pm like a shell on the host.

## Reporting a problem

Please do not open a public issue for a security problem. Report it
privately on GitHub: **Security** tab → **Report a vulnerability**
(<https://github.com/dungtq/polymux/security/advisories/new>), with steps to
reproduce. You should get an answer within a
week. Fixes go out as a normal release with a CHANGELOG entry.

## Threat model

- One user on one Linux host. pm runs as that user, never as root.
- pm listens on loopback (and, if you add it, a Docker bridge gateway). It is
  reached from other machines only through a TLS reverse proxy.
- Anyone who can log in can do what the tier ceiling allows on every
  unprotected tmux session, container and VM.

## Deployment rules

- Never bind `0.0.0.0` or a public address. pm does not stop you; it is your
  job.
- Always put pm behind a TLS reverse proxy for any access that is not from
  the host itself. The session cookie is `Secure`, `HttpOnly`,
  `SameSite=Strict`, so browsers send it over plain `http://` only to
  `127.0.0.1` / `localhost`.
- Set `base_url` to the public `https://` URL. `allowed_origins` defaults to
  it.
- Keep `~/.config/pm/` and `~/.local/state/pm/` at mode 0700 / 0600.

## Login

| Item | What the code does |
|---|---|
| Token | `~/.config/pm/token`. If missing at start, pm creates it with a random 32-byte URL-safe value, mode 0600 (`O_EXCL`, never overwrites). Compared with `hmac.compare_digest` |
| Dev token | `~/.config/pm/dev-token`, created the same way. A second full-access token for scripts and agents. Delete it to revoke; a new value appears on the next start |
| Rate limit | 5 free failures per client, then exponential backoff (2 s doubling, max 300 s) |
| Passkeys | WebAuthn, registered in Settings after a token login. The relying-party id is the host of `base_url` |
| TOTP | Optional. Set up in Settings; secret in `totp_file` (0600) |
| Unlock | `require_unlock = true` asks for a TOTP or passkey unlock (valid `unlock_ttl_s`, 10 min) before every change. Off by default |
| Session | Random id in a signed cookie; server-side store keeps only hashes of ids on disk. Sessions can be listed and revoked in Settings |

Rotate the token: write a new random value to the file (0600) and restart
pm. pm never prints, logs or sends the token.

## Every change

Every mutating request goes through one guard (`src/pm/security/guard.py`):

1. Logged-in session, else 401.
2. Origin check against `allowed_origins`, else 403.
3. Per-session CSRF token, else 403.
4. Unlocked session, only with `require_unlock = true`.
5. Tier check against `tier_ceiling`.
6. Audit line written to `state_dir/audit.jsonl` (0600, fsync). If the write
   fails, the request is refused with 503 (fail closed).

Changes are POST only. Denied attempts are audited too. Message text sent to
agents is stored in the audit log only as length and SHA-256.

## Tiers

| Tier | Name | Examples |
|---|---|---|
| 0 | view | read pages |
| 1 | safe | edit backlog / TODO, git remotes, project settings |
| 2 | message | send text to agents, fork, exit, quick chat, terminal ticket |
| 3 | terminal | reserved, not enabled |

`tier_ceiling` (default 2) caps what any request may do.

## Protected tmux sessions

Every tmux session is protected unless its name matches a glob in
`unprotected_sessions` (default empty). Sessions named `pm*` are always
protected. Auto-exit of idle agents acts only in `autoexit_sessions` and never
in `pm*`.

Containers matching `container_protected` and VMs matching `vm_protected` are
never changed.

## Pane text

Pane text is stripped of terminal escapes and then masked
(`src/pm/security/mask.py`: password / secret / API-key patterns) before it
leaves the process. It is never logged, indexed or sent to ntfy.

One exception: the optional read-only terminal (`terminal_enabled`, off by
default) streams raw tmux output to the browser. It is still never logged.
Input typed into it never reaches tmux.

## Subprocesses

tmux, git, docker and virsh run with argv lists, a fixed binary, a timeout and
no shell. tmux targets are ids. User values come after `--`.

## Claude Code hook

`POST /hooks/claude` accepts only loopback peers without forwarding headers,
needs the secret from `state_dir/hook_secret` (0600, created by pm), and limits
the body to 16 KB.

## Builder preview

The preview iframe is sandboxed with a CSP that blocks network calls
(`connect-src 'none'`). Extra frame origins must be listed in
`builder_frame_hosts`.
