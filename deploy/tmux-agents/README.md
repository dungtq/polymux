# tmux-agents (optional)

`tmux-agents` is a single-file Python 3 script (stdlib only, plus tmux 3.x). It spawns coding agents
(Claude Code, Codex, Devin, or a plain shell) in their own tmux windows, sends them prompts, and pastes
their replies back into the pane that asked, so agents can hand work to each other. With `pmctl` on the
`PATH` it also coordinates parallel work: agents claim what they are about to change, close the claim
when done, and check the other agents before merging.

pm itself needs only one command: `tmux-agents id` prints the pm agent id of the current pane (for
example `claude-7cd08a`), so `pmctl` can record which agent wrote a tracker entry. Without the script pm
works the same; `pmctl` uses `$PM_ACTOR`, else the name `agent`.

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
`$CLAUDE_CODE_SESSION_ID` is set, it prints `claude-<6-character hash of that id>`, the same id pm gives
that Claude session. Otherwise it prints nothing and exits 1.

## Windows and messages

- `spawn NAME --agent claude|codex|devin|shell` always opens a new tmux window (detached unless
  `--focus`) with `remain-on-exit on`, so the window and its output stay when the agent quits. Nothing
  closes it except `tmux-agents kill NAME`.
- Address an agent by its name, a pane id (`%12`) or its pm agent id (`claude-7cd08a`). pm ids resolve
  from tmux pane tags, pm's database (read-only) or the running Claude process, so this works while the
  pm web service is down.
- `send` pastes the prompt as one bracketed paste once the target's screen settles (at most 3 s; a busy
  agent queues it). A target that shows an interactive prompt (permission dialog, y/n, menu) gets
  nothing pasted: the message waits in its inbox and a short-lived background retrier delivers it when
  the prompt is gone. `inbox` shows waiting messages; `--now` skips the checks.
- The receiving agent answers with `tmux-agents reply ID [--status done|failed|blocked] "result"`.
  `broadcast --project NAME` sends a note to every live agent working in that project.

## Coordination (needs `pmctl`)

Claims live in pm's database through `pmctl`, which writes it directly (no web service needed).

```sh
tmux-agents claim "Fix the inbox sort" --path 'src/pm/agents/inbox*' --issue 12   # before implementing
tmux-agents board                                                                 # who is doing what
tmux-agents done c3 --commit abc1234                                              # after committing
tmux-agents merge-check                                                           # before merging (exit 1 = overlap)
```

`claim` and `done` message the agents whose claims overlap. `merge-check` compares the branch's changed
files with the other agents' active claims and with the other git work trees, and warns when the base
branch has moved.

Run `tmux-agents` with no arguments for the full usage. Message state lives in
`${XDG_STATE_HOME:-~/.local/state}/tmux-agents` (`$TA_STATE` overrides it). Requirements: python3 >= 3.10
and tmux 3.x.

## License

Same as polymux: MIT, see [LICENSE](../../LICENSE).
