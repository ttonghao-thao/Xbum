import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DB

from app.api.auth import rate_limit
from app.api.library import apply_feedback
from app.api.schemas import DeviceInput, PairInput, PlaybackInput, SettingsPatch, page_items
from app.core.config import defaults, settings
from app.core.security import (
    audit,
    current,
    digest,
    display_actor,
    expires,
    household,
    owner,
    photo_access,
    privacy_changed,
    scoped,
    writer,
)
from app.db.models import (
    DisplayDevice,
    Event,
    Face,
    Job,
    Person,
    Photo,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
    SearchQuery,
    Story,
    now,
    serialize,
    uid,
)
from app.db.session import get_db
from app.services.calendar import is_quiet, local_now
from app.services.recommendations import build_daily_playlist, playlist_payload
from app.services.storage import storage
from app.services.stories import story_access
from app.services.system import model_status, processing_status
from app.workers.queue import enqueue, enqueue_photo

router = APIRouter()


@router.get("/system/processing")
def processing(actor=Depends(current), db: DB = Depends(get_db)):
    return processing_status(household(db, actor))


def latest_playlist(db, h, day):
    return db.scalar(
        select(Playlist)
        .where(Playlist.household_id == h.id, Playlist.playlist_date == day)
        .order_by(Playlist.created_at.desc(), Playlist.id.desc())
        .limit(1)
    )


@router.get("/playlists/today")
def today_playlist(actor=Depends(current), db: DB = Depends(get_db)):
    h = household(db, actor)
    today = local_now(h.timezone).date()
    playlist = latest_playlist(db, h, today.isoformat())
    if not playlist:
        playlist = build_daily_playlist(db, h, today)
        db.commit()
    return playlist_payload(db, playlist, h)


@router.get("/playlists/{day}")
def historic_playlist(day: date, actor=Depends(current), db: DB = Depends(get_db)):
    h = household(db, actor)
    return playlist_payload(db, latest_playlist(db, h, day.isoformat()), h)


@router.post("/playlists/regenerate")
def regenerate(actor=Depends(writer), db: DB = Depends(get_db)):
    h = household(db, actor)
    playlist = build_daily_playlist(db, h)
    db.commit()
    return playlist_payload(db, playlist, h)


@router.post("/display/devices")
def new_device(payload: DeviceInput, actor=Depends(owner), db: DB = Depends(get_db)):
    code = secrets.token_hex(5).upper()
    device = DisplayDevice(
        household_id=actor.household_id, pair_hash=digest(code), name=payload.name, expires_at=expires(1 / 6)
    )
    db.add(device)
    db.commit()
    return {"code": code, "device": serialize(device), "expires_minutes": 10}


@router.get("/display/devices")
def devices(
    page: int = Query(1, ge=1), page_size: int = Query(40, ge=1, le=100), actor=Depends(owner), db: DB = Depends(get_db)
):
    items = list(db.scalars(select(DisplayDevice).where(DisplayDevice.household_id == actor.household_id)))
    return page_items([serialize(i) for i in items], page, page_size)


@router.delete("/display/devices/{did}")
def revoke_device(did: str, actor=Depends(owner), db: DB = Depends(get_db)):
    device = scoped(db, DisplayDevice, did, actor)
    device.revoked_at = now()
    privacy_changed(db, actor)
    db.commit()
    return {"ok": True}


@router.post("/display/pair")
def pair(payload: PairInput, request: Request, response: Response, db: DB = Depends(get_db)):
    rate_limit(db, request, "pair")
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    device = db.scalar(
        select(DisplayDevice).where(
            DisplayDevice.pair_hash == digest(payload.code.upper()),
            DisplayDevice.expires_at > now(),
            DisplayDevice.revoked_at.is_(None),
            DisplayDevice.paired_at.is_(None),
        )
    )
    if not device:
        raise HTTPException(410, "配对码无效或已过期")
    token = secrets.token_urlsafe(40)
    device.token_hash, device.pair_hash, device.paired_at, device.expires_at = (
        digest(token),
        None,
        now(),
        expires(24 * 30),
    )
    response.set_cookie(
        "xbum_display",
        token,
        httponly=True,
        samesite="strict",
        secure=settings.session_cookie_secure,
        max_age=30 * 86400,
    )
    db.commit()
    return {"paired": True, "name": device.name}


