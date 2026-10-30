from __future__ import annotations

import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings

SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_EDGE = 1024


class ImageValidationError(ValueError):
    pass


def load_preprocessed_image(path: Path) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                if source.format not in SUPPORTED_FORMATS:
                    raise ImageValidationError("Image must be JPEG, PNG or WEBP")
                width, height = source.size
                if width <= 0 or height <= 0:
                    raise ImageValidationError("Image dimensions must be positive")
                if width * height > get_settings().max_image_pixels:
                    raise ImageValidationError("Image dimensions exceed the allowed limit")
                source.load()
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail(
                    (MAX_IMAGE_EDGE, MAX_IMAGE_EDGE),
                    Image.Resampling.LANCZOS,
                )
                return image
    except ImageValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as error:
        raise ImageValidationError("The uploaded file is not a valid supported image") from error
