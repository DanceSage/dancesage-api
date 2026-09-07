"""What a grant lets you see shows up where you look for it."""
import json
import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["STORAGE_BACKEND"] = "local"

from fastapi.testclient import TestClient  # noqa: E402

from dsplatform.main import app  # noqa: E402
from dsplatform.db import SessionLocal  # noqa: E402
from dsplatform.models import User  # noqa: E402
from dsplatform.auth import issue_session  # noqa: E402

client = TestClient(app)


def _user(handle: str) -> str:
    db = SessionLocal()
    try:
        u = User(handle=handle, display_name=handle.title(), auth_uid=f"uid-{handle}")
        db.add(u); db.commit(); db.refresh(u)
        return issue_session(u)
    finally:
        db.close()


def _post(token: str, title: str, visibility: str) -> int:
    pose = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})
    r = client.post("/v1/videos", data={"title": title, "pose3d": pose, "pose2d": pose,
                                        "visibility": visibility},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _inbox_titles(token: str) -> list[str]:
    r = client.get("/v1/shared", headers={"Authorization": f"Bearer {token}"})
    return [v["title"] for who in r.json()["from"] for v in who["videos"]]


def test_a_public_video_shared_by_grant_still_reaches_the_inbox():
    owner, viewer = _user("abdu"), _user("andy")
    public = _post(owner, "Social dancing", "public")
    private = _post(owner, "Rehearsal", "private")
    hidden = _post(owner, "Not for you", "private")

    for vid in (public, private):
        r = client.post("/v1/grants", json={"handle": "@andy", "video_id": vid},
                        headers={"Authorization": f"Bearer {owner}"})
        assert r.status_code == 200, r.text

    # Both granted clips arrive — the public one was the bug — and nothing else.
    assert sorted(_inbox_titles(viewer)) == ["Rehearsal", "Social dancing"]

    # The web page draws from the same list.
    page = client.get("/me", cookies={"ds_session": viewer}).text
    # In the body, after the page's own content — not smuggled into <title>.
    body = page.split("<body", 1)[1]
    assert body.index("Who can see what") < body.index("Shared with you")
    assert "Social dancing" in body and "Rehearsal" in body and "Not for you" not in body

    # Making a shared clip private again takes it out of the inbox.
    client.post(f"/v1/videos/{private}/visibility", json={"visibility": "private"},
                headers={"Authorization": f"Bearer {owner}"})
    assert _inbox_titles(viewer) == ["Social dancing"]


def test_groups_share_with_everyone_at_once_and_anyone_can_decline():
    teacher = _user("teach")
    maya, leo, zoe = _user("maya"), _user("leo"), _user("zoe")
    clip = _post(teacher, "Cross body lead", "private")

    # A class, named, with two members; a third joins later; a stranger can't be added.
    r = client.post("/v1/groups", json={"name": "Tuesday bachata", "handles": ["@maya", "leo"]},
                    headers={"Authorization": f"Bearer {teacher}"})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    assert [m["handle"] for m in r.json()["members"]] == ["maya", "leo"]
    assert client.post(f"/v1/groups/{gid}/members", json={"handle": "zoe"},
                       headers={"Authorization": f"Bearer {teacher}"}).status_code == 200
    assert client.post(f"/v1/groups/{gid}/members", json={"handle": "ghost"},
                       headers={"Authorization": f"Bearer {teacher}"}).status_code == 404
    assert client.get("/v1/groups", headers={"Authorization": f"Bearer {maya}"}).json()["groups"] == []

    # One share, three grants — and the clip is marked Shared.
    r = client.post("/v1/grants", json={"group_id": gid, "video_id": clip},
                    headers={"Authorization": f"Bearer {teacher}"})
    assert r.status_code == 200, r.text
    assert sorted(g["handle"] for g in r.json()["granted"]) == ["leo", "maya", "zoe"]
    for who in (maya, leo, zoe):
        assert _inbox_titles(who) == ["Cross body lead"]

    # Two clips from one teacher arrive as one sender, not one entry per grant.
    second = _post(teacher, "Copa", "private")
    client.post("/v1/grants", json={"group_id": gid, "video_id": second},
                headers={"Authorization": f"Bearer {teacher}"})
    inbox = client.get("/v1/shared", headers={"Authorization": f"Bearer {maya}"}).json()["from"]
    assert len(inbox) == 1 and inbox[0]["handle"] == "teach"
    assert sorted(v["title"] for v in inbox[0]["videos"]) == ["Copa", "Cross body lead"]
    assert all("grant_id" in v for v in inbox[0]["videos"])

    # Maya declines one; it leaves her inbox and the teacher's ledger. Leo keeps his.
    declined = next(v for v in inbox[0]["videos"] if v["title"] == "Copa")["grant_id"]
    assert client.delete(f"/v1/shared/{declined}", headers={"Authorization": f"Bearer {leo}"}).status_code == 404
    assert client.delete(f"/v1/shared/{declined}", headers={"Authorization": f"Bearer {maya}"}).status_code == 200
    assert _inbox_titles(maya) == ["Cross body lead"]
    assert sorted(_inbox_titles(leo)) == ["Copa", "Cross body lead"]
    ledger = client.get("/v1/grants", headers={"Authorization": f"Bearer {teacher}"}).json()["grants"]
    assert not any(g["handle"] == "maya" and g["scope"] == "Copa" for g in ledger)

    # Leaving the group takes nothing back; deleting it neither.
    client.delete(f"/v1/groups/{gid}/members/zoe", headers={"Authorization": f"Bearer {teacher}"})
    assert sorted(_inbox_titles(zoe)) == ["Copa", "Cross body lead"]
    assert client.delete(f"/v1/groups/{gid}", headers={"Authorization": f"Bearer {teacher}"}).status_code == 200
    assert sorted(_inbox_titles(zoe)) == ["Copa", "Cross body lead"]
