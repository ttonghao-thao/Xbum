"""Read-side albums over local analysis results; never run a model in the API.

Albums are complete collections, independent of the bounded daily playlist. Human
event/person choices take precedence. Missing evidence yields an empty collection.
"""

import hashlib
import json
import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import Event, EventPhoto, Face, Job, Milestone, Person, PhotoAnalysis
from app.services.calendar import anniversary_projection, local_now
from app.services.photos import album_candidates, current_vision, family_candidates

ALBUMS = {
    "on_this_day": ("往年今日", "按家庭时区，整理往年同月同日及前后 3 天的照片，保留实际拍摄年份。"),
    "growth": ("慢慢长大", "自动整理成长相关画面与跨月人物记录，按拍摄时间串起来；人工选择优先。"),
    "travel": ("一起走过", "根据照片内容与事件线索，自动整理旅行和出游；人工修正优先。"),
    "recent": ("最近的日子", "按真实拍摄日期回顾最近 30 天，或所选月份的完整照片；未知日期不冒充最近拍摄。"),
}
CHILD_WORDS = re.compile(
    r"婴儿|嬰兒|宝宝|寶寶|幼儿|幼兒|儿童|兒童|小孩|孩子|小朋友|\b(?:baby|babies|infant|toddler|child|children|kids?)\b",
    re.I,
)
NO_CHILD = re.compile(
    r"(?:没有|沒有|无|無|未见|未見)(?:明显的?|明顯的?)?(?:婴儿|嬰兒|宝宝|寶寶|儿童|兒童|孩子|小孩)|\bno\s+(?:children|child|baby|kids?)\b",
    re.I,
)


def select_highlights(db, photos, limit=24, preferred_ids=(), household=None, actor=None, score_multipliers=None):
    """Choose representatives across dates before filling from quality-ranked photos.

    This selects existing photographs, never invents aesthetic or expression scores.
    Exact/known duplicate groups contribute one representative and originals remain.
    """
    if not photos or limit <= 0:
        return []
    ids = [p.id for p in photos]
    analyses = {a.photo_id: a for a in db.scalars(select(PhotoAnalysis).where(PhotoAnalysis.photo_id.in_(ids)))}
    preferred = set(preferred_ids)

    def rank(photo):
        a = analyses.get(photo.id)
        quality = a.quality_score if a and a.quality_score is not None else 0.5
        weighted_quality = quality * (score_multipliers or {}).get(photo.id, 1)
        return (photo.id not in preferred, -weighted_quality, photo.taken_at or "9999", photo.id)

    # Keep the best photograph from each known near-duplicate group.
    distinct = {}
    for photo in sorted(photos, key=rank):
        distinct.setdefault(photo.duplicate_group or photo.id, photo)
    candidates = list(distinct.values())
    if limit == 1:
        return sorted(candidates, key=rank)[:1]
    buckets = defaultdict(list)
    for photo in candidates:
        buckets[(photo.taken_at or "unknown")[:10]].append(photo)
    for bucket in buckets.values():
        bucket.sort(key=rank)
    # Evenly sample date buckets when there are more dates than film slots.
    days = sorted(buckets)
    if len(days) > limit:
        chosen = {round(i * (len(days) - 1) / (limit - 1)) for i in range(limit)} if limit > 1 else {0}
        days = [day for i, day in enumerate(days) if i in chosen]
    result = []
    # Explicit user representatives are chosen first even in a densely photographed day.
    for photo in sorted(candidates, key=rank):
        if photo.id in preferred and len(result) < limit:
            result.append(photo)
    while len(result) < limit:
        progressed = False
        selected = {p.id for p in result}
        for day in days:
            bucket = buckets[day]
            while bucket and bucket[0].id in selected:
                bucket.pop(0)
            if bucket and len(result) < limit:
                result.append(bucket.pop(0))
                progressed = True
        if not progressed:
            break
    return sorted(result, key=lambda p: (p.taken_at or "9999", p.id))


