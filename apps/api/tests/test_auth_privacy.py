import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.db.models import AuditLog, AudioMemory, Household, Photo, PhotoDerivative, StorySlide, User
from app.db.session import Base, SessionLocal
from app.main import app
from app.services.storage import storage
from conftest import PASSWORD, drain, setup, upload


def invite(client, owner, other, role="member"):
    response = client.post(f"/api/v1/households/{owner['household']['id']}/invites", json={"role": role})
    assert response.status_code == 200
    code = response.json()["code"]
    result = other.post(
        f"/api/v1/invites/{code}/join", json={"username": "second", "password": PASSWORD, "display_name": "第二位家人"}
    )
    assert result.status_code == 200, result.text
    other.headers["X-CSRF-Token"] = result.json()["csrf"]
    return code


def test_setup_login_csrf_and_password(client, owner):
    assert client.post("/api/v1/auth/setup", json={"username": "x2", "password": PASSWORD}).status_code == 409
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/auth/logout").status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    result = client.post("/api/v1/auth/login", json={"username": "owner", "password": PASSWORD})
    assert result.status_code == 200
    assert "httponly" in result.headers["set-cookie"].lower()
    assert "password_hash" not in client.get("/api/v1/auth/me").text


def test_second_member_explicit_share_and_revocation(client, owner):
    public = upload(client, 1)["photo_id"]
    private = upload(client, 2, shared=False)["photo_id"]
    with TestClient(app) as other:
        code = invite(client, owner, other)
        ids = {p["id"] for p in other.get("/api/v1/photos").json()["items"]}
        assert public in ids and private not in ids
        assert other.get(f"/api/v1/photos/{private}/media/320").status_code == 404
        assert other.patch(f"/api/v1/photos/{public}", json={"title": "bad"}).status_code == 404
        assert (
            other.post(f"/api/v1/invites/{code}/join", json={"username": "third", "password": PASSWORD}).status_code
            == 410
        )
        assert client.patch(f"/api/v1/photos/{public}", json={"visibility": "private"}).status_code == 200
        assert other.get(f"/api/v1/photos/{public}").status_code == 404
        members = client.get(f"/api/v1/households/{owner['household']['id']}/members").json()["items"]
        mid = next(m["id"] for m in members if m["user_id"] != owner["user"]["id"])
        assert client.delete(f"/api/v1/members/{mid}").status_code == 200
        assert other.get("/api/v1/auth/me").status_code == 401


def test_viewer_permissions_and_invite_revoke(client, owner):
    with TestClient(app) as viewer:
        invite(client, owner, viewer, "viewer")
        assert viewer.post("/api/v1/photos/upload", files={"file": ("a.png", b"bad", "image/png")}).status_code == 403
        assert viewer.patch("/api/v1/settings", json={"autoplay": False}).status_code == 403
    inv = client.post(f"/api/v1/households/{owner['household']['id']}/invites", json={}).json()
    assert client.delete("/api/v1/invites/" + inv["id"]).status_code == 200
    with TestClient(app) as other:
        assert (
            other.post(
                f"/api/v1/invites/{inv['code']}/join", json={"username": "third", "password": PASSWORD}
            ).status_code
            == 410
        )


def test_cross_household_original_thumbnail_api_and_audit(client, owner):
    pid = upload(client)["photo_id"]
    with TestClient(app) as other:
        setup(other, "another", True)
        for path in [
            f"/photos/{pid}",
            f"/photos/{pid}/media/original",
            f"/photos/{pid}/media/960",
            f"/households/{owner['household']['id']}",
        ]:
            assert other.get("/api/v1" + path).status_code == 404
    with SessionLocal() as db:
        assert len(list(db.scalars(select(AuditLog).where(AuditLog.action == "access_denied")))) >= 3


def pair(client):
    result = client.post("/api/v1/display/devices", json={"name": "test-frame"})
    assert result.status_code == 200, result.text
    display = TestClient(app)
    response = display.post("/api/v1/display/pair", json={"code": result.json()["code"]})
    assert response.status_code == 200, response.text
    display.headers["X-Display-Request"] = "1"
    return display, result.json()["device"]["id"]


