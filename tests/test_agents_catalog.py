import dataclasses
import json

from pm.agents import catalog
from pm.core.models import AgentKind

CLAUDE_HELP = """  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name.
  -n, --name <name>                     Set a display name for this session
"""
DEVIN = """Available models (2 families)

Adaptive (adaptive)
  adaptive                                       Adaptive  [$0.5 / 1M Input]

Claude Opus 5.5 (claude-opus-5.5)
  aliases: opus, claude-opus
  claude-opus-5-5-high                           Claude Opus 5.5 High  [1M context, $4 / 1M Input]
"""


def test_parse_claude():
    assert [m[0] for m in catalog.parse_claude(CLAUDE_HELP)] == ["fable", "opus", "sonnet"]


def test_parse_codex_hides_hidden():
    t = json.dumps({"models": [{"slug": "gpt-6-sol", "visibility": "list", "display_name": "S"},
                               {"slug": "gpt-5.5", "visibility": "hide"}]})
    assert catalog.parse_codex(t) == [("gpt-6-sol", "S", "gpt-6-sol", "")]


def test_parse_codex_efforts():
    t = json.dumps({"models": [{"slug": "gpt-6-sol", "visibility": "list", "display_name": "S",
                                "supported_reasoning_levels": [{"effort": "low"}, {"effort": "max"}]}]})
    assert catalog.parse_codex(t) == [("gpt-6-sol", "S", "gpt-6-sol", "low,max")]


def test_claude_rows_full_ids_and_1m():
    help_ = CLAUDE_HELP + "  --effort <level>   Effort level (low, medium, high, xhigh, max)\n"
    rows = catalog.claude_rows(help_)
    ids = [r[0] for r in rows]
    for want in ("opus", "opus[1m]", "claude-opus-5-5", "claude-opus-5-5[1m]", "haiku",
                 "claude-haiku-4-5-20251001", "claude-fable-5-1", "claude-sonnet-5-5[1m]", "opusplan"):
        assert want in ids
    assert "haiku[1m]" not in ids
    assert {r[3] for r in rows} == {"low,medium,high,xhigh,max"}
    fams = catalog.families(rows)
    assert [f["name"] for f in fams][:4] == ["fable", "opus", "sonnet", "haiku"]
    assert fams[1]["efforts"] == ["low", "medium", "high", "xhigh", "max"]


def test_cursor_variants_fold_into_base():
    rows = catalog.group_cursor(catalog.parse_cursor(
        "auto - Auto\nclaude-opus-5-5-low-fast - A\nclaude-opus-5-thinking-high - B\n"
        "gpt-5.5-extra-high - C\ncomposer-2.5 - D\nclaude-opus-5-5-high - E\n"))
    fams = {f["name"]: [v[0] for v in f["variants"]] for f in catalog.families(rows)}
    assert fams["claude-opus-5-5"] == ["claude-opus-5-5-low-fast", "claude-opus-5-5-high"]
    assert fams["claude-opus-5"] == ["claude-opus-5-thinking-high"]
    assert fams["gpt-5.5"] == ["gpt-5.5-extra-high"] and fams["composer-2.5"] and fams["auto"]


def test_parse_cursor():
    t = "Available models\n\nauto - Auto (default)\ngpt-5.2 - GPT-5.2\n"
    assert catalog.parse_cursor(t) == [("auto", "Auto (default)"), ("gpt-5.2", "GPT-5.2")]


def test_parse_devin_families_and_aliases():
    got = catalog.parse_devin(DEVIN)
    assert got == [("adaptive", "Adaptive", "Adaptive"),
                   ("opus", "opus (alias)", "Claude Opus 5.5"),
                   ("claude-opus", "claude-opus (alias)", "Claude Opus 5.5"),
                   ("claude-opus-5-5-high", "Claude Opus 5.5 High", "Claude Opus 5.5")]


def test_models_cached_and_failure_empty(tmp_path, monkeypatch, settings):
    s = dataclasses.replace(settings, state_dir=tmp_path)
    calls = []
    monkeypatch.setattr(catalog, "fetch", lambda st, k: calls.append(k) or [("a", "A", "", "")])
    assert catalog.models_for(s, AgentKind.CODEX, now=1000) == [("a", "A", "", "")]
    assert catalog.models_for(s, AgentKind.CODEX, now=1001) == [("a", "A", "", "")] and len(calls) == 1

    def boom(st, k):
        raise OSError("no cli")
    monkeypatch.setattr(catalog, "fetch", boom)
    assert catalog.models_for(s, AgentKind.DEVIN, now=1000) == []
    # stale cache survives a failing CLI
    assert catalog.models_for(s, AgentKind.CODEX, now=10**9) == [("a", "A", "", "")]


def test_real_clis_models_and_modes_accepted(tmp_path):
    """Asks the installed CLIs (no mocks): listings parse, ids pass the launch validator, and
    every selectable mode is a value the CLI itself accepts."""
    import pathlib
    import subprocess

    import pytest

    from pm.agents import launch
    s = dataclasses.replace(_settings(tmp_path), state_dir=tmp_path)
    ran = 0
    for kind in (AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.DEVIN, AgentKind.CURSOR):
        if not pathlib.Path(launch.agent_binary(s, kind)).exists():
            continue
        ran += 1
        models = catalog.models_for(s, kind)
        assert models, f"{kind.value}: CLI listed no models"
        bad = [m[0] for m in models if not launch._MODEL_RE[kind].fullmatch(m[0])]
        assert not bad, bad[:3]
        every = [*launch._MODES[kind].items(), *launch._BYPASS_MODES.get(kind, {}).items(),
                 *launch._EXTRA.get(kind, {}).items()]
        if kind is AgentKind.CLAUDE:
            every += [(e, ["--effort", e]) for e in launch.EFFORTS[kind]]
        for mode, argv in every:
            r = subprocess.run([launch.agent_binary(s, kind), *argv, "--version"],
                               capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
            assert r.returncode == 0 and "invalid" not in (r.stderr + r.stdout).lower(), (kind, mode)
    if not ran:
        pytest.skip("no agent CLIs installed")


def _settings(tmp_path):
    from pm.core.config import load_settings
    return load_settings()
