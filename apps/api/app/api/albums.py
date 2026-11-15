"""Album read models and member-owned presentation choices."""

import hashlib
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DB

from app.api.schemas import Strict
from app.core.security import audit, current, household, photo_access, writer
from app.db.album_features import AlbumPhotoChoice, AlbumPin
from app.db.models import Photo, PhotoAnalysis
from app.db.session import get_db
from app.services.albums import ALBUMS, MemoryAlbums, select_highlights
from app.services.photos import album_candidates, photo_payload

router = APIRouter()
AlbumKind = Literal["on_this_day", "growth", "travel", "recent"]
Scope = Literal["visible", "family"]


class PinInput(Strict):
    pinned: bool


class DuplicateInput(Strict):
    action: Literal["representative", "detach"]


class PreferredInput(Strict):
    preferred: bool


def private_photo_count(db, actor):
    return db.scalar(select(func.count()).select_from(Photo).where(
        Photo.household_id == actor.household_id,
        Photo.uploader_id == actor.user_id,
        Photo.visibility == "private",
        Photo.is_hidden.is_(False), Photo.is_deleted.is_(False),
    ))


def row_payload(db, row):
    return {**photo_payload(db, row["photo"]), **{k: v for k, v in row.items() if k != "photo"}}


def preferred_ids(db, actor):
    return set(db.scalars(select(AlbumPhotoChoice.photo_id).where(
        AlbumPhotoChoice.household_id == actor.household_id,
        AlbumPhotoChoice.user_id == actor.user_id,
        AlbumPhotoChoice.preferred.is_(True),
    )))


def album_highlights(db, actor, h, photos, limit=24):
    from app.services.recommendations import recommendation_effects

    effects = recommendation_effects(db, h.id, photos, actor=actor, household_scope=False)
    candidates = [p for p in photos if not effects.get(p.id, {}).get("blocked", False)]
    return select_highlights(
        db, candidates, limit=limit, preferred_ids=preferred_ids(db, actor),
        score_multipliers={pid: effect.get("multiplier", 1) for pid, effect in effects.items()},
    )


@router.get("/albums")
def albums(scope: Scope = "visible", actor=Depends(current), db: DB = Depends(get_db)):
    h = household(db, actor)
    index = MemoryAlbums(db, h, actor=actor if scope == "visible" else None)
    pins = set(db.scalars(select(AlbumPin.kind).where(
        AlbumPin.household_id == actor.household_id, AlbumPin.user_id == actor.user_id,
    )))
    items = []
    for kind in ALBUMS:
        cover = album_highlights(db, actor, h, [r["photo"] for r in index.rows[kind]], limit=1)
        items.append({
            **index.metadata(kind), "pinned": kind in pins,
            "cover": photo_payload(db, cover[0]) if cover else None,
        })
    return {
        "scope": scope,
        "private_photo_count": private_photo_count(db, actor),
        "items": items,
    }


@router.get("/albums/{kind}")
def album(
    kind: AlbumKind, day: date | None = None, scope: Scope = "visible",
    person_id: str | None = None, group: str | None = None, period: Literal["day", "month", "year"] = "day",
    page: int = Query(1, ge=1), page_size: int = Query(40, ge=1, le=100),
    actor=Depends(current), db: DB = Depends(get_db),
):
    h = household(db, actor)
    index = MemoryAlbums(db, h, day, actor=actor if scope == "visible" else None,
                         person_id=person_id, group=group, period=period)
    rows = index.rows[kind]
    preferred = preferred_ids(db, actor)
    highlights = album_highlights(db, actor, h, [r["photo"] for r in rows])
    selected = {p.id for p in highlights}
    pinned = db.scalar(select(AlbumPin.id).where(
        AlbumPin.household_id == actor.household_id, AlbumPin.user_id == actor.user_id, AlbumPin.kind == kind,
    )) is not None
    return {
        **index.metadata(kind), "scope": scope, "pinned": pinned,
        "private_photo_count": private_photo_count(db, actor),
        "items": [{**row_payload(db, row), "preferred": row["photo"].id in preferred}
                  for row in rows[(page - 1) * page_size: page * page_size]],
        "highlights": [{**row_payload(db, row), "preferred": row["photo"].id in preferred}
                       for row in rows if row["photo"].id in selected],
        "page": page, "page_size": page_size,
    }


@router.get("/albums/{kind}/playback")
def playback(
    kind: AlbumKind, day: date | None = None, scope: Scope = "visible",
    person_id: str | None = None, group: str | None = None, period: Literal["day", "month", "year"] = "day",
    limit: int = Query(24, ge=1, le=200),
    actor=Depends(current), db: DB = Depends(get_db),
):
    h = household(db, actor)
    index = MemoryAlbums(db, h, day, actor=actor if scope == "visible" else None,
                         person_id=person_id, group=group, period=period)
    rows = index.rows[kind]
    photos = [r["photo"] for r in rows]
    # Recommendation preferences apply to proactive film selection, never to the full collection.
    selected = album_highlights(db, actor, h, photos, limit=limit)
    by_id = {r["photo"].id: r for r in rows}
    signature = index.revision(kind) + ":" + ",".join(p.id for p in selected)
    explanation = index.metadata(kind)["explanation"]
    if photos and not selected:
        explanation = [*explanation, "相册照片已被你的推荐规则排除，仍可在下方主动浏览"]
    return {
        **index.metadata(kind), "scope": scope, "mode": "photo_film",
        "explanation": explanation,
        "revision": hashlib.sha256(signature.encode()).hexdigest()[:24],
        "source_total": len(rows), "selected_count": len(selected),
        "interval_seconds": min(60, max(1, h.settings_json["display_interval_seconds"])),
        "items": [row_payload(db, by_id[p.id]) for p in selected],
    }


