import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models import (
    AudioMemory,
    Embedding,
    Event,
    Face,
    Household,
    Job,
    Person,
    Photo,
    PhotoAnalysis,
    PhotoDerivative,
    SearchQuery,
    Story,
    now,
    serialize,
)
from app.db.session import SessionLocal
from app.services.calendar import local_now
from app.services.events import cluster_events, event_payload
from app.services.photos import family_candidates, refresh_metadata
from app.services.recommendations import build_daily_playlist
from app.services.storage import storage
from app.services.stories import export_mp4
from app.workers.queue import claim, enqueue, enqueue_coalesced, fail, recover, reconcile_vision

logger = logging.getLogger("xbum.worker")


def isolated_provider(kind, **payload):
    with tempfile.TemporaryDirectory(prefix="xbum-model-") as temp:
        output = Path(temp) / "result.json"
        request = {"kind": kind, "output": str(output), **payload}
        process = subprocess.Popen(
            [sys.executable, "-m", "app.ai.runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={
                **os.environ,
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "OMP_NUM_THREADS": "2",
            },
        )
        try:
            process.communicate(json.dumps(request).encode(), timeout=settings.model_timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return {"ok": False, "code": "timeout"}
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        if process.returncode or not output.is_file():
            return {"ok": False, "code": "resource_error"}
        return json.loads(output.read_text())


def quality(path, width, height):
    import cv2

    with Image.open(path) as image:
        gray = np.asarray(image.convert("L"))
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    mean = float(gray.mean())
    score = (
        0.5 * min(1, sharpness / 180)
        + 0.3 * max(0, 1 - abs(mean - 128) / 128)
        + 0.2 * min(1, width * height / 1_000_000)
    )
    reasons = []
    if sharpness < 35:
        reasons.append("画面可能模糊")
    if mean < 40:
        reasons.append("画面偏暗")
    if mean > 220:
        reasons.append("画面偏亮")
    if width * height < 300_000:
        reasons.append("分辨率较低")
    return round(score, 4), reasons


def apply_faces(db, photo, result, config):
    # Human-corrected assignments and explicit manual tags survive all re-analysis.
    locked = list(db.scalars(select(Face).where(Face.photo_id == photo.id, Face.locked.is_(True))))
    previous = list(db.scalars(select(Face).where(Face.photo_id == photo.id, Face.locked.is_(False))))
    previous_matches = [(f.bounding_box_json, f.person_id) for f in previous]
    db.execute(delete(Face).where(Face.photo_id == photo.id, Face.locked.is_(False)))
    household_photo_ids = select(Photo.id).where(
        Photo.household_id == photo.household_id,
        Photo.visibility == "family",
        Photo.is_hidden.is_(False),
        Photo.is_deleted.is_(False),
    )
    references = list(
        db.scalars(select(Face).where(Face.photo_id.in_(household_photo_ids), Face.embedding_blob.is_not(None)))
    )
    threshold = config["face_similarity"]
    for detected in result["faces"]:
        bbox = detected["bbox"]
        # Avoid recreating a detector box already explicitly corrected by a user.
        if any(
            f.bounding_box_json
            and abs(f.bounding_box_json[0] - bbox[0]) < 12
            and abs(f.bounding_box_json[1] - bbox[1]) < 12
            for f in locked
        ):
            continue
        vector = np.asarray(detected["vector"], dtype=np.float32)
        person_id = next(
            (
                pid
                for prior_box, pid in previous_matches
                if prior_box and abs(prior_box[0] - bbox[0]) < 12 and abs(prior_box[1] - bbox[1]) < 12
            ),
            None,
        )
        best = threshold
        for reference in references:
            if reference.photo_id == photo.id:
                continue
            ref = np.frombuffer(reference.embedding_blob, dtype=np.float32)
            sim = float(vector @ ref) if len(ref) == len(vector) else -1
            if sim > best:
                person_id, best = reference.person_id, sim
        if not person_id:
            person = Person(household_id=photo.household_id, consent_flag=True)
            db.add(person)
            db.flush()
            person_id = person.id
        face = Face(
            photo_id=photo.id,
            person_id=person_id,
            cluster_id=person_id,
            bounding_box_json=bbox,
            embedding_blob=vector.tobytes(),
            quality=detected["quality"],
        )
        db.add(face)


def export_data(db, h, job):
    photos = family_candidates(db, h.id)
    key = f"{h.id}/exports/{job.id}.zip"
    destination = storage.read_media(key)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    events = [
        event_payload(db, e, {p.id for p in photos})
        for e in db.scalars(select(Event).where(Event.household_id == h.id))
    ]
    metadata = {
        "format": "xbum-authorized-export-v1",
        "household": serialize(h),
        "photos": [serialize(p) for p in photos],
        "events": [e for e in events if e["photo_ids"]],
    }
    with zipfile.ZipFile(destination.with_suffix(".tmp"), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        for photo in photos:
            archive.write(storage.read_media(photo.storage_key), f"photos/{photo.id}/original")
    os.replace(destination.with_suffix(".tmp"), destination)
    return {"key": key, "privacy_revision": h.privacy_revision, "photo_count": len(photos)}


def run_job(db, job, provider=isolated_provider):
    started = time.monotonic()
    job_id = job.id
    token = job.lease_token
    h = db.get(Household, job.household_id)
    config = h.settings_json
    kind = job.job_type
    result = {"ok": True}
    target = None
    if job.target_type == "photo":
        target = db.get(Photo, job.target_id)
        if not target or target.is_deleted:
            job.state = "cancelled"
            db.commit()
            return
        # Biometric consent applies only to explicitly family-shared photos.
        enabled = config.get(
            {"embedding": "embedding_enabled", "vlm": "vlm_enabled", "faces": "faces_enabled", "ocr": "ocr_enabled"}.get(kind, ""), True
        )
        if not enabled or (kind in {"faces", "ocr"} and (target.visibility != "family" or target.is_hidden)):
            job.state, job.duration_ms = "skipped", 0
            db.commit()
            return
        derivative = db.scalar(
            select(PhotoDerivative).where(PhotoDerivative.photo_id == target.id, PhotoDerivative.variant == "960")
        )
        path = storage.read_media(derivative.storage_key)
        if kind == "ocr":
            path = storage.read_media(target.storage_key)
        if kind in {"embedding", "vlm", "faces", "ocr"}:
            db.commit()  # No database write lock while a model runs.
            result = provider(kind, path=str(path.resolve()))
        elif kind == "metadata":
            refresh_metadata(db, target, h.timezone)
            job.model_version = "pillow-local/metadata-1"
        elif kind == "quality":
            result["quality"], result["reasons"] = quality(path, target.width, target.height)
        elif kind == "postprocess":
            enqueue_coalesced(db, h.id, "events")
            enqueue_coalesced(db, h.id, "recommendation")
    elif kind == "text_embedding":
        target = db.get(SearchQuery, job.target_id)
        if target and config["embedding_enabled"]:
            db.commit()
            result = provider(kind, text=target.text)
        else:
            job.state = "skipped"
            db.commit()
            return
    elif kind == "asr":
        target = db.get(AudioMemory, job.target_id)
        if not target or not config["speech_enabled"]:
            job.state = "skipped"
            db.commit()
            return
        db.commit()
        result = provider(kind, path=str(storage.read_media(target.audio_key).resolve()))
    elif kind == "events":
        result["event_count"] = cluster_events(db, h)
    elif kind == "recommendation":
        result["playlist_id"] = build_daily_playlist(db, h).id
    elif kind == "story_compose":
        from app.services.stories import compose_story

        target = db.get(Story, job.target_id)
        if not target:
            job.state = "cancelled"
            db.commit()
            return
        if not compose_story(db, target):
            job.state = "skipped"
            db.commit()
            return
    elif kind == "export":
        target = db.get(Story, job.target_id)
        if not target:
            job.state = "cancelled"
            db.commit()
            return
        version = target.version
        key = export_mp4(db, target)
        db.expire_all()
        target = db.get(Story, job.target_id)
        if not target or target.version != version:
            storage.delete_media(key)
            raise ValueError("story revision changed")
        target.mp4_key, target.export_version, target.status = key, version, "ready"
        result["version"] = version
    elif kind == "data_export":
        result.update(export_data(db, h, job))
    # Revalidate leases, deletion, and consent after every slow operation.
    db.flush()
    db.expire_all()
    job = db.get(Job, job_id)
    if not job or job.lease_token != token:
        return
    h = db.get(Household, job.household_id)
    if not result.get("ok"):
        code = result.get("code", "resource_error")
        fail(job, code, retryable=code != "unsupported_media")
    else:
        if job.target_type == "photo":
            target = db.get(Photo, job.target_id)
            if not target or target.is_deleted:
                job.state = "cancelled"
                db.commit()
                return
            analysis = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == target.id))
            if kind == "quality":
                analysis.quality_score, analysis.quality_reasons_json = result["quality"], result["reasons"]
            elif kind == "embedding" and h.settings_json["embedding_enabled"]:
                vector = np.asarray(result["vector"], dtype=np.float32)
                vector /= np.linalg.norm(vector)
                db.execute(
                    delete(Embedding).where(
                        Embedding.photo_id == target.id, Embedding.model_version == result["version"]
                    )
                )
                db.add(
                    Embedding(
                        photo_id=target.id,
                        model_id=result["model"],
                        model_version=result["version"],
                        dimension=len(vector),
                        data_blob=vector.tobytes(),
                    )
                )
                job.model_version = result["version"]
            elif kind == "vlm" and h.settings_json["vlm_enabled"]:
                from app.api.schemas import AnalysisOutput, VISION_CONTRACT_VERSION

                output = AnalysisOutput.model_validate(result["analysis"])
                analysis.analysis_json = output.model_dump(mode="json")
                analysis.tags_json, analysis.caption_ai = output.scene_tags + output.objects, output.caption
                analysis.confidence_json = {"confidence": output.confidence, "uncertainties": output.uncertainties}
                analysis.model_name, analysis.model_version = settings.vlm_model, result["version"]
                analysis.status, job.model_version = "succeeded", result["version"]
                analysis.pipeline_version = VISION_CONTRACT_VERSION
            elif kind == "ocr":
                from app.db.collection_features import PhotoText

                if not h.settings_json.get("ocr_enabled", True) or not target.visibility == "family" or target.is_hidden:
                    job.state = "skipped"
                    db.commit()
                    return
                text_record = db.scalar(select(PhotoText).where(PhotoText.photo_id == target.id))
                if not text_record:
                    text_record = PhotoText(photo_id=target.id)
                    db.add(text_record)
                text_record.text = result["text"]
                text_record.regions_json = result["regions"]
                text_record.model_version = result["version"]
                text_record.updated_at = now()
                job.model_version = result["version"]
            elif (
                kind == "faces"
                and h.settings_json["faces_enabled"]
                and target.visibility == "family"
                and not target.is_hidden
            ):
                apply_faces(db, target, result, h.settings_json)
                job.model_version = "yunet-2023mar/sface-2021dec"
            analysis.updated_at = now()
            if kind in {"quality", "embedding", "vlm", "faces"}:
                enqueue_coalesced(db, h.id, "events")
                enqueue_coalesced(db, h.id, "recommendation")
        elif kind == "text_embedding":
            target = db.get(SearchQuery, job.target_id)
            if target and h.settings_json["embedding_enabled"]:
                vector = np.asarray(result["vector"], dtype=np.float32)
                target.data_blob, target.dimension = vector.tobytes(), len(vector)
                target.model_id, target.model_version = result["model"], result["version"]
                job.model_version = result["version"]
        elif kind == "asr":
            target = db.get(AudioMemory, job.target_id)
            if target and not target.transcript_confirmed and h.settings_json["speech_enabled"]:
                target.transcript = result["transcript"]
                job.model_version = result["version"]
        job.state, job.error_code, job.error_message_sanitized = "succeeded", None, None
        job.result_json = {
            k: v
            for k, v in result.items()
            if k in {"key", "privacy_revision", "photo_count", "playlist_id", "version", "event_count"}
        }
        job.lease_until, job.lease_token = None, None
    job.duration_ms = int((time.monotonic() - started) * 1000)
    job.updated_at = now()
    db.commit()
    logger.info(
        json.dumps(
            {
                "event": "job_finished",
                "job_id": job.id,
                "type": kind,
                "state": job.state,
                "duration_ms": job.duration_ms,
            }
        )
    )


