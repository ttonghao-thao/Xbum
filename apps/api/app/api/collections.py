from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.api.schemas import SearchInput, Strict
from app.core.security import current, household, photo_access, writer
from app.db.collection_features import PhotoText, SmartCollection
from app.db.models import Job, now, serialize
from app.db.session import get_db
from app.services.collections import (
    checked_photo_ids,
    collection_access,
    collection_payload,
    collection_photos,
    collection_playback,
)
from app.services.photos import family_candidates
from app.services.search import search
from app.workers.queue import enqueue
from app.ai.ocr import OCR_VERSION

router = APIRouter()


class CollectionInput(Strict):
    title: str = Field(min_length=1, max_length=300)
    mode: Literal["static", "dynamic"] = "dynamic"
    query: SearchInput = Field(default_factory=SearchInput)
    photo_ids: list[str] = Field(default_factory=list, max_length=3000)
    pinned: bool = False


class CollectionPatch(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    pinned: bool | None = None
    cover_photo_id: str | None = None
    include_ids: list[str] | None = Field(default=None, max_length=3000)
    exclude_ids: list[str] | None = Field(default=None, max_length=3000)


@router.get("/collections")
def collections(actor=Depends(current), db: DB = Depends(get_db)):
    rows = db.scalars(
        select(SmartCollection)
        .where(SmartCollection.household_id == actor.household_id, SmartCollection.created_by == actor.user_id)
        .order_by(SmartCollection.pinned.desc(), SmartCollection.created_at.desc())
    )
    h = household(db, actor)
    values = [collection_payload(db, actor, h, row, page_size=1) for row in rows]
    return {"items": values, "total": len(values)}


@router.post("/collections")
def create_collection(payload: CollectionInput, actor=Depends(writer), db: DB = Depends(get_db)):
    if payload.mode == "dynamic" and payload.query.reference_photo_id:
        raise HTTPException(422, "以图找图结果请保存为固定照片集合")
    query = payload.query.model_dump(mode="json", exclude={"query_id", "reference_photo_id", "page", "page_size"})
    query["semantic"] = False  # Dynamic membership uses reproducible hard conditions, no pending text encoding.
    if payload.mode == "dynamic":
        search(db, actor, household(db, actor), SearchInput.model_validate(query), create_query=False)
    ids = checked_photo_ids(db, actor, payload.photo_ids)
    row = SmartCollection(
        household_id=actor.household_id,
        created_by=actor.user_id,
        title=payload.title,
        mode=payload.mode,
        query_json=query,
        photo_ids_json=ids,
        pinned=payload.pinned,
    )
    if payload.mode == "static" and not ids:
        raise HTTPException(422, "请选择静态相册照片")
    db.add(row)
    db.flush()
    result = collection_payload(db, actor, household(db, actor), row)
    db.commit()
    return result


@router.get("/collections/{cid}")
def collection(
    cid: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    row = collection_access(db, cid, actor)
    return collection_payload(db, actor, household(db, actor), row, page, page_size)


@router.patch("/collections/{cid}")
def edit_collection(cid: str, payload: CollectionPatch, actor=Depends(writer), db: DB = Depends(get_db)):
    row = collection_access(db, cid, actor)
    allowed = {p.id for p in family_candidates(db, actor.household_id)}
    include = (
        list(
            dict.fromkeys(
                [
                    *checked_photo_ids(db, actor, payload.include_ids),
                    *(pid for pid in row.include_ids_json if pid not in allowed),
                ]
            )
        )
        if payload.include_ids is not None
        else row.include_ids_json
    )
    exclude = (
        list(
            dict.fromkeys(
                [
                    *checked_photo_ids(db, actor, payload.exclude_ids),
                    *(pid for pid in row.exclude_ids_json if pid not in allowed),
                ]
            )
        )
        if payload.exclude_ids is not None
        else row.exclude_ids_json
    )
    if set(include) & set(exclude):
        raise HTTPException(422, "同一照片不能同时加入和排除")
    row.include_ids_json, row.exclude_ids_json = include, exclude
    if payload.title is not None:
        row.title = payload.title
    if payload.pinned is not None:
        row.pinned = payload.pinned
    if "cover_photo_id" in payload.model_fields_set:
        ids = {p.id for p in collection_photos(db, actor, household(db, actor), row)}
        if payload.cover_photo_id is not None and payload.cover_photo_id not in ids:
            raise HTTPException(422, "封面必须在当前相册内")
        row.cover_photo_id = payload.cover_photo_id
    row.updated_at = now()
    db.commit()
    return collection_payload(db, actor, household(db, actor), row)


@router.delete("/collections/{cid}")
def delete_collection(cid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    row = collection_access(db, cid, actor)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/collections/{cid}/playback")
def playback(cid: str, actor=Depends(current), db: DB = Depends(get_db)):
    return collection_playback(db, actor, household(db, actor), collection_access(db, cid, actor))


class TextCorrection(Strict):
    text: str = Field(max_length=64000)


@router.get("/photos/{pid}/text")
def text_detail(pid: str, actor=Depends(current), db: DB = Depends(get_db)):
    photo_access(db, pid, actor)
    row = db.scalar(select(PhotoText).where(PhotoText.photo_id == pid))
    jobs = list(
        db.scalars(
            select(Job)
            .where(Job.target_type == "photo", Job.target_id == pid, Job.job_type == "ocr")
            .order_by(Job.created_at.desc())
        )
    )
    return {"text": serialize(row) if row else None, "job": serialize(jobs[0]) if jobs else None}


@router.patch("/photos/{pid}/text")
def correct_text(pid: str, payload: TextCorrection, actor=Depends(writer), db: DB = Depends(get_db)):
    photo_access(db, pid, actor, edit=True)
    row = db.scalar(select(PhotoText).where(PhotoText.photo_id == pid))
    if not row:
        row = PhotoText(photo_id=pid)
        db.add(row)
    row.corrected_text, row.confirmed, row.updated_at = payload.text, True, now()
    db.commit()
    return serialize(row)


@router.post("/photos/{pid}/ocr")
def request_ocr(pid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    photo_access(db, pid, actor, edit=True)
    if pid not in {p.id for p in family_candidates(db, actor.household_id)}:
        raise HTTPException(422, "图片文字识别仅处理当前家庭共享照片")
    if not household(db, actor).settings_json.get("ocr_enabled", True):
        raise HTTPException(409, "本地图片文字识别已关闭")
    # Manual retry uses the same contract and only retries a failed OCR task.
    job = enqueue(db, actor.household_id, "photo", pid, "ocr", key=f"photo:{pid}:ocr:{OCR_VERSION}")
    if job.state in {"failed", "skipped"}:
        job.state, job.attempts, job.next_run_at = "queued", 0, now()
    db.commit()
    return {"job_id": job.id, "state": job.state}
