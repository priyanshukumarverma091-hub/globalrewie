"""App-aware helpers: slugs, uploads, logging, parsing."""
import os
import re
import secrets
from datetime import datetime
from urllib.parse import urlparse

from flask import current_app, request
from flask_login import current_user
from PIL import Image, ImageOps, UnidentifiedImageError

from .extensions import db
from .models import AdminLog
from .textutil import slugify

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
Image.MAX_IMAGE_PIXELS = 40_000_000  # decompression-bomb guard
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class UploadError(ValueError):
    pass


def unique_slug(model, base: str, exclude_id: int | None = None) -> str:
    base = slugify(base, 110)
    slug, n = base, 2
    while True:
        q = model.query.filter(model.slug == slug)
        if exclude_id:
            q = q.filter(model.id != exclude_id)
        if q.first() is None:
            return slug
        slug, n = f"{base}-{n}", n + 1


def log_action(action: str, target: str = "", detail: str = "", user_id=None):
    uid = user_id if user_id is not None else (current_user.id if current_user.is_authenticated else None)
    db.session.add(AdminLog(user_id=uid, action=action[:60], target=target[:200],
                            detail=detail[:500], ip=(request.remote_addr or "")[:64]))


def save_image(file_storage) -> str:
    """Validate, re-encode and store an uploaded image. Returns the base filename.
    Re-encoding with Pillow strips metadata and any embedded payloads."""
    filename = (file_storage.filename or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        raise UploadError("Only JPG, PNG, WebP or GIF images are allowed.")
    try:
        probe = Image.open(file_storage.stream)
        probe.verify()
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        if img.format not in ALLOWED_IMAGE_FORMATS:
            raise UploadError("Unsupported image format.")
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise UploadError("That file is not a valid image.")

    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    base = secrets.token_hex(10)
    for width, suffix in ((1600, ".jpg"), (800, "-800.jpg")):
        copy = img.copy()
        if copy.width > width:
            copy.thumbnail((width, 10000), Image.LANCZOS)
        copy.save(os.path.join(folder, base + suffix), "JPEG", quality=84, optimize=True, progressive=True)
    return base + ".jpg"


def delete_image(name: str):
    if not name or not re.fullmatch(r"[0-9a-f]{20}\.jpg", name):
        return
    folder = current_app.config["UPLOAD_FOLDER"]
    for suffix in (".jpg", "-800.jpg"):
        try:
            os.remove(os.path.join(folder, name[:-4] + suffix))
        except OSError:
            pass


def parse_local_datetime(value: str):
    """HTML datetime-local value ('2026-11-07T14:30') interpreted as UTC."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_date(value: str):
    return parse_local_datetime(value) if value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()) else None


def safe_next_url(target: str | None) -> str | None:
    """Only allow same-site relative redirects (open-redirect protection)."""
    if not target:
        return None
    p = urlparse(target)
    if p.scheme or p.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def is_http_url(value: str) -> bool:
    p = urlparse(value or "")
    return p.scheme in ("http", "https") and bool(p.netloc)