def schedule(db):
    for h in db.scalars(select(Household)):
        reconcile_vision(db, h)
        from app.ai.ocr import reconcile_ocr

        reconcile_ocr(db, h)
        local = local_now(h.timezone)
        if local.hour >= h.settings_json["daily_generation_hour"]:
            enqueue(
                db,
                h.id,
                "household",
                h.id,
                "recommendation",
                key=f"daily:{h.id}:{local.date().isoformat()}",
                priority=-10,
            )
    # Short-lived semantic queries/voice-search recordings are not permanent family memories.
    cutoff = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    for query in db.scalars(select(SearchQuery).where(SearchQuery.created_at < cutoff)):
        db.execute(delete(Job).where(Job.target_type == "search", Job.target_id == query.id))
        db.delete(query)
    for audio in db.scalars(
        select(AudioMemory).where(AudioMemory.query_only.is_(True), AudioMemory.created_at < cutoff)
    ):
        storage.delete_media(audio.audio_key)
        db.execute(delete(Job).where(Job.target_type == "audio", Job.target_id == audio.id))
        db.delete(audio)
    db.commit()


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    service_lock = open(settings.data_dir / "service.lock", "a+")
    fcntl.flock(service_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    lock = open(settings.data_dir / "worker.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("已有 Worker 正在运行；16GB 配置仅允许一个 Worker") from None
    with SessionLocal() as db:
        recover(db)
    last_schedule = 0
    while True:
        with open(settings.data_dir / "maintenance.lock", "a+") as maintenance:
            fcntl.flock(maintenance, fcntl.LOCK_SH)
            with SessionLocal() as db:
                if time.monotonic() - last_schedule > 60:
                    schedule(db)
                    last_schedule = time.monotonic()
                job = claim(db)
                if job:
                    job_id = job.id
                    try:
                        run_job(db, job)
                    except Exception:
                        db.rollback()
                        job = db.get(Job, job_id)
                        if job:
                            fail(job, "resource_error")
                            db.commit()
                        logger.error(json.dumps({"event": "job_failed", "job_id": job_id, "code": "resource_error"}))
        time.sleep(0.1 if job else 1)


if __name__ == "__main__":
    main()
