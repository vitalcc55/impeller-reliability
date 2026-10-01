from __future__ import annotations

MEDIA_SIGNATURES: dict[str, bytes] = {
    "application/pdf": b"%PDF-",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


def matches_media_signature(prefix: bytes, media_type: str) -> bool:
    signature = MEDIA_SIGNATURES.get(media_type)
    return signature is not None and prefix.startswith(signature)
