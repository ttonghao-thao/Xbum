import hashlib
import io
import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import imagehash
import numpy as np
from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
from sqlalchemy import delete, or_, select

from app.core.config import settings
from app.db.models import AudioMemory, Job, Photo, PhotoAnalysis, PhotoDerivative, Story, StorySlide, serialize, uid
from app.services.storage import storage

register_heif_opener()
Image.MAX_IMAGE_PIXELS = 50_000_000
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "HEIF": "image/heif"}


def parse_exif(image, timezone):
    exif = image.getexif()
    detail = exif.get_ifd(34665) if exif.get(34665) else exif
    result = {"taken_at": None, "capture_time_source": "upload_uncertain", "gps_lat": None, "gps_lon": None}
    value = detail.get(36867) or exif.get(306)
    if value:
        try:
            parsed = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
            offset = detail.get(36881)
            if offset and len(str(offset)) == 6:
                parsed = datetime.fromisoformat(parsed.isoformat() + str(offset))
                result["capture_time_source"] = "exif"
            else:
                parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
                result["capture_time_source"] = "exif_assumed_timezone"
            result["taken_at"] = parsed.astimezone(UTC).isoformat()
        except (ValueError, TypeError):
            pass
    try:
        gps = exif.get_ifd(34853)

        def coord(values, ref):
            number = float(values[0]) + float(values[1]) / 60 + float(values[2]) / 3600
            return -number if ref in {"S", "W", b"S", b"W"} else number

        lat, lon = coord(gps[2], gps[1]), coord(gps[4], gps[3])
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            result.update(gps_lat=lat, gps_lon=lon)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return result


def decode(data, claimed_mime=None):
    try:
        with Image.open(io.BytesIO(data)) as check:
            fmt = check.format
            if check.width * check.height > Image.MAX_IMAGE_PIXELS:
                raise HTTPException(415, "图片像素数量超过安全限制")
            if fmt not in FORMATS:
                raise HTTPException(415, "支持 JPEG、PNG、WEBP 和 HEIC/HEIF 图片")
            if claimed_mime and claimed_mime not in {
                FORMATS[fmt],
                "image/heic",
                "image/heif",
                "application/octet-stream",
            }:
                raise HTTPException(415, "图片内容与 MIME 不匹配")
            check.verify()
        image = Image.open(io.BytesIO(data))
        image.load()
        return image, FORMATS[fmt]
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(415, "图片损坏、不支持或像素数量超限") from None


def photo_payload(db, photo, detail=False):
    result = serialize(photo)
    from app.db.models import Household

    result["household_timezone"] = db.get(Household, photo.household_id).timezone
    result["capture_time_candidate"] = photo.created_at if not photo.taken_at else None
    analysis = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == photo.id))
    result["analysis"] = serialize(analysis) if analysis else None
    result["media_url"] = f"/api/v1/photos/{photo.id}/media/960"
    result["thumbnail_url"] = f"/api/v1/photos/{photo.id}/media/320"
    if detail:
        result["jobs"] = [
            serialize(j)
            for j in db.scalars(select(Job).where(Job.target_id == photo.id).order_by(Job.created_at.desc()))
        ]
        result["memories"] = [
            serialize(m) for m in db.scalars(select(AudioMemory).where(AudioMemory.photo_id == photo.id))
        ]
    return result


def ingest(db, actor, data, shared, timezone, claimed_mime=None):
    if not data or len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"单张图片限 {settings.max_upload_mb}MB")
    sha = hashlib.sha256(data).hexdigest()
    existing = db.scalar(
        select(Photo).where(
            Photo.household_id == actor.household_id,
            Photo.sha256 == sha,
            or_(
                Photo.uploader_id == actor.user_id,
                (Photo.visibility == "family") & Photo.is_hidden.is_(False) & Photo.is_deleted.is_(False),
            ),
        )
    )
    if existing:
        return {"photo_id": existing.id, "job_id": None, "status": "duplicate", "is_deleted": existing.is_deleted}
    image, mime = decode(data, claimed_mime)
    metadata = parse_exif(image, timezone)
    normalized = ImageOps.exif_transpose(image).convert("RGB")
    phash = str(imagehash.phash(normalized))
    photo_id = uid()
    prefix = f"{actor.household_id}/photos/{photo_id}"
    photo = Photo(
        id=photo_id,
        household_id=actor.household_id,
        uploader_id=actor.user_id,
        storage_key=f"{prefix}/original",
        sha256=sha,
        phash=phash,
        mime=mime,
        width=normalized.width,
        height=normalized.height,
        visibility="family" if shared else "private",
        **metadata,
    )
    # Only compare images the uploader can access. Never reveal someone else's private hashes.
    recent = db.scalars(
        select(Photo).where(
            Photo.household_id == actor.household_id,
            Photo.is_deleted.is_(False),
            or_(Photo.uploader_id == actor.user_id, (Photo.visibility == "family") & Photo.is_hidden.is_(False)),
        )
    )
    created = datetime.now(UTC)
    tiny = np.asarray(normalized.resize((32, 32)), dtype=np.float32)
    for other in recent:
        if not other.phash or (int(phash, 16) ^ int(other.phash, 16)).bit_count() > 8:
            continue
        left = datetime.fromisoformat(photo.taken_at) if photo.taken_at else created
        right = datetime.fromisoformat(other.taken_at or other.created_at)
        if abs((left - right).total_seconds()) > 600:
            continue
        derivative = db.scalar(
            select(PhotoDerivative).where(PhotoDerivative.photo_id == other.id, PhotoDerivative.variant == "320")
        )
        if derivative:
            with Image.open(storage.read_media(derivative.storage_key)) as comparison:
                distance = float(
                    np.abs(tiny - np.asarray(comparison.convert("RGB").resize((32, 32)), dtype=np.float32)).mean()
                )
            if distance < 25:
                photo.duplicate_group = other.duplicate_group or other.id
                other.duplicate_group = photo.duplicate_group
                break
    keys = []
    try:
        storage.put_original(photo.storage_key, data)
        keys.append(photo.storage_key)
        db.add(photo)
        db.flush()
        for size in (320, 960):
            thumb = normalized.copy()
            thumb.thumbnail((size, size))
            output = io.BytesIO()
            thumb.save(output, "JPEG", quality=86)
            key = f"{prefix}/{size}.jpg"
            storage.create_derivative(key, output.getvalue())
            keys.append(key)
            db.add(
                PhotoDerivative(
                    photo_id=photo.id, variant=str(size), storage_key=key, width=thumb.width, height=thumb.height
                )
            )
        db.add(PhotoAnalysis(photo_id=photo.id))
        from app.workers.queue import enqueue_photo

        jobs = enqueue_photo(db, photo)
        db.commit()
        return {"photo_id": photo.id, "job_id": jobs[0].id, "status": "queued"}
    except Exception:
        db.rollback()
        for key in keys:
            storage.delete_media(key)
        # Concurrent uploads can race at the unique constraint, without orphaning files.
        existing = db.scalar(
            select(Photo).where(
                Photo.household_id == actor.household_id, Photo.uploader_id == actor.user_id, Photo.sha256 == sha
            )
        )
        if existing:
            return {"photo_id": existing.id, "job_id": None, "status": "duplicate"}
        raise
    finally:
        image.close()
        normalized.close()


