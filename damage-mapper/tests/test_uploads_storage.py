from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from app.core.config import get_settings
from app.services import uploads


def test_supabase_upload_read_materialize_and_delete(
    tmp_path: Path, png_image: bytes, monkeypatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "supabase")
    monkeypatch.setattr(settings, "supabase_url", "https://project.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "test-service-role")
    monkeypatch.setattr(settings, "supabase_storage_bucket", "incident-evidence")
    local_uploads = tmp_path / "uploads"
    monkeypatch.setattr(uploads, "UPLOADS", local_uploads)
    objects: dict[str, bytes] = {}
    operations: list[tuple[str, str]] = []

    def fake_storage_request(
        method: str,
        storage_path: str,
        *,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        operations.append((method, storage_path))
        if method == "POST":
            assert content_type == "image/png"
            assert content is not None
            objects[storage_path] = content
            return b""
        if method == "GET":
            return objects[storage_path]
        if method == "DELETE":
            objects.pop(storage_path)
            return b""
        raise AssertionError(f"Unexpected storage method: {method}")

    monkeypatch.setattr(uploads, "_storage_request", fake_storage_request)
    stored = uploads.save_upload(BytesIO(png_image), "image/png", "evidence.png")

    assert stored.storage_path in objects
    assert stored.perceptual_hash
    assert not local_uploads.exists()
    with uploads.materialize_upload(stored.storage_path) as materialized:
        assert materialized.is_file()
        with Image.open(materialized) as image:
            assert image.format == "PNG"
    assert not materialized.exists()
    uploads.remove_upload(stored.storage_path)

    assert operations == [
        ("POST", stored.storage_path),
        ("GET", stored.storage_path),
        ("DELETE", stored.storage_path),
    ]
    assert not objects