def test_display_minimal_privileges_and_immediate_privacy(client, owner):
    pid = upload(client)["photo_id"]
    private = upload(client, 2, False)["photo_id"]
    display, did = pair(client)
    try:
        feed = display.get("/api/v1/display/feed").json()
        assert [i["photo_id"] for i in feed["items"]] == [pid]
        assert "analysis" not in feed["items"][0]["photo"]
        assert display.get("/api/v1/photos/" + pid + "/media/960?display=1").status_code == 200
        assert display.get("/api/v1/photos/" + pid + "/media/original?display=1").status_code == 403
        assert display.get("/api/v1/photos/" + private + "/media/960?display=1").status_code == 404
        assert display.get("/api/v1/auth/me").status_code == 401
        assert display.get("/api/v1/jobs").status_code == 401
        assert display.get("/api/v1/system/models").status_code == 401
        assert (
            display.post(
                "/api/v1/display/events", json={"photo_id": pid, "action": "play", "dwell_seconds": 3}
            ).status_code
            == 200
        )
        client.patch("/api/v1/photos/" + pid, json={"visibility": "private"})
        assert display.get("/api/v1/display/feed").json()["items"] == []
        assert display.get("/api/v1/photos/" + pid + "/media/960?display=1").status_code == 404
        client.delete("/api/v1/display/devices/" + did)
        assert display.get("/api/v1/display/feed").status_code == 401
    finally:
        display.close()


def test_login_rate_limit_and_cross_origin(client):
    for _ in range(20):
        assert client.post("/api/v1/auth/login", json={"username": "none", "password": "none"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "none", "password": "none"}).status_code == 429
    assert client.post("/api/v1/auth/register", headers={"Origin": "https://evil.test"}, json={}).status_code == 403


def test_one_owner_required(client, owner):
    mid = client.get(f"/api/v1/households/{owner['household']['id']}/members").json()["items"][0]["id"]
    assert client.delete("/api/v1/members/" + mid).status_code == 409


def test_delete_household_requires_name_confirmation(client, owner):
    hid = owner["household"]["id"]
    assert client.delete(f"/api/v1/households/{hid}").status_code == 422
    response = client.delete(f"/api/v1/households/{hid}", params={"confirm": "wrong-name"})
    assert response.status_code == 409, response.text
    assert client.get(f"/api/v1/households/{hid}").status_code == 200


def test_owner_can_delete_own_household(client, owner):
    hid = owner["household"]["id"]
    public = upload(client, 1, exif=True)["photo_id"]
    private = upload(client, 2, shared=False)["photo_id"]
    client.patch("/api/v1/settings", json={"faces_enabled": True})
    drain()  # Synthetic images and the explicit test provider; no real model or family data.
    event_id = client.get("/api/v1/events").json()["items"][0]["id"]
    story = client.post("/api/v1/stories/generate", json={"event_id": event_id}).json()
    audio_key = f"{hid}/audio/synthetic.wav"
    storage.put_original(audio_key, b"synthetic audio cleanup fixture")
    with SessionLocal() as db:
        db.add(AudioMemory(household_id=hid, photo_id=public, uploader_id=owner["user"]["id"], audio_key=audio_key))
        db.commit()
    with TestClient(app) as member, TestClient(app) as other:
        invite(client, owner, member)
        setup(other, "unaffected", True)
        kept = upload(other, 3)["photo_id"]
        display, _ = pair(client)
        try:
            response = client.delete(f"/api/v1/households/{hid}", params={"confirm": owner["household"]["name"]})
            assert response.status_code == 200, response.text
            assert client.get("/api/v1/auth/me").status_code == 401
            assert member.get("/api/v1/auth/me").status_code == 401
            assert display.get("/api/v1/display/feed").status_code == 401
            assert not storage.read_media(hid).exists()
            assert other.get("/api/v1/auth/me").status_code == 200
            assert other.get(f"/api/v1/photos/{kept}/media/original").status_code == 200
        finally:
            display.close()
    with SessionLocal() as db:
        assert db.get(Household, hid) is None
        assert db.get(User, owner["user"]["id"]) is not None  # Deleting a household does not delete accounts.
        for table in Base.metadata.sorted_tables:
            if "household_id" in table.c:
                assert db.scalar(select(func.count()).select_from(table).where(table.c.household_id == hid)) == 0
        assert db.get(Photo, public) is None and db.get(Photo, private) is None
        assert not list(db.scalars(select(PhotoDerivative).where(PhotoDerivative.photo_id.in_([public, private]))))
        assert not list(db.scalars(select(StorySlide).where(StorySlide.story_id == story["id"])))
        deletion = db.scalar(select(AuditLog).where(AuditLog.action == "household_deleted", AuditLog.object_id == hid))
        assert deletion is not None and deletion.household_id is None
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []


