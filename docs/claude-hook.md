# Claude Code hook for pm

pm learns "waiting / finished / running" instantly when Claude Code POSTs its
hook events to `http://127.0.0.1:8093/hooks/claude`. Polling still works
without it (every 2 s); hooks just win for a few seconds.

Rules of the receiver: loopback peer only, no `X-Forwarded-For`/`X-Real-IP`,
header `X-PM-Hook-Secret` must match `~/.local/state/pm/hook_secret` (created
0600 by pm on first start), body max 16 KB. Notification text is ignored.

## Snippet for `~/.claude/settings.json`

Add these entries **alongside** any hooks you already have (append to each
array, do not replace it). You install this yourself; pm never edits it.

```json
{
  "hooks": {
    "Notification": [
      { "hooks": [ { "type": "command", "command": "curl -fsS --max-time 2 -H \"X-PM-Hook-Secret: $(cat ~/.local/state/pm/hook_secret)\" --data @- http://127.0.0.1:8093/hooks/claude >/dev/null 2>&1 || true" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "curl -fsS --max-time 2 -H \"X-PM-Hook-Secret: $(cat ~/.local/state/pm/hook_secret)\" --data @- http://127.0.0.1:8093/hooks/claude >/dev/null 2>&1 || true" } ] }
    ],
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "curl -fsS --max-time 2 -H \"X-PM-Hook-Secret: $(cat ~/.local/state/pm/hook_secret)\" --data @- http://127.0.0.1:8093/hooks/claude >/dev/null 2>&1 || true" } ] }
    ]
  }
}
```

Claude Code feeds the hook JSON (`hook_event_name`, `session_id`, `cwd`, ...)
on stdin; `--data @-` forwards it. The `|| true` keeps pm downtime from ever
blocking Claude.

## Check

`curl -i -X POST http://127.0.0.1:8093/hooks/claude` answers 401 without the secret header: the receiver is up.
