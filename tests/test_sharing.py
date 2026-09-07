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
    # Maya's attempt names the lesson it answers.
    pose = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})
    attempt = client.post("/v1/videos", data={"title": "Enchufla — my attempt", "pose3d": pose, "pose2d": pose,
                                              "reply_to": lesson}, headers=hdr(maya)).json()["id"]
    r = client.post(f"/v1/groups/{gid}/share", json={"video_id": attempt}, headers=hdr(maya))
    assert r.status_code == 200, r.text
    assert "Enchufla — my attempt" in _inbox_titles(teacher)
    assert _offer_titles(teacher) == []

    wall = client.get(f"/v1/groups/{gid}/wall", headers=hdr(teacher)).json()
    assert wall["group"]["mine"] is True
    assert [l["title"] for l in wall["lessons"]] == ["Enchufla"]
    assert sorted((m["handle"], m["accepted"]) for m in wall["lessons"][0]["members"]) == [("lee", False), ("mia", True)]
    # …and the wall files it under that lesson, not loose.
    assert wall["replies"] == []
    assert [(v["title"], v["by"]["handle"]) for v in wall["lessons"][0]["replies"]] == [("Enchufla — my attempt", "mia")]
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
    assert "Enchufla — my attempt" in page and "1 attempt" in page and "1/2 accepted" in page
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


def test_public_videos_shared_with_you_show_on_your_public_page_with_the_sharer():
    owner, andy = _user("teach2"), _user("andy2")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    public = _post(owner, "Open combo", "public")
    private = _post(owner, "Closed combo", "private")
    for vid in (public, private):
        client.post("/v1/grants", json={"handle": "andy2", "video_id": vid}, headers=hdr(owner))
    # Nothing shows until accepted.
    assert "Open combo" not in client.get("/@andy2").text
    _accept_all(andy)
    page = client.get("/@andy2").text
    assert "Open combo" in page and "shared by" in page and "Teach2" in page
    assert "Closed combo" not in page
    # The owner turning it private takes it off Andy's page.
    client.post(f"/v1/videos/{public}/visibility", json={"visibility": "private"}, headers=hdr(owner))
    assert "Open combo" not in client.get("/@andy2").text