@router.get("/display/feed")
def feed(actor=Depends(display_actor), db: DB = Depends(get_db)):
    h = household(db, actor)
    day = local_now(h.timezone).date()
    playlist = latest_playlist(db, h, day.isoformat())
    if not playlist:
        playlist = build_daily_playlist(db, h, day)
        db.commit()
    data = playlist_payload(db, playlist, h)
    # A display is deliberately given no AI records, raw paths, vectors, job results or uploader identity.
    for item in data["items"]:
        p = item["photo"]
        item["photo"] = {
            key: p.get(key)
            for key in ["id", "title", "caption_user", "taken_at", "capture_time_source", "household_timezone"]
        }
        item["photo"]["media_url"] = f"/api/v1/photos/{p['id']}/media/960?display=1"
        item["photo"]["thumbnail_url"] = f"/api/v1/photos/{p['id']}/media/320?display=1"
    data["quiet"] = is_quiet(h.settings_json, h.timezone)
    data["autoplay"] = h.settings_json["autoplay"]
    data["interval_seconds"] = h.settings_json["display_interval_seconds"]
    data["server_time"] = local_now(h.timezone).isoformat()
    data["household_name"] = h.name
    from app.services.photos import display_candidates

    data["authorized_photo_ids"] = [p.id for p in display_candidates(db, h)]
    data.pop("config_json", None)
    return data


@router.post("/display/events")
def display_event(payload: PlaybackInput, actor=Depends(display_actor), db: DB = Depends(get_db)):
    if not payload.photo_id and not payload.story_id:
        raise HTTPException(422, "请选择展示对象")
    photo = photo_access(db, payload.photo_id, actor) if payload.photo_id else None
    if payload.story_id:
        story_access(db, payload.story_id, actor)
    if payload.action in {"favorite", "hide", "dislike"}:
        if not photo:
            raise HTTPException(422, "反馈需要具体照片")
        apply_feedback(db, actor, photo, payload.action)
    db.add(
        PlaybackEvent(
            household_id=actor.household_id,
            display_device_id=actor.device_id,
            photo_id=payload.photo_id,
            story_id=payload.story_id,
            dwell_seconds=payload.dwell_seconds,
            action=payload.action,
        )
    )
    if photo and payload.action == "play":
        for item in db.scalars(
            select(PlaylistItem)
            .join(Playlist)
            .where(
                Playlist.household_id == actor.household_id,
                PlaylistItem.photo_id == photo.id,
                Playlist.playlist_date == local_now(household(db, actor).timezone).date().isoformat(),
            )
        ):
            item.play_count += 1
    db.commit()
    return {"ok": True}


def job_access(db, job, actor):
    if job.household_id != actor.household_id:
        raise HTTPException(404, "任务不存在")
    if job.target_type == "photo":
        photo_access(db, job.target_id, actor, allow_deleted=True)
    elif job.target_type == "audio":
        from app.api.memories import audio_access

        audio_access(db, job.target_id, actor)
    elif job.target_type == "search":
        query = db.get(SearchQuery, job.target_id)
        if not query or query.user_id != actor.user_id:
            raise HTTPException(404, "任务不存在")
    elif job.target_type == "story":
        scoped(db, Story, job.target_id, actor)
    elif job.target_type == "export" and actor.role != "owner":
        raise HTTPException(403, "需要管理员权限")


@router.get("/jobs")
def jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    state: str | None = None,
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    query = select(Job).where(Job.household_id == actor.household_id)
    if state:
        query = query.where(Job.state == state)
    rows = []
    for job in db.scalars(query.order_by(Job.created_at.desc(), Job.id)):
        try:
            job_access(db, job, actor)
        except HTTPException:
            continue
        rows.append(serialize(job))
    return {"items": rows[(page - 1) * page_size : page * page_size], "total": len(rows), "page": page}


@router.get("/jobs/{jid}")
def get_job(jid: str, actor=Depends(current), db: DB = Depends(get_db)):
    job = scoped(db, Job, jid, actor)
    job_access(db, job, actor)
    return serialize(job)


