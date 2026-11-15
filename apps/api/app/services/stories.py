import os
import hashlib
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select

from app.core.security import eligible, photo_access, scoped
from app.db.models import AudioMemory, Event, EventPhoto, Face, Household, Job, Person, Photo, Story, StorySlide, serialize
from app.db.story_features import StoryNarration, StoryPlan
from app.services.storage import storage


def story_access(db, story_id, actor, edit=False):
    story = scoped(db, Story, story_id, actor)
    if edit and (actor.role == "viewer" or (story.created_by != actor.user_id and actor.role != "owner")):
        raise HTTPException(403, "只能编辑自己的故事")
    slides = list(db.scalars(select(StorySlide).where(StorySlide.story_id == story.id).order_by(StorySlide.position)))
    if not slides:
        return story, slides
    for slide in [] if edit else slides:
        photo = photo_access(db, slide.photo_id, actor)
        if not eligible(photo):
            raise HTTPException(404, "故事含已撤回共享的照片，请编辑后再播放")
    if not edit:
        for binding in db.scalars(select(StoryNarration).where(StoryNarration.story_id == story.id)):
            narration_source(db, story, binding, {s.photo_id for s in slides})
    return story, slides


def story_payload(db, story, slides=None):
    slides = (
        slides
        if slides is not None
        else list(db.scalars(select(StorySlide).where(StorySlide.story_id == story.id).order_by(StorySlide.position)))
    )
    event = db.get(Event, story.event_id) if story.event_id else None
    plan = db.scalar(select(StoryPlan).where(StoryPlan.story_id == story.id))
    narration_by_photo = {b.photo_id: b for b in db.scalars(select(StoryNarration).where(StoryNarration.story_id == story.id))}
    slide_values = []
    for slide in slides:
        row = serialize(slide)
        binding = narration_by_photo.get(slide.photo_id)
        if binding:
            try:
                narration_source(db, story, binding, {s.photo_id for s in slides})
                row["narration_url"] = f"/api/v1/stories/{story.id}/narrations/{binding.id}/audio"
                row["audio_memory_id"] = binding.audio_memory_id
                row["narration_transcript"] = db.get(AudioMemory, binding.audio_memory_id).transcript
            except (HTTPException, ValueError):
                row["narration_invalid"] = True
        slide_values.append(row)
    compose_job = db.scalar(select(Job).where(Job.target_type == "story", Job.target_id == story.id,
                                            Job.job_type == "story_compose").order_by(Job.created_at.desc(), Job.id.desc())) if plan else None
    current_ids = {s.photo_id for s in slides}
    chapters = [{**chapter, "photo_ids": [pid for pid in chapter["photo_ids"] if pid in current_ids]}
                for chapter in plan.chapters_json if any(pid in current_ids for pid in chapter["photo_ids"])] if plan else []
    sources = [source for source in plan.sources_json if source.get("photo_id") in current_ids] if plan else []
    return {
        **serialize(story),
        "slides": slide_values,
        "composition": {"length": plan.length, "state": plan.state if story.version == plan.expected_version else "edited", "generator": plan.generator,
                        "chapters": chapters, "sources": sources,
                        "job_id": compose_job.id if compose_job else None,
                        "job_state": compose_job.state if compose_job else None,
                        "error_message": compose_job.error_message_sanitized if compose_job else None} if plan else None,
        "start_at": event.start_at if event else None,
        "end_at": event.end_at if event else None,
    }


