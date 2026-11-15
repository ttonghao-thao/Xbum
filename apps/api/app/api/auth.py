import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DB

from app.api.schemas import Credentials, HouseholdPatch, InviteInput, Login, RoleInput, page_items
from app.core.config import defaults, settings
from app.core.security import (
    Actor,
    account_current,
    audit,
    current,
    digest,
    expires,
    login_session,
    owner,
    password_hash,
    privacy_changed,
    verify_password,
)
from app.db.models import Household, Invitation, LoginAttempt, Membership, Session, User, now, serialize
from app.db.session import get_db

router = APIRouter()


def rate_limit(db, request, key):
    key = digest(f"{request.client.host if request.client else 'local'}:{key}")
    cutoff = (datetime.now(UTC) - timedelta(minutes=15)).isoformat()
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    count = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(LoginAttempt.key == key, LoginAttempt.created_at > cutoff)
    )
    if count >= 20:
        raise HTTPException(429, "尝试次数过多，请在 15 分钟后重试")
    db.add(LoginAttempt(key=key))
    db.commit()


def new_user(db, payload):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "此用户名已存在")
    user = User(
        username=payload.username, display_name=payload.display_name, password_hash=password_hash(payload.password)
    )
    db.add(user)
    db.flush()
    return user


@router.get("/auth/status")
def auth_status(db: DB = Depends(get_db)):
    return {"needs_setup": not bool(db.scalar(select(User.id).limit(1)))}


@router.post("/auth/setup")
def setup(payload: Credentials, request: Request, response: Response, db: DB = Depends(get_db)):
    rate_limit(db, request, "setup")
    # Serialize first-run setup across API processes.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    if db.scalar(select(User.id).limit(1)):
        raise HTTPException(409, "已完成初始化，请使用邀请或注册新家庭")
    user = new_user(db, payload)
    h = Household(name=payload.household_name, timezone=settings.default_household_timezone, settings_json=defaults())
    db.add(h)
    db.flush()
    db.add(Membership(household_id=h.id, user_id=user.id, role="owner"))
    csrf = login_session(db, response, user, h.id)
    db.commit()
    return {"user": serialize(user), "household": serialize(h), "role": "owner", "csrf": csrf}


@router.post("/auth/register")
def register(payload: Credentials, request: Request, response: Response, db: DB = Depends(get_db)):
    rate_limit(db, request, "register")
    user = new_user(db, payload)
    h = Household(name=payload.household_name, timezone=settings.default_household_timezone)
    db.add(h)
    db.flush()
    db.add(Membership(household_id=h.id, user_id=user.id, role="owner"))
    csrf = login_session(db, response, user, h.id)
    db.commit()
    return {"csrf": csrf, "household": serialize(h), "role": "owner"}


@router.post("/auth/login")
def login(payload: Login, request: Request, response: Response, db: DB = Depends(get_db)):
    rate_limit(db, request, "login")
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(user.password_hash, payload.password):
        raise HTTPException(401, "用户名或口令不正确")
    query = select(Membership).where(Membership.user_id == user.id, Membership.status == "active")
    if payload.household_id:
        query = query.where(Membership.household_id == payload.household_id)
    membership = db.scalar(query.order_by(Membership.id))
    if not membership and payload.household_id:
        raise HTTPException(403, "没有有效家庭成员资格")
    hid = membership.household_id if membership else None
    role = membership.role if membership else "account"
    csrf = login_session(db, response, user, hid)
    audit(db, Actor(user.id, hid, role), "login")
    db.commit()
    return {"csrf": csrf, "role": role, "needs_household": membership is None}


@router.get("/auth/me")
def me(actor=Depends(account_current), db: DB = Depends(get_db)):
    return {
        "user": serialize(db.get(User, actor.user_id)),
        "role": actor.role,
        "csrf": actor.csrf,
        "household": serialize(db.get(Household, actor.household_id)) if actor.household_id else None,
        "needs_household": actor.household_id is None,
        "capabilities": {"max_upload_mb": settings.max_upload_mb},
    }


@router.post("/auth/logout")
def logout(request: Request, response: Response, actor=Depends(account_current), db: DB = Depends(get_db)):
    db.execute(delete(Session).where(Session.token_hash == digest(request.cookies.get("xbum_session", ""))))
    db.commit()
    response.delete_cookie("xbum_session")
    return {"ok": True}


@router.get("/households")
def households(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(account_current),
    db: DB = Depends(get_db),
):
    items = list(
        db.scalars(
            select(Household).join(Membership).where(Membership.user_id == actor.user_id, Membership.status == "active")
        )
    )
    return page_items([serialize(h) for h in items], page, page_size)


@router.post("/households")
def create_household(
    payload: HouseholdPatch, response: Response, actor=Depends(account_current), db: DB = Depends(get_db)
):
    h = Household(name=payload.name or "我们的家", timezone=payload.timezone or settings.default_household_timezone)
    db.add(h)
    db.flush()
    db.add(Membership(household_id=h.id, user_id=actor.user_id, role="owner"))
    csrf = login_session(db, response, db.get(User, actor.user_id), h.id)
    db.commit()
    return {**serialize(h), "csrf": csrf}


@router.get("/households/{hid}")
def get_household(hid: str, actor=Depends(current), db: DB = Depends(get_db)):
    if hid != actor.household_id:
        raise HTTPException(404, "家庭不存在")
    return serialize(db.get(Household, hid))


