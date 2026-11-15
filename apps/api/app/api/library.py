from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DB

from app.api.schemas import BatchInput, FeedbackInput, PhotoPatch
from app.core.config import settings
from app.core.security import audit, current, household, media_actor, photo_access, privacy_changed, writer
from app.db.models import EventPhoto, Face, Feedback, Photo, PhotoAnalysis, PhotoDerivative, serialize
from app.db.session import get_db
from app.services.photos import ingest, photo_payload, purge_photo
from app.services.storage import storage
from app.workers.queue import enqueue_coalesced, enqueue_photo

router = APIRouter()
decode_slots = asyncio.Semaphore(2)


@router.post("/photos/upload")
async def upload(
    file: UploadFile = File(...), shared: bool = Form(False), actor=Depends(writer), db: DB = Depends(get_db)
):
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"单张图片限 {settings.max_upload_mb}MB")
        chunks.append(chunk)
    # CPU decoding belongs in a thread, not the event loop.
    from starlette.concurrency import run_in_threadpool

    async with decode_slots:
        return await run_in_threadpool(
            ingest, db, actor, b"".join(chunks), shared, household(db, actor).timezone, file.content_type
        )


@router.get("/photos")
def photos(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    visibility: str | None = None,
    uploader_id: str | None = None,
    event_id: str | None = None,
    person_id: str | None = None,
    quality: str | None = None,
    start: date | None = None,
    end: date | None = None,
    deleted: bool = False,
    duplicates: bool = False,
    q: str = "",
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    query = select(Photo).where(
        Photo.household_id == actor.household_id,
        Photo.is_deleted == deleted,
        or_(Photo.uploader_id == actor.user_id, (Photo.visibility == "family") & Photo.is_hidden.is_(False)),
    )
    if deleted:
        query = query.where(Photo.uploader_id == actor.user_id)
    if visibility:
        query = query.where(Photo.visibility == visibility)
    if uploader_id:
        query = query.where(Photo.uploader_id == uploader_id)
    if event_id:
        query = query.where(Photo.id.in_(select(EventPhoto.photo_id).where(EventPhoto.event_id == event_id)))
    if person_id:
        query = query.where(Photo.id.in_(select(Face.photo_id).where(Face.person_id == person_id)))
    if start:
        lower = datetime.combine(start, time.min, ZoneInfo(household(db, actor).timezone)).astimezone(UTC).isoformat()
        query = query.where(Photo.taken_at >= lower)
    if end:
        upper = (
            datetime.combine(end + timedelta(days=1), time.min, ZoneInfo(household(db, actor).timezone))
            .astimezone(UTC)
            .isoformat()
        )
        query = query.where(Photo.taken_at < upper)
    if quality:
        query = query.join(PhotoAnalysis).where(
            PhotoAnalysis.quality_score < 0.3 if quality == "low" else PhotoAnalysis.quality_score >= 0.3
        )
    if duplicates:
        query = query.where(Photo.duplicate_group.is_not(None))
    if q:
        query = query.where(
            or_(Photo.title.contains(q), Photo.caption_user.contains(q), Photo.location_user.contains(q))
        )
    from sqlalchemy import func

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(
        db.scalars(
            query.order_by(Photo.taken_at.desc().nullslast(), Photo.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {"items": [photo_payload(db, p) for p in items], "total": total, "page": page, "page_size": page_size}


@router.get("/photos/{pid}")
def photo_detail(pid: str, actor=Depends(current), db: DB = Depends(get_db)):
    photo = photo_access(db, pid, actor, allow_deleted=True)
    result = photo_payload(db, photo, detail=True)
    result["can_edit"] = actor.role != "viewer" and (photo.uploader_id == actor.user_id or actor.role == "owner")
    result["faces"] = [serialize(f) for f in db.scalars(select(Face).where(Face.photo_id == pid))]
    return result


@router.get("/photos/{pid}/media/{variant}")
def media(pid: str, variant: str, actor=Depends(media_actor), db: DB = Depends(get_db)):
    photo = photo_access(db, pid, actor)
    if variant == "original":
        if actor.role == "display":
            raise HTTPException(403, "展示设备无权导出原图")
        key, mime = photo.storage_key, photo.mime
    elif variant in {"320", "960"}:
        derivative = db.scalar(
            select(PhotoDerivative).where(PhotoDerivative.photo_id == pid, PhotoDerivative.variant == variant)
        )
        if not derivative:
            raise HTTPException(404, "缩略图尚未就绪")
        key, mime = derivative.storage_key, "image/jpeg"
    else:
        raise HTTPException(404, "衍生图规格不存在")
    path = storage.read_media(key)
    if not path.is_file():
        raise HTTPException(404, "媒体文件缺失，请检查备份")
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store, private"})


@router.patch("/photos/{pid}")
def edit_photo(pid: str, payload: PhotoPatch, actor=Depends(writer), db: DB = Depends(get_db)):
    photo = photo_access(db, pid, actor, edit=True)
    prior_visibility = photo.visibility
    changes = payload.model_dump(exclude_unset=True)
    if any(value is None for key, value in changes.items() if key != "taken_at"):
        raise HTTPException(422, "此字段不可为空")
    for key, value in changes.items():
        if key == "taken_at":
            value = value.astimezone(UTC).isoformat() if value else None
            photo.capture_time_source = "user" if value else "unknown"
        setattr(photo, key, value)
    photo.locked_fields = sorted(set(photo.locked_fields) | set(changes))
    privacy_changed(db, actor)
    enqueue_coalesced(db, actor.household_id, "events")
    enqueue_coalesced(db, actor.household_id, "recommendation")
    if prior_visibility != "family" and photo.visibility == "family" and household(db, actor).settings_json["faces_enabled"]:
        enqueue_photo(db, photo, rerun=True, steps=["faces"])
    audit(db, actor, "photo_edited", "photos", pid, {"fields": list(changes)})
    db.commit()
    return photo_payload(db, photo)


@router.delete("/photos/{pid}")
def delete_photo(pid: str, permanent: bool = False, confirm: str = "", actor=Depends(writer), db: DB = Depends(get_db)):
    photo = photo_access(db, pid, actor, edit=True, allow_deleted=True)
    privacy_changed(db, actor)
    if permanent:
        if confirm != pid or not photo.is_deleted:
            raise HTTPException(409, "须先移入回收站，并以照片 ID 二次确认")
        audit(db, actor, "photo_purged", "photos", pid)
        purge_photo(db, photo)
    else:
        photo.is_deleted = True
        audit(db, actor, "photo_trashed", "photos", pid)
        db.commit()
    return {"ok": True}


@router.post("/photos/{pid}/restore")
def restore_photo(pid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    photo = photo_access(db, pid, actor, edit=True, allow_deleted=True)
    photo.is_deleted = False
    privacy_changed(db, actor)
    db.commit()
    return photo_payload(db, photo)


@router.post("/photos/{pid}/analyze")
def analyze_photo(pid: str, step: str | None = None, actor=Depends(writer), db: DB = Depends(get_db)):
    photo = photo_access(db, pid, actor, edit=True)
    if step and step not in {"metadata", "quality", "embedding", "vlm", "faces", "postprocess"}:
        raise HTTPException(422, "不支持的分析步骤")
    jobs = enqueue_photo(db, photo, rerun=True, steps=[step] if step else None)
    db.commit()
    return {"job_ids": [j.id for j in jobs], "status": "queued"}


def apply_feedback(db, actor, photo, kind):
    if kind in {"hide", "unhide"}:
        photo.is_hidden = kind == "hide"
        privacy_changed(db, actor)
    db.add(
        Feedback(
            household_id=actor.household_id,
            actor_id=actor.user_id,
            photo_id=photo.id,
            type=kind,
            until_at=(datetime.now(UTC) + timedelta(days=7)).isoformat() if kind == "snooze" else None,
        )
    )
    enqueue_coalesced(db, actor.household_id, "recommendation")


@router.post("/photos/{pid}/feedback")
def feedback(pid: str, payload: FeedbackInput, actor=Depends(current), db: DB = Depends(get_db)):
    photo = photo_access(db, pid, actor)
    apply_feedback(db, actor, photo, payload.type)
    db.commit()
    return {"ok": True}


@router.post("/photos/batch")
def batch(payload: BatchInput, actor=Depends(writer), db: DB = Depends(get_db)):
    photos = [photo_access(db, pid, actor, edit=True, allow_deleted=True) for pid in dict.fromkeys(payload.photo_ids)]
    if payload.action == "share" and any(p.uploader_id != actor.user_id or p.is_deleted for p in photos):
        raise HTTPException(422, "只能将本人未删除的照片加入家庭共享")
    for photo in photos:
        if payload.action == "favorite":
            apply_feedback(db, actor, photo, "favorite")
        elif payload.action == "hide":
            photo.is_hidden = True
        elif payload.action == "delete":
            photo.is_deleted = True
        elif payload.action == "restore":
            photo.is_deleted = False
        elif payload.action == "share":
            newly_shared = photo.visibility != "family"
            photo.visibility = "family"
            photo.locked_fields = sorted(set(photo.locked_fields) | {"visibility"})
            if newly_shared and household(db, actor).settings_json["faces_enabled"]:
                enqueue_photo(db, photo, rerun=True, steps=["faces"])
        else:
            enqueue_photo(db, photo, rerun=True)
    privacy_changed(db, actor)
    if payload.action == "share":
        enqueue_coalesced(db, actor.household_id, "events")
        enqueue_coalesced(db, actor.household_id, "recommendation")
        audit(db, actor, "photos_shared", "photos", actor.household_id, {"count": len(photos)})
    db.commit()
    return {"ok": True, "count": len(photos)}
