from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Protocol
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select

from app.db.models import (
    Embedding,
    Event,
    EventPhoto,
    Face,
    Feedback,
    Milestone,
    Person,
    PhotoAnalysis,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
    Story,
)
from app.services.albums import MemoryAlbums
from app.services.calendar import festivals
from app.services.photos import display_candidates, photo_payload


class RecommendationService(Protocol):
    def build_daily_playlist(self, household_id: str, day: date, config: dict): ...


def build_daily_playlist(db, household, day=None):
    day = day or datetime.now(ZoneInfo(household.timezone)).date()
    config = household.settings_json
    day_start = datetime.combine(day, time.min, ZoneInfo(household.timezone))
    day_end = (day_start + timedelta(days=1)).astimezone(UTC).isoformat()
    albums = MemoryAlbums(db, household, day)
    photos = albums.photos
    album_reasons = {kind: {r["photo"].id: r["album_reason"] for r in rows} for kind, rows in albums.rows.items()}
    photo_ids = [p.id for p in photos]
    analyses = {a.photo_id: a for a in db.scalars(select(PhotoAnalysis).where(PhotoAnalysis.photo_id.in_(photo_ids)))}
    feedback = list(db.scalars(select(Feedback).where(Feedback.household_id == household.id)))
    latest = {}
    preferences = {}
    favorites, disliked, blocked = set(), set(), set()
    for f in sorted(feedback, key=lambda f: (f.created_at, f.id)):
        if f.type in {"favorite", "unfavorite"}:
            latest[f.photo_id] = f.type
        if f.type in {"like", "dislike"}:
            preferences[f.photo_id] = f.type
        if (
            f.type == "snooze"
            and f.until_at
            and datetime.fromisoformat(f.until_at).astimezone(ZoneInfo(household.timezone)).date() > day
        ):
            blocked.add(f.photo_id)
    favorites = {key for key, val in latest.items() if val == "favorite"}
    liked = {key for key, val in preferences.items() if val == "like"}
    disliked = {key for key, val in preferences.items() if val == "dislike"}
    plays = list(
        db.scalars(
            select(PlaybackEvent).where(
                PlaybackEvent.household_id == household.id,
                PlaybackEvent.action == "play",
                PlaybackEvent.played_at < day_end,
            )
        )
    )
    total_plays = Counter(p.photo_id for p in plays)
    cutoff = (day_start - timedelta(days=config["recent_days"] - 1)).astimezone(UTC).isoformat()
    recent_plays = Counter(p.photo_id for p in plays if p.played_at >= cutoff)
    events = {e.id: e for e in db.scalars(select(Event).where(Event.household_id == household.id))}
    by_photo = {}
    for link in db.scalars(select(EventPhoto).where(EventPhoto.photo_id.in_(photo_ids)).order_by(EventPhoto.event_id)):
        by_photo.setdefault(link.photo_id, events.get(link.event_id))
    persons = {
        p.id: p
        for p in db.scalars(
            select(Person).where(Person.household_id == household.id, Person.manually_confirmed.is_(True))
        )
    }
    by_person = defaultdict(set)
    for face in db.scalars(select(Face).where(Face.photo_id.in_(photo_ids), Face.person_id.in_(list(persons)))):
        by_person[face.photo_id].add(face.person_id)
    for photo_id, event in by_photo.items():
        if event and event.confirmed:
            by_person[photo_id].update(pid for pid in event.participants_json if pid in persons)
    milestones = list(
        db.scalars(select(Milestone).where(Milestone.household_id == household.id, Milestone.confirmed.is_(True)))
    )
    vectors = {
        e.photo_id: (e.model_version, np.frombuffer(e.data_blob, dtype=np.float32))
        for e in db.scalars(select(Embedding).where(Embedding.photo_id.in_(photo_ids)))
    }
    weights = config["weights"]
    norm = sum(weights.values()) or 1
    rows = []
    enabled = config["scenes"]
    hashes = {p.id: int(p.phash, 16) for p in photos if p.phash}
    for photo in photos:
        analysis = analyses.get(photo.id)
        quality = analysis.quality_score if analysis and analysis.quality_score is not None else 0.5
        if quality < config["quality_floor"] or photo.id in blocked:
            continue
        event = by_photo.get(photo.id)
        scene, reasons = "daily", ["日常精选"]
        calendar_relevance = 0.0
        taken = (
            datetime.fromisoformat(photo.taken_at).astimezone(ZoneInfo(household.timezone)).date()
            if photo.taken_at
            else None
        )
        if photo.id in album_reasons["on_this_day"] and enabled["on_this_day"]:
            exact_day = (taken.month, taken.day) == (day.month, day.day)
            calendar_relevance = 1.0 if exact_day else 0.7
            scene = "on_this_day"
            reasons = [album_reasons["on_this_day"][photo.id]]
        confirmed_people = [persons[pid] for pid in sorted(by_person[photo.id])]
        birthday = [
            p
            for p in confirmed_people
            if p.birthday
            and (date.fromisoformat(p.birthday).month, date.fromisoformat(p.birthday).day) == (day.month, day.day)
        ]
        if birthday and enabled["birthday"]:
            scene, calendar_relevance, reasons = "birthday", 1.0, [f"今天是{birthday[0].name or '家人'}登记的生日"]
        elif (
            event
            and event.confirmed
            and event.event_type == "festival"
            and festivals(day)
            and enabled["festival"]
            and (
                (taken and set(festivals(taken)) & set(festivals(day))) or any(f in event.title for f in festivals(day))
            )
        ):
            scene, calendar_relevance, reasons = "festival", 1.0, ["已确认的节日记忆", *festivals(day)]
        elif scene == "daily" and photo.id in album_reasons["travel"] and enabled["travel"]:
            scene, reasons = "travel", [album_reasons["travel"][photo.id]]
        elif scene == "daily" and photo.id in album_reasons["growth"] and enabled["growth"]:
            scene, reasons = "growth", [album_reasons["growth"][photo.id]]
        age = max(
            0, (day - datetime.fromisoformat(photo.created_at).astimezone(ZoneInfo(household.timezone)).date()).days
        )
        if scene == "daily" and age <= 7 and total_plays[photo.id] == 0 and enabled["new_share"]:
            scene, reasons = "new_share", ["家人最近分享，还未播放"]
        if scene == "daily" and not enabled["daily"]:
            continue
        recorded = [
            m for m in milestones if taken and m.date == taken.isoformat() and m.person_id in by_person[photo.id]
        ]
        if recorded:
            reasons += ["人工记录的里程碑：" + m.title for m in recorded]
            calendar_relevance = max(calendar_relevance, 0.8)
        if photo.id in favorites:
            reasons.append("你收藏过的照片")
        feature = {
            "quality": quality,
            "event_relevance": 1.0 if recorded or (event and event.confirmed) else 0.3,
            "calendar_relevance": calendar_relevance,
            "user_preference": 1.0
            if photo.id in favorites
            else 0.1
            if photo.id in disliked
            else 0.8
            if photo.id in liked
            else 0.5,
            "freshness": 1 / (1 + age / 30),
            "underexposure": 1 / (1 + total_plays[photo.id]),
        }
        score = sum(weights[key] * value for key, value in feature.items()) / norm
        score = score / (1 + 0.3 * recent_plays[photo.id]) - (0.25 if photo.id in disliked else 0)
        rows.append(
            {
                "photo": photo,
                "event": event.id if event else None,
                "scene": scene,
                "reasons": reasons,
                "score": score,
                "persons": by_person[photo.id],
            }
        )
    limit = min(config["daily_photo_limit"], len(rows))
    selected, counts, covered, duplicate_groups = [], Counter(), set(), set()
    event_cap = max(1, ceil(limit * 0.4))

    def similarity(left, right):
        lp, rp = left["photo"], right["photo"]
        if lp.duplicate_group and lp.duplicate_group == rp.duplicate_group:
            return 1.0
        if lp.id in vectors and rp.id in vectors:
            lv, rv = vectors[lp.id], vectors[rp.id]
            if lv[0] == rv[0] and len(lv[1]) == len(rv[1]):
                return max(0.0, float(lv[1] @ rv[1]))
        if lp.phash and rp.phash:
            return max(0.0, 1 - (hashes[lp.id] ^ hashes[rp.id]).bit_count() / 32)
        return 0.0

    while rows and len(selected) < limit:
        candidates = [
            r for r in rows if not r["photo"].duplicate_group or r["photo"].duplicate_group not in duplicate_groups
        ]
        capped = [r for r in candidates if not r["event"] or counts[r["event"]] < event_cap]
        relaxed = not capped
        candidates = capped or candidates
        if not candidates:
            break

        def marginal(row, covered=covered):
            redundancy = max((similarity(row, prior) for prior in selected[-3:]), default=0)
            diversity = 0.05 * len(row["persons"] - covered)
            return row["score"] - 0.3 * redundancy + min(0.1, diversity)

        best = sorted(candidates, key=lambda row: (-marginal(row), row["photo"].id))[0]
        if relaxed and best["event"] and counts[best["event"]] >= event_cap:
            best["reasons"].append("候选活动较少，适当放宽活动配额")
        selected.append(best)
        counts[best["event"]] += 1
        covered |= best["persons"]
        if best["photo"].duplicate_group:
            duplicate_groups.add(best["photo"].duplicate_group)
        rows.remove(best)
    playlist = Playlist(household_id=household.id, playlist_date=day.isoformat(), config_json=config.copy())
    db.add(playlist)
    db.flush()
    stories = list(db.scalars(select(Story).where(Story.household_id == household.id)))
    story_by_event = {s.event_id: s for s in stories}
    for index, row in enumerate(selected):
        story = story_by_event.get(row["event"])
        db.add(
            PlaylistItem(
                playlist_id=playlist.id,
                photo_id=row["photo"].id,
                story_id=story.id if story and row["scene"] == "travel" else None,
                position=index,
                score=round(row["score"], 6),
                reason_codes_json=row["reasons"],
                scene_type=row["scene"],
            )
        )
    db.flush()
    return playlist


def playlist_payload(db, playlist, household):
    if not playlist:
        return {"items": [], "playlist_date": "", "privacy_revision": household.privacy_revision}
    allowed = {p.id: p for p in display_candidates(db, household)}
    items = []
    from app.db.models import serialize

    for item in db.scalars(
        select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id).order_by(PlaylistItem.position)
    ):
        if item.photo_id not in allowed:
            continue
        data = serialize(item)
        data["photo"] = photo_payload(db, allowed[item.photo_id])
        items.append(data)
    return {**serialize(playlist), "items": items, "privacy_revision": household.privacy_revision}
