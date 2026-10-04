"""A tenant's branding: the logo its console shows in the header.

Only raster images are accepted, in the three formats every supported browser
renders. SVG is refused: it is a document that can carry script, and a logo is
shown to everyone in the tenant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# Measured on the decoded bytes, so the limit does not depend on the encoding
# the upload arrived in.
MAX_LOGO_BYTES = 256 * 1024

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"
_WEBP_CHUNKS = frozenset({b"VP8 ", b"VP8L", b"VP8X"})


class LogoContentType(StrEnum):
    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"


@dataclass(frozen=True, kw_only=True)
class TenantLogo:
    """The one logo a tenant has. A tenant without one shows the default mark."""

    tenant_id: str
    content_type: LogoContentType
    data: bytes
    sha256: str
    updated_at: datetime
    updated_by: str


def sniff_logo_content_type(data: bytes) -> LogoContentType | None:
    """The accepted image format the bytes are, or None.

    Read from the file's own leading bytes, never from a file name or a declared
    type, so a renamed SVG or HTML file is not taken for an image.
    """
    # PNG: the 8-byte signature, then the IHDR chunk that must come first.
    if data.startswith(_PNG_SIGNATURE) and data[12:16] == b"IHDR":
        return LogoContentType.PNG
    # JPEG: the start-of-image marker followed by the next marker's 0xFF.
    if data.startswith(_JPEG_SIGNATURE):
        return LogoContentType.JPEG
    # WebP: a RIFF container of form type WEBP whose first chunk is VP8, VP8L or VP8X.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and data[12:16] in _WEBP_CHUNKS:
        return LogoContentType.WEBP
    return None
