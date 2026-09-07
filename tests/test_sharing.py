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