def test_deleted_household_account_can_login_and_create_a_new_home(client, owner):
    hid = owner["household"]["id"]
    uid = owner["user"]["id"]
    assert client.delete(f"/api/v1/households/{hid}", params={"confirm": owner["household"]["name"]}).status_code == 200
    response = client.post("/api/v1/auth/login", json={"username": "owner", "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["needs_household"] is True
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    account = client.get("/api/v1/auth/me").json()
    assert account["user"]["id"] == uid
    assert account["role"] == "account" and account["household"] is None
    assert client.get("/api/v1/households").json()["items"] == []
    for route in ["/photos", "/albums", "/settings", "/jobs", "/events", "/people", "/system/stats"]:
        assert client.get("/api/v1" + route).status_code == 403
    assert (
        client.post("/api/v1/households", json={"name": "新家庭"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )
    created = client.post("/api/v1/households", json={"name": "新家庭"})
    assert created.status_code == 200, created.text
    client.headers["X-CSRF-Token"] = created.json()["csrf"]
    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["id"] == uid and me["role"] == "owner"
    assert me["household"]["id"] != hid and me["household"]["name"] == "新家庭"
    assert client.get("/api/v1/photos").json()["items"] == []
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200


def test_account_without_household_still_requires_password_scope_and_csrf(client, owner):
    hid = owner["household"]["id"]
    client.delete(f"/api/v1/households/{hid}", params={"confirm": owner["household"]["name"]})
    assert client.post("/api/v1/auth/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "owner", "password": PASSWORD, "household_id": hid}
        ).status_code
        == 403
    )
    result = client.post("/api/v1/auth/login", json={"username": "owner", "password": PASSWORD})
    assert result.status_code == 200
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    client.headers["X-CSRF-Token"] = result.json()["csrf"]
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_account_without_household_can_join_invitation_without_new_account(client, owner):
    uid = owner["user"]["id"]
    hid = owner["household"]["id"]
    client.delete(f"/api/v1/households/{hid}", params={"confirm": owner["household"]["name"]})
    with TestClient(app) as other:
        new_home = setup(other, "host", True)["household"]["id"]
        shared = upload(other)["photo_id"]
        code = other.post(f"/api/v1/households/{new_home}/invites", json={"role": "member"}).json()["code"]
        login = client.post("/api/v1/auth/login", json={"username": "owner", "password": PASSWORD})
        assert login.status_code == 200
        joined = client.post(f"/api/v1/invites/{code}/join", json={"username": "owner", "password": PASSWORD})
        assert joined.status_code == 200, joined.text
        client.headers["X-CSRF-Token"] = joined.json()["csrf"]
        me = client.get("/api/v1/auth/me").json()
        assert me["user"]["id"] == uid and me["household"]["id"] == new_home and me["role"] == "member"
        assert client.get(f"/api/v1/photos/{shared}/media/960").status_code == 200


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_delete_household_rejects_non_owner(client, owner, role):
    hid = owner["household"]["id"]
    with TestClient(app) as other:
        invite(client, owner, other, role)
        response = other.delete(f"/api/v1/households/{hid}", params={"confirm": owner["household"]["name"]})
        assert response.status_code == 403
    assert client.get(f"/api/v1/households/{hid}").status_code == 200


def test_delete_household_rejects_csrf_cross_household_and_display(client, owner):
    hid = owner["household"]["id"]
    path = f"/api/v1/households/{hid}"
    params = {"confirm": owner["household"]["name"]}
    csrf = client.headers.pop("X-CSRF-Token")
    assert client.delete(path, params=params).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    with TestClient(app) as other:
        setup(other, "another", True)
        assert other.delete(path, params=params).status_code == 404
        assert other.get("/api/v1/auth/me").status_code == 200
    assert client.delete("/api/v1/households/missing", params=params).status_code == 404
    display, _ = pair(client)
    try:
        assert display.delete(path, params=params).status_code == 401
    finally:
        display.close()
    assert client.get(path).status_code == 200
