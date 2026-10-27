"""Shared state behind the website: the stores, upload folders, per-case locks and image handling."""
from __future__ import annotations

import io
import secrets
import shutil
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from PIL import Image, ImageOps

from ark_angel.cases.store import CASE_ID_PATTERN, FileCaseStore
from ark_angel.web.db import SiteDB

IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MAX_IMAGE_PIXELS = 40_000_000  # ~ a 7700x5200 photo; bigger is treated as a decompression bomb
PROFILE_PHOTO_MAX = 1200


class UploadError(ValueError):
    """An upload that was refused; the message is safe to show."""


@dataclass
class Site:
    """Everything a request needs: case data, site data and where files go."""

    store: FileCaseStore
    db: SiteDB
    base_dir: Path
    _locks: dict[str, threading.Lock] = field(default_factory=dict)
    _locks_guard: threading.Lock = field(default_factory=threading.Lock)

    @property
    def media_dir(self) -> Path:
        """Public profile photos (metadata stripped)."""
        return self.base_dir / "media"

    def uploads_dir(self, case_id: str) -> Path:
        """Original evidence files for one case. Never served publicly."""
        if not CASE_ID_PATTERN.match(case_id):
            raise ValueError("bad case id")
        return self.base_dir / "uploads" / case_id

    @contextmanager
    def case_lock(self, case_id: str) -> Iterator[None]:
        """Serialize read-modify-write on one case (tips, uploads and reviews can arrive together)."""
        with self._locks_guard:
            lock = self._locks.setdefault(case_id, threading.Lock())
        with lock:
            yield

    def remove_case_files(self, case_id: str, photos: tuple[str, ...] = ()) -> None:
        shutil.rmtree(self.uploads_dir(case_id), ignore_errors=True)
        for name in photos:
            self.remove_photo(name)

    def remove_photo(self, name: str) -> None:
        if name and _safe_name(name):
            (self.media_dir / name).unlink(missing_ok=True)


def _safe_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and not name.startswith(".")


def read_image(data: bytes) -> tuple[Image.Image, str]:
    """Open and fully decode untrusted image bytes. Returns the image and its file extension.

    Raises:
        UploadError: not a JPEG/PNG/WebP, corrupt, or too large in pixels.
    """
    # The pixel check reads only the header, before anything is decoded. (No global Pillow or warnings state is
    # touched: requests run on several threads.)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            kind = probe.format
            width, height = probe.size
            if kind not in IMAGE_FORMATS:
                raise UploadError("Photos must be JPEG, PNG or WebP.")
            if width * height > MAX_IMAGE_PIXELS:
                raise UploadError("That image is too large.")
            probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
    except UploadError:
        raise
    except Image.DecompressionBombError:
        raise UploadError("That image is too large.") from None
    except Exception:  # Pillow raises many types for bad input; all mean "not a usable image"
        raise UploadError("That file isn't a readable photo.") from None
    return image, IMAGE_FORMATS[kind]


def save_profile_photo(site: Site, data: bytes) -> str:
    """Re-encode a profile photo as a fresh JPEG with no metadata (no GPS of the family's home). Returns its name."""
    image, _ = read_image(data)
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGB")
    image.thumbnail((PROFILE_PHOTO_MAX, PROFILE_PHOTO_MAX))
    site.media_dir.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}.jpg"
    image.save(site.media_dir / name, "JPEG", quality=85, optimize=True)
    return name


def save_evidence_photo(site: Site, case_id: str, data: bytes) -> Path:
    """Keep an evidence photo byte-for-byte (its EXIF GPS and time are the point), after checking it is one."""
    _, ext = read_image(data)
    folder = site.uploads_dir(case_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"photo-{secrets.token_hex(8)}{ext}"
    path.write_bytes(data)
    return path


def save_upload(site: Site, case_id: str, filename: str, data: bytes) -> Path:
    """Store an uploaded evidence file under a fresh folder, keeping a cleaned version of its name as the source."""
    from werkzeug.utils import secure_filename

    name = secure_filename(filename) or "upload.txt"
    folder = site.uploads_dir(case_id) / secrets.token_hex(6)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def photo_kind(path: Path) -> Optional[str]:
    """Content type for a stored, already-validated photo."""
    return {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower())
