"""pm.core.uploads.store_upload: content-addressed upload file, 0600 in a 0700 dir."""

import hashlib
import os
import stat

from pm.core import uploads

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def test_store_upload_names_by_hash_and_is_private(settings):
    p = uploads.store_upload(settings, PNG, "png")
    assert p == settings.state_dir / "uploads" / f"{hashlib.sha256(PNG).hexdigest()[:24]}.png"
    assert p.read_bytes() == PNG
    assert uploads.UPLOAD_RE.fullmatch(p.name)
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700


def test_store_upload_same_bytes_same_file(settings):
    a = uploads.store_upload(settings, PNG, "png")
    mtime = a.stat().st_mtime_ns
    assert uploads.store_upload(settings, PNG, "png") == a
    assert a.stat().st_mtime_ns == mtime


def test_store_upload_dest_dir(settings, tmp_path):
    d = tmp_path / "elsewhere" / "tracker"
    p = uploads.store_upload(settings, PNG, "png", dest_dir=d)
    assert p.parent == d and p.read_bytes() == PNG and not (settings.state_dir / "uploads").exists()
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_img_max_bytes_is_ten_mb():
    assert uploads.IMG_MAX_BYTES == 10 * 1024 * 1024
