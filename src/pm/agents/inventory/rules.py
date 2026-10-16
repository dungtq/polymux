"""Rules files: the canonical ~/agents/AGENTS.md and every agent's copy or symlink."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pm.core.models import RuleInfo

from .fsread import (
    _is_file,
    _lexists,
    _list_dir,
    _read,
    _resolve,
    _summary,
    display_path,
    is_blocked,
)
from .specs import MAX_RULES, NO_SUMMARY, _Spec


def _digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def _rule_for_file(
    p: Path,
    agent: str,
    home: Path,
    canon_digest: str | None,
    canon_res: Path | None,
    compare: bool,
) -> RuleInfo:
    disp = display_path(p, home)
    if p.is_symlink():
        rp = _resolve(p)
        try:
            raw = os.readlink(p)
        except OSError:
            raw = ""
        target = display_path(Path(raw), home) if raw else ""
        if rp is None or not rp.exists():
            return RuleInfo(
                p.name, agent, disp, "(broken symlink)", "symlink", target, 0, "broken"
            )
        if is_blocked(p, home):
            return RuleInfo(p.name, agent, disp, NO_SUMMARY, "symlink", target, 0, "ok")
        text = _read(p, home)
        return RuleInfo(
            p.name,
            agent,
            disp,
            _summary(text),
            "symlink",
            target,
            _count_lines(text),
            "ok",
        )
    text = _read(p, home)
    if text is None:
        return RuleInfo(p.name, agent, disp, "(unreadable)", "file", "", 0, "broken")
    mode, status = "file", "ok"
    if compare and canon_digest is not None:
        mode = "copy"
        status = "ok" if _digest(text) == canon_digest else "drift"
    del canon_res
    return RuleInfo(
        p.name, agent, disp, _summary(text), mode, "", _count_lines(text), status
    )


def _count_lines(text: str | None) -> int:
    return len(text.splitlines()) if text else 0


def _collect_rules(
    home: Path, specs: list[_Spec], installed: set[str]
) -> list[RuleInfo]:
    rules: list[RuleInfo] = []
    canon_dir = home / "agents"
    canon_text = _read(canon_dir / "AGENTS.md", home)
    canon_digest = _digest(canon_text) if canon_text is not None else None
    canon_res = _resolve(canon_dir / "AGENTS.md")
    for fname in ("AGENTS.md", "agents-index.md"):
        p = canon_dir / fname
        if _lexists(p):
            r = _rule_for_file(p, "", home, None, None, False)
            rules.append(r)
        elif fname == "AGENTS.md":
            rules.append(
                RuleInfo(fname, "", display_path(p, home), "", "file", "", 0, "missing")
            )
    for spec in specs:
        if spec.rules_file:
            p = home / spec.rules_file
            if _lexists(p):
                rules.append(
                    _rule_for_file(p, spec.name, home, canon_digest, canon_res, True)
                )
            elif spec.name in installed:
                rules.append(
                    RuleInfo(
                        p.name,
                        spec.name,
                        display_path(p, home),
                        "",
                        "file",
                        "",
                        0,
                        "missing",
                    )
                )
        seen: set[Path] = set()
        for rel, suffix in spec.rules_dirs:
            for e in _list_dir(home / rel, home):
                if (
                    e.suffix != suffix
                    or e in seen
                    or not (_is_file(e) or e.is_symlink())
                ):
                    continue
                seen.add(e)
                rules.append(_rule_for_file(e, spec.name, home, None, None, False))
    return rules[:MAX_RULES]
