"""Read, replace and remove a tenant's logo.

An upload is checked here rather than trusted: the declared type must be one of
the accepted raster formats, and the file's own leading bytes must say the same,
so a renamed SVG or HTML file is refused whatever it claims to be.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from core.domain.branding import (
    MAX_LOGO_BYTES,
    LogoContentType,
    TenantLogo,
    sniff_logo_content_type,
)
from core.domain.errors import OpenProgramError
from core.ports.branding import TenantLogoRepository

_ACCEPTED_FORMATS = "PNG, JPEG or WebP"
# Base64 carries 3 bytes in every 4 characters. A longer string cannot decode to
# an accepted logo, so it is refused before any decoding is done.
_MAX_BASE64_CHARS = -(-MAX_LOGO_BYTES // 3) * 4


class LogoRejected(OpenProgramError):
    """Raised when an upload cannot become the tenant's logo."""


class InvalidLogoData(LogoRejected):
    """The upload carries no bytes, or bytes that are not valid base64."""


class LogoTooLarge(LogoRejected):
    """The decoded file is over the size limit."""


class UnsupportedLogoType(LogoRejected):
    """The upload is not, or does not contain, an accepted raster image."""


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True, kw_only=True)
class BrandingService:
    repository: TenantLogoRepository
    clock: Callable[[], datetime] = _utc_now

    async def logo(self, tenant_id: str) -> TenantLogo | None:
        return await self.repository.get_logo(tenant_id)

    async def replace_logo(
        self,
        tenant_id: str,
        *,
        content_type: str,
        data_base64: str,
        updated_by: str,
    ) -> TenantLogo:
        """Make the upload the tenant's one logo, or raise ``LogoRejected``."""
        declared = _declared_type(content_type)
        data = _decoded(data_base64)
        actual = sniff_logo_content_type(data)
        if actual is None:
            raise UnsupportedLogoType(f"The file is not a {_ACCEPTED_FORMATS} image.")
        if actual is not declared:
            raise UnsupportedLogoType(
                f"The file is {_format_name(actual)}, but it was sent as {declared.value}."
            )
        logo = TenantLogo(
            tenant_id=tenant_id,
            content_type=declared,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            updated_at=self.clock(),
            updated_by=updated_by,
        )
        await self.repository.save_logo(logo)
        return logo

    async def remove_logo(self, tenant_id: str) -> bool:
        """Go back to the default mark. False when the tenant had no logo."""
        return await self.repository.delete_logo(tenant_id)


def _declared_type(value: str) -> LogoContentType:
    normalized = value.strip().lower()
    if normalized == "image/svg+xml":
        raise UnsupportedLogoType(
            "SVG logos are not accepted because an SVG can carry script. "
            f"Upload a {_ACCEPTED_FORMATS} image."
        )
    try:
        return LogoContentType(normalized)
    except ValueError:
        shown = normalized or "a file without a type"
        raise UnsupportedLogoType(
            f"A logo must be a {_ACCEPTED_FORMATS} image, not {shown}."
        ) from None


def _decoded(data_base64: str) -> bytes:
    if len(data_base64) > _MAX_BASE64_CHARS:
        raise LogoTooLarge(_too_large_message())
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        # Non-ASCII text raises ValueError rather than binascii.Error.
        raise InvalidLogoData("data_base64 is not valid base64.") from exc
    if not data:
        raise InvalidLogoData("The logo file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise LogoTooLarge(_too_large_message())
    return data


def _too_large_message() -> str:
    return f"A logo must be at most {MAX_LOGO_BYTES // 1024} KB."


def _format_name(content_type: LogoContentType) -> str:
    return {
        LogoContentType.PNG: "a PNG image",
        LogoContentType.JPEG: "a JPEG image",
        LogoContentType.WEBP: "a WebP image",
    }[content_type]
