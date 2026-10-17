#!/bin/sh
# Claude Code status line command that feeds pm's usage limits.
# Reads the status line JSON on stdin, stores only its `rate_limits` object (percentages and reset
# times, no text) in ~/.local/state/pm/claude-limits.json, and prints nothing. A named account (CLAUDE_CONFIG_DIR is
# <state>/accounts/claude/<name>) writes claude-limits-<name>.json instead: every account has its own plan limits.
# Enable: "statusLine": {"type": "command", "command": "~/polymux/tools/pm-statusline.sh"} (path of your clone) in
# ~/.claude/settings.json (or chain it from an existing status line script).
d="${PM_STATE_DIR:-$HOME/.local/state/pm}"
mkdir -p "$d" && exec python3 -c '
import json, os, sys
try:
    rl = json.load(sys.stdin).get("rate_limits")
except Exception:
    sys.exit(0)
if isinstance(rl, dict) and rl:
    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "").rstrip("/")
    acc = os.path.basename(cfg) if cfg and os.path.dirname(cfg) == os.path.join(sys.argv[1], "accounts", "claude") else ""
    p = os.path.join(sys.argv[1], "claude-limits-%s.json" % acc if acc else "claude-limits.json")
    open(p + ".tmp", "w").write(json.dumps(rl))
    os.replace(p + ".tmp", p)
' "$d"
