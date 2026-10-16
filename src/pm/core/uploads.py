"""Uploaded images: names under ``state_dir/uploads`` and the auto-import of agent screenshots.

Only files that exist in ``uploads`` under the names ``/a/{id}/image`` writes (24 hex chars +
image extension) count; an image path under ``AUTO_ROOTS`` is copied in under a content-hash name.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from pm.core.fsutil import atomic_write

UPLOAD_RE = re.compile(r"^[0-9a-f]{24}\.(?:png|jpg|gif|webp)$")
_PATH_LINE = re.compile(r"^(?:/[^\s]*/uploads/)?([0-9a-f]{24}\.(?:png|jpg|gif|webp))$")
_EXT_PATH = re.compile(r"^/[^\s]+\.(?:png|jpe?g|gif|webp)$", re.IGNORECASE)
# agent screenshots and generated images live here; a path under these roots is copied into uploads and shown as an image
AUTO_ROOTS = (Path(f"/tmp/claude-{os.getuid()}"), Path("/tmp/pm-dev"), Path.home() / "dev")
AUTO_MAX_BYTES = 5 * 1024 * 1024
IMG_MAX_BYTES = 10 * 1024 * 1024  # one composer upload (and one tracker image)


def upload_name(path: str, uploads: Path | None) -> str | None:
    """Upload file name for ``path`` when it points into ``uploads`` and the file exists."""
    if uploads is None:
        return None
    p = (path or "").strip()
    m = _PATH_LINE.match(p)
    if not m:
        return _auto_import(p, uploads)
    if "/" in p and Path(p).parent != uploads:
        return None
    try:
        return m.group(1) if (uploads / m.group(1)).is_file() else None
    except OSError:
        return None


def _auto_import(p: str, uploads: Path) -> str | None:
    """Image file under AUTO_ROOTS (e.g. a screenshot path in an agent reply): copy it into
    ``uploads`` under a content-hash name and return that name. Magic bytes must match."""
    if not _EXT_PATH.match(p):
        return None
    try:
        real = Path(os.path.realpath(p))
        if not any(real.is_relative_to(r) for r in AUTO_ROOTS) or real.stat().st_size > AUTO_MAX_BYTES:
            return None
        data = real.read_bytes()
        ext = sniff_image(data)
        if not ext:
            return None
        name = f"{hashlib.sha256(data).hexdigest()[:24]}.{ext}"
        dest = uploads / name
        if not dest.is_file():
            uploads.mkdir(parents=True, exist_ok=True)
            atomic_write(dest, data, mode=0o600, fsync=False)
        return name
    except (OSError, ValueError):
        return None


def store_upload(settings, data: bytes, ext: str, *, dest_dir: str | Path | None = None) -> Path:
    """Store ``data`` as ``<dest_dir>/<sha256[:24]>.<ext>`` (0600, dir 0700; ``dest_dir`` defaults to
    ``<state_dir>/uploads``); same bytes, same file, written only when missing."""
    d = Path(dest_dir) if dest_dir is not None else Path(settings.state_dir) / "uploads"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = d / f"{hashlib.sha256(data).hexdigest()[:24]}.{ext}"
    if not dest.exists():
        atomic_write(dest, data, mode=0o600, fsync=False)
    return dest


# ------------------------------------------------------------------ image sniffing
_IMG_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)


def sniff_image(data: bytes) -> str | None:
    for magic, ext in _IMG_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None