def test_a_series_is_a_standing_offer_that_stays_live():
    teacher, maya, leo = _user("srsteach"), _user("srsmaya"), _user("srsleo")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    basic = _post(teacher, "Salsa basic", "private")
    cbl = _post(teacher, "Cross body lead", "private")

    # A series with two videos; a group with two students.
    r = client.post("/v1/series", json={"name": "Salsa Friday class", "video_ids": [basic, cbl]}, headers=hdr(teacher))
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["video_count"] == 2
    gid = client.post("/v1/groups", json={"name": "salsa_friday_class", "handles": ["srsmaya", "srsleo"]},
                      headers=hdr(teacher)).json()["id"]

    # Share the series with the group: one offer per student, nothing viewable yet.
    r = client.post("/v1/grants", json={"series_id": sid, "group_id": gid}, headers=hdr(teacher))
    assert r.status_code == 200, r.text
    assert sorted(g["handle"] for g in r.json()["granted"]) == ["srsleo", "srsmaya"]
    inbox = _inbox(maya)
    assert [s["name"] for s in inbox["series_offers"]] == ["Salsa Friday class"]
    assert inbox["offers"] == [] and inbox["from"] == []
    assert client.get(f"/v/{basic}", cookies={"ds_session": maya}).status_code == 404

    # Maya accepts once; both videos arrive, filed under the series.
    sgid = inbox["series_offers"][0]["series_grant_id"]
    assert client.post(f"/v1/shared/series/{sgid}/accept", headers=hdr(maya)).status_code == 200
    inbox = _inbox(maya)
    assert [s["name"] for s in inbox["series"]] == ["Salsa Friday class"]
    assert sorted(v["title"] for v in inbox["from"][0]["videos"]) == ["Cross body lead", "Salsa basic"]
    assert all(v["series"]["name"] == "Salsa Friday class" for v in inbox["from"][0]["videos"])
    assert client.get(f"/v/{basic}", cookies={"ds_session": maya}).status_code == 200
    # Leo hasn't answered: still nothing for him.
    assert _inbox_titles(leo) == []

    # Live: a video added next week reaches Maya without asking, not Leo.
    enchufla = _post(teacher, "Enchufla", "private")
    client.post(f"/v1/series/{sid}/videos", json={"video_id": enchufla}, headers=hdr(teacher))
    assert "Enchufla" in _inbox_titles(maya)
    assert client.get(f"/v/{enchufla}", cookies={"ds_session": maya}).status_code == 200
    assert _inbox_titles(leo) == []

    # The wall groups series → video → attempts.
    pose = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})
    attempt = client.post("/v1/videos", data={"title": "CBL — my attempt", "pose3d": pose, "pose2d": pose,
                                              "reply_to": cbl}, headers=hdr(maya)).json()["id"]
    client.post(f"/v1/groups/{gid}/share", json={"video_id": attempt}, headers=hdr(maya))
    wall = client.get(f"/v1/groups/{gid}/wall", headers=hdr(teacher)).json()
    assert [s["name"] for s in wall["series"]] == ["Salsa Friday class"]
    titles = {v["title"]: v for v in wall["series"][0]["videos"]}
    assert set(titles) == {"Salsa basic", "Cross body lead", "Enchufla"}
    assert [r["title"] for r in titles["Cross body lead"]["replies"]] == ["CBL — my attempt"]
    assert wall["lessons"] == [] and wall["replies"] == []

    # The owner's ledger shows the series share; revoking it takes every video back.
    ledger = client.get("/v1/grants", headers=hdr(teacher)).json()
    assert [(s["handle"], s["accepted"]) for s in sorted(ledger["series_grants"], key=lambda s: s["handle"])] == [("srsleo", False), ("srsmaya", True)]
    assert all(g["series"] for g in ledger["grants"] if g["handle"] == "srsmaya")
    maya_sg = next(s["id"] for s in ledger["series_grants"] if s["handle"] == "srsmaya")
    assert client.delete(f"/v1/series/grants/{maya_sg}", headers=hdr(teacher)).status_code == 200
    assert _inbox_titles(maya) == [] and _inbox(maya)["series"] == []
    assert client.get(f"/v/{basic}", cookies={"ds_session": maya}).status_code == 404

    # Leo declines; a stranger can't touch any of it; deleting the series ends the rest.
    leo_sg = _inbox(leo)["series_offers"][0]["series_grant_id"]
    assert client.delete(f"/v1/shared/series/{leo_sg}", headers=hdr(maya)).status_code == 404
    assert client.delete(f"/v1/shared/series/{leo_sg}", headers=hdr(leo)).status_code == 200
    assert _inbox(leo)["series_offers"] == []
    assert client.delete(f"/v1/series/{sid}", headers=hdr(teacher)).status_code == 200
    assert client.get("/v1/series", headers=hdr(teacher)).json()["series"] == []
    # …and the videos themselves are untouched.
    assert len(client.get("/v1/me", headers=hdr(teacher)).json()["videos"]) == 3


def test_series_on_the_pages():
    teacher, maya = _user("pgteach"), _user("pgmaya")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    basic = _post(teacher, "Salsa basic", "private")
    sid = client.post("/v1/series", json={"name": "Friday", "video_ids": [basic]}, headers=hdr(teacher)).json()["id"]
    client.post("/v1/grants", json={"series_id": sid, "handle": "pgmaya"}, headers=hdr(teacher))
    page = client.get("/me", cookies={"ds_session": teacher}).text
    assert 'data-series="%d"' % sid in page and "Share series" in page and "Add to series" in page
    page = client.get("/me", cookies={"ds_session": maya}).text
    assert "shared the series “Friday” with you" in page
    for s_ in client.get("/v1/shared", headers=hdr(maya)).json()["series_offers"]:
        client.post(f"/v1/shared/series/{s_['series_grant_id']}/accept", headers=hdr(maya))
    page = client.get("/me", cookies={"ds_session": maya}).text
    assert "Series shared with you" in page and "series “Friday”" in page


