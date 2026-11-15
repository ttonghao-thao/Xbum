from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import settings
from app.db.models import Household, Job, now, uid
from app.api.schemas import VISION_CONTRACT_VERSION


def enqueue(db, household_id, target_type, target_id, job_type, key=None, priority=0):
    key = key or f"{target_type}:{target_id}:{job_type}:1"
    existing = db.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        return existing
    job = Job(
        household_id=household_id,
        target_type=target_type,
        target_id=target_id,
        job_type=job_type,
        idempotency_key=key,
        priority=priority,
    )
    db.add(job)
    db.flush()
    return job


def enqueue_photo(db, photo, rerun=False, steps=None):
    revision = uid() if rerun else None
    return [
        enqueue(
            db,
            photo.household_id,
            "photo",
            photo.id,
            step,
            key=f"photo:{photo.id}:{step}:{revision or ('vision-' + VISION_CONTRACT_VERSION if step == 'vlm' else '1')}",
            priority=10 if step in {"metadata", "quality"} else 0,
        )
        for step in (steps or ["metadata", "quality", "embedding", "vlm", "faces", "postprocess"])
    ]


def reconcile_vision(db, household):
    """Upgrade old/missing results once per contract, without resetting retries.

    Executed by the single Worker on startup and periodic reconciliation; normal
    uploads use the same idempotency key, so the two paths never duplicate work.
    """
    from app.db.models import Photo, PhotoAnalysis
    from app.services.photos import current_vision

    if household.settings_json["jobs_paused"] or not household.settings_json["vlm_enabled"]:
        return 0
    photos = list(
        db.scalars(
            select(Photo).where(
                Photo.household_id == household.id,
                Photo.is_deleted.is_(False),
                Photo.is_hidden.is_(False),
            )
        )
    )
    ids = [p.id for p in photos]
    analyses = {a.photo_id: a for a in db.scalars(select(PhotoAnalysis).where(PhotoAnalysis.photo_id.in_(ids)))}
    active = set(
        db.scalars(
            select(Job.target_id).where(
                Job.household_id == household.id,
                Job.target_type == "photo",
                Job.job_type == "vlm",
                Job.state.in_(["queued", "retry", "running"]),
            )
        )
    )
    count = 0
    for photo in photos:
        analysis = analyses.get(photo.id)
        if current_vision(analysis) or photo.id in active:
            continue
        key = f"photo:{photo.id}:vlm:vision-{VISION_CONTRACT_VERSION}"
        if db.scalar(select(Job.id).where(Job.idempotency_key == key)):
            continue  # A terminal failure is visible; never retry forever on each tick.
        if not analysis:
            db.add(PhotoAnalysis(photo_id=photo.id))
        enqueue(db, household.id, "photo", photo.id, "vlm", key=key)
        count += 1
    return count


def enqueue_coalesced(db, household_id, kind):
    existing = db.scalar(
        select(Job).where(
            Job.household_id == household_id,
            Job.job_type == kind,
            Job.target_type == "household",
            Job.state.in_(["queued", "retry"]),
        )
    )
    if existing:
        return existing
    job = enqueue(
        db,
        household_id,
        "household",
        household_id,
        kind,
        key=f"refresh:{household_id}:{kind}:{uid()}",
        priority=2 if kind == "events" else 1,
    )
    job.next_run_at = (datetime.now(UTC) + timedelta(seconds=15)).isoformat()
    return job


def recover(db):
    # Must only be invoked by the holder of the global worker lock.
    db.execute(
        update(Job)
        .where(Job.state == "running")
        .values(
            state="queued",
            lease_until=None,
            lease_token=None,
            error_code="worker_restarted",
            error_message_sanitized="Worker 重启，任务已安全恢复",
        )
    )
    db.commit()


def claim(db):
    stamp = now()
    db.execute(
        update(Job)
        .where(Job.state == "running", Job.lease_until < stamp)
        .values(state="queued", lease_token=None, lease_until=None)
    )
    jobs = db.scalars(
        select(Job)
        .join(Household, Household.id == Job.household_id)
        .where(Job.state.in_(["queued", "retry"]), Job.next_run_at <= stamp)
        .where(Household.settings_json["jobs_paused"].as_boolean().is_(False))
        .order_by(Job.priority.desc(), Job.created_at, Job.id)
        .limit(100)
    )
    for job in jobs:
        h = db.get(Household, job.household_id)
        if h.settings_json.get("jobs_paused"):
            continue
        token = uid()
        expiry = (datetime.now(UTC) + timedelta(seconds=settings.model_timeout + 120)).isoformat()
        result = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.state.in_(["queued", "retry"]))
            .values(state="running", lease_token=token, lease_until=expiry, attempts=Job.attempts + 1, updated_at=stamp)
        )
        db.commit()
        if result.rowcount:
            db.expire_all()
            return db.get(Job, job.id)
    db.commit()
    return None


def fail(job, code, retryable=True):
    messages = {
        "model_missing": "本地模型尚未安装，请运行 scripts/download_models.sh 后重试",
        "model_unavailable": "本地模型服务不可用，请检查本机服务后重试",
        "invalid_model_json": "模型输出不符合结构化 JSON 契约，可重试",
        "timeout": "本地任务超时，可重试或降低模型负载",
        "unsupported_media": "媒体文件无法处理，请检查格式",
        "resource_error": "本地资源不足或任务失败，请检查磁盘及模型状态",
    }
    job.error_code = code
    job.error_message_sanitized = messages.get(code, "任务失败，请查看模型与服务状态")
    if code == "model_missing" and job.job_type == "asr":
        job.error_message_sanitized = "尚未安装本地语音模型，请管理员运行 scripts/download_models.sh whisper 后重试"
    job.state = "retry" if retryable and job.attempts < job.max_attempts else "failed"
    job.next_run_at = (datetime.now(UTC) + timedelta(seconds=min(300, 5 * 2**job.attempts))).isoformat()
    job.lease_token = None
    job.lease_until = None
    job.updated_at = now()
