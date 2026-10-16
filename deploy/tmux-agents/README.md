# tmux-agents (optional)

`tmux-agents` is a small bash + tmux script. It spawns coding agents (Claude Code, Codex, Devin, or a
plain shell) in tmux panes or windows, sends them prompts, and pastes their replies back into the pane
that asked, so agents can hand work to each other.

pm needs none of that. It uses only one command: `tmux-agents id` prints the pm agent id of the current
pane (for example `claude-7cd08a`), so `pmctl` can record which agent wrote a tracker entry. Without the
script pm works the same; `pmctl` uses `$PM_ACTOR`, else the name `agent`.

## Install

```sh
install -m 0755 deploy/tmux-agents/tmux-agents ~/.local/bin/tmux-agents
tmux-agents help
```

`~/.local/bin` must be on your `PATH`. pm calls it only when `pm.toml` sets

```toml
actor_cmd = ["tmux-agents", "id"]   # use the absolute path if it is not on the PATH of the pm unit
```

## How `id` works

`tmux-agents id` reads pm's database read-only (`~/.local/state/pm/pm.db`, or `$TA_PM_DB`) and prints
the id of the live agent whose last pane is `$TMUX_PANE`. When there is none and
`$CLAUDE_CODE_SESSION_ID` is set, it prints `claude-<6-character hash of that id>`, the same id pm gives that Claude session. Otherwise it
prints nothing and exits 1.

## Other commands

Run `tmux-agents` with no arguments for the full usage (`spawn`, `send`, `reply`, `wait`, `run`, `read`,
`idle`, `ls`, `kill`, `log`). Message state lives in `${XDG_STATE_HOME:-~/.local/state}/tmux-agents`
(`$TA_STATE` overrides it). Requirements: bash, tmux, and `python3` for `id`.

## License

Same as polymux: MIT, see [LICENSE](../../LICENSE).