def purge_photo(db, photo):
    keys = [photo.storage_key]
    keys += list(db.scalars(select(PhotoDerivative.storage_key).where(PhotoDerivative.photo_id == photo.id)))
    keys += list(db.scalars(select(AudioMemory.audio_key).where(AudioMemory.photo_id == photo.id)))
    stories = list(db.scalars(select(Story).join(StorySlide).where(StorySlide.photo_id == photo.id)))
    for story in stories:
        if story.mp4_key:
            keys.append(story.mp4_key)
            story.mp4_key = None
            story.status = "draft"
            story.version += 1
    memory_ids = list(db.scalars(select(AudioMemory.id).where(AudioMemory.photo_id == photo.id)))
    db.execute(delete(Job).where(Job.target_id.in_([photo.id, *memory_ids])))
    db.delete(photo)
    db.commit()
    for key in keys:
        storage.delete_media(key)


def refresh_metadata(db, photo, timezone):
    data = storage.read_media(photo.storage_key).read_bytes()
    if hashlib.sha256(data).hexdigest() != photo.sha256:
        raise ValueError("original integrity mismatch")
    image, mime = decode(data)
    try:
        metadata = parse_exif(image, timezone)
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        photo.width, photo.height, photo.mime = normalized.width, normalized.height, mime
        photo.phash = str(imagehash.phash(normalized))
        for key, value in metadata.items():
            if key in photo.locked_fields or (key == "capture_time_source" and "taken_at" in photo.locked_fields):
                continue
            setattr(photo, key, value)
        for size in (320, 960):
            thumb = normalized.copy()
            thumb.thumbnail((size, size))
            output = io.BytesIO()
            thumb.save(output, "JPEG", quality=86)
            key = f"{photo.household_id}/photos/{photo.id}/{size}.jpg"
            storage.create_derivative(key, output.getvalue())
            row = db.scalar(
                select(PhotoDerivative).where(
                    PhotoDerivative.photo_id == photo.id, PhotoDerivative.variant == str(size)
                )
            )
            if row:
                row.storage_key, row.width, row.height, row.status = key, thumb.width, thumb.height, "ready"
            else:
                db.add(
                    PhotoDerivative(
                        photo_id=photo.id, variant=str(size), storage_key=key, width=thumb.width, height=thumb.height
                    )
                )
        normalized.close()
    finally:
        image.close()


def family_candidates(db, household_id):
    return list(
        db.scalars(
            select(Photo)
            .where(
                Photo.household_id == household_id,
                Photo.visibility == "family",
                Photo.is_hidden.is_(False),
                Photo.is_deleted.is_(False),
            )
            .order_by(Photo.id)
        )
    )


def album_candidates(db, actor):
    """Private organization for its uploader, plus already shared family photos.

    This is not a display/search/story candidate source; those stay family-only.
    """
    return list(
        db.scalars(
            select(Photo)
            .where(
                Photo.household_id == actor.household_id,
                Photo.is_hidden.is_(False),
                Photo.is_deleted.is_(False),
                or_(Photo.visibility == "family", Photo.uploader_id == actor.user_id),
            )
            .order_by(Photo.id)
        )
    )


def current_vision(analysis):
    from app.api.schemas import AnalysisOutput, VISION_CONTRACT_VERSION
    from pydantic import ValidationError

    if not analysis or analysis.status != "succeeded" or analysis.pipeline_version != VISION_CONTRACT_VERSION:
        return False
    try:
        AnalysisOutput.model_validate(analysis.analysis_json)
        return True
    except ValidationError:
        return False


def display_candidates(db, household):
    photos = family_candidates(db, household.id)
    scores = dict(
        db.execute(
            select(PhotoAnalysis.photo_id, PhotoAnalysis.quality_score).where(
                PhotoAnalysis.photo_id.in_([p.id for p in photos])
            )
        ).all()
    )
    return [p for p in photos if scores.get(p.id) is None or scores[p.id] >= household.settings_json["quality_floor"]]
