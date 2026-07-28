"""Safe logo decoding, normalization, storage, and lookup."""

from __future__ import annotations

import os
import tempfile
import uuid
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_IMAGE_FORMATS = {"PNG", "JPEG", "GIF", "WEBP"}


class LogoValidationError(ValueError):
    """Raised when an uploaded logo is not safe to store or render."""


def store_logo(
    file_storage,
    *,
    user_id: int,
    upload_folder: str,
    max_pixels: int,
    max_dimension: int,
) -> str:
    """Decode and re-encode one static image to a server-named PNG."""
    extension = Path(file_storage.filename or "").suffix.lower()
    if extension not in ALLOWED_LOGO_EXTENSIONS:
        raise LogoValidationError("Logo must be a PNG, JPG, GIF, or WebP image.")

    try:
        file_storage.stream.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(file_storage.stream)
            if image.format not in ALLOWED_IMAGE_FORMATS:
                raise LogoValidationError("The uploaded file is not a supported image.")
            width, height = image.size
            if (
                width <= 0
                or height <= 0
                or width > max_dimension
                or height > max_dimension
                or width * height > max_pixels
            ):
                raise LogoValidationError("Logo dimensions are too large.")
            if getattr(image, "is_animated", False):
                raise LogoValidationError("Animated logos are not supported.")
            image.load()
            image = ImageOps.exif_transpose(image)
            has_alpha = image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            normalized = image.convert("RGBA" if has_alpha else "RGB")
            image.close()
    except LogoValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ):
        raise LogoValidationError("The uploaded file is not a valid image.") from None

    os.makedirs(upload_folder, mode=0o750, exist_ok=True)
    filename = f"{user_id}_{uuid.uuid4().hex}.png"
    destination = os.path.join(upload_folder, filename)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=upload_folder,
            prefix=f".{user_id}_",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
        normalized.save(temporary_path, format="PNG", optimize=True)
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, destination)
    except OSError:
        raise LogoValidationError("The logo could not be stored.") from None
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
    return filename


def delete_user_logo(filename: str | None, *, user_id: int, upload_folder: str) -> None:
    if not filename or os.path.basename(filename) != filename:
        return
    if not filename.startswith(f"{user_id}_"):
        return
    path = os.path.join(upload_folder, filename)
    try:
        os.remove(path)
    except OSError:
        pass


def resolve_logo_path(
    filename: str | None,
    *,
    upload_folder: str,
    legacy_logo_folder: str,
) -> str | None:
    """Resolve a basename in the private upload folder or legacy static folder."""
    if not filename or os.path.basename(filename) != filename:
        return None
    for folder in (upload_folder, legacy_logo_folder):
        candidate = os.path.join(folder, filename)
        if os.path.isfile(candidate):
            return candidate
    return None
