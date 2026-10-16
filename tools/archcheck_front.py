"""Frontend rules of tools/archcheck.py (static CSS/JS), split out of it to keep the module small.

Over src/pm/static (``check_frontend``, baselined like the Python rules): ``css-root-token:``,
``css-color:``, ``js-csrf:``, ``js-fetch:``. Over a theme static tree given with ``--static-dir DIR``
(``check_static_dir``, zero tolerance, no baseline): the same rules with DIR/css/tokens.css as the only
tokens file and DIR/js/core.js as the only file allowed CSRF lookups and raw ``fetch(``, plus
``asset-lines:``, ``line-len:`` (a .js/.css line longer than LINE_MAX chars) and ``z-index:`` (a CSS
``z-index`` whose value is not ``var(--z-...)``, a calc of one, or a keyword such as ``auto``).
"""

from __future__ import annotations

import re
from pathlib import Path

ASSET_MAX = 500
LINE_MAX = 120  # --static-dir trees only
# Per-file count kinds of the frontend rules, with the report label (see COUNTED_LABELS in archcheck.py).
FRONT_LABELS = {
    "css-color:": "colour literals outside tokens.css",
    "js-csrf:": "CSRF lookups outside core.js",
    "js-fetch:": "fetch( call sites outside core.js",
}

# Frontend rules (relpaths under src/pm): design tokens (``:root`` custom properties, colour literals)
# live only in TOKENS_CSS; CSRF lookups and raw ``fetch(`` only in the JS core (pm.fetch).
TOKENS_CSS = "static/css/tokens.css"
CSRF_JS = {"static/js/core.js"}
FETCH_JS = {"static/js/core.js", "static/js/sw.js", "static/sw.js"}  # service worker: no window.pm
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_URL = re.compile(r"url\((?:\"[^\"]*\"|'[^']*'|[^)]*)\)", re.I)
_CSS_ROOT = re.compile(r":root(?![\w-])")
_CSS_COLOR = re.compile(r"(?<![\w#&-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])"
                        r"|(?<![\w-])(?:rgba?|hsla?)\(", re.I)
_CSS_ZINDEX = re.compile(r"(?<![\w-])z-index\s*:\s*([^;}]*)", re.I)
_Z_TOKEN = re.compile(r"var\(\s*--z-")
_Z_KEYWORDS = {"auto", "inherit", "initial", "unset", "revert", "revert-layer"}
_JS_CSRF = re.compile(r"X-CSRF-Token|csrf-token|\[name=[\"']?csrf[\"']?\]")
_JS_FETCH = re.compile(r"(?<![.\w$])(?:(?:window|self|globalThis)\.)?fetch\s*\(")


def assets(static: Path) -> list[Path]:
    """JS/CSS under static/js, static/css (recursive) and static/ itself; vendor dirs skipped."""
    found = [*(static / "js").rglob("*.js"), *(static / "css").rglob("*.css"),
             *static.glob("*.js"), *static.glob("*.css")]
    return sorted(p for p in found if "vendor" not in p.relative_to(static).parts)


def _css_blocks(text: str) -> list[tuple[str, str]]:
    """(prelude, direct body) of every ``{...}`` block of comment-free CSS, nested blocks cut out of
    their parent's body (``@media (...) { :root { --x: 1 } }`` gives the ``:root`` block on its own)."""
    out: list[tuple[str, str]] = []
    stack: list[tuple[str, str]] = []  # (prelude, parent's direct body so far)
    buf = ""
    quote = ""
    for ch in text:
        if quote:
            buf += ch
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            buf += ch
            quote = ch
        elif ch == "{":
            cut = max(buf.rfind(";"), buf.rfind("}"))
            stack.append((buf[cut + 1:].strip(), buf[:cut + 1]))
            buf = ""
        elif ch == "}" and stack:
            prelude, parent = stack.pop()
            out.append((prelude, buf))
            buf = parent + ";"
        else:
            buf += ch
    return out


def _css_decls(body: str) -> list[tuple[str, str]]:
    """(property, value) of each declaration in a block body."""
    out = []
    for decl in body.split(";"):
        prop, sep, value = decl.partition(":")
        if sep:
            out.append((prop.strip(), value))
    return out


def check_css(text: str, rel: str, tokens_css: str = TOKENS_CSS) -> tuple[dict[str, str | int], list[tuple[str, str]]]:
    """``css-color:`` count (colour literals in declarations, not in custom-property definitions or
    ``url(...)``) and ``css-root-token:`` findings (custom properties defined on ``:root`` outside tokens_css)."""
    out: dict[str, str | int] = {}
    found: list[tuple[str, str]] = []
    if rel == tokens_css:
        return out, found
    colours = 0
    for prelude, body in _css_blocks(_CSS_COMMENT.sub(" ", text)):
        decls = _css_decls(body)
        names = [p for p, _ in decls if p.startswith("--")]
        if names and _CSS_ROOT.search(prelude):
            found.append((f"css-root-token:{rel}", f"custom properties {', '.join(names)} on {prelude!r}; "
                          f"define tokens in {tokens_css}"))
        colours += sum(len(_CSS_COLOR.findall(_CSS_URL.sub("", v))) for p, v in decls if not p.startswith("--"))
    if colours:
        out[f"css-color:{rel}"] = colours
    return out, found[:1]


