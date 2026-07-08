from django.core.exceptions import ValidationError


def validate_pyq_upload(uploaded_file, *, max_size_bytes=None):
    if not uploaded_file:
        raise ValidationError("PYQ PDF upload is required.")
    if max_size_bytes and uploaded_file.size > max_size_bytes:
        raise ValidationError("Uploaded PYQ file exceeds the configured size limit.")
    header = uploaded_file.read(4)
    uploaded_file.seek(0)
    if not header.startswith(b"%PDF"):
        raise ValidationError("Only PDF uploads are supported for PYQ extraction.")
