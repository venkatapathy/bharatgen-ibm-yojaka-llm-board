"""Helpers to expand PDF / ZIP uploads into individual PDF members."""

from __future__ import annotations

import io
import os
import zipfile


def iter_upload_pdf_members(uploaded_file):
    """
    Yield (filename, payload_bytes) for each PDF in an upload.

    A single .pdf yields one member. A .zip yields one member per PDF entry
    (ZIP itself is never kept as the stored context file).
    """
    if not uploaded_file:
        return

    name = (uploaded_file.name or "").lower()
    uploaded_file.seek(0)
    data = uploaded_file.read()
    uploaded_file.seek(0)

    is_zip = name.endswith(".zip") or data[:2] == b"PK"
    if is_zip:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                if member.endswith("/") or not member.lower().endswith(".pdf"):
                    continue
                # Block path traversal / absolute paths inside ZIP
                normalized = os.path.normpath(member).replace("\\", "/")
                if normalized.startswith("../") or normalized.startswith("/") or ".." in normalized.split("/"):
                    continue
                basename = os.path.basename(normalized)
                if not basename:
                    continue
                payload = archive.read(member)
                if not payload.startswith(b"%PDF"):
                    continue
                yield basename, payload
        return

    basename = os.path.basename(uploaded_file.name) or "document.pdf"
    yield basename, data
