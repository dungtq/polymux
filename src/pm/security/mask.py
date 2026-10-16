"""Pane-text sanitising: strip terminal escapes, then mask secrets.

`render_pane` is the only entry point other code should use. There is no
off switch. Order matters: escapes are stripped first so an SGR sequence in
the middle of a token cannot defeat the patterns.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable

MASK = "••••[masked]"
_NOT_MASKED = r"(?!••••)"

# ---------------------------------------------------------------- strip ANSI
_ST = r"(?:\x07|\x1b\\|\x9c)"
_ESC_RES = [
    # OSC (incl. OSC 8 hyperlinks): BEL or ST terminated; unterminated runs to EOL
    re.compile(r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c\n]*(?:" + _ST + r"|(?=\n)|\Z)"),
    # DCS / SOS / PM / APC strings
    re.compile(r"\x1b[PX^_][^\x1b\x9c\n]*(?:\x1b\\|\x9c|(?=\n)|\Z)"),
    # CSI incl. SGR
    re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"),
    # other 2/3-byte escapes (charset select, keypad modes, ...)
    re.compile(r"\x1b[ -/]*[0-~]"),
]
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_ansi(text: str) -> str:
    if not text:
        return text
    for rx in _ESC_RES:
        text = rx.sub("", text)
    return _CTRL.sub("", text)


# ---------------------------------------------------------------- masking
_SECRETISH = re.compile(r"password|passwd|secret|api[_-]?key", re.I)
_BENIGN_KEY_END = re.compile(
    r"(?:_type|_file|_path|_dir|_count|_limit|_budget|_details|_info|_usage)$", re.I
)
_FILE_KEY = re.compile(r"\.(?:py|md|toml|json|ya?ml|sh|txt|js|ts)$")
_BENIGN_VALUE = frozenset({"none", "null", "nil", "true", "false"})


def _generic(m: re.Match[str]) -> str:
    key = re.sub(r"[\s\"':=]+$", "", m.group(1)).lower()
    val = m.group(2)
    num = val.rstrip("}]).,")
    if val.lower() in _BENIGN_VALUE or _FILE_KEY.search(key):
        return m.group(0)
    if not _SECRETISH.search(key):
        if "tokens" in key and (num.isdigit() or val[0] in "{["):
            return m.group(0)
        if num.isdigit() and "token" in key:
            return m.group(0)
        if _BENIGN_KEY_END.search(key):
            return m.group(0)
    return m.group(1) + MASK


_SECRET_NAME = re.compile(
    r"key|token|secret|pass|pwd|auth|credential|private|totp|cookie", re.I
)


def _assign(m: re.Match[str]) -> str:
    name, val = m.group(2), m.group(4)
    if m.group(1) is None and not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        return m.group(0)  # bare lowercase `foo_key=` only counts after `export`
    if not _SECRET_NAME.search(name) or _BENIGN_KEY_END.search(name):
        return m.group(0)
    if val.strip("'\"").lower() in _BENIGN_VALUE or val.startswith("••••"):
        return m.group(0)
    if val.strip("'\"") == "" or val.startswith(("$", "`")):
        return m.group(0)  # empty or a shell expansion, no literal secret
    return f"{m.group(1) or ''}{name}{m.group(3)}{MASK}"


_B = r"[A-Za-z0-9_-]"
_DEFAULTS: list[tuple[re.Pattern[str], object]] = [
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|PGP [A-Z ]*BLOCK)-----"
            r"[\s\S]*?(?:-----END [A-Z0-9 ]*-----|\Z)"
        ),
        MASK,
    ),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"), MASK),
    (re.compile(r"\bsk-" + _B + r"{16,}"), MASK),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), MASK),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (
        re.compile(
            r"\b(?:AIza[0-9A-Za-z_-]{30,}|glpat-" + _B + r"{16,}|npm_[A-Za-z0-9]{30,}"
            r"|hf_[A-Za-z0-9]{30,}|pypi-" + _B + r"{30,}"
            r"|[sr]k_(?:live|test)_[A-Za-z0-9]{16,}|ya29\.[A-Za-z0-9_-]{20,}"
            r"|tvly-" + _B + r"{16,}|fc-[a-f0-9]{32})"
        ),
        MASK,
    ),
    # ntfy access tokens and pm-prefixed tokens
    (re.compile(r"\b(?:tk_[a-z0-9]{20,}|pm_[A-Za-z0-9_-]{24,})"), MASK),
    (re.compile(r"hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+"), MASK),
    (re.compile(r"(?i)\b(Bearer\s+)" + _NOT_MASKED + r"[A-Za-z0-9._~+/=-]{8,}"), r"\1" + MASK),
    (
        re.compile(
            r"\b(Basic\s+)" + _NOT_MASKED
            + r"(?:(?=[A-Za-z0-9+/]*[\d+/])|(?=[a-z][A-Za-z0-9+/]*[A-Z]))[A-Za-z0-9+/]{16,}={0,2}"
        ),
        r"\1" + MASK,
    ),
    (
        re.compile(
            r"(?i)(\b(?:authorization|x-api-key|api-key)[\"']?\s*[:=]\s*[\"']?)"
            r"(?!••••|Bearer\b|Basic\b)[A-Za-z0-9._~+/=-]{20,}"
        ),
        r"\1" + MASK,
    ),
    # URL userinfo (user may be empty)
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)" + _NOT_MASKED + r"[^/\s:@]*:[^/\s@]+@"), r"\1" + MASK + "@"),
    # export X=... / KEY=value for secret-looking names
    (
        re.compile(
            r"(?m)(?:^|(?<=[\s;&|(]))(?:(export\s+|declare\s+-x\s+|set\s+-x\s+)|)"
            r"([A-Za-z_][A-Za-z0-9_]*)(=)(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"';|&]+)"
        ),
        _assign,
    ),
    # word-prefixed generic key=value
    (
        re.compile(
            r"(?i)(\b[\w.-]*(?:password|passwd|secret|token|api[_-]?key)[\w.-]*[\"']?\s*[:=]\s*[\"']?)"
            + _NOT_MASKED + r"([^\s\"',;]+)"
        ),
        _generic,
    ),
    # prose: "the token is 9f86d0...", "password abc123..."
    (
        re.compile(
            r"(?i)(\b(?:key|token|secret|password|passwd)\b[\s:=\"'`]{1,6}(?:is\s+)?)"
            + _NOT_MASKED
            + r"(?=[A-Za-z0-9+/_-]*\d)[A-Za-z0-9+/_-]{24,}={0,2}"
        ),
        r"\1" + MASK,
    ),
]

# A masked token at end of a hard-wrapped line: mask a lone token-like next line.
_WRAP_TAIL = re.compile(
    r"(?m)(" + re.escape(MASK) + r"[ \t]*\n)([ \t]*(?=[A-Za-z0-9+/_=-]*(?:\d|[A-Z]))[A-Za-z0-9+/_=-]{8,}[ \t]*)$"
)


class Masker:
    def __init__(self, literals: Iterable[str] = (), patterns: Iterable[str] = ()):
        self.literals = sorted({s for s in literals if s and len(s) >= 8}, key=len, reverse=True)
        self.extra = [re.compile(p) for p in patterns if p]

    def mask(self, text: str) -> str:
        if not text:
            return text
        for lit in self.literals:
            text = text.replace(lit, MASK)
        for rx in self.extra:
            text = rx.sub(MASK, text)
        for rx, repl in _DEFAULTS:
            text = rx.sub(repl, text)  # type: ignore[arg-type]
        return _WRAP_TAIL.sub(lambda m: m.group(1) + MASK, text)


_lock = threading.Lock()
_active = Masker()


def configure(literals: Iterable[str] = (), patterns: Iterable[str] = ()) -> None:
    """Install extra literals/patterns (e.g. pm's own token); additive to defaults."""
    global _active
    m = Masker(literals, patterns)
    with _lock:
        _active = m


def mask(text: str) -> str:
    return _active.mask(text)


def render_pane(text: str) -> str:
    """Strip terminal escapes, then mask. The only entry point for pane text."""
    return mask(strip_ansi(text))
