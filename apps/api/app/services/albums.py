"""Read-side albums over local analysis results; never run a model in the API.

Albums are complete collections, independent of the bounded daily playlist. Human
event/person choices take precedence. Missing evidence yields an empty collection.
"""

import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import Event, EventPhoto, Face, Job, Person, PhotoAnalysis
from app.services.calendar import anniversary_projection, local_now
from app.services.photos import album_candidates, current_vision, family_candidates

ALBUMS = {
    "on_this_day": ("往年今日", "按家庭时区，整理往年同月同日及前后 3 天的照片，保留实际拍摄年份。"),
    "growth": ("慢慢长大", "自动整理成长相关画面与跨月人物记录，按拍摄时间串起来；人工选择优先。"),
    "travel": ("一起走过", "根据照片内容与事件线索，自动整理旅行和出游；人工修正优先。"),
}
CHILD_WORDS = re.compile(
    r"婴儿|嬰兒|宝宝|寶寶|幼儿|幼兒|儿童|兒童|小孩|孩子|小朋友|\b(?:baby|babies|infant|toddler|child|children|kids?)\b",
    re.I,
)
NO_CHILD = re.compile(
    r"(?:没有|沒有|无|無|未见|未見)(?:明显的?|明顯的?)?(?:婴儿|嬰兒|宝宝|寶寶|儿童|兒童|孩子|小孩)|\bno\s+(?:children|child|baby|kids?)\b",
    re.I,
)


class MemoryAlbums:
    def __init__(self, db, household, day=None, actor=None):
        self.household = household
        self.day = day or local_now(household.timezone).date()
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
        self.rows = {"on_this_day": self._anniversaries(), "growth": self._growth(), "travel": self._travel()}

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

    def metadata(self, kind):
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
        }
