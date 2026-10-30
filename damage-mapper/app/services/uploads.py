from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator
from urllib.parse import quote

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import UPLOADS, get_settings

logger = logging.getLogger(__name__)
MIME_FORMATS = {
    "image/jpeg": ("JPEG", ".jpg"),
    "image/png": ("PNG", ".png"),
    "image/webp": ("WEBP", ".webp"),
}
FORMAT_MIMES = {image_format: mime for mime, (image_format, _) in MIME_FORMATS.items()}
CHUNK_SIZE = 64 * 1024


class UploadValidationError(ValueError):
    pass


class StorageUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredUpload:
    storage_path: str
    content_type: str
    file_size: int
    sha256: str
    perceptual_hash: str
    original_filename: str | None


def _storage_object_url(storage_path: str) -> str:
    if (
        not storage_path
        or storage_path in {".", ".."}
        or "/" in storage_path
        or "\\" in storage_path
        or any(ord(character) < 32 for character in storage_path)
    ):
        raise ValueError("Invalid image storage path")
    settings = get_settings()
    return (
        f"{settings.supabase_url}/storage/v1/object/"
        f"{quote(settings.supabase_storage_bucket, safe='')}/"
        f"{quote(storage_path, safe='')}"
    )


def _storage_request(
    method: str,
    storage_path: str,
    *,
    content: bytes | None = None,
    content_type: str | None = None,
) -> bytes:
    settings = get_settings()
    headers = {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    if method == "POST":
        headers["x-upsert"] = "false"
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.request(
                method,
                _storage_object_url(storage_path),
                headers=headers,
                content=content,
            )
            response.raise_for_status()
            return response.content
    except httpx.HTTPError as error:
        logger.error(
            "Supabase Storage %s failed with HTTP status %s",
            method,
            error.response.status_code
            if isinstance(error, httpx.HTTPStatusError)
            else "unavailable",
        )
        raise StorageUnavailableError(
            "Supabase Storage could not complete the image operation"
        ) from error


def _difference_hash(path: Path) -> str:
    with Image.open(path) as image:
        pixels = list(image.convert("L").resize((9, 8)).getdata())
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = (bits << 1) | int(pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return f"{bits:016x}"


def save_upload(file: BinaryIO, content_type: str | None, filename: str | None) -> StoredUpload:
    if content_type not in MIME_FORMATS:
        raise UploadValidationError("Upload a JPG, PNG or WEBP image")

    if get_settings().storage_backend == "local":
        UPLOADS.mkdir(parents=True, exist_ok=True)
    raw_path: Path | None = None
    normalized_path: Path | None = None
    destination: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".upload", delete=False) as raw:
            raw_path = Path(raw.name)
            total = 0
            file.seek(0)
            while chunk := file.read(CHUNK_SIZE):
                total += len(chunk)
                if total > get_settings().max_upload_bytes:
                    raise UploadValidationError("Image must be 10 MB or smaller")
                raw.write(chunk)
        if total == 0:
            raise UploadValidationError("The uploaded image is empty")

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(raw_path) as image:
                actual_format = image.format
                width, height = image.size
                if actual_format != MIME_FORMATS[content_type][0]:
                    raise UploadValidationError("Image content does not match its declared type")
                if width <= 0 or height <= 0 or width * height > get_settings().max_image_pixels:
                    raise UploadValidationError("Image dimensions exceed the allowed limit")
                image.verify()

            with Image.open(raw_path) as image:
                image = ImageOps.exif_transpose(image)
                mode = "RGBA" if "A" in image.getbands() else "RGB"
                image = image.convert(mode)
                extension = MIME_FORMATS[content_type][1]
                with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as normalized:
                    normalized_path = Path(normalized.name)
                save_options: dict[str, int | bool] = {"optimize": True}
                if actual_format in ("JPEG", "WEBP"):
                    save_options["quality"] = 88
                image.save(normalized_path, format=actual_format, **save_options)

        digest = hashlib.sha256()
        with normalized_path.open("rb") as normalized:
            for chunk in iter(lambda: normalized.read(CHUNK_SIZE), b""):
                digest.update(chunk)
        file_size = normalized_path.stat().st_size
        if file_size > get_settings().max_upload_bytes:
            raise UploadValidationError("Normalized image exceeds the configured size limit")
        perceptual_hash = _difference_hash(normalized_path)
        storage_path = f"{uuid.uuid4()}{MIME_FORMATS[content_type][1]}"
        if get_settings().storage_backend == "supabase":
            _storage_request(
                "POST",
                storage_path,
                content=normalized_path.read_bytes(),
                content_type=content_type,
            )
        else:
            destination = UPLOADS / storage_path
            os.replace(normalized_path, destination)
            normalized_path = None
        clean_name = Path(filename).name[:255] if filename else None
        stored_upload = StoredUpload(
            storage_path=storage_path,
            content_type=content_type,
            file_size=file_size,
            sha256=digest.hexdigest(),
            perceptual_hash=perceptual_hash,
            original_filename=clean_name,
        )
        destination = None
        return stored_upload
    except UploadValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as error:
        logger.info("Rejected invalid image upload: %s", error)
        raise UploadValidationError("The uploaded file is not a valid supported image") from error
    finally:
        for temporary in (raw_path, normalized_path):
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if destination is not None and destination.exists():
            destination.unlink(missing_ok=True)


def upload_path(storage_path: str) -> Path:
    if get_settings().storage_backend != "local":
        raise RuntimeError("Supabase objects must be accessed through the storage adapter")
    safe_name = Path(storage_path).name
    if (
        safe_name != storage_path
        or not safe_name
        or storage_path in {".", ".."}
        or "/" in storage_path
        or "\\" in storage_path
        or any(ord(character) < 32 for character in storage_path)
    ):
        raise ValueError("Invalid image storage path")
    return UPLOADS / safe_name


def read_upload(storage_path: str) -> bytes:
    if get_settings().storage_backend == "supabase":
        return _storage_request("GET", storage_path)
    return upload_path(storage_path).read_bytes()


@contextmanager
def materialize_upload(storage_path: str) -> Iterator[Path]:
    if get_settings().storage_backend == "local":
        yield upload_path(storage_path)
        return

    content = read_upload(storage_path)
    suffix = Path(storage_path).suffix
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
        yield temporary_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def remove_upload(storage_path: str) -> None:
    try:
        if get_settings().storage_backend == "supabase":
            _storage_request("DELETE", storage_path)
        else:
            upload_path(storage_path).unlink(missing_ok=True)
    except (OSError, StorageUnavailableError, ValueError):
        logger.exception("Could not remove deleted incident image %s", storage_path)