def test_an_attempt_goes_back_to_its_teacher_and_nowhere_else():
    teacher, maya, friend = _user("bossy"), _user("mayaa"), _user("frienda")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    lesson = _post(teacher, "Sombrero", "private")
    client.post("/v1/grants", json={"handle": "mayaa", "video_id": lesson}, headers=hdr(teacher))
    _accept_all(maya)
    pose = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})
    attempt = client.post("/v1/videos", data={"title": "Sombrero — my attempt", "pose3d": pose, "pose2d": pose,
                                              "reply_to": lesson}, headers=hdr(maya)).json()["id"]
    # To the teacher: fine. To a friend, or into her own series: no.
    assert client.post("/v1/grants", json={"handle": "bossy", "video_id": attempt}, headers=hdr(maya)).status_code == 200
    assert client.post("/v1/grants", json={"handle": "frienda", "video_id": attempt}, headers=hdr(maya)).status_code == 400
    sid = client.post("/v1/series", json={"name": "Mine"}, headers=hdr(maya)).json()["id"]
    assert client.post(f"/v1/series/{sid}/videos", json={"video_id": attempt}, headers=hdr(maya)).status_code == 400
    # Her own clean video: hers to do anything with.
    own = _post(maya, "My own sombrero", "private")
    assert client.post(f"/v1/series/{sid}/videos", json={"video_id": own}, headers=hdr(maya)).status_code == 200
    assert client.post("/v1/grants", json={"handle": "frienda", "video_id": own}, headers=hdr(maya)).status_code == 200


def test_attempts_live_under_my_lessons_not_on_the_profile():
    teacher, maya = _user("lteach"), _user("lmaya")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    lesson = _post(teacher, "Basic", "private")
    client.post("/v1/grants", json={"handle": "lmaya", "video_id": lesson}, headers=hdr(teacher))
    _accept_all(maya)
    pose = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})
    r = client.post("/v1/videos", data={"title": "Basic — try 1", "pose3d": pose, "pose2d": pose,
                                        "reply_to": lesson, "visibility": "public"}, headers=hdr(maya))
    attempt = r.json()["id"]
    # Private no matter what was asked; not a post; not on the public page.
    assert r.json()["visibility"] == "private"
    assert client.get("/v1/me", headers=hdr(maya)).json()["videos"] == []
    assert "Basic — try 1" not in client.get("/@lmaya").text
    assert "Basic — try 1" not in client.get("/me", cookies={"ds_session": maya}).text.split("<body", 1)[1].split("Shared with you")[0]
    assert client.post(f"/v1/videos/{attempt}/visibility", json={"visibility": "public"}, headers=hdr(maya)).status_code == 400

    # It is under My lessons, unsent; the teacher has nothing yet.
    mine = client.get("/v1/lessons", headers=hdr(maya)).json()["lessons"]
    assert [l["lesson"]["title"] for l in mine] == ["Basic"]
    assert [(a["title"], a["sent"]) for a in mine[0]["attempts"]] == [("Basic — try 1", False)]
    assert _inbox_titles(teacher) == []
    page = client.get("/lessons", cookies={"ds_session": maya}).text
    assert "Basic — try 1" in page and "Send to Lteach" in page

    # Sent: the teacher has it at once, no offer; the page says so.
    assert client.post(f"/v1/lessons/{attempt}/send", headers=hdr(maya)).status_code == 200
    assert _inbox_titles(teacher) == ["Basic — try 1"] and _offer_titles(teacher) == []
    assert client.get("/v1/lessons", headers=hdr(maya)).json()["lessons"][0]["attempts"][0]["sent"] is True
    assert "sent to Lteach" in client.get("/lessons", cookies={"ds_session": maya}).text
    # Nobody else's attempt can be sent by her.
    assert client.post(f"/v1/lessons/{lesson}/send", headers=hdr(maya)).status_code == 404


