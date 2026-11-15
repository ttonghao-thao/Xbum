import json
import io
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DB
from PIL import Image

from app.api.schemas import (
    EventPatch,
    GenerateStory,
    MergeEvents,
    MergePeople,
    MilestoneInput,
    PersonPatch,
    SearchInput,
    SplitEvent,
    SplitPeople,
    StoryPatch,
    TranscriptPatch,
    page_items,
)
from app.core.security import audit, current, eligible, household, media_actor, photo_access, scoped, writer
from app.db.models import (
    AudioMemory,
    Event,
    EventPhoto,
    Face,
    Job,
    Milestone,
    Person,
    Photo,
    PhotoDerivative,
    Story,
    StorySlide,
    serialize,
    uid,
)
from app.db.session import get_db
from app.services.photos import family_candidates, photo_payload
from app.services.albums import ALBUMS, MemoryAlbums
from app.services.events import event_payload
from app.services.search import search
from app.services.storage import storage
from app.services.stories import create_draft, story_access, story_payload
from app.workers.queue import enqueue, enqueue_coalesced

router = APIRouter()


def private_photo_count(db, actor):
    return db.scalar(
        select(func.count())
        .select_from(Photo)
        .where(
            Photo.household_id == actor.household_id,
            Photo.uploader_id == actor.user_id,
            Photo.visibility == "private",
            Photo.is_hidden.is_(False),
            Photo.is_deleted.is_(False),
        )
    )


@router.get("/albums")
def albums(scope: Literal["visible", "family"] = "visible", actor=Depends(current), db: DB = Depends(get_db)):
    index = MemoryAlbums(db, household(db, actor), actor=actor if scope == "visible" else None)
    return {
        "scope": scope,
        "private_photo_count": private_photo_count(db, actor),
        "items": [
            {
                **index.metadata(kind),
                "cover": photo_payload(db, index.rows[kind][0]["photo"]) if index.rows[kind] else None,
            }
            for kind in ALBUMS
        ],
    }


