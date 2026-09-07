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


def _inbox(token: str) -> dict:
    return client.get("/v1/shared", headers={"Authorization": f"Bearer {token}"}).json()


def _inbox_titles(token: str) -> list[str]:
    return [v["title"] for who in _inbox(token)["from"] for v in who["videos"]]


def _offer_titles(token: str) -> list[str]:
    return [v["title"] for who in _inbox(token)["offers"] for v in who["videos"]]


def _accept_all(token: str) -> None:
    for who in _inbox(token)["offers"]:
        for v in who["videos"]:
            r = client.post(f"/v1/shared/{v['grant_id']}/accept",
                            headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200, r.text


def test_a_public_video_shared_by_grant_still_reaches_the_inbox():
    owner, viewer = _user("abdu"), _user("andy")
    public = _post(owner, "Social dancing", "public")
    private = _post(owner, "Rehearsal", "private")
    hidden = _post(owner, "Not for you", "private")

    for vid in (public, private):
        r = client.post("/v1/grants", json={"handle": "@andy", "video_id": vid},
                        headers={"Authorization": f"Bearer {owner}"})
        assert r.status_code == 200, r.text

    # Both arrive as offers — the public one was the bug — and nothing else;
    # nothing is in the inbox, or viewable, until accepted.
    assert sorted(_offer_titles(viewer)) == ["Rehearsal", "Social dancing"]
    assert _inbox_titles(viewer) == []
    assert client.get(f"/v/{private}", cookies={"ds_session": viewer}).status_code == 404
    _accept_all(viewer)
    assert sorted(_inbox_titles(viewer)) == ["Rehearsal", "Social dancing"]
    assert client.get(f"/v/{private}", cookies={"ds_session": viewer}).status_code == 200

    # The web page draws from the same list.
    page = client.get("/me", cookies={"ds_session": viewer}).text
    # In the body, after the page's own content — not smuggled into <title>.
    body = page.split("<body", 1)[1]
    assert body.index('<div class="grid">') < body.index("Shared with you")
    assert "Social dancing" in body and "Rehearsal" in body and "Not for you" not in body

    # Private or public, what was shared stays shared; only revoking ends it.
    client.post(f"/v1/videos/{public}/visibility", json={"visibility": "private"},
                headers={"Authorization": f"Bearer {owner}"})
    assert sorted(_inbox_titles(viewer)) == ["Rehearsal", "Social dancing"]
    assert client.get(f"/v/{public}", cookies={"ds_session": viewer}).status_code == 200
    # …and nobody else sees a private clip.
    assert client.get(f"/v/{public}", cookies={"ds_session": _user("passerby")}).status_code == 404


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

    # One share, three grants — and the clip stays private to everyone else.
    r = client.post("/v1/grants", json={"group_id": gid, "video_id": clip},
                    headers={"Authorization": f"Bearer {teacher}"})
    assert r.status_code == 200, r.text
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {teacher}"}).json()["videos"][0]["visibility"] == "private"
    assert sorted(g["handle"] for g in r.json()["granted"]) == ["leo", "maya", "zoe"]
    for who in (maya, leo, zoe):
        assert _offer_titles(who) == ["Cross body lead"]
        _accept_all(who)
        assert _inbox_titles(who) == ["Cross body lead"]

    # Two clips from one teacher arrive as one sender, not one entry per grant.
    second = _post(teacher, "Copa", "private")
    client.post("/v1/grants", json={"group_id": gid, "video_id": second},
                headers={"Authorization": f"Bearer {teacher}"})
    for who in (maya, leo, zoe):
        _accept_all(who)
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


def test_the_whole_life_of_a_share():
    """Offer → accept → stop, from either end; sharing again is a fresh offer."""
    owner, viewer = _user("sender"), _user("receiver")
    clip = _post(owner, "Basic step", "private")
    hdr_o = {"Authorization": f"Bearer {owner}"}
    hdr_v = {"Authorization": f"Bearer {viewer}"}

    # An offer: the sender's ledger says so, the receiver can't watch yet.
    gid = client.post("/v1/grants", json={"handle": "receiver", "video_id": clip}, headers=hdr_o).json()["id"]
    ledger = client.get("/v1/grants", headers=hdr_o).json()["grants"]
    assert ledger[0]["accepted"] is False
    assert client.get(f"/v/{clip}", cookies={"ds_session": viewer}).status_code == 404
    # Nobody else can answer it.
    assert client.post(f"/v1/shared/{gid}/accept", headers=hdr_o).status_code == 404

    # Declined: gone for both.
    assert client.delete(f"/v1/shared/{gid}", headers=hdr_v).status_code == 200
    assert _offer_titles(viewer) == [] and _inbox_titles(viewer) == []
    assert client.get("/v1/grants", headers=hdr_o).json()["grants"] == []

    # Shared again: a fresh offer on the same row, not a silent re-open.
    gid2 = client.post("/v1/grants", json={"handle": "receiver", "video_id": clip}, headers=hdr_o).json()["id"]
    assert gid2 == gid
    assert _offer_titles(viewer) == ["Basic step"]
    assert client.post(f"/v1/shared/{gid}/accept", headers=hdr_v).status_code == 200
    assert _inbox_titles(viewer) == ["Basic step"]
    assert client.get("/v1/grants", headers=hdr_o).json()["grants"][0]["accepted"] is True
    assert client.get(f"/v/{clip}", cookies={"ds_session": viewer}).status_code == 200

    # The receiver stops it.
    assert client.delete(f"/v1/shared/{gid}", headers=hdr_v).status_code == 200
    assert client.get(f"/v/{clip}", cookies={"ds_session": viewer}).status_code == 404

    # Offered and accepted once more; this time the sender revokes.
    client.post("/v1/grants", json={"handle": "receiver", "video_id": clip}, headers=hdr_o)
    client.post(f"/v1/shared/{gid}/accept", headers=hdr_v)
    assert _inbox_titles(viewer) == ["Basic step"]
    assert client.delete(f"/v1/grants/{gid}", headers=hdr_o).status_code == 200
    assert _inbox_titles(viewer) == [] and _offer_titles(viewer) == []
    assert client.get(f"/v/{clip}", cookies={"ds_session": viewer}).status_code == 404

    # The page shows offers and accepted shares in their own places.
    client.post("/v1/grants", json={"handle": "receiver", "video_id": clip}, headers=hdr_o)
    body = client.get("/me", cookies={"ds_session": viewer}).text.split("<body", 1)[1]
    assert "Sender shared a video with you" in body and "Basic step" in body
    assert "Shared with you</h2>" not in body
    client.post(f"/v1/shared/{gid}/accept", headers=hdr_v)
    body = client.get("/me", cookies={"ds_session": viewer}).text.split("<body", 1)[1]
    assert "Shared with you</h2>" in body and "shared a video with you" not in body


def test_upgrade_keeps_what_people_already_had():
    """A grants table from before offers existed gets the column, and every
    share on it counts as accepted — nobody loses access on deploy."""
    from sqlalchemy import text
    from dsplatform.db import engine
    from dsplatform.main import _migrate_grant_offers
    owner, viewer = _user("olduser"), _user("oldviewer")
    clip = _post(owner, "Old share", "private")
    gid = client.post("/v1/grants", json={"handle": "oldviewer", "video_id": clip},
                      headers={"Authorization": f"Bearer {owner}"}).json()["id"]
    with engine.begin() as conn:
        # Rebuild the table as it was before offers: same schema, no accepted_at.
        conn.execute(text("""CREATE TABLE grants_old (
            id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, viewer_id INTEGER NOT NULL,
            video_id INTEGER, created_at DATETIME, revoked_at DATETIME)"""))
        conn.execute(text("INSERT INTO grants_old SELECT id, owner_id, viewer_id, video_id, created_at, revoked_at FROM grants"))
        conn.execute(text("DROP TABLE grants"))
        conn.execute(text("ALTER TABLE grants_old RENAME TO grants"))
    engine.dispose()   # nothing cached from the table that no longer exists
    _migrate_grant_offers()
    assert _inbox_titles(viewer) == ["Old share"]
    assert client.get("/v1/grants", headers={"Authorization": f"Bearer {owner}"}).json()["grants"][0]["accepted"] is True
    # and a new share after the upgrade is an offer, as it should be
    clip2 = _post(owner, "New share", "private")
    client.post("/v1/grants", json={"handle": "oldviewer", "video_id": clip2},
                headers={"Authorization": f"Bearer {owner}"})
    assert _offer_titles(viewer) == ["New share"]
    assert _inbox_titles(viewer) == ["Old share"]


def test_the_video_menu_lives_on_the_video_page_too_and_stats_count_shared():
    owner, viewer = _user("host"), _user("guest")
    clip = _post(owner, "Sombrero", "private")
    client.post("/v1/grants", json={"handle": "guest", "video_id": clip},
                headers={"Authorization": f"Bearer {owner}"})
    _accept_all(viewer)

    # The owner's page counts it as shared, not private; the card carries the
    # chip list that the menu paints.
    page = client.get("/me", cookies={"ds_session": owner}).text
    body = page.split("<body", 1)[1]
    assert "<span>shared</span>" in body and "<span>private</span>" in body
    assert body.split("<script", 1)[0].count("share-who-card") == 1
    # the offer reads like a notification for the receiver, and the nav counts it
    clip2 = _post(owner, "Copa", "private")
    client.post("/v1/grants", json={"handle": "guest", "video_id": clip2},
                headers={"Authorization": f"Bearer {owner}"})
    guest = client.get("/me", cookies={"ds_session": viewer}).text
    assert "Host shared a video with you" in guest and "1 to accept" in guest

    # On the clip's own page the owner gets the menu; the viewer does not.
    assert 'class="vmenu"' in client.get(f"/v/{clip}", cookies={"ds_session": owner}).text
    assert 'class="vmenu"' not in client.get(f"/v/{clip}", cookies={"ds_session": viewer}).text


def test_a_group_has_a_wall_and_members_share_back():
    teacher, maya, leo, other = _user("prof"), _user("mia"), _user("lee"), _user("bystander")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    gid = client.post("/v1/groups", json={"name": "Thursday salsa", "handles": ["mia", "lee"]},
                      headers=hdr(teacher)).json()["id"]
    lesson = _post(teacher, "Enchufla", "private")
    client.post("/v1/grants", json={"group_id": gid, "video_id": lesson}, headers=hdr(teacher))

    # Members know they're in it, and the offer says which group it came through.
    assert [g["name"] for g in client.get("/v1/groups", headers=hdr(maya)).json()["member_of"]] == ["Thursday salsa"]
    offer = _inbox(maya)["offers"][0]["videos"][0]
    assert offer["group"]["name"] == "Thursday salsa"
    _accept_all(maya)

    # Maya shares an attempt back: it reaches the teacher at once, filed under the group.
    attempt = _post(maya, "Enchufla — my attempt", "private")
    r = client.post(f"/v1/groups/{gid}/share", json={"video_id": attempt}, headers=hdr(maya))
    assert r.status_code == 200, r.text
    assert "Enchufla — my attempt" in _inbox_titles(teacher)
    assert _offer_titles(teacher) == []

    wall = client.get(f"/v1/groups/{gid}/wall", headers=hdr(teacher)).json()
    assert wall["group"]["mine"] is True
    assert [l["title"] for l in wall["lessons"]] == ["Enchufla"]
    assert sorted((m["handle"], m["accepted"]) for m in wall["lessons"][0]["members"]) == [("lee", False), ("mia", True)]
    assert [(v["title"], v["by"]["handle"]) for v in wall["replies"]] == [("Enchufla — my attempt", "mia")]
    assert client.get(f"/v/{attempt}", cookies={"ds_session": teacher}).status_code == 200

    # Leo sees the lesson and his own (empty) replies, not Maya's; a stranger sees nothing.
    leo_wall = client.get(f"/v1/groups/{gid}/wall", headers=hdr(leo)).json()
    assert leo_wall["group"]["mine"] is False and leo_wall["replies"] == []
    assert [l["title"] for l in leo_wall["lessons"]] == ["Enchufla"]
    assert client.get(f"/v1/groups/{gid}/wall", headers=hdr(other)).status_code == 404
    assert client.post(f"/v1/groups/{gid}/share", json={"video_id": lesson}, headers=hdr(teacher)).status_code == 400
    assert client.post(f"/v1/groups/{gid}/share", json={"video_id": lesson}, headers=hdr(leo)).status_code == 404

    # The wall page: the teacher sees the reply; Leo sees the lesson and a way to share back.
    page = client.get(f"/g/{gid}", cookies={"ds_session": teacher}).text
    assert "Enchufla — my attempt" in page and "Shared back by members" in page
    page = client.get(f"/g/{gid}", cookies={"ds_session": leo}).text
    assert "Share back" in page and "Enchufla — my attempt" not in page
    assert client.get(f"/g/{gid}", cookies={"ds_session": other}).status_code == 404
    assert "Thursday salsa" in client.get("/me", cookies={"ds_session": leo}).text


def test_passing_on_a_video_you_were_shown():
    owner, andy, zoe = _user("maker"), _user("andyy"), _user("zoey")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    public = _post(owner, "Open class", "public")
    private = _post(owner, "Closed class", "private")
    client.post("/v1/grants", json={"handle": "andyy", "video_id": private}, headers=hdr(owner))
    _accept_all(andy)

    # A private clip shared with you is not yours to pass on — no button, no route.
    assert client.post("/v1/grants", json={"handle": "zoey", "video_id": private}, headers=hdr(andy)).status_code == 404
    assert 'class="vmenu"' not in client.get(f"/v/{private}", cookies={"ds_session": andy}).text

    # A public clip you can pass on: Zoe gets the offer from Andy, and can open it.
    page = client.get(f"/v/{public}", cookies={"ds_session": andy}).text
    assert 'class="vmenu"' in page and "Share with a dancer or a group" in page and 'class="vopt"' not in page
    assert client.post("/v1/grants", json={"handle": "zoey", "video_id": public}, headers=hdr(andy)).status_code == 200
    assert _inbox(zoe)["offers"][0]["handle"] == "andyy"
    _accept_all(zoe)
    assert _inbox_titles(zoe) == ["Open class"]
    # …until the owner makes it private again.
    client.post(f"/v1/videos/{public}/visibility", json={"visibility": "private"}, headers=hdr(owner))
    assert _inbox_titles(zoe) == []
    assert client.get(f"/v/{public}", cookies={"ds_session": zoe}).status_code == 404