@router.get("/album-pins")
def album_pins(actor=Depends(current), db: DB = Depends(get_db)):
    return {"items": list(db.scalars(select(AlbumPin.kind).where(
        AlbumPin.household_id == actor.household_id, AlbumPin.user_id == actor.user_id,
    ).order_by(AlbumPin.kind)))}


@router.put("/album-pins/{kind}")
def pin_album(kind: AlbumKind, payload: PinInput, actor=Depends(current), db: DB = Depends(get_db)):
    choice = db.scalar(select(AlbumPin).where(
        AlbumPin.household_id == actor.household_id, AlbumPin.user_id == actor.user_id, AlbumPin.kind == kind,
    ))
    if payload.pinned and not choice:
        db.add(AlbumPin(household_id=actor.household_id, user_id=actor.user_id, kind=kind))
    elif not payload.pinned and choice:
        db.delete(choice)
    db.commit()
    return {"kind": kind, "pinned": payload.pinned}


@router.put("/album-photos/{photo_id}/preferred")
def prefer_photo(photo_id: str, payload: PreferredInput, actor=Depends(writer), db: DB = Depends(get_db)):
    photo = photo_access(db, photo_id, actor)
    if photo.uploader_id != actor.user_id:
        raise HTTPException(403, "只能固定本人上传的代表照片")
    choice = db.scalar(select(AlbumPhotoChoice).where(
        AlbumPhotoChoice.household_id == actor.household_id,
        AlbumPhotoChoice.user_id == actor.user_id, AlbumPhotoChoice.photo_id == photo.id,
    ))
    if payload.preferred and not choice:
        db.add(AlbumPhotoChoice(household_id=actor.household_id, user_id=actor.user_id, photo_id=photo.id))
    elif not payload.preferred and choice:
        db.delete(choice)
    db.commit()
    return {"photo_id": photo.id, "preferred": payload.preferred}


@router.get("/duplicates")
def duplicates(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    actor=Depends(current), db: DB = Depends(get_db),
):
    groups = {}
    photos = album_candidates(db, actor)
    for photo in photos:
        if photo.duplicate_group:
            groups.setdefault(photo.duplicate_group, []).append(photo)
    choices = preferred_ids(db, actor)
    analyses = {a.photo_id: a for a in db.scalars(select(PhotoAnalysis).where(PhotoAnalysis.photo_id.in_([p.id for p in photos])))}
    rows = []
    for group_id, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        preferred = select_highlights(db, members, limit=1, preferred_ids=choices)[0]
        items = []
        for photo in members:
            analysis = analyses.get(photo.id)
            items.append({
                **photo_payload(db, photo), "can_choose": photo.uploader_id == actor.user_id,
                "quality_reasons": analysis.quality_reasons_json if analysis else [],
            })
        exact = len({p.sha256 for p in members}) == 1
        rows.append({
            "id": group_id, "kind": "exact" if exact else "similar",
            "title": "完全重复" if exact else "相似连拍候选",
            "representative_id": preferred.id, "items": items,
            "reason": "本人固定的代表照片" if preferred.id in choices else "参考清晰度、曝光与分辨率；不判断表情或纪念价值",
        })
    return {"items": rows[(page - 1) * page_size:page * page_size], "total": len(rows), "page": page, "page_size": page_size}


@router.put("/duplicates/{photo_id}")
def duplicate_choice(photo_id: str, payload: DuplicateInput, actor=Depends(writer), db: DB = Depends(get_db)):
    photo = photo_access(db, photo_id, actor)
    if photo.uploader_id != actor.user_id:
        raise HTTPException(403, "只能为本人上传的照片设置代表或拆分")
    if not photo.duplicate_group:
        raise HTTPException(409, "该照片不在相似候选组内")
    if payload.action == "detach":
        photo.duplicate_group = None
        db.execute(delete(AlbumPhotoChoice).where(
            AlbumPhotoChoice.user_id == actor.user_id, AlbumPhotoChoice.photo_id == photo.id,
        ))
    else:
        group_ids = select(Photo.id).where(
            Photo.household_id == actor.household_id, Photo.uploader_id == actor.user_id,
            Photo.duplicate_group == photo.duplicate_group,
        )
        db.execute(delete(AlbumPhotoChoice).where(
            AlbumPhotoChoice.user_id == actor.user_id, AlbumPhotoChoice.photo_id.in_(group_ids),
        ))
        db.add(AlbumPhotoChoice(household_id=actor.household_id, user_id=actor.user_id, photo_id=photo.id))
    audit(db, actor, "duplicate_" + payload.action, "photos", photo.id)
    db.commit()
    return {"ok": True, "photo_id": photo.id, "action": payload.action}