@router.post("/jobs/{jid}/retry")
def retry_job(jid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    job = scoped(db, Job, jid, actor)
    job_access(db, job, actor)
    if job.state == "running":
        raise HTTPException(409, "任务正在运行")
    job.state, job.attempts, job.next_run_at = "queued", 0, now()
    job.error_code = job.error_message_sanitized = None
    db.commit()
    return serialize(job)


@router.post("/jobs/pause")
def pause(actor=Depends(owner), db: DB = Depends(get_db)):
    h = household(db, actor)
    h.settings_json = {**h.settings_json, "jobs_paused": True}
    db.commit()
    return {"paused": True, "note": "当前任务完成后暂停领取"}


@router.post("/jobs/resume")
def resume(actor=Depends(owner), db: DB = Depends(get_db)):
    h = household(db, actor)
    h.settings_json = {**h.settings_json, "jobs_paused": False}
    db.commit()
    return {"paused": False}


@router.get("/settings")
def get_settings(actor=Depends(current), db: DB = Depends(get_db)):
    h = household(db, actor)
    return {"household": serialize(h), "settings": h.settings_json}


@router.patch("/settings")
def patch_settings(payload: SettingsPatch, actor=Depends(owner), db: DB = Depends(get_db)):
    h = household(db, actor)
    config = {**h.settings_json}
    was_enabled = config["faces_enabled"]
    if payload.delete_biometrics and payload.faces_enabled is True:
        raise HTTPException(422, "删除生物特征时不能同时开启人脸分析")
    changes = payload.model_dump(exclude_none=True, exclude={"face_consent", "delete_biometrics"})
    if changes.get("faces_enabled") and not config["faces_enabled"] and not payload.face_consent:
        raise HTTPException(422, "开启人脸分析须明确同意，包括儿童生物特征处理告知")
    if payload.weights is not None:
        expected = set(defaults()["weights"])
        if (
            set(payload.weights) != expected
            or any(not 0 <= w <= 1 for w in payload.weights.values())
            or sum(payload.weights.values()) <= 0
        ):
            raise HTTPException(422, "推荐权重须完整、在 0–1 内且总和大于 0")
    if payload.scenes is not None and set(payload.scenes) != set(defaults()["scenes"]):
        raise HTTPException(422, "必须包含全部展示类别")
    config.update(changes)
    config["config_version"] += 1
    h.settings_json = config
    if payload.delete_biometrics:
        # Keep explicit manual person tags; erase detector-created biometric associations/features.
        photo_ids = select(Photo.id).where(Photo.household_id == h.id)
        db.execute(delete(Face).where(Face.photo_id.in_(photo_ids), Face.status != "manual_tag"))
        audit(db, actor, "biometrics_erased")
    if payload.faces_enabled is True and not was_enabled:
        for photo in family_candidates_local(db, h.id):
            enqueue_photo(db, photo, rerun=True, steps=["faces"])
    privacy_changed(db, actor)
    db.commit()
    return {"settings": h.settings_json}


def family_candidates_local(db, hid):
    from app.services.photos import family_candidates

    return family_candidates(db, hid)


@router.get("/system/health")
def health(db: DB = Depends(get_db)):
    db.connection().exec_driver_sql("SELECT 1")
    return {"status": "ok", "version": "1.0.0", "inference": "local-only"}


@router.get("/system/models")
def models(actor=Depends(owner)):
    return model_status()


@router.get("/system/stats")
def stats(actor=Depends(current), db: DB = Depends(get_db)):
    values = {}
    for name, cls in [("photos", Photo), ("events", Event), ("people", Person), ("playbacks", PlaybackEvent)]:
        values[name] = db.scalar(select(func.count()).select_from(cls).where(cls.household_id == actor.household_id))
    grouped = dict(
        db.execute(
            select(Job.state, func.count()).where(Job.household_id == actor.household_id).group_by(Job.state)
        ).all()
    )
    values["jobs"] = grouped
    values["task_success_rate"] = grouped.get("succeeded", 0) / max(
        1, grouped.get("succeeded", 0) + grouped.get("failed", 0)
    )
    ai = dict(
        db.execute(
            select(Job.state, func.count())
            .where(
                Job.household_id == actor.household_id,
                Job.job_type.in_(["embedding", "vlm", "faces", "text_embedding", "asr"]),
            )
            .group_by(Job.state)
        ).all()
    )
    assessed = ai.get("succeeded", 0) + ai.get("failed", 0)
    values["ai_success_rate"] = ai.get("succeeded", 0) / assessed if assessed else None
    values["ai_outcomes"] = ai
    values["pending"] = grouped.get("queued", 0) + grouped.get("retry", 0) + grouped.get("running", 0)
    values["mean_job_ms"] = db.scalar(select(func.avg(Job.duration_ms)).where(Job.household_id == actor.household_id))
    return values


@router.post("/system/export")
def export_family(actor=Depends(owner), db: DB = Depends(get_db)):
    job = enqueue(db, actor.household_id, "export", actor.household_id, "data_export", key=f"export:{uid()}")
    audit(db, actor, "data_export_requested")
    db.commit()
    return {"job_id": job.id}


@router.get("/system/export/{jid}")
def get_export(jid: str, actor=Depends(owner), db: DB = Depends(get_db)):
    job = scoped(db, Job, jid, actor)
    if job.job_type != "data_export" or job.state != "succeeded":
        raise HTTPException(409, "导出尚未完成")
    # Export snapshots contain formerly shared data; disallow download after any privacy change.
    h = household(db, actor)
    if job.result_json.get("privacy_revision") != h.privacy_revision:
        raise HTTPException(409, "共享权限已变化，请重新导出")
    return FileResponse(
        storage.read_media(job.result_json["key"]), filename="family-export.zip", media_type="application/zip"
    )
