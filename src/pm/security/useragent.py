"""Browser and OS family from a User-Agent header ("Chrome · Linux"), for labels only.

The full header is never stored: callers keep only what ``family`` returns. Order matters: Edge and Opera say
"Chrome", Chrome says "Safari", and iOS browsers say "Safari" plus their own token.
"""

from __future__ import annotations

_BROWSERS = (
    ("Edg/", "Edge"), ("EdgiOS/", "Edge"), ("EdgA/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"), ("CriOS/", "Chrome"), ("Chrome/", "Chrome"), ("Chromium/", "Chromium"),
    ("Safari/", "Safari"), ("curl/", "curl"),
)
_SYSTEMS = (
    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("CrOS", "ChromeOS"), ("Windows", "Windows"),
    ("Macintosh", "macOS"), ("Mac OS X", "macOS"), ("Linux", "Linux"),
)


def family(ua: object) -> str:
    """``"<browser> · <os>"``, one of them alone when the other is unknown, or ``""``."""
    s = str(ua or "")[:512]
    if not s:
        return ""
    browser = next((name for tok, name in _BROWSERS if tok in s), "")
    system = next((name for tok, name in _SYSTEMS if tok in s), "")
    return " · ".join(p for p in (browser, system) if p)
