from collections import defaultdict
from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import delete, select

from app.db.models import Embedding, Event, EventPhoto, Face, PhotoAnalysis, serialize
from app.services.photos import family_candidates


def event_payload(db, event, allowed=None):
    allowed = allowed if allowed is not None else {p.id for p in family_candidates(db, event.household_id)}
    links = list(db.scalars(select(EventPhoto).where(EventPhoto.event_id == event.id).order_by(EventPhoto.position)))
    ids = [link.photo_id for link in links if link.photo_id in allowed]
    value = serialize(event)
    value["photo_ids"] = ids
    value["segments_json"] = [
        {"date": s["date"], "photo_ids": [pid for pid in s["photo_ids"] if pid in allowed]}
        for s in event.segments_json
        if any(pid in allowed for pid in s["photo_ids"])
    ]
    value["cover_photo_id"] = event.cover_photo_id if event.cover_photo_id in ids else ids[0] if ids else None
    return value


def gps_distance(left, right):
    if None in (left.gps_lat, left.gps_lon, right.gps_lat, right.gps_lon):
        return None
    a, b = radians(left.gps_lat), radians(right.gps_lat)
    delta = sin((b - a) / 2) ** 2 + cos(a) * cos(b) * sin(radians(right.gps_lon - left.gps_lon) / 2) ** 2
    return 6371 * 2 * asin(sqrt(min(1, delta)))


def cluster_events(db, household):
    photos = family_candidates(db, household.id)
    locked = list(db.scalars(select(Event).where(Event.household_id == household.id, Event.locked.is_(True))))
    locked_ids = list(db.scalars(select(EventPhoto.photo_id).where(EventPhoto.event_id.in_([e.id for e in locked]))))
    photos = [p for p in photos if p.id not in set(locked_ids) and p.taken_at]
    photos.sort(key=lambda p: (p.taken_at, p.id))
    ids = [p.id for p in photos]
    vectors = {
        e.photo_id: (e.model_version, np.frombuffer(e.data_blob, dtype=np.float32))
        for e in db.scalars(select(Embedding).where(Embedding.photo_id.in_(ids)))
    }
    analyses = {a.photo_id: a for a in db.scalars(select(PhotoAnalysis).where(PhotoAnalysis.photo_id.in_(ids)))}
    people = defaultdict(set)
    for face in db.scalars(select(Face).where(Face.photo_id.in_(ids), Face.person_id.is_not(None))):
        people[face.photo_id].add(face.person_id)
    groups = []
    for photo in photos:
        if not groups:
            groups.append([photo])
            continue
        prior = groups[-1][-1]
        gap = (datetime.fromisoformat(photo.taken_at) - datetime.fromisoformat(prior.taken_at)).total_seconds() / 3600
        distance = gps_distance(photo, prior)
        similarity = 0
        if photo.id in vectors and prior.id in vectors:
            lv, rv = vectors[photo.id], vectors[prior.id]
            if lv[0] == rv[0] and len(lv[1]) == len(rv[1]):
                similarity = float(lv[1] @ rv[1])
        cooccurrence = bool(people[photo.id] & people[prior.id])
        normal = gap <= household.settings_json["event_gap_hours"] and (distance is None or distance < 80)
        types = [
            analyses[p.id].analysis_json.get("candidate_event_type")
            for p in (photo, prior)
            if p.id in analyses and analyses[p.id].analysis_json.get("confidence", 0) >= 0.5
        ]
        travel = (
            gap <= household.settings_json["travel_gap_hours"]
            and "travel" in types
            and (similarity >= 0.75 or (distance is not None and distance < 50) or cooccurrence)
        )
        # Never merge the same scene across different years.
        if (normal or travel) and photo.taken_at[:4] == prior.taken_at[:4]:
            groups[-1].append(photo)
        else:
            groups.append([photo])
    old = list(db.scalars(select(Event).where(Event.household_id == household.id, Event.locked.is_(False))))
    old_by_photo = {}
    for link in db.scalars(select(EventPhoto).where(EventPhoto.event_id.in_([e.id for e in old]))):
        old_by_photo[link.photo_id] = link.event_id
    old_map = {e.id: e for e in old}
    used = set()

    def local_date(p):
        return datetime.fromisoformat(p.taken_at).astimezone(ZoneInfo(household.timezone)).date().isoformat()

    for group in groups:
        event_id = next(
            (old_by_photo[p.id] for p in group if p.id in old_by_photo and old_by_photo[p.id] not in used), None
        )
        event = old_map.get(event_id)
        if not event:
            event = Event(household_id=household.id, title="家庭日常")
            db.add(event)
            db.flush()
        else:
            event.revision += 1
        used.add(event.id)
        db.execute(delete(EventPhoto).where(EventPhoto.event_id == event.id))
        event.start_at, event.end_at = group[0].taken_at, group[-1].taken_at
        types = [
            analyses[p.id].analysis_json.get("candidate_event_type", "daily")
            for p in group
            if p.id in analyses and analyses[p.id].analysis_json.get("confidence", 0) >= 0.5
        ]
        event.event_type = max(sorted(set(types)), key=types.count) if types else "daily"
        event.title = f"{local_date(group[0])} · 家庭记忆"
        event.cover_photo_id = group[0].id
        event.confidence = 0.65 if len(group) > 1 else 0.35
        event.participants_json = sorted(set().union(*(people[p.id] for p in group)))
        segments = defaultdict(list)
        for position, photo in enumerate(group):
            db.add(EventPhoto(event_id=event.id, photo_id=photo.id, position=position))
            segments[local_date(photo)].append(photo.id)
        event.segments_json = [{"date": key, "photo_ids": value} for key, value in segments.items()]
        # Coordinates are evidence; never translate them into an invented city name.
        if group[0].gps_lat is not None:
            event.location_label = f"{group[0].gps_lat:.4f}, {group[0].gps_lon:.4f}"
            event.location_source = "exif_gps"
    for event in old:
        if event.id not in used:
            db.delete(event)
    db.flush()
    return len(groups)
