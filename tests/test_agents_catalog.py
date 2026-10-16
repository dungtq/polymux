import dataclasses
import json
import threading
import time

from pm.agents import catalog
from pm.core import executors
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


def test_parse_antigravity():
    t = "gemini-4-argon-medium\tGemini 4 Argon (Medium)\ngemini-4-argon-high\tGemini 4 Argon (High)\n"
    assert catalog.parse_antigravity(t) == [
        ("gemini-4-argon-medium", "Gemini 4 Argon (Medium)", "gemini-4-argon", ""),
        ("gemini-4-argon-high", "Gemini 4 Argon (High)", "gemini-4-argon", ""),
    ]


def _sync_submit(monkeypatch):
    """executors.fanout.submit runs the job at once; returns the list of submitted jobs."""
    jobs = []
    monkeypatch.setattr(executors.fanout, "submit", lambda fn, *a: jobs.append(fn) or fn(*a))
    return jobs


def _held_submit(monkeypatch):
    """executors.fanout.submit only records the job; call the returned items to run them."""
    jobs = []
    monkeypatch.setattr(executors.fanout, "submit", lambda fn, *a: jobs.append(lambda: fn(*a)))
    return jobs


def test_models_cached_and_failure_empty(tmp_path, monkeypatch, settings):
    s = dataclasses.replace(settings, state_dir=tmp_path)
    jobs = _sync_submit(monkeypatch)
    calls = []
    monkeypatch.setattr(catalog, "fetch", lambda st, k: calls.append(k) or [("a", "A", "", "")])
    assert catalog.models_for(s, AgentKind.CODEX, now=1000) == [("a", "A", "", "")]
    assert catalog.models_for(s, AgentKind.CODEX, now=1001) == [("a", "A", "", "")] and len(calls) == 1

    def boom(st, k):
        raise OSError("no cli")
    monkeypatch.setattr(catalog, "fetch", boom)
    assert catalog.models_for(s, AgentKind.DEVIN, now=1000) == []
    # expired: the stale rows at once, one background refresh; a failing CLI keeps them
    assert catalog.models_for(s, AgentKind.CODEX, now=10**9) == [("a", "A", "", "")]
    assert len(jobs) == 1
    hit = json.loads((tmp_path / "agent-models.json").read_text())["codex"]
    assert hit["err"] == 1 and hit["models"] == [["a", "A", "", ""]]


def test_expired_serves_stale_and_refreshes_once(tmp_path, monkeypatch, settings):
    s = dataclasses.replace(settings, state_dir=tmp_path)
    monkeypatch.setattr(catalog, "fetch", lambda st, k: [("old", "O", "", "")])
    catalog.models_for(s, AgentKind.CODEX, now=1000)
    jobs = _held_submit(monkeypatch)
    calls = []
    monkeypatch.setattr(catalog, "fetch", lambda st, k: calls.append(k) or [("new", "N", "", "")])
    late = 1000 + catalog.CACHE_TTL
    assert catalog.models_for(s, AgentKind.CODEX, now=late) == [("old", "O", "", "")]
    assert catalog.models_for(s, AgentKind.CODEX, now=late) == [("old", "O", "", "")]
    assert len(jobs) == 1 and calls == []  # second call while in flight: no new refresh
    jobs[0]()
    assert calls == [AgentKind.CODEX]
    assert catalog.models_for(s, AgentKind.CODEX) == [("new", "N", "", "")]
    catalog.models_for(s, AgentKind.CODEX, now=time.time() + catalog.CACHE_TTL)
    assert len(jobs) == 2  # in-flight mark cleared after the refresh


def test_failure_cached_for_fail_ttl(tmp_path, monkeypatch, settings):
    s = dataclasses.replace(settings, state_dir=tmp_path)
    jobs = _sync_submit(monkeypatch)
    calls = []

    def boom(st, k):
        calls.append(k)
        raise catalog.ProcError("timeout")
    monkeypatch.setattr(catalog, "fetch", boom)
    assert catalog.models_for(s, AgentKind.DEVIN, now=1000) == [] and len(calls) == 1
    assert catalog.models_for(s, AgentKind.DEVIN, now=1000 + catalog.FAIL_TTL - 1) == []
    assert len(calls) == 1 and jobs == []
    monkeypatch.setattr(catalog, "fetch", lambda st, k: calls.append(k) or [("d", "D", "", "")])
    assert catalog.models_for(s, AgentKind.DEVIN, now=1000 + catalog.FAIL_TTL) == []
    assert len(calls) == 2 and len(jobs) == 1
    assert catalog.models_for(s, AgentKind.DEVIN) == [("d", "D", "", "")]


def test_empty_listing_counts_as_failure(tmp_path, monkeypatch, settings):
    s = dataclasses.replace(settings, state_dir=tmp_path)
    monkeypatch.setattr(catalog, "fetch", lambda st, k: [])
    assert catalog.models_for(s, AgentKind.CURSOR, now=1000) == []
    assert json.loads((tmp_path / "agent-models.json").read_text())["cursor-agent"]["err"] == 1


def test_all_models_cold_kinds_fetch_in_parallel(tmp_path, monkeypatch, settings):
    s = dataclasses.replace(settings, state_dir=tmp_path)
    kinds = ["claude", "codex", "cursor-agent", "devin"]
    barrier = threading.Barrier(len(kinds), timeout=5)

    def fetch(st, k):
        barrier.wait()  # BrokenBarrierError (-> test failure) unless all four run at once
        return [(k.value + "-m", "M", "", "")]
    monkeypatch.setattr(catalog, "fetch", fetch)
    got = catalog.all_models(s, kinds)
    assert {k: [r[0] for r in rows] for k, rows in got.items()} == {k: [k + "-m"] for k in kinds}
    saved = json.loads((tmp_path / "agent-models.json").read_text())
    assert sorted(saved) == sorted(kinds)  # parallel writers did not lose each other's entries


def test_parallel_saves_keep_every_kind(tmp_path, monkeypatch, settings):
    s = dataclasses.replace(settings, state_dir=tmp_path)
    monkeypatch.setattr(catalog, "fetch", lambda st, k: [(k.value, k.value, "", "")])
    kinds = [AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.CURSOR, AgentKind.DEVIN]
    ts = [threading.Thread(target=catalog._refresh, args=(s, k)) for k in kinds for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    saved = json.loads((tmp_path / "agent-models.json").read_text())
    assert sorted(saved) == sorted(k.value for k in kinds)


def test_real_clis_models_and_modes_accepted(tmp_path):
    """Asks the installed CLIs (no mocks): listings parse, ids pass the launch validator, and
    every selectable mode is a value the CLI itself accepts."""
    import pathlib
    import subprocess

    import pytest

    from pm.agents import launch
    s = dataclasses.replace(_settings(tmp_path), state_dir=tmp_path)
    ran = 0
    for kind in (AgentKind.CLAUDE, AgentKind.CODEX, AgentKind.DEVIN, AgentKind.CURSOR,
                 AgentKind.ANTIGRAVITY):
        if not pathlib.Path(launch.agent_binary(s, kind)).exists():
            continue
        ran += 1
        models = catalog.models_for(s, kind)
        assert models, f"{kind.value}: CLI listed no models"
        bad = [m[0] for m in models if not launch._MODEL_RE[kind].fullmatch(m[0])]
        assert not bad, bad[:3]
        every = [*launch._MODES[kind].items(), *launch._BYPASS_MODES.get(kind, {}).items(),
                 *launch._EXTRA.get(kind, {}).items()]
        if kind in (AgentKind.CLAUDE, AgentKind.ANTIGRAVITY):
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
