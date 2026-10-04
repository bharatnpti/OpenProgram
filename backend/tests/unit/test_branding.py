"""The tenant logo: what is accepted, who may change it, and how it is stored."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config.settings import Settings
from core.application.branding_service import (
    BrandingService,
    InvalidLogoData,
    LogoTooLarge,
    UnsupportedLogoType,
)
from core.domain.branding import (
    MAX_LOGO_BYTES,
    LogoContentType,
    TenantLogo,
    sniff_logo_content_type,
)
from infra.persistence.in_memory_branding import InMemoryTenantLogoRepository
from infra.persistence.postgres_branding import PostgresTenantLogoRepository

NOW = datetime(2026, 10, 4, 9, 30, tzinfo=UTC)
# A real 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
WEBP = b"RIFF" + struct.pack("<I", 26) + b"WEBP" + b"VP8L" + b"\x0d\x00\x00\x00" + b"\x2f" * 14
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _service() -> tuple[BrandingService, InMemoryTenantLogoRepository]:
    repository = InMemoryTenantLogoRepository()
    return BrandingService(repository=repository, clock=lambda: NOW), repository


def _png_of_size(size: int) -> bytes:
    return PNG + b"\x00" * (size - len(PNG))


# --- What counts as an image -------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (PNG, LogoContentType.PNG),
        (JPEG, LogoContentType.JPEG),
        (WEBP, LogoContentType.WEBP),
        (SVG, None),
        (GIF, None),
        (b"<!doctype html><html></html>", None),
        (b"", None),
        # A PNG signature with no IHDR chunk after it is not an image.
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, None),
        # A RIFF file of another form, such as WAVE audio.
        (b"RIFF" + struct.pack("<I", 4) + b"WAVEfmt ", None),
    ],
)
def test_logo_type_is_read_from_the_file_bytes(
    data: bytes, expected: LogoContentType | None
) -> None:
    assert sniff_logo_content_type(data) is expected


# --- Service -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("content_type", "data"),
    [("image/png", PNG), ("image/jpeg", JPEG), ("image/webp", WEBP)],
)
async def test_an_accepted_image_becomes_the_tenant_logo(content_type: str, data: bytes) -> None:
    service, _ = _service()

    logo = await service.replace_logo(
        "acme", content_type=content_type, data_base64=_b64(data), updated_by="admin-1"
    )

    assert logo == TenantLogo(
        tenant_id="acme",
        content_type=LogoContentType(content_type),
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        updated_at=NOW,
        updated_by="admin-1",
    )
    assert await service.logo("acme") == logo


async def test_a_new_upload_replaces_the_old_logo() -> None:
    service, _ = _service()
    await service.replace_logo(
        "acme", content_type="image/png", data_base64=_b64(PNG), updated_by="admin-1"
    )

    await service.replace_logo(
        "acme", content_type="image/jpeg", data_base64=_b64(JPEG), updated_by="admin-2"
    )

    stored = await service.logo("acme")
    assert stored is not None
    assert (stored.content_type, stored.data, stored.updated_by) == (
        LogoContentType.JPEG,
        JPEG,
        "admin-2",
    )


async def test_each_tenant_has_its_own_logo() -> None:
    service, _ = _service()
    await service.replace_logo(
        "acme", content_type="image/png", data_base64=_b64(PNG), updated_by="admin-1"
    )

    assert await service.logo("globex") is None
    assert await service.remove_logo("globex") is False
    assert await service.logo("acme") is not None


async def test_removing_the_logo_goes_back_to_the_default() -> None:
    service, _ = _service()
    await service.replace_logo(
        "acme", content_type="image/png", data_base64=_b64(PNG), updated_by="admin-1"
    )

    assert await service.remove_logo("acme") is True
    assert await service.logo("acme") is None
    assert await service.remove_logo("acme") is False


async def test_the_declared_type_is_matched_loosely_for_case_and_spaces() -> None:
    service, _ = _service()

    logo = await service.replace_logo(
        "acme", content_type=" Image/PNG ", data_base64=_b64(PNG), updated_by="admin-1"
    )

    assert logo.content_type is LogoContentType.PNG


@pytest.mark.parametrize(
    ("content_type", "data", "message"),
    [
        ("image/svg+xml", SVG, "SVG logos are not accepted"),
        ("image/gif", GIF, "must be a PNG, JPEG or WebP image, not image/gif"),
        ("", PNG, "not a file without a type"),
        # A script renamed to .png: the declared type is fine, the bytes are not.
        ("image/png", SVG, "The file is not a PNG, JPEG or WebP image"),
        ("image/png", JPEG, "The file is a JPEG image, but it was sent as image/png"),
    ],
)
async def test_anything_but_an_accepted_raster_image_is_refused(
    content_type: str, data: bytes, message: str
) -> None:
    service, repository = _service()

    with pytest.raises(UnsupportedLogoType, match=message):
        await service.replace_logo(
            "acme", content_type=content_type, data_base64=_b64(data), updated_by="admin-1"
        )

    assert await repository.get_logo("acme") is None


@pytest.mark.parametrize(
    "data_base64",
    [
        "not base64!",
        # A data URL is not the bare base64 the API asks for.
        f"data:image/png;base64,{_b64(PNG)}",
        # Line-wrapped base64 is refused rather than guessed at.
        _b64(PNG)[:20] + "\n" + _b64(PNG)[20:],
        "ünïcode",
    ],
)
async def test_bytes_that_are_not_base64_are_refused(data_base64: str) -> None:
    service, _ = _service()

    with pytest.raises(InvalidLogoData, match="not valid base64"):
        await service.replace_logo(
            "acme", content_type="image/png", data_base64=data_base64, updated_by="admin-1"
        )


async def test_an_empty_file_is_refused() -> None:
    service, _ = _service()

    with pytest.raises(InvalidLogoData, match="empty"):
        await service.replace_logo(
            "acme", content_type="image/png", data_base64="", updated_by="admin-1"
        )


async def test_a_logo_of_exactly_the_limit_is_accepted() -> None:
    service, _ = _service()
    data = _png_of_size(MAX_LOGO_BYTES)

    logo = await service.replace_logo(
        "acme", content_type="image/png", data_base64=_b64(data), updated_by="admin-1"
    )

    assert len(logo.data) == MAX_LOGO_BYTES


@pytest.mark.parametrize(
    "data_base64",
    [
        # One byte over, which still fits the longest base64 the limit allows.
        _b64(_png_of_size(MAX_LOGO_BYTES + 1)),
        # Far over: refused on its length, before it is decoded.
        "A" * (MAX_LOGO_BYTES * 2),
    ],
)
async def test_a_logo_over_256_kb_is_refused(data_base64: str) -> None:
    service, repository = _service()

    with pytest.raises(LogoTooLarge, match="at most 256 KB"):
        await service.replace_logo(
            "acme", content_type="image/png", data_base64=data_base64, updated_by="admin-1"
        )

    assert await repository.get_logo("acme") is None


# --- API -----------------------------------------------------------------------


def test_admin_uploads_reads_and_removes_the_logo(settings: Settings) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        before = client.get("/config/branding")
        uploaded = client.put(
            "/config/branding/logo",
            json={"content_type": "image/png", "data_base64": _b64(PNG)},
        )
        after_upload = client.get("/config/branding")
        removed = client.delete("/config/branding/logo")
        after_remove = client.get("/config/branding")
        removed_again = client.delete("/config/branding/logo")

    assert before.status_code == 200
    assert before.json() == {"logo": None}

    assert uploaded.status_code == 200
    logo = uploaded.json()["logo"]
    assert logo["data_url"] == f"data:image/png;base64,{_b64(PNG)}"
    assert logo["content_type"] == "image/png"
    assert logo["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert logo["updated_by"] == settings.dev_principal_subject
    assert datetime.fromisoformat(logo["updated_at"]).tzinfo is not None
    assert after_upload.json() == uploaded.json()

    assert removed.status_code == 204
    assert after_remove.json() == {"logo": None}
    assert removed_again.status_code == 204


@pytest.mark.parametrize(
    ("body", "status_code", "detail"),
    [
        (
            {"content_type": "image/svg+xml", "data_base64": _b64(SVG)},
            415,
            "SVG logos are not accepted",
        ),
        (
            {"content_type": "image/png", "data_base64": _b64(SVG)},
            415,
            "The file is not a PNG, JPEG or WebP image",
        ),
        (
            {"content_type": "image/png", "data_base64": _b64(_png_of_size(MAX_LOGO_BYTES + 1))},
            413,
            "at most 256 KB",
        ),
        ({"content_type": "image/png", "data_base64": "not base64!"}, 400, "not valid base64"),
    ],
)
def test_a_refused_upload_says_why_and_keeps_the_current_logo(
    settings: Settings, body: dict[str, str], status_code: int, detail: str
) -> None:
    app = create_app(settings=settings)
    with TestClient(app) as client:
        client.put(
            "/config/branding/logo",
            json={"content_type": "image/jpeg", "data_base64": _b64(JPEG)},
        )
        refused = client.put("/config/branding/logo", json=body)
        current = client.get("/config/branding")

    assert refused.status_code == status_code
    assert detail in refused.json()["detail"]
    assert current.json()["logo"]["content_type"] == "image/jpeg"


@pytest.mark.parametrize("role", ["dev", "po", "sm", "mgr", "exec"])
def test_every_role_reads_the_logo_but_only_an_admin_changes_it(
    settings: Settings, role: str
) -> None:
    app = create_app(settings=settings.model_copy(update={"dev_principal_roles": role}))
    with TestClient(app, raise_server_exceptions=False) as client:
        asyncio.run(
            app.state.registry.tenant_logo_repository().save_logo(
                TenantLogo(
                    tenant_id=settings.tenant_id,
                    content_type=LogoContentType.PNG,
                    data=PNG,
                    sha256=hashlib.sha256(PNG).hexdigest(),
                    updated_at=NOW,
                    updated_by="admin-1",
                )
            )
        )
        read = client.get("/config/branding")
        replaced = client.put(
            "/config/branding/logo",
            json={"content_type": "image/jpeg", "data_base64": _b64(JPEG)},
        )
        removed = client.delete("/config/branding/logo")
        after = client.get("/config/branding")

    assert read.status_code == 200
    assert read.json()["logo"]["updated_by"] == "admin-1"
    assert replaced.status_code == 403
    assert removed.status_code == 403
    assert after.json() == read.json()


# --- Postgres ------------------------------------------------------------------


@dataclass
class _RecordingExecutor:
    rows: list[dict[str, object]]
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)

    async def execute(self, query: str, params: Sequence[object] = ()) -> object:
        self.calls.append((query, tuple(params)))
        return None

    async def fetch(
        self, query: str, params: Sequence[object] = ()
    ) -> Sequence[Mapping[str, object]]:
        self.calls.append((query, tuple(params)))
        return self.rows


def _row(data: object) -> dict[str, object]:
    return {
        "tenant_id": "acme",
        "content_type": "image/png",
        "data": data,
        "sha256": hashlib.sha256(PNG).hexdigest(),
        "updated_at": NOW,
        "updated_by": "admin-1",
    }


@pytest.mark.parametrize("data", [PNG, memoryview(PNG)])
async def test_postgres_reads_the_tenant_row(data: object) -> None:
    executor = _RecordingExecutor(rows=[_row(data)])

    logo = await PostgresTenantLogoRepository(executor).get_logo("acme")

    query, params = executor.calls[0]
    assert "FROM tenant_logos" in query
    assert "WHERE tenant_id = %s" in query
    assert params == ("acme",)
    assert logo == TenantLogo(
        tenant_id="acme",
        content_type=LogoContentType.PNG,
        data=PNG,
        sha256=hashlib.sha256(PNG).hexdigest(),
        updated_at=NOW,
        updated_by="admin-1",
    )


async def test_postgres_reads_no_logo_when_the_tenant_has_none() -> None:
    executor = _RecordingExecutor(rows=[])

    assert await PostgresTenantLogoRepository(executor).get_logo("acme") is None


async def test_postgres_save_upserts_the_one_row_per_tenant() -> None:
    executor = _RecordingExecutor(rows=[])
    logo = TenantLogo(
        tenant_id="acme",
        content_type=LogoContentType.WEBP,
        data=WEBP,
        sha256=hashlib.sha256(WEBP).hexdigest(),
        updated_at=NOW,
        updated_by="admin-1",
    )

    await PostgresTenantLogoRepository(executor).save_logo(logo)

    query, params = executor.calls[0]
    assert "INSERT INTO tenant_logos" in query
    assert "ON CONFLICT (tenant_id) DO UPDATE SET" in query
    for column in ("content_type", "data", "sha256", "updated_at", "updated_by"):
        assert f"{column} = EXCLUDED.{column}" in query
    assert params == (
        "acme",
        "image/webp",
        WEBP,
        hashlib.sha256(WEBP).hexdigest(),
        NOW,
        "admin-1",
    )


@pytest.mark.parametrize(("rows", "deleted"), [([{"tenant_id": "acme"}], True), ([], False)])
async def test_postgres_delete_reports_whether_a_logo_was_removed(
    rows: list[dict[str, object]], deleted: bool
) -> None:
    executor = _RecordingExecutor(rows=rows)

    assert await PostgresTenantLogoRepository(executor).delete_logo("acme") is deleted

    query, params = executor.calls[0]
    assert "DELETE FROM tenant_logos" in query
    assert "WHERE tenant_id = %s" in query
    assert "RETURNING" in query
    assert params == ("acme",)