@router.get("/albums/{kind}")
def album(
    kind: Literal["on_this_day", "growth", "travel"],
    day: date | None = None,
    scope: Literal["visible", "family"] = "visible",
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    index = MemoryAlbums(db, household(db, actor), day, actor=actor if scope == "visible" else None)
    rows = index.rows[kind]
    return {
        **index.metadata(kind),
        "scope": scope,
        "private_photo_count": private_photo_count(db, actor),
        "items": [
            {**photo_payload(db, row["photo"]), **{k: v for k, v in row.items() if k != "photo"}}
            for row in rows[(page - 1) * page_size : page * page_size]
        ],
        "page": page,
        "page_size": page_size,
    }


@router.get("/faces/{fid}/media")
def face_thumbnail(fid: str, actor=Depends(current), db: DB = Depends(get_db)):
    face = db.get(Face, fid)
    if not face:
        raise HTTPException(404, "人脸候选不存在")
    photo_access(db, face.photo_id, actor)
    source = db.scalar(
        select(PhotoDerivative).where(PhotoDerivative.photo_id == face.photo_id, PhotoDerivative.variant == "960")
    )
    if not source:
        raise HTTPException(404, "衍生图尚未生成")
    with Image.open(storage.read_media(source.storage_key)) as original:
        image = original.convert("RGB")
        if len(face.bounding_box_json) == 4:
            x, y, width, height = face.bounding_box_json
            left, top = max(0, int(x)), max(0, int(y))
            right, bottom = min(image.width, int(x + width)), min(image.height, int(y + height))
            if right <= left or bottom <= top:
                raise HTTPException(404, "人脸区域无效")
            image = image.crop((left, top, right, bottom))
        image.thumbnail((160, 160))
        output = io.BytesIO()
        image.save(output, "JPEG", quality=86)
    return Response(output.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store, private"})


def set_event_photos(db, event, ids, actor):
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "照片不能重复")
    for pid in ids:
        photo = photo_access(db, pid, actor)
        if not eligible(photo):
            raise HTTPException(422, "事件仅包含未隐藏的家庭共享照片")
    db.execute(delete(EventPhoto).where(EventPhoto.event_id == event.id))
    for position, pid in enumerate(ids):
        db.add(EventPhoto(event_id=event.id, photo_id=pid, position=position, source="user"))
    photos = [db.get(Photo, pid) for pid in ids]
    dates = sorted(p.taken_at for p in photos if p.taken_at)
    event.start_at, event.end_at = (dates[0], dates[-1]) if dates else (None, None)
    event.cover_photo_id = event.cover_photo_id if event.cover_photo_id in ids else ids[0] if ids else None


@router.get("/events")
def events(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    allowed = {p.id for p in family_candidates(db, actor.household_id)}
    eligible_ids = list(db.scalars(select(EventPhoto.event_id).where(EventPhoto.photo_id.in_(allowed)).distinct()))
    rows = db.scalars(
        select(Event)
        .where(Event.household_id == actor.household_id, Event.id.in_(eligible_ids))
        .order_by(Event.start_at.desc(), Event.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [event_payload(db, e, allowed) for e in rows],
        "total": len(eligible_ids),
        "page": page,
        "page_size": page_size,
    }


@router.post("/events/recluster")
def recluster(actor=Depends(writer), db: DB = Depends(get_db)):
    job = enqueue(
        db, actor.household_id, "household", actor.household_id, "events", key=f"events:{actor.household_id}:{uid()}"
    )
    db.commit()
    return {"job_id": job.id}


@router.get("/events/{eid}")
def event_detail(eid: str, actor=Depends(current), db: DB = Depends(get_db)):
    event = scoped(db, Event, eid, actor)
    result = event_payload(db, event)
    if not result["photo_ids"]:
        raise HTTPException(404, "事件没有当前可访问的共享照片")
    result["photos"] = [photo_payload(db, db.get(Photo, pid)) for pid in result["photo_ids"]]
    result["memories"] = [
        serialize(m)
        for m in db.scalars(select(AudioMemory).where(AudioMemory.event_id == eid, AudioMemory.query_only.is_(False)))
    ]
    return result


@router.patch("/events/{eid}")
def edit_event(eid: str, payload: EventPatch, actor=Depends(writer), db: DB = Depends(get_db)):
    event = scoped(db, Event, eid, actor)
    fields = payload.model_dump(exclude_none=True, mode="json")
    ids = fields.pop("photo_ids", None)
    if ids is not None:
        set_event_photos(db, event, ids, actor)
    if "participants_json" in fields:
        for pid in fields["participants_json"]:
            scoped(db, Person, pid, actor)
    if fields.get("cover_photo_id"):
        photo_access(db, fields["cover_photo_id"], actor)
        if fields["cover_photo_id"] not in (ids if ids is not None else event_payload(db, event)["photo_ids"]):
            raise HTTPException(422, "封面必须属于该事件")
    for key, value in fields.items():
        setattr(event, key, value)
    if "location_label" in fields:
        event.location_source = "user"
    event.locked, event.revision = True, event.revision + 1
    audit(db, actor, "event_corrected", "events", eid, {"revision": event.revision})
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return event_payload(db, event)


@router.post("/events/merge")
def merge_events(payload: MergeEvents, actor=Depends(writer), db: DB = Depends(get_db)):
    events = [scoped(db, Event, eid, actor) for eid in dict.fromkeys(payload.event_ids)]
    if len(events) < 2:
        raise HTTPException(422, "请选择不同事件")
    target = events[0]
    ids = list(dict.fromkeys(pid for event in events for pid in event_payload(db, event)["photo_ids"]))
    ids.sort(key=lambda pid: (db.get(Photo, pid).taken_at or "9999", pid))
    set_event_photos(db, target, ids, actor)
    target.title, target.locked, target.confirmed = payload.title, True, True
    target.revision += 1
    for event in events[1:]:
        # Preserve recorded stories/audio by moving their event association.
        for story in db.scalars(select(Story).where(Story.event_id == event.id)):
            story.event_id = target.id
        for audio in db.scalars(select(AudioMemory).where(AudioMemory.event_id == event.id)):
            audio.event_id = target.id
        db.delete(event)
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return event_payload(db, target)


@router.post("/events/{eid}/split")
def split_event(eid: str, payload: SplitEvent, actor=Depends(writer), db: DB = Depends(get_db)):
    event = scoped(db, Event, eid, actor)
    prior = event_payload(db, event)["photo_ids"]
    chosen = set(payload.photo_ids)
    if not chosen < set(prior):
        raise HTTPException(422, "拆分须选择事件内部分照片，并保留至少一张")
    new = Event(
        household_id=actor.household_id, title=payload.title, locked=True, confirmed=True, event_type=event.event_type
    )
    db.add(new)
    db.flush()
    set_event_photos(db, new, [pid for pid in prior if pid in chosen], actor)
    set_event_photos(db, event, [pid for pid in prior if pid not in chosen], actor)
    event.locked, event.revision = True, event.revision + 1
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return event_payload(db, new)


@router.get("/people")
def people(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    allowed = {p.id for p in family_candidates(db, actor.household_id)}
    items = []
    for person in db.scalars(
        select(Person).where(Person.household_id == actor.household_id).order_by(Person.name, Person.id)
    ):
        faces = [f for f in db.scalars(select(Face).where(Face.person_id == person.id)) if f.photo_id in allowed]
        if not faces and not person.name and not person.manually_confirmed:
            continue
        items.append(
            {
                **serialize(person),
                "faces": [serialize(f) for f in faces],
                "photo_ids": list(dict.fromkeys(f.photo_id for f in faces)),
            }
        )
    return page_items(items, page, page_size)


@router.post("/people")
def create_person(payload: PersonPatch, actor=Depends(writer), db: DB = Depends(get_db)):
    person = Person(household_id=actor.household_id, **payload.model_dump(exclude_none=True, mode="json"))
    apply_growth_choice(person, payload)
    if person.growth_enabled and not (person.is_family and person.manually_confirmed):
        raise HTTPException(422, "成长线需要人工确认家庭人物")
    db.add(person)
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return serialize(person)


@router.patch("/people/{pid}")
def edit_person(pid: str, payload: PersonPatch, actor=Depends(writer), db: DB = Depends(get_db)):
    person = scoped(db, Person, pid, actor)
    for key, value in payload.model_dump(exclude_unset=True, mode="json").items():
        setattr(person, key, value)
    apply_growth_choice(person, payload)
    if person.growth_enabled and not (person.is_family and person.manually_confirmed):
        raise HTTPException(422, "成长线需要人工确认家庭人物")
    for face in db.scalars(select(Face).where(Face.person_id == pid)):
        face.locked, face.status = True, "confirmed" if person.manually_confirmed else "candidate"
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return serialize(person)


def apply_growth_choice(person, payload):
    # Keep the original boolean API working while recording an explicit opt-out.
    if "growth_mode" in payload.model_fields_set and payload.growth_mode is None:
        raise HTTPException(422, "请选择 auto/include/exclude，恢复自动整理请使用 auto")
    mode = payload.growth_mode
    if mode is None and payload.growth_enabled is not None:
        mode = "include" if payload.growth_enabled else "exclude"
    if mode is not None:
        if payload.growth_enabled is not None and payload.growth_enabled != (mode == "include"):
            raise HTTPException(422, "成长设置冲突")
        person.growth_mode, person.growth_enabled = mode, mode == "include"


@router.post("/people/merge")
def merge_people(payload: MergePeople, actor=Depends(writer), db: DB = Depends(get_db)):
    people = [scoped(db, Person, pid, actor) for pid in dict.fromkeys(payload.person_ids)]
    if len(people) < 2:
        raise HTTPException(422, "请选择不同人物")
    target = people[0]
    for person in people[1:]:
        for face in db.scalars(select(Face).where(Face.person_id == person.id)):
            face.person_id, face.cluster_id, face.locked = target.id, target.id, True
        for milestone in db.scalars(select(Milestone).where(Milestone.person_id == person.id)):
            milestone.person_id = target.id
        for event in db.scalars(select(Event).where(Event.household_id == actor.household_id)):
            if person.id in event.participants_json:
                event.participants_json = list(
                    dict.fromkeys(target.id if p == person.id else p for p in event.participants_json)
                )
        db.delete(person)
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return serialize(target)


@router.post("/people/split")
def split_people(payload: SplitPeople, actor=Depends(writer), db: DB = Depends(get_db)):
    person = scoped(db, Person, payload.person_id, actor)
    selected = list(db.scalars(select(Face).where(Face.person_id == person.id, Face.id.in_(payload.face_ids))))
    if len(selected) != len(set(payload.face_ids)):
        raise HTTPException(422, "选定的人脸不属于该人物")
    new = Person(household_id=actor.household_id)
    db.add(new)
    db.flush()
    for face in selected:
        face.person_id, face.cluster_id, face.locked = new.id, new.id, True
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return serialize(new)


@router.post("/people/{pid}/photos/{photo_id}")
def tag_person(pid: str, photo_id: str, actor=Depends(writer), db: DB = Depends(get_db)):
    scoped(db, Person, pid, actor)
    photo_access(db, photo_id, actor)
    existing = db.scalar(select(Face).where(Face.photo_id == photo_id, Face.person_id == pid))
    if not existing:
        db.add(
            Face(
                photo_id=photo_id,
                person_id=pid,
                cluster_id=pid,
                bounding_box_json=[],
                quality=1.0,
                status="manual_tag",
                locked=True,
            )
        )
    enqueue_coalesced(db, actor.household_id, "recommendation")
    db.commit()
    return {"ok": True}


@router.get("/timeline")
def timeline(
    person_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    photos = family_candidates(db, actor.household_id)
    milestones = []
    if person_id:
        person = scoped(db, Person, person_id, actor)
        if not (person.manually_confirmed and person.is_family and person.growth_enabled):
            raise HTTPException(422, "请先启用已确认家庭人物的成长线")
        ids = set(db.scalars(select(Face.photo_id).where(Face.person_id == person_id)))
        photos = [p for p in photos if p.id in ids]
        milestones = [
            serialize(m)
            for m in db.scalars(select(Milestone).where(Milestone.person_id == person_id).order_by(Milestone.date))
        ]
    photos.sort(key=lambda p: (p.taken_at or "9999", p.id))
    result = page_items([photo_payload(db, p) for p in photos], page, page_size)
    result["milestones"] = milestones
    return result


@router.post("/milestones")
def milestone(payload: MilestoneInput, actor=Depends(writer), db: DB = Depends(get_db)):
    person = scoped(db, Person, payload.person_id, actor)
    if not person.manually_confirmed:
        raise HTTPException(422, "请先确认人物")
    item = Milestone(household_id=actor.household_id, **payload.model_dump(mode="json"))
    db.add(item)
    db.commit()
    return serialize(item)


@router.patch("/milestones/{mid}")
def edit_milestone(mid: str, payload: MilestoneInput, actor=Depends(writer), db: DB = Depends(get_db)):
    item = scoped(db, Milestone, mid, actor)
    scoped(db, Person, payload.person_id, actor)
    for key, value in payload.model_dump(mode="json").items():
        setattr(item, key, value)
    db.commit()
    return serialize(item)


@router.delete("/milestones/{mid}")
def remove_milestone(mid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    db.delete(scoped(db, Milestone, mid, actor))
    db.commit()
    return {"ok": True}


@router.post("/stories/generate")
def generate_story(payload: GenerateStory, actor=Depends(writer), db: DB = Depends(get_db)):
    # Draft assembly is deterministic database work. Expensive media/model work is queued.
    story = create_draft(db, actor, payload.event_id, payload.person_id)
    db.commit()
    return story_payload(db, story)


@router.get("/stories")
def stories(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    items = []
    for story in db.scalars(
        select(Story).where(Story.household_id == actor.household_id).order_by(Story.created_at.desc())
    ):
        try:
            story_access(db, story.id, actor)
        except HTTPException:
            continue
        items.append(story_payload(db, story))
    return page_items(items, page, page_size)


@router.get("/stories/{sid}")
def get_story(sid: str, actor=Depends(media_actor), db: DB = Depends(get_db)):
    story, slides = story_access(db, sid, actor)
    return story_payload(db, story, slides)


@router.patch("/stories/{sid}")
def edit_story(sid: str, payload: StoryPatch, actor=Depends(writer), db: DB = Depends(get_db)):
    story, _ = story_access(db, sid, actor, edit=True)
    fields = payload.model_dump(exclude_none=True)
    slides = fields.pop("slides", None)
    if slides is not None:
        for slide in slides:
            photo = photo_access(db, slide["photo_id"], actor)
            if not eligible(photo):
                raise HTTPException(422, "故事只可包含未隐藏的家庭共享照片")
        db.execute(delete(StorySlide).where(StorySlide.story_id == sid))
        for index, slide in enumerate(slides):
            db.add(StorySlide(story_id=sid, position=index, **slide))
    if fields.get("cover_photo_id"):
        photo = photo_access(db, fields["cover_photo_id"], actor)
        selected_ids = (
            [s["photo_id"] for s in slides]
            if slides is not None
            else list(db.scalars(select(StorySlide.photo_id).where(StorySlide.story_id == sid)))
        )
        if not eligible(photo) or photo.id not in selected_ids:
            raise HTTPException(422, "故事封面须为故事内的家庭共享照片")
    for key, value in fields.items():
        setattr(story, key, value)
    if story.mp4_key:
        storage.delete_media(story.mp4_key)
    story.version, story.status, story.mp4_key = story.version + 1, "draft", None
    db.commit()
    return story_payload(db, story)


@router.get("/stories/{sid}/json")
def story_json(sid: str, actor=Depends(current), db: DB = Depends(get_db)):
    story, slides = story_access(db, sid, actor)
    return Response(
        json.dumps(story_payload(db, story, slides), ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="story-{sid}.json"'},
    )


@router.post("/stories/{sid}/export")
def export_story(sid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    story, _ = story_access(db, sid, actor, edit=True)
    job = enqueue(db, actor.household_id, "story", sid, "export", key=f"story:{sid}:export:{story.version}")
    story.status = "export_queued"
    db.commit()
    return {"job_id": job.id, "status": job.state}


@router.get("/stories/{sid}/export")
def download_story(sid: str, actor=Depends(current), db: DB = Depends(get_db)):
    story, _ = story_access(db, sid, actor)
    if not story.mp4_key or story.export_version != story.version:
        raise HTTPException(409, "视频尚未生成，请在任务页查看进度或重试")
    return FileResponse(
        storage.read_media(story.mp4_key),
        media_type="video/mp4",
        filename=f"story-{sid}.mp4",
        headers={"Cache-Control": "no-store, private"},
    )


@router.delete("/stories/{sid}")
def delete_story(sid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    story = scoped(db, Story, sid, actor)
    if actor.role != "owner" and story.created_by != actor.user_id:
        raise HTTPException(403, "只能删除自己的故事")
    if story.mp4_key:
        storage.delete_media(story.mp4_key)
    db.execute(delete(Job).where(Job.target_type == "story", Job.target_id == sid))
    db.delete(story)
    db.commit()
    return {"ok": True}


@router.post("/search")
def search_photos(payload: SearchInput, actor=Depends(current), db: DB = Depends(get_db)):
    return search(db, actor, household(db, actor), payload)


async def save_audio(file, actor, db, photo_id, event_id, query_only):
    config = household(db, actor).settings_json
    if not config["speech_enabled"]:
        raise HTTPException(409, "语音处理已关闭，请管理员在设置中开启")
    if photo_id:
        photo_access(db, photo_id, actor)
    if event_id:
        event = scoped(db, Event, event_id, actor)
        if not event_payload(db, event)["photo_ids"]:
            raise HTTPException(404, "事件没有当前可访问的共享照片")
    chunks, size = [], 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > 30 * 1024 * 1024:
            raise HTTPException(413, "录音不能超过 30MB / 10 分钟")
        chunks.append(chunk)
    data = b"".join(chunks)
    supported = (
        (data.startswith(b"RIFF") and data[8:12] == b"WAVE")
        or data.startswith(b"\x1aE\xdf\xa3")
        or data.startswith(b"OggS")
        or (len(data) > 12 and data[4:8] == b"ftyp")
    )
    if not supported:
        raise HTTPException(415, "支持浏览器 WebM/MP4、WAV 和 Ogg 录音")
    memory_id = uid()
    key = f"{actor.household_id}/audio/{memory_id}/original"
    storage.put_original(key, data)
    memory = AudioMemory(
        id=memory_id,
        household_id=actor.household_id,
        photo_id=photo_id,
        event_id=event_id,
        uploader_id=actor.user_id,
        audio_key=key,
        query_only=query_only,
    )
    db.add(memory)
    db.flush()
    job = enqueue(db, actor.household_id, "audio", memory_id, "asr", priority=20)
    db.commit()
    return {"memory_id": memory.id, "job_id": job.id, "status": "queued"}


@router.post("/speech/transcribe")
async def transcribe(file: UploadFile = File(...), actor=Depends(writer), db: DB = Depends(get_db)):
    return await save_audio(file, actor, db, None, None, True)


@router.post("/memories/audio")
async def record_memory(
    file: UploadFile = File(...),
    photo_id: str | None = Form(None),
    event_id: str | None = Form(None),
    actor=Depends(writer),
    db: DB = Depends(get_db),
):
    if not photo_id and not event_id:
        raise HTTPException(422, "请选择照片或事件以关联口述记忆")
    return await save_audio(file, actor, db, photo_id, event_id, False)


def audio_access(db, mid, actor, edit=False):
    memory = scoped(db, AudioMemory, mid, actor)
    if memory.query_only and memory.uploader_id != actor.user_id:
        raise HTTPException(404, "录音不存在")
    if memory.photo_id:
        photo_access(db, memory.photo_id, actor)
    if memory.event_id:
        event = scoped(db, Event, memory.event_id, actor)
        if not event_payload(db, event)["photo_ids"]:
            raise HTTPException(404, "事件没有当前可访问的共享照片")
    if edit and memory.uploader_id != actor.user_id and actor.role != "owner":
        raise HTTPException(403, "只能编辑自己的录音")
    return memory


@router.get("/memories/audio/{mid}")
def get_memory(mid: str, actor=Depends(current), db: DB = Depends(get_db)):
    memory = audio_access(db, mid, actor)
    jobs = list(db.scalars(select(Job).where(Job.target_type == "audio", Job.target_id == mid)))
    return {**serialize(memory), "jobs": [serialize(j) for j in jobs]}


@router.get("/memories/audio/{mid}/media")
def get_audio(mid: str, actor=Depends(current), db: DB = Depends(get_db)):
    memory = audio_access(db, mid, actor)
    path = storage.read_media(memory.audio_key)
    with path.open("rb") as stream:
        header = stream.read(16)
    mime = (
        "audio/wav"
        if header.startswith(b"RIFF")
        else "audio/webm"
        if header.startswith(b"\x1aE\xdf\xa3")
        else "audio/ogg"
        if header.startswith(b"OggS")
        else "audio/mp4"
    )
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store, private"})


@router.patch("/memories/audio/{mid}")
def edit_memory(mid: str, payload: TranscriptPatch, actor=Depends(writer), db: DB = Depends(get_db)):
    memory = audio_access(db, mid, actor, edit=True)
    memory.transcript, memory.transcript_confirmed = payload.transcript, True
    db.commit()
    return serialize(memory)


@router.delete("/memories/audio/{mid}")
def delete_memory(mid: str, actor=Depends(writer), db: DB = Depends(get_db)):
    memory = audio_access(db, mid, actor, edit=True)
    key = memory.audio_key
    db.execute(delete(Job).where(Job.target_type == "audio", Job.target_id == mid))
    db.delete(memory)
    db.commit()
    storage.delete_media(key)
    return {"ok": True}
