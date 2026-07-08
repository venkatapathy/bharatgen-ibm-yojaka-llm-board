"""Validation helpers for PDF context uploads."""

import io
import zipfile

from django.core.exceptions import ValidationError


def validate_pdf_upload(uploaded_file, *, max_size_bytes=None):
    if not uploaded_file:
        raise ValidationError("Upload is required.")
    if max_size_bytes and uploaded_file.size > max_size_bytes:
        raise ValidationError("Uploaded file exceeds the configured size limit.")

    header = uploaded_file.read(8)
    uploaded_file.seek(0)
    is_pdf = header.startswith(b"%PDF")
    is_zip = header.startswith(b"PK")
    name = uploaded_file.name.lower()

    if not (is_pdf or is_zip):
        raise ValidationError("Only PDF or ZIP uploads are supported.")
    if is_pdf and not name.endswith(".pdf"):
        raise ValidationError("PDF uploads must use a .pdf filename.")
    if is_zip:
        if not name.endswith(".zip"):
            raise ValidationError("ZIP uploads must use a .zip filename.")
        data = uploaded_file.read()
        uploaded_file.seek(0)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            pdf_entries = [entry for entry in archive.namelist() if entry.lower().endswith(".pdf")]
            if not pdf_entries:
                raise ValidationError("ZIP uploads must contain at least one PDF.")