@router.patch("/households/{hid}")
def patch_household(hid: str, payload: HouseholdPatch, actor=Depends(owner), db: DB = Depends(get_db)):
    if hid != actor.household_id:
        raise HTTPException(404, "家庭不存在")
    h = db.get(Household, hid)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(h, key, value)
    db.commit()
    return serialize(h)


@router.post("/households/{hid}/invites")
def create_invite(hid: str, payload: InviteInput, actor=Depends(owner), db: DB = Depends(get_db)):
    if hid != actor.household_id:
        raise HTTPException(404, "家庭不存在")
    token = secrets.token_urlsafe(24)
    invite = Invitation(
        household_id=hid,
        token_hash=digest(token),
        role=payload.role,
        expires_at=expires(payload.hours),
        max_uses=payload.max_uses,
    )
    db.add(invite)
    audit(db, actor, "invite_created")
    db.commit()
    return {"code": token, "url": f"/invite/{token}", **serialize(invite)}


@router.get("/households/{hid}/invites")
def invites(
    hid: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(owner),
    db: DB = Depends(get_db),
):
    if hid != actor.household_id:
        raise HTTPException(404, "家庭不存在")
    items = list(db.scalars(select(Invitation).where(Invitation.household_id == hid)))
    return page_items([serialize(i) for i in items], page, page_size)


@router.delete("/invites/{iid}")
def revoke_invite(iid: str, actor=Depends(owner), db: DB = Depends(get_db)):
    from app.core.security import scoped

    invitation = scoped(db, Invitation, iid, actor)
    invitation.revoked = True
    db.commit()
    return {"ok": True}


@router.post("/invites/{code}/join")
def join(code: str, payload: Credentials, request: Request, response: Response, db: DB = Depends(get_db)):
    rate_limit(db, request, "join")
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    invitation = db.scalar(
        select(Invitation).where(
            Invitation.token_hash == digest(code),
            Invitation.revoked.is_(False),
            Invitation.expires_at > now(),
            Invitation.used_count < Invitation.max_uses,
        )
    )
    if not invitation:
        raise HTTPException(410, "邀请已过期、撤销或达到使用次数")
    user = db.scalar(select(User).where(User.username == payload.username))
    if user:
        if not verify_password(user.password_hash, payload.password):
            raise HTTPException(401, "已有账户口令不正确")
    else:
        user = new_user(db, payload)
    existing = db.scalar(
        select(Membership).where(Membership.household_id == invitation.household_id, Membership.user_id == user.id)
    )
    if existing:
        if existing.status == "active":
            raise HTTPException(409, "已是该家庭成员")
        existing.status, existing.role = "active", invitation.role
    else:
        db.add(Membership(household_id=invitation.household_id, user_id=user.id, role=invitation.role))
    invitation.used_count += 1
    csrf = login_session(db, response, user, invitation.household_id)
    db.commit()
    return {"csrf": csrf, "role": invitation.role}


@router.get("/households/{hid}/members")
def members(
    hid: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    actor=Depends(current),
    db: DB = Depends(get_db),
):
    if hid != actor.household_id:
        raise HTTPException(404, "家庭不存在")
    items = []
    for member, user in db.execute(
        select(Membership, User).join(User).where(Membership.household_id == hid, Membership.status == "active")
    ):
        items.append({**serialize(member), "display_name": user.display_name})
    return page_items(items, page, page_size)


def membership_guard(db, mid, actor):
    from app.core.security import scoped

    # Serialize the count and mutation; two owners cannot concurrently demote
    # each other after both observing an old count of two.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    member = scoped(db, Membership, mid, actor)
    owners = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.household_id == actor.household_id, Membership.role == "owner", Membership.status == "active")
    )
    if member.role == "owner" and owners <= 1:
        raise HTTPException(409, "必须保留至少一位家庭管理员")
    return member


@router.patch("/members/{mid}")
def change_member(mid: str, payload: RoleInput, actor=Depends(owner), db: DB = Depends(get_db)):
    member = membership_guard(db, mid, actor)
    member.role = payload.role
    audit(db, actor, "role_changed", "memberships", mid)
    db.commit()
    return serialize(member)


@router.delete("/members/{mid}")
def remove_member(mid: str, actor=Depends(owner), db: DB = Depends(get_db)):
    member = membership_guard(db, mid, actor)
    member.status = "removed"
    db.execute(delete(Session).where(Session.user_id == member.user_id, Session.household_id == actor.household_id))
    privacy_changed(db, actor)
    audit(db, actor, "member_removed", "memberships", mid)
    db.commit()
    return {"ok": True}


@router.delete("/households/{hid}")
def delete_household(hid: str, confirm: str, actor=Depends(owner), db: DB = Depends(get_db)):
    from app.services.storage import storage
    import shutil

    # A household is the scope root: unlike its children it has no household_id.
    h = db.get(Household, hid)
    if not h or h.id != actor.household_id:
        audit(db, actor, "access_denied", "households", hid)
        db.commit()
        raise HTTPException(404, "家庭不存在或没有权限")
    if confirm != h.name:
        raise HTTPException(409, "须以完整家庭名称二次确认")
    path = storage.read_media(hid)
    audit(db, actor, "household_deleted", "households", hid)
    db.delete(h)
    db.commit()
    if path.is_dir():
        shutil.rmtree(path)
    return {"ok": True}
