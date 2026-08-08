"""File storage that writes to MEDIA_ROOT and mirrors to MEDIA_MIRROR_ROOT (NFS).

Reads always use the primary Docker media location (MEDIA_ROOT).
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage

logger = logging.getLogger(__name__)


class MirroredFileSystemStorage(FileSystemStorage):
    """Primary = Docker volume; optional secondary = NFS mirror."""

    def _mirror_root(self) -> Path | None:
        raw = (getattr(settings, "MEDIA_MIRROR_ROOT", None) or "").strip()
        if not raw:
            return None
        root = Path(raw)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("MEDIA_MIRROR_ROOT unavailable (%s): %s", root, exc)
            return None
        return root

    def _mirror_path(self, name: str) -> Path | None:
        root = self._mirror_root()
        if root is None or not name:
            return None
        # Prevent path escape outside mirror root.
        dest = (root / name).resolve()
        if not str(dest).startswith(str(root.resolve())):
            logger.error("Refusing mirror path outside root: %s", name)
            return None
        return dest

    def _mirror_copy(self, name: str) -> None:
        dest = self._mirror_path(name)
        if dest is None:
            return
        src = Path(self.path(name))
        if not src.is_file():
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        except OSError as exc:
            logger.warning("Media mirror copy failed for %s: %s", name, exc)

    def _mirror_delete(self, name: str) -> None:
        dest = self._mirror_path(name)
        if dest is None:
            return
        try:
            if dest.is_file():
                dest.unlink()
        except OSError as exc:
            logger.warning("Media mirror delete failed for %s: %s", name, exc)

    def _save(self, name, content):
        name = super()._save(name, content)
        self._mirror_copy(name)
        return name

    def delete(self, name):
        super().delete(name)
        self._mirror_delete(name)
