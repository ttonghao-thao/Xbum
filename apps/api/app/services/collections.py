"""Saved family-photo sets owned by the member who created them."""

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.api.schemas import SearchInput
from app.db.album_features import AlbumPhotoChoice
from app.db.collection_features import SmartCollection
from app.db.models import serialize
from app.services.albums import select_highlights
from app.services.photos import family_candidates, photo_payload
from app.services.search import search


def collection_access(db, cid, actor):
    row = db.get(SmartCollection, cid)
    if not row or row.household_id != actor.household_id or row.created_by != actor.user_id:
        raise HTTPException(404, "相册不存在或没有权限")
    return row


def checked_photo_ids(db, actor, ids):
    allowed = {p.id for p in family_candidates(db, actor.household_id)}
    if set(ids) - allowed:
        raise HTTPException(422, "只能选取未隐藏的家庭共享照片")
    return list(dict.fromkeys(ids))


def collection_selection(db, actor, household, row):
    warning = ""
    if row.mode == "dynamic":
        params = SearchInput.model_validate({**row.query_json, "semantic": False, "query_id": None})
        try:
            results = search(db, actor, household, params, paginate=False, create_query=False)
            ids = [p["id"] for p in results["items"]]
            warning = "；".join(results["unknown_conditions"])
        except HTTPException as exc:
            if exc.status_code not in {404, 422}:
                raise
            # A saved person/event may have been deleted or merged. Never relax
            # the condition to every photo; explicit manual additions survive.
            ids = []
            warning = "相册条件中的人物或事件已变化，请重新检查条件。"
    else:
        ids = row.photo_ids_json
    ids = list(dict.fromkeys([*ids, *row.include_ids_json]))
    allowed = {p.id: p for p in family_candidates(db, actor.household_id)}
    return [allowed[pid] for pid in ids if pid in allowed and pid not in row.exclude_ids_json], warning


def collection_photos(db, actor, household, row):
    return collection_selection(db, actor, household, row)[0]


def collection_payload(db, actor, household, row, page=1, page_size=40):
    photos, warning = collection_selection(db, actor, household, row)
    cover = next((p for p in photos if p.id == row.cover_photo_id), photos[0] if photos else None)
    value = serialize(row)
    # Do not return stored ids for photos whose sharing was later withdrawn.
    allowed = {p.id for p in photos}
    value["photo_ids_json"] = [pid for pid in row.photo_ids_json if pid in allowed]
    value["include_ids_json"] = [pid for pid in row.include_ids_json if pid in allowed]
    current_ids = {p.id for p in family_candidates(db, actor.household_id)}
    value["exclude_ids_json"] = [pid for pid in row.exclude_ids_json if pid in current_ids]
    value["cover_photo_id"] = cover.id if cover else None
    return {
        **value,
        "total": len(photos),
        "page": page,
        "page_size": page_size,
        "rule_warning": warning,
        "cover": photo_payload(db, cover) if cover else None,
        "items": [photo_payload(db, p) for p in photos[(page - 1) * page_size : page * page_size]],
    }


def collection_playback(db, actor, household, row):
    photos = collection_photos(db, actor, household, row)
    from app.services.recommendations import recommendation_effects

    effects = recommendation_effects(db, household.id, photos, actor=actor, household_scope=False)
    candidates = [p for p in photos if not effects[p.id]["blocked"]]
    preferred = set(db.scalars(select(AlbumPhotoChoice.photo_id).where(
        AlbumPhotoChoice.household_id == actor.household_id,
        AlbumPhotoChoice.user_id == actor.user_id,
        AlbumPhotoChoice.preferred.is_(True),
    )))
    selected = select_highlights(
        db, candidates, limit=24, preferred_ids=preferred,
        score_multipliers={pid: effect["multiplier"] for pid, effect in effects.items()},
    )
    revision = hashlib.sha256(
        json.dumps({
            "source": [(p.id, p.title, p.caption_user, p.taken_at) for p in photos],
            "selected": [p.id for p in selected],
        }).encode()
    ).hexdigest()
    duration = min(60, max(1, household.settings_json["display_interval_seconds"]))
    return {
        "title": row.title,
        "mode": "photo_film",
        # total/source_total describe the complete collection; selected_count
        # describes only the representative sequence, independent of grid pages.
        "total": len(photos),
        "source_total": len(photos),
        "selected_count": len(selected),
        "interval_seconds": duration,
        "revision": revision,
        "explanation": ["相册照片已被你的推荐规则排除，仍可在下方主动浏览"] if photos and not selected else [],
        "items": [
            {
                **photo_payload(db, p),
                "caption": p.caption_user or p.title or (p.taken_at[:10] if p.taken_at else "拍摄时间未知"),
                "duration_s": duration,
                "reason": "智能相册对应照片",
            }
            for p in selected
        ],
    }