class MemoryAlbums:
    def __init__(self, db, household, day=None, actor=None, person_id=None, group=None, period="day"):
        self.db = db
        self.household = household
        self.day = day or local_now(household.timezone).date()
        self.period = period
        self.person_id = person_id
        self.group = group
        self.photos = album_candidates(db, actor) if actor else family_candidates(db, household.id)
        ids = [p.id for p in self.photos]
        self.dates = {
            p.id: datetime.fromisoformat(p.taken_at).astimezone(ZoneInfo(household.timezone)).date()
            if p.taken_at
            else None
            for p in self.photos
        }
        self.analyses = {
            a.photo_id: a for a in db.scalars(select(PhotoAnalysis).where(PhotoAnalysis.photo_id.in_(ids)))
        }
        self.events = {e.id: e for e in db.scalars(select(Event).where(Event.household_id == household.id))}
        self.by_event = defaultdict(list)
        for link in db.scalars(select(EventPhoto).where(EventPhoto.photo_id.in_(ids)).order_by(EventPhoto.event_id)):
            if link.event_id in self.events:
                self.by_event[link.photo_id].append(self.events[link.event_id])
        self.people = {p.id: p for p in db.scalars(select(Person).where(Person.household_id == household.id))}
        self.by_person = defaultdict(set)
        for face in db.scalars(select(Face).where(Face.photo_id.in_(ids), Face.person_id.in_(list(self.people)))):
            # Disabling face analysis does not disable explicit, human photo tags.
            if household.settings_json["faces_enabled"] or face.locked or face.status == "manual_tag":
                self.by_person[face.person_id].add(face.photo_id)
        active = set(
            db.scalars(
                select(Job.target_id).where(
                    Job.household_id == household.id,
                    Job.target_type == "photo",
                    Job.target_id.in_(ids),
                    Job.job_type.in_(
                        [
                            *(["vlm"] if household.settings_json["vlm_enabled"] else []),
                            *(["faces"] if household.settings_json["faces_enabled"] else []),
                        ]
                    ),
                    Job.state.in_(["queued", "retry", "running"]),
                )
            )
        )
        from app.api.schemas import VISION_CONTRACT_VERSION

        failed = set(
            db.scalars(
                select(Job.target_id).where(
                    Job.household_id == household.id,
                    Job.target_type == "photo",
                    Job.target_id.in_(ids),
                    Job.job_type == "vlm",
                    Job.state == "failed",
                    Job.idempotency_key.like(f"%:vlm:vision-{VISION_CONTRACT_VERSION}"),
                )
            )
        )
        stale = {p.id for p in self.photos if not current_vision(self.analyses.get(p.id))}
        self.failed = len((stale & failed) - active)
        self.pending = len(active | ((stale - failed) if household.settings_json["vlm_enabled"] else set()))
        self.mode = "automatic"
        self.window = 0
        self.rows = {
            "on_this_day": self._anniversaries(),
            "growth": self._growth(),
            "travel": self._travel(),
            "recent": self._recent(),
        }
        if period == "year":
            for row in self.rows["growth"]:
                taken = self.dates[row["photo"].id]
                if taken:
                    row["album_group"], row["album_group_title"] = str(taken.year), f"{taken.year} 年"
        # All UI layers consume this same filtered collection. An explicit person
        # requires human-confirmed identity; candidates are never silently substituted.
        person_ids = None
        if person_id:
            person = self.people.get(person_id)
            person_ids = self.by_person[person_id] if person and person.manually_confirmed else set()
        for kind in self.rows:
            self.rows[kind] = [
                row for row in self.rows[kind]
                if (person_ids is None or row["photo"].id in person_ids)
                and (not group or row["album_group"] == group)
            ]

    def _row(self, photo, reason, group=None, title=None, event=None):
        taken = self.dates[photo.id]
        return {
            "photo": photo,
            "album_reason": reason,
            "album_group": group or (taken.strftime("%Y-%m") if taken else "unknown"),
            "album_group_title": title or (taken.strftime("%Y 年 %m 月") if taken else "拍摄时间未知"),
            "album_event_id": event.id if event else None,
        }

    def _anniversaries(self):
        anniversaries = {}
        for photo in self.photos:
            taken = self.dates[photo.id]
            if not taken or taken.year >= self.day.year:
                continue
            projected = anniversary_projection(taken, self.day)
            years = projected.year - taken.year
            distance = abs((projected - self.day).days)
            # A photo from yesterday, Dec 31, isn't a one-year memory on Jan 1.
            if years >= 1 and distance <= 3:
                anniversaries[photo.id] = years
        historical = [p for p in self.photos if p.id in anniversaries]
        candidates = historical
        self.window = 3
        rows = []
        for photo in sorted(candidates, key=lambda p: (p.taken_at, p.id), reverse=True):
            year = self.dates[photo.id].year
            exact = (self.dates[photo.id].month, self.dates[photo.id].day) == (self.day.month, self.day.day)
            reason = f"{anniversaries[photo.id]}年前的" + ("今天" if exact else "这几天")
            rows.append(self._row(photo, reason, str(year), f"{year} 年 · {anniversaries[photo.id]}年前的这段时光"))
        return rows

    def _analysis(self, pid):
        analysis = self.analyses.get(pid)
        data = analysis.analysis_json if analysis else {}
        # Low-confidence output and uncertainties are not classification evidence.
        return data if data.get("confidence", 0) >= 0.5 else {}

    def _child_content(self, pid):
        data = self._analysis(pid)
        fields = [
            *data.get("scene_tags", []),
            *data.get("objects", []),
            data.get("visible_activity", ""),
            data.get("caption", ""),
        ]
        return any(CHILD_WORDS.search(field) and not NO_CHILD.search(field) for field in fields)

    def _growth(self):
        selected = {p.id for p in self.people.values() if p.growth_mode == "include" or p.growth_enabled}
        excluded = {p.id for p in self.people.values() if p.growth_mode == "exclude"}
        blocked = set().union(*(self.by_person[pid] for pid in excluded)) if excluded else set()
        reasons = {}
        if selected:
            self.mode = "manual"
            for pid in sorted(selected - excluded):
                person = self.people[pid]
                for photo_id in self.by_person[pid]:
                    reasons[photo_id] = f"人工指定的成长人物：{person.name or '已登记人物'}"
        else:
            children = {p.id for p in self.photos if self._child_content(p.id)}
            reasons.update({pid: "AI 识别到成长相关画面（候选，不代表同一人物）" for pid in children})
            for pid, ids in self.by_person.items():
                person = self.people[pid]
                months = {self.dates[i].strftime("%Y-%m") for i in ids if self.dates[i]}
                # Repeated clusters are hints, not an inferred name or relationship.
                if len(months) >= 2 and ((person.manually_confirmed and person.is_family) or bool(ids & children)):
                    reason = (
                        "已登记人物的跨月记录"
                        if person.manually_confirmed
                        else "成长画面中相似人物的跨月记录（AI 候选）"
                    )
                    reasons.update({i: reason for i in ids})
        return [
            self._row(p, reasons[p.id])
            for p in sorted(self.photos, key=lambda p: (p.taken_at or "9999", p.id))
            if p.id in reasons and p.id not in blocked
        ]

    def _travel(self):
        groups = defaultdict(list)
        supported = {
            event.id
            for photo in self.photos
            if self._analysis(photo.id).get("candidate_event_type") in {"travel", "outing"}
            for event in self.by_event[photo.id]
        }
        for photo in self.photos:
            events = self.by_event[photo.id]
            manual = [e for e in events if e.locked or e.confirmed]
            trips = [
                e for e in (manual or events) if e.event_type in {"travel", "outing"} and (manual or e.id in supported)
            ]
            event = trips[0] if trips else None
            if manual and not event:
                continue  # A human reclassified this photo's event; don't undo it with VLM.
            if not event and self._analysis(photo.id).get("candidate_event_type") not in {"travel", "outing"}:
                continue
            reason = "人工整理的旅行/出游" if manual else "根据照片内容自动整理的旅行/出游（候选）"
            row = self._row(photo, reason, event.id if event else None, event.title if event else None, event)
            groups[row["album_group"]].append(row)
        ordered = sorted(groups.values(), key=lambda rows: max(r["photo"].taken_at or "" for r in rows), reverse=True)
        return [
            r for group in ordered for r in sorted(group, key=lambda r: (r["photo"].taken_at or "9999", r["photo"].id))
        ]

    def _recent(self):
        start = (
            self.day.replace(day=1) if self.period == "month"
            else self.day - timedelta(days=min(29, (self.day - date.min).days))
        )
        end = (
            self.day.replace(day=monthrange(self.day.year, self.day.month)[1])
            if self.period == "month" else self.day
        )
        return [
            self._row(
                p, "可信拍摄日期的近期记录",
                self.dates[p.id].strftime("%Y-%m" if self.period == "month" else "%Y-%m-%d"),
                self.dates[p.id].strftime("%Y 年 %m 月" if self.period == "month" else "%Y 年 %m 月 %d 日"),
            )
            for p in sorted(self.photos, key=lambda p: (p.taken_at or "", p.id), reverse=True)
            if self.dates[p.id] and start <= self.dates[p.id] <= end
        ]

    def groups(self, kind):
        groups = {}
        for row in self.rows[kind]:
            key = row["album_group"]
            entry = groups.setdefault(key, {
                "id": key, "title": row["album_group_title"], "total": 0,
                "event_id": row["album_event_id"], "dates": {}, "milestones": [],
            })
            entry["total"] += 1
            taken = self.dates[row["photo"].id]
            key_date = taken.isoformat() if taken else "unknown"
            entry["dates"][key_date] = entry["dates"].get(key_date, 0) + 1
        if kind == "growth" and self.person_id:
            for item in self.db.scalars(select(Milestone).where(
                Milestone.household_id == self.household.id,
                Milestone.person_id == self.person_id,
                Milestone.confirmed.is_(True),
            ).order_by(Milestone.date)):
                entry = groups.get(item.date[:4] if self.period == "year" else item.date[:7])
                if entry:
                    entry["milestones"].append({"id": item.id, "date": item.date, "title": item.title, "note": item.note})
        return list(groups.values())

    def revision(self, kind):
        # Includes permission and visible metadata changes, so old playback state
        # can be invalidated without generating or exposing a durable private file.
        values = [
            (r["photo"].id, r["photo"].visibility, r["photo"].taken_at, r["photo"].title,
             r["photo"].caption_user, r["album_reason"], r["album_group"])
            for r in self.rows[kind]
        ]
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:24]

    def metadata(self, kind):
        missing_dates = sum(value is None for value in self.dates.values())
        reasons = []
        if not self.rows[kind]:
            reasons.append("当前可见范围没有匹配照片，不会用全部照片填充")
        if kind in {"on_this_day", "recent"} and missing_dates:
            reasons.append(f"{missing_dates} 张照片没有可信拍摄日期，未按上传时间归入")
        if self.pending:
            reasons.append("部分照片仍在本地分析，完成后自动更新")
        if self.failed:
            reasons.append("部分视觉分析失败，可在处理任务中只重试失败步骤")
        if not self.household.settings_json["vlm_enabled"]:
            reasons.append("视觉理解已关闭；已有结果和日期整理仍可使用")
        if self.person_id and not self.people.get(self.person_id):
            reasons.append("所选人物不存在于当前家庭")
        elif self.person_id and not self.people[self.person_id].manually_confirmed:
            reasons.append("所选人物尚未人工确认，未生成身份叙事")
        return {
            "kind": kind,
            "title": ALBUMS[kind][0],
            "description": ALBUMS[kind][1],
            "total": len(self.rows[kind]),
            "date_basis": self.day.isoformat(),
            "mode": self.mode if kind == "growth" else "automatic",
            "match_window_days": self.window if kind == "on_this_day" else None,
            "pending_count": self.pending,
            "failed_count": self.failed,
            "missing_date_count": missing_dates,
            "person_id": self.person_id,
            "period": self.period,
            "group": self.group,
            "revision": self.revision(kind),
            "explanation": reasons,
            "groups": self.groups(kind),
            "people": [
                {"id": person.id, "name": person.name or "已确认人物"}
                for person in self.people.values()
                if person.manually_confirmed and self.by_person[person.id]
            ],
        }