def test_a_ta_chooses_where_attempts_go():
    boss, ta, zoe = _user("bigboss"), _user("ta"), _user("zoe2")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    video = _post(boss, "Cross body lead", "public")
    pose = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})

    # Default: attempts come to the TA who passed it on.
    client.post("/v1/grants", json={"handle": "zoe2", "video_id": video}, headers=hdr(ta))
    _accept_all(zoe)
    assert client.get("/v1/lessons", headers=hdr(zoe)).json() == {"lessons": []}
    a1 = client.post("/v1/videos", data={"title": "try 1", "pose3d": pose, "pose2d": pose, "reply_to": video}, headers=hdr(zoe)).json()["id"]
    assert client.get("/v1/lessons", headers=hdr(zoe)).json()["lessons"][0]["teacher"]["handle"] == "ta"
    assert client.post(f"/v1/lessons/{a1}/send", headers=hdr(zoe)).json()["to"] == "ta"
    assert _inbox_titles(ta) == ["try 1"] and _inbox_titles(boss) == []

    # The TA re-shares choosing the owner; now attempts go to the boss.
    client.delete(f"/v1/grants/{_inbox(zoe)['from'][0]['videos'][0]['grant_id']}", headers=hdr(ta))
    client.post("/v1/grants", json={"handle": "zoe2", "video_id": video, "replies_to": "owner"}, headers=hdr(ta))
    _accept_all(zoe)
    a2 = client.post("/v1/videos", data={"title": "try 2", "pose3d": pose, "pose2d": pose, "reply_to": video}, headers=hdr(zoe)).json()["id"]
    assert client.get("/v1/lessons", headers=hdr(zoe)).json()["lessons"][0]["teacher"]["handle"] == "bigboss"
    assert client.post(f"/v1/lessons/{a2}/send", headers=hdr(zoe)).json()["to"] == "bigboss"
    assert _inbox_titles(boss) == ["try 2"]
    # And Zoe can't route an attempt anywhere else herself.
    assert client.post("/v1/grants", json={"handle": "ta", "video_id": a2}, headers=hdr(zoe)).status_code == 400
    assert client.post("/v1/grants", json={"handle": "zoe2", "video_id": video, "replies_to": "nobody"}, headers=hdr(ta)).status_code == 400


def test_a_lesson_exists_from_add_to_lessons_and_can_be_deleted_whole():
    teacher, maya = _user("lt2"), _user("lm2")
    hdr = lambda t: {"Authorization": f"Bearer {t}"}
    basic = _post(teacher, "Basic", "private")
    sid = client.post("/v1/series", json={"name": "Friday", "video_ids": [basic]}, headers=hdr(teacher)).json()["id"]
    client.post("/v1/grants", json={"series_id": sid, "handle": "lm2"}, headers=hdr(teacher))
    for s_ in _inbox(maya)["series_offers"]:
        client.post(f"/v1/shared/series/{s_['series_grant_id']}/accept", headers=hdr(maya))

    # Add to Lessons: it's there at once, empty, filed under the series it came by.
    r = client.post("/v1/lessons", json={"video_id": basic, "name": "Salsa basic"}, headers=hdr(maya))
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    mine = client.get("/v1/lessons", headers=hdr(maya)).json()["lessons"]
    assert [(l["name"], l["series"]["name"], l["attempts"]) for l in mine] == [("Salsa basic", "Friday", [])]
    # Twice is once.
    assert client.post("/v1/lessons", json={"video_id": basic}, headers=hdr(maya)).json()["id"] == lid
    page = client.get("/lessons", cookies={"ds_session": maya}).text
    assert "Friday" in page and "Salsa basic" in page and "No attempts yet" in page

    # Two attempts; delete one; the other stays.
    pose = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})
    ids = [client.post("/v1/videos", data={"title": f"try {i}", "pose3d": pose, "pose2d": pose, "reply_to": basic},
                       headers=hdr(maya)).json()["id"] for i in (1, 2)]
    client.post(f"/v1/lessons/{ids[0]}/send", headers=hdr(maya))
    assert _inbox_titles(teacher) == ["try 1"]
    assert client.delete(f"/v1/lessons/attempts/{ids[0]}", headers=hdr(maya)).status_code == 200
    assert _inbox_titles(teacher) == []
    assert [a["title"] for a in client.get("/v1/lessons", headers=hdr(maya)).json()["lessons"][0]["attempts"]] == ["try 2"]
    # Someone else can't delete her attempt or her lesson.
    assert client.delete(f"/v1/lessons/attempts/{ids[1]}", headers=hdr(teacher)).status_code == 404
    assert client.delete(f"/v1/lessons/{lid}", headers=hdr(teacher)).status_code == 404
    # Delete the lesson: attempts go with it; the teacher's video is untouched.
    assert client.delete(f"/v1/lessons/{lid}", headers=hdr(maya)).json()["attempts_deleted"] == 1
    assert client.get("/v1/lessons", headers=hdr(maya)).json()["lessons"] == []
    assert client.get(f"/v/{basic}", cookies={"ds_session": teacher}).status_code == 200