def _js_code(text: str) -> str:
    """JS source with comments blanked; strings, template literals and regex literals kept."""
    out: list[str] = []
    i, n = 0, len(text)
    prev = ""  # last significant code char, to tell a regex literal from a division
    while i < n:
        ch, nxt = text[i], text[i + 1:i + 2]
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if ch in "\"'`" or (ch == "/" and (not prev or prev in "(,=:[!&|?{};+-*%<>~^")):
            j = i + 1
            in_class = False
            while j < n and (text[j] != ch or in_class):
                if text[j] == "\\":
                    j += 1
                elif ch == "/" and text[j] in "[]":
                    in_class = text[j] == "["
                elif ch == "/" and text[j] == "\n":
                    break
                j += 1
            out.append(text[i:j + 1])
            i = j + 1
            prev = ch
            continue
        out.append(ch)
        if not ch.isspace():
            prev = ch
        i += 1
    return "".join(out)


def check_js(text: str, rel: str, csrf_js: set[str] = CSRF_JS,
             fetch_js: set[str] = FETCH_JS) -> dict[str, str | int]:
    """``js-csrf:`` and ``js-fetch:`` per-file counts (comments excluded)."""
    out: dict[str, str | int] = {}
    code = _js_code(text)
    csrf = 0 if rel in csrf_js else len(_JS_CSRF.findall(code))
    fetch = 0 if rel in fetch_js else len(_JS_FETCH.findall(code))
    if csrf:
        out[f"js-csrf:{rel}"] = csrf
    if fetch:
        out[f"js-fetch:{rel}"] = fetch
    return out


def check_frontend(static: Path, pkg: Path) -> tuple[dict[str, str | int], list[tuple[str, str]]]:
    """Frontend rules over static/css/**/*.css, static/js/**/*.js and static/*.js|*.css (vendor dirs skipped)."""
    out: dict[str, str | int] = {}
    found: list[tuple[str, str]] = []
    for path in sorted([*(static / "css").rglob("*.css"), *(static / "js").rglob("*.js"),
                        *static.glob("*.js"), *static.glob("*.css")]):
        if "vendor" in path.relative_to(static).parts:
            continue
        rel = path.relative_to(pkg).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".css":
            o, f = check_css(text, rel)
            out.update(o)
            found += f
        else:
            out.update(check_js(text, rel))
    return out, found


def check_line_len(text: str, rel: str) -> list[tuple[str, str]]:
    """``line-len:rel:N`` for every line longer than LINE_MAX chars."""
    return [(f"line-len:{rel}:{i}", f"{len(ln)} chars > {LINE_MAX}")
            for i, ln in enumerate(text.splitlines(), 1) if len(ln) > LINE_MAX]


def check_zindex(text: str, rel: str) -> list[tuple[str, str]]:
    """``z-index:rel:N`` for every CSS ``z-index`` that is not a ``var(--z-...)`` token (or calc of one)
    or a keyword. Comments are blanked with their newlines kept, so N is the real line."""
    code = _CSS_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    out = []
    for m in _CSS_ZINDEX.finditer(code):
        value = m.group(1).replace("!important", "").strip()
        if value.lower() in _Z_KEYWORDS or _Z_TOKEN.search(value):
            continue
        line = code.count("\n", 0, m.start()) + 1
        out.append((f"z-index:{rel}:{line}", f"z-index: {value}; use a var(--z-...) token"))
    return out


def check_static_dir(static: Path) -> list[tuple[str, str]]:
    """All findings (key, detail) of a theme static tree; keys carry the path as given. Zero tolerance."""
    prefix = static.as_posix().rstrip("/") or "/"
    if not static.is_dir():
        return [(f"static-dir:{prefix}", "not a directory")]
    tokens, core = f"{prefix}/css/tokens.css", {f"{prefix}/js/core.js"}
    found: list[tuple[str, str]] = []
    for path in assets(static):
        rel = f"{prefix}/{path.relative_to(static).as_posix()}"
        text = path.read_text(encoding="utf-8", errors="replace")
        n = len(text.splitlines())
        if n > ASSET_MAX:
            found.append((f"asset-lines:{rel}", f"{n} lines > {ASSET_MAX}"))
        if path.suffix == ".css":
            counts, root_tokens = check_css(text, rel, tokens)
            found += root_tokens + check_zindex(text, rel)
        else:
            counts = check_js(text, rel, core, core)
        for key, val in counts.items():
            label = next(v for k, v in FRONT_LABELS.items() if key.startswith(k))
            found.append((key, f"{val} {label}"))
        found += check_line_len(text, rel)
    return found
