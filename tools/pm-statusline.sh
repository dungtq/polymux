#!/bin/sh
# Claude Code status line command that feeds pm's usage limits.
# Reads the status line JSON on stdin, stores only its `rate_limits` object (percentages and reset
# times, no text) in ~/.local/state/pm/claude-limits.json, and prints nothing.
# Enable: "statusLine": {"type": "command", "command": "~/dev/pm/tools/pm-statusline.sh"} in
# ~/.claude/settings.json (or chain it from an existing status line script).
d="${PM_STATE_DIR:-$HOME/.local/state/pm}"
mkdir -p "$d" && exec python3 -c '
import json, os, sys
try:
    rl = json.load(sys.stdin).get("rate_limits")
except Exception:
    sys.exit(0)
if isinstance(rl, dict) and rl:
    p = os.path.join(sys.argv[1], "claude-limits.json")
    open(p + ".tmp", "w").write(json.dumps(rl))
    os.replace(p + ".tmp", p)
' "$d"
