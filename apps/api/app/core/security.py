import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from app.core.config import settings
from app.db.models import AuditLog, DisplayDevice, Household, Membership, Photo, Session, now
from app.db.session import get_db

hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=2)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password):
    if len(password) < 12 or len(password) > 256:
        raise HTTPException(422, "口令长度须为 12–256 个字符")
    return hasher.hash(password)


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def expires(hours=72):
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def audit(db, actor, action, object_type="request", object_id="", metadata=None):
    db.add(
        AuditLog(
            household_id=actor.household_id if actor else None,
            actor_id=actor.user_id if actor else None,
            action=action,
            object_type=object_type,
            object_id=object_id,
            metadata_sanitized=metadata or {},
        )
    )


@dataclass
class Actor:
    user_id: str | None
    household_id: str | None
    role: str
    csrf: str = ""
    device_id: str | None = None


def account_current(request: Request, db: DB = Depends(get_db)):
    token = request.cookies.get("xbum_session", "")
    session = db.scalar(select(Session).where(Session.token_hash == digest(token), Session.expires_at > now()))
    if not session:
        raise HTTPException(401, "请先登录")
    role = "account"
    if session.household_id is not None:
        member = db.scalar(
            select(Membership).where(
                Membership.household_id == session.household_id,
                Membership.user_id == session.user_id,
                Membership.status == "active",
            )
        )
        if not member:
            raise HTTPException(401, "家庭成员资格已撤销")
        role = member.role
    actor = Actor(session.user_id, session.household_id, role, session.csrf)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "CSRF 校验失败，请刷新登录状态")
    request.state.actor = actor
    return actor


def current(request: Request, db: DB = Depends(get_db)):
    actor = account_current(request, db)
    if actor.household_id is None:
        raise HTTPException(403, "请先创建或加入一个家庭")
    return actor


def owner(actor: Actor = Depends(current)):
    if actor.role != "owner":
        raise HTTPException(403, "此操作需要家庭管理员权限")
    return actor


def writer(actor: Actor = Depends(current)):
    if actor.role not in {"owner", "member"}:
        raise HTTPException(403, "只读成员不能修改内容")
    return actor


def display_actor(request: Request, db: DB = Depends(get_db)):
    token = request.cookies.get("xbum_display", "")
    device = db.scalar(
        select(DisplayDevice).where(
            DisplayDevice.token_hash == digest(token),
            DisplayDevice.revoked_at.is_(None),
            DisplayDevice.expires_at > now(),
        )
    )
    if not device:
        raise HTTPException(401, "展示设备尚未配对或授权已过期")
    if request.method == "POST" and request.headers.get("x-display-request") != "1":
        raise HTTPException(403, "缺少展示请求标记")
    actor = Actor(None, device.household_id, "display", device_id=device.id)
    request.state.actor = actor
    return actor


def media_actor(request: Request, db: DB = Depends(get_db)):
    # Explicit selection prevents a display page borrowing an owner's cookie.
    if request.query_params.get("display") == "1":
        return display_actor(request, db)
    return current(request, db)


def login_session(db, response: Response, user, household_id):
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.add(
        Session(
            token_hash=digest(token),
            user_id=user.id,
            household_id=household_id,
            csrf=csrf,
            expires_at=expires(settings.session_hours),
        )
    )
    response.set_cookie(
        "xbum_session",
        token,
        httponly=True,
        samesite="strict",
        secure=settings.session_cookie_secure,
        max_age=settings.session_hours * 3600,
    )
    return csrf


def scoped(db, cls, object_id, actor):
    obj = db.get(cls, object_id)
    if not obj or getattr(obj, "household_id", None) != actor.household_id:
        audit(db, actor, "access_denied", cls.__tablename__, object_id)
        db.commit()
        raise HTTPException(404, "对象不存在或没有权限")
    return obj


def photo_access(db, photo_id, actor, edit=False, allow_deleted=False):
    photo = scoped(db, Photo, photo_id, actor)
    denied = photo.is_deleted and not allow_deleted
    if actor.role == "display":
        denied |= not display_eligible(db, photo)
    else:
        denied |= photo.visibility == "private" and photo.uploader_id != actor.user_id
        denied |= photo.is_hidden and photo.uploader_id != actor.user_id
        if edit:
            denied |= actor.role == "viewer" or (photo.uploader_id != actor.user_id and actor.role != "owner")
    if denied:
        audit(db, actor, "access_denied", "photos", photo_id)
        db.commit()
        raise HTTPException(404, "照片不存在或没有权限")
    return photo


def eligible(photo):
    return photo.visibility == "family" and not photo.is_hidden and not photo.is_deleted


def display_eligible(db, photo):
    if not eligible(photo):
        return False
    from app.db.models import PhotoAnalysis

    analysis = db.scalar(select(PhotoAnalysis).where(PhotoAnalysis.photo_id == photo.id))
    config = db.get(Household, photo.household_id).settings_json
    return not analysis or analysis.quality_score is None or analysis.quality_score >= config["quality_floor"]


def household(db, actor):
    return db.get(Household, actor.household_id)


def privacy_changed(db, actor):
    h = household(db, actor)
    h.privacy_revision += 1
