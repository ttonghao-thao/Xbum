import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select

from app.core.security import eligible, photo_access, scoped
from app.db.models import Event, EventPhoto, Face, Person, Photo, Story, StorySlide, serialize
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
    return story, slides


def story_payload(db, story, slides=None):
    slides = (
        slides
        if slides is not None
        else list(db.scalars(select(StorySlide).where(StorySlide.story_id == story.id).order_by(StorySlide.position)))
    )
    event = db.get(Event, story.event_id) if story.event_id else None
    return {
        **serialize(story),
        "slides": [serialize(s) for s in slides],
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
    for index, photo in enumerate(photos[:200]):
        # Template is grounded in user/EXIF facts; AI guesses are never promoted to narrative facts.
        caption = (
            photo.caption_user or photo.title or (f"拍摄于 {photo.taken_at[:10]}" if photo.taken_at else "拍摄时间未知")
        )
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
    paths = []
    for slide in slides:
        photo = db.get(Photo, slide.photo_id)
        if not photo or not eligible(photo):
            raise ValueError("story authorization changed")
        paths.append(storage.read_media(f"{photo.household_id}/photos/{photo.id}/960.jpg"))
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
            command = [
                ffmpeg_binary(),
                "-nostdin",
                "-v",
                "error",
                "-loop",
                "1",
                "-i",
                str(jpg),
                "-t",
                str(slide.duration_s),
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
            if slide.transition == "fade":
                command += ["-vf", f"fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, slide.duration_s - 0.3)}:d=0.3"]
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
        os.replace(output, destination)
    return key
