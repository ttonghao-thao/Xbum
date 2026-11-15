from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditLog
from app.db.session import SessionLocal
from app.main import app
from conftest import PASSWORD, setup, upload


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