def create_draft(db, actor, event_id=None, person_id=None):
    if event_id:
        event = scoped(db, Event, event_id, actor)
        ids = list(
            db.scalars(select(EventPhoto.photo_id).where(EventPhoto.event_id == event_id).order_by(EventPhoto.position))
        )
        title = event.title
    elif person_id:
        person = scoped(db, Person, person_id, actor)
        if not (person.is_family and person.manually_confirmed and person.growth_enabled):
            raise HTTPException(422, "须先人工确认家庭人物并启用成长线")
        ids = list(db.scalars(select(Photo.id).join(Face).where(Face.person_id == person_id).order_by(Photo.taken_at)))
        title = f"{person.name or '家人'}的成长记忆"
    else:
        raise HTTPException(422, "请选择事件或成长人物")
    photos = [db.get(Photo, pid) for pid in dict.fromkeys(ids)]
    photos = [p for p in photos if p and eligible(p)]
    if not photos:
        raise HTTPException(422, "没有可用于故事的家庭共享照片")
    story = Story(
        household_id=actor.household_id,
        event_id=event_id,
        title=title,
        created_by=actor.user_id,
        cover_photo_id=photos[0].id,
        generated_by_ai=False,
    )
    db.add(story)
    db.flush()
    from app.services.albums import select_highlights

    photos = select_highlights(db, photos, limit=min(24, len(photos)))
    timezone = ZoneInfo(db.get(Household, actor.household_id).timezone)
    for index, photo in enumerate(photos):
        # Template is grounded in user/EXIF facts; AI guesses are never promoted to narrative facts.
        day = datetime.fromisoformat(photo.taken_at).astimezone(timezone).date().isoformat() if photo.taken_at else None
        caption = (photo.caption_user or photo.title or (f"拍摄于 {day}" if day else "拍摄时间未知"))[:2000]
        db.add(StorySlide(story_id=story.id, photo_id=photo.id, position=index, caption=caption))
    db.flush()
    return story


def _font(size):
    for filename in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if Path(filename).is_file():
            return ImageFont.truetype(filename, size)
    return ImageFont.load_default(size=size)


