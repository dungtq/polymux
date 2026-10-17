import json
import os
import subprocess
import time
from pathlib import Path

from pm.agents import limits

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "pm-statusline.sh"


def _run(state, env_extra):
    rl = {"five_hour": {"used_percentage": 40, "resets_at": time.time() + 3600}}
    env = {**os.environ, "PM_STATE_DIR": str(state), **env_extra}
    env.pop("CLAUDE_CONFIG_DIR", None) if "CLAUDE_CONFIG_DIR" not in env_extra else None
    subprocess.run(["sh", str(SCRIPT)], input=json.dumps({"rate_limits": rl}), text=True, env=env, check=True)


def test_statusline_writes_one_file_per_account(tmp_path):
    _run(tmp_path, {})
    _run(tmp_path, {"CLAUDE_CONFIG_DIR": str(tmp_path / "accounts" / "claude" / "work")})
    _run(tmp_path, {"CLAUDE_CONFIG_DIR": "/elsewhere/other"})  # not a pm account folder: the default file
    assert sorted(p.name for p in tmp_path.glob("claude-limits*.json")) == ["claude-limits-work.json", "claude-limits.json"]


def test_read_limits_picks_the_account_file(tmp_path):
    now = time.time()
    for name, pct in (("claude-limits.json", 10), ("claude-limits-work.json", 77)):
        (tmp_path / name).write_text(json.dumps({"five_hour": {"used_percentage": pct, "resets_at": now + 3600}}))
    assert limits.read_limits(tmp_path, now)[0].pct == 10
    assert limits.read_limits(tmp_path, now, "work")[0].pct == 77
    assert limits.read_limits(tmp_path, now, "gone") == []