def export_mp4(db, story):
    from app.ai.providers import ffmpeg_binary

    slides = list(db.scalars(select(StorySlide).where(StorySlide.story_id == story.id).order_by(StorySlide.position)))
    if not slides:
        raise ValueError("empty story")
    photo_ids = {s.photo_id for s in slides}
    bindings = {b.photo_id: b for b in db.scalars(select(StoryNarration).where(StoryNarration.story_id == story.id))}
    narrations = {pid: narration_source(db, story, b, photo_ids) for pid, b in bindings.items()}
    paths = []
    for slide in slides:
        photo = db.get(Photo, slide.photo_id)
        if not photo or not eligible(photo):
            raise ValueError("story authorization changed")
        paths.append(storage.read_media(f"{photo.household_id}/photos/{photo.id}/960.jpg"))
    # Release the read transaction before slow FFmpeg work; source checks below see latest authorizations.
    db.commit()
    key = f"{story.household_id}/stories/{story.id}/v{story.version}.mp4"
    destination = storage.read_media(key)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="xbum-video-", dir=destination.parent) as temp:
        root = Path(temp)
        clips = []
        for index, (slide, path) in enumerate(zip(slides, paths, strict=True)):
            frame = Image.new("RGB", (1280, 720), story.background)
            with Image.open(path) as original:
                image = ImageOps.contain(original.convert("RGB"), (1280, 590))
                frame.paste(image, ((1280 - image.width) // 2, (590 - image.height) // 2))
            draw = ImageDraw.Draw(frame)
            caption = slide.caption
            # Two lines below image, never over a face.
            for line in range(2):
                draw.text((48, 610 + line * 38), caption[line * 44 : (line + 1) * 44], font=_font(27), fill="white")
            jpg = root / f"frame{index}.jpg"
            frame.save(jpg)
            clip = root / f"clip{index}.mp4"
            duration = slide.duration_s
            audio = narrations.get(slide.photo_id)
            if audio:
                duration = max(duration, _audio_duration(storage.read_media(audio.audio_key)))
            command = [
                ffmpeg_binary(),
                "-nostdin",
                "-v",
                "error",
                "-loop",
                "1",
                "-i",
                str(jpg),
            ]
            if narrations:
                if audio:
                    command += ["-i", str(storage.read_media(audio.audio_key))]
                else:
                    command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            command += [
                "-t",
                str(duration),
                "-r",
                "24",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
            ]
            if narrations:
                command += ["-af", "apad", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
            if slide.transition == "fade":
                command += ["-vf", f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, duration - 0.3)}:d=0.3"]
            subprocess.run([*command, str(clip)], capture_output=True, check=True, timeout=120)
            clips.append(clip)
        listing = root / "concat.txt"
        listing.write_text("\n".join(f"file '{clip.name}'" for clip in clips))
        output = root / "complete.mp4"
        subprocess.run(
            [
                ffmpeg_binary(),
                "-nostdin",
                "-v",
                "error",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(listing),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output),
            ],
            capture_output=True,
            check=True,
            timeout=120,
        )
        db.expire_all()
        for slide in slides:
            photo = db.get(Photo, slide.photo_id)
            if not photo or not eligible(photo):
                raise ValueError("story authorization changed")
        for binding in bindings.values():
            narration_source(db, story, binding, photo_ids)
        os.replace(output, destination)
    return key


def narration_digest(memory):
    return hashlib.sha256(repr((memory.id, memory.transcript, memory.audio_key, memory.photo_id,
                               memory.event_id, memory.transcript_confirmed)).encode()).hexdigest()


def narration_source(db, story, binding, slide_photo_ids):
    memory = db.get(AudioMemory, binding.audio_memory_id) if binding.audio_memory_id else None
    photo = db.get(Photo, binding.photo_id)
    if (
        not memory or memory.household_id != story.household_id or memory.query_only
        or not memory.transcript_confirmed or not memory.transcript.strip()
        or not photo or photo.household_id != story.household_id or not eligible(photo)
        or binding.photo_id not in slide_photo_ids
        or not (memory.photo_id == binding.photo_id or (
            memory.photo_id is None and memory.event_id and memory.event_id == story.event_id
        ))
        or binding.source_digest != narration_digest(memory)
    ):
        raise HTTPException(404, "故事旁白来源已删除、变更或撤回，请重新选择")
    if memory.event_id and not db.scalar(select(EventPhoto.id).where(
        EventPhoto.event_id == memory.event_id, EventPhoto.photo_id == binding.photo_id
    )):
        raise HTTPException(404, "故事旁白关联事件已变更，请重新选择")
    if not storage.read_media(memory.audio_key).is_file():
        raise HTTPException(404, "故事旁白音频不存在")
    return memory


def invalidate_story_export(story):
    if story.mp4_key:
        storage.delete_media(story.mp4_key)
    story.version += 1
    story.mp4_key, story.export_version, story.status = None, None, "draft"


def create_composition(db, actor, event_id=None, person_id=None, photo_ids=None, title=None, length="medium"):
    from app.workers.queue import enqueue

    provided = bool(photo_ids)
    if sum([bool(event_id), bool(person_id), provided]) != 1:
        raise HTTPException(422, "请选择一个事件、确认人物或一批家庭共享照片")
    if event_id:
        event = scoped(db, Event, event_id, actor)
        ids = list(db.scalars(select(EventPhoto.photo_id).where(EventPhoto.event_id == event.id)))
        title = title or event.title
    elif person_id:
        person = scoped(db, Person, person_id, actor)
        if not (person.manually_confirmed and person.is_family):
            raise HTTPException(422, "请先确认家庭人物")
        ids = list(db.scalars(select(Face.photo_id).where(Face.person_id == person.id)))
        title = title or f"{person.name or '家人'}的成长记忆"
    else:
        ids = list(dict.fromkeys(photo_ids))
        title = title or "我们一起的记忆"
    photos = []
    if not provided:
        # Metadata links may outlive sharing; derive candidates from the family boundary first.
        ids = [pid for pid in ids if (p := db.get(Photo, pid)) and p.household_id == actor.household_id and eligible(p)]
    for pid in ids:
        photo = photo_access(db, pid, actor)
        if eligible(photo):
            photos.append(photo)
        elif provided:
            raise HTTPException(422, "故事只能使用未隐藏的家庭共享照片")
    if not photos:
        raise HTTPException(422, "没有可用于故事的家庭共享照片")
    story = Story(household_id=actor.household_id, created_by=actor.user_id, event_id=event_id,
                  title=title, cover_photo_id=photos[0].id, generated_by_ai=False, status="compose_queued")
    db.add(story)
    db.flush()
    db.add(StoryPlan(household_id=actor.household_id, story_id=story.id, length=length,
                     expected_version=story.version, source_photo_ids=[p.id for p in photos]))
    job = enqueue(db, actor.household_id, "story", story.id, "story_compose",
                  key=f"story:{story.id}:compose:{story.version}", priority=15)
    db.flush()
    return story, job


def compose_story(db, story):
    """Worker entry point. Selection/templates are local, deterministic and source-grounded."""
    from sqlalchemy import delete
    from app.services.albums import select_highlights

    plan = db.scalar(select(StoryPlan).where(StoryPlan.story_id == story.id))
    if not plan:
        raise ValueError("missing composition plan")
    if story.version != plan.expected_version:
        plan.state = "skipped_user_edit"
        return False
    household = db.get(Household, story.household_id)
    photos = [db.get(Photo, pid) for pid in plan.source_photo_ids]
    if any(not p or p.household_id != story.household_id or not eligible(p) for p in photos):
        plan.state = "authorization_changed"
        story.status = "draft"
        return False
    selected = select_highlights(db, photos, limit={"short": 12, "medium": 24, "long": 60}[plan.length])
    db.execute(delete(StorySlide).where(StorySlide.story_id == story.id))
    chapters, sources = defaultdict(list), []
    for position, photo in enumerate(selected):
        day = datetime.fromisoformat(photo.taken_at).astimezone(ZoneInfo(household.timezone)).date().isoformat() if photo.taken_at else None
        caption = (photo.caption_user or photo.title or (f"拍摄于 {day}" if day else "拍摄时间未知"))[:2000]
        source = "human_caption" if photo.caption_user else "human_title" if photo.title else "capture_time" if day else "unknown"
        db.add(StorySlide(story_id=story.id, photo_id=photo.id, position=position, caption=caption))
        chapters[day or "拍摄时间未知"].append(photo.id)
        sources.append({"photo_id": photo.id, "caption_source": source, "date": day,
                        "capture_time_source": photo.capture_time_source})
    plan.chapters_json = [{"title": day, "photo_ids": ids} for day, ids in chapters.items()]
    plan.sources_json, plan.state = sources, "ready"
    story.cover_photo_id = selected[0].id
    story.generated_by_ai, story.status = False, "draft"
    db.flush()
    return True


def memory_suggestions(db, actor, household):
    from app.services.albums import MemoryAlbums, select_highlights
    from app.services.recommendations import recommendation_effects
    from app.services.photos import photo_payload

    index = MemoryAlbums(db, household)  # Family-only suggestions; private album playback is separate.
    suggestions = []
    effects = recommendation_effects(db, household.id, index.photos, actor=actor)
    for kind in ("on_this_day", "growth", "travel"):
        photos = [r["photo"] for r in index.rows[kind] if not effects[r["photo"].id]["blocked"]]
        if not photos:
            continue
        chosen = select_highlights(db, photos, limit=60)
        neutral = [p for p in chosen if effects[p.id]["multiplier"] == 1]
        cover = neutral[0] if neutral else chosen[0]
        preference_score = sum(effects[p.id]["multiplier"] for p in photos) / len(photos)
        suggestions.append({"kind": kind, "title": index.metadata(kind)["title"], "photo_ids": [p.id for p in chosen],
                            "photo_count": len(photos), "cover": photo_payload(db, cover), "preference_score": preference_score,
                            "reason": "从当前家庭共享相册中自动整理，创建故事前可检查素材"})
    return sorted(suggestions, key=lambda row: -row["preference_score"])


def _audio_duration(path):
    import json
    import math
    from app.ai.providers import ffmpeg_binary

    executable = str(Path(ffmpeg_binary()).with_name("ffprobe"))
    process = subprocess.run([executable, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                             capture_output=True, check=True, timeout=30)
    duration = float(json.loads(process.stdout)["format"]["duration"])
    if not math.isfinite(duration) or not 0 < duration <= 600:
        raise ValueError("unsupported narration duration")
    return duration
