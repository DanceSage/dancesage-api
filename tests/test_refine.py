"""Refine: a body queued, claimed by a worker, delivered, and read back under the
video's own access rule."""
import io
import json
import os
import pathlib
import tempfile

_TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["STORAGE_BACKEND"] = "local"
os.environ["REFINE_WORKER_TOKEN"] = "worker-secret"

from fastapi.testclient import TestClient  # noqa: E402

from dsplatform.main import app  # noqa: E402
from dsplatform.db import SessionLocal  # noqa: E402
from dsplatform.models import User  # noqa: E402
from dsplatform.auth import issue_session  # noqa: E402

client = TestClient(app)
POSE = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]]})
COUPLE = json.dumps({"j": [[[[0.1 * j, 0.2 * j, 0.0] for j in range(33)] for _ in range(4)]] * 2})


def _user(handle, plan="free"):
    db = SessionLocal()
    u = User(handle=handle, display_name=handle.title(), auth_uid=f"uid-{handle}", plan=plan)
    db.add(u); db.commit(); db.refresh(u)
    tok = issue_session(u); db.close()
    return tok


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _post_video(tok, title, with_video=True, couple=False):
    files = {"video": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 64, "video/mp4")} if with_video else None
    pose = COUPLE if couple else POSE
    r = client.post("/v1/videos", data={"title": title, "pose3d": pose, "pose2d": pose, "visibility": "private"},
                    files=files, headers=hdr(tok))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_refine_goes_through_the_queue_to_the_worker_and_back():
    maya = _user("refmaya", plan="pro")
    vid = _post_video(maya, "Basic")

    # a skeleton-only post cannot be refined
    bare = _post_video(maya, "Bare", with_video=False)
    assert client.post(f"/v1/videos/{bare}/refine", json={"tier": "refined"}, headers=hdr(maya)).status_code == 400

    # queued; asking again returns the same job
    r = client.post(f"/v1/videos/{vid}/refine", json={"tier": "3d"}, headers=hdr(maya))
    assert r.status_code == 200 and r.json()["status"] == "queued"
    job_id = r.json()["id"]
    assert client.post(f"/v1/videos/{vid}/refine", json={"tier": "3d"}, headers=hdr(maya)).json()["id"] == job_id
    assert client.get(f"/v1/videos/{vid}/body", headers=hdr(maya)).json() == {
        "summary": {"3d": {"id": job_id, "status": "queued", "fps": 0, "dancers": 0, "has_mesh": False, "has_turntable": False, "pose_key": ""}},
        "track": None}
    # the card says so too
    me = client.get("/v1/me", headers=hdr(maya)).json()
    assert [v["body"] for v in me["videos"] if v["id"] == vid][0]["3d"]["status"] == "queued"

    # only a worker may claim
    assert client.get("/v1/refine/next").status_code == 401
    w = {"X-Worker-Token": "worker-secret"}
    job = client.get("/v1/refine/next?worker=pod-1", headers=w).json()["job"]
    assert job["id"] == job_id and job["video_id"] == vid and job["tier"] == "3d" and job["video_url"].startswith("http")
    assert client.get("/v1/refine/next", headers=w).json()["job"] is None
    assert client.get("/v1/refine/queue", headers=w).json() == {"queued": 0, "running": 1}

    # the worker delivers
    r = client.post(f"/v1/refine/{job_id}/result", headers=w,
                    data={"engine": "sam-body4d", "fps": 10, "dancers": 2, "frames": 38, "seconds": 148},
                    files={"joints": ("joints.json", json.dumps({"people": []}).encode(), "application/json"),
                           "meta": ("meta.json", b"{}", "application/json"),
                           "mesh": ("mesh.bin", b"\x00" * 16, "application/octet-stream"),
                           "turntable": ("turntable.mp4", b"\x00" * 16, "video/mp4")})
    assert r.status_code == 200, r.text
    body = client.get(f"/v1/videos/{vid}/body", headers=hdr(maya)).json()
    assert body["track"]["tier"] == "3d" and body["track"]["engine"] == "sam-body4d"
    assert set(body["track"]["files"]) == {"joints", "meta", "mesh", "turntable"}
    assert client.get(f"/body/{job_id}/joints.json", headers=hdr(maya)).json() == {"people": []}
    assert body["track"]["pose_key"] == ""                      # nothing to convert from an empty file

    # a stranger sees neither the body nor its files: the post is private
    leo = _user("refleo")
    assert client.get(f"/v1/videos/{vid}/body", headers=hdr(leo)).status_code == 404
    assert client.get(f"/body/{job_id}/mesh.bin", headers=hdr(leo)).status_code == 404
    assert client.get(f"/body/{job_id}/mesh.bin").status_code == 404


def test_the_3d_tier_needs_the_paid_plan_and_refined_does_not():
    andy = _user("refandy")
    solo = _post_video(andy, "Shine")
    assert client.post(f"/v1/videos/{solo}/refine", json={"tier": "3d"}, headers=hdr(andy)).status_code == 402
    # the light skeleton is free and works on a solo dancer too
    assert client.post(f"/v1/videos/{solo}/refine", json={"tier": "refined"}, headers=hdr(andy)).json()["status"] == "queued"
    vid = _post_video(andy, "Cross body lead", couple=True)
    assert client.post(f"/v1/videos/{vid}/refine", json={"tier": "refined"}, headers=hdr(andy)).json()["status"] == "queued"
    # a failed job is reported, and the tier can be asked for again
    w = {"X-Worker-Token": "worker-secret"}
    job = client.get("/v1/refine/next", headers=w).json()["job"]
    while job and job["video_id"] != vid:                       # the solo job queued above goes first
        job = client.get("/v1/refine/next", headers=w).json()["job"]
    assert client.post(f"/v1/refine/{job['id']}/fail", json={"error": "out of memory"}, headers=w).status_code == 200
    assert client.get(f"/v1/videos/{vid}/body", headers=hdr(andy)).json()["summary"]["refined"]["status"] == "failed"
    assert client.post(f"/v1/videos/{vid}/refine", json={"tier": "refined"}, headers=hdr(andy)).json()["status"] == "queued"


def test_the_video_page_and_the_viewer_page_show_the_body():
    zoe = _user("refzoe", plan="pro")
    vid = _post_video(zoe, "Turn")
    w = {"X-Worker-Token": "worker-secret"}
    job = client.post(f"/v1/videos/{vid}/refine", json={"tier": "3d"}, headers=hdr(zoe)).json()["id"]
    # the queue is shared with earlier tests: claim until this job comes up
    while (j := client.get("/v1/refine/next", headers=w).json()["job"]) and j["id"] != job:
        pass
    assert j and j["id"] == job
    r = client.post(f"/v1/refine/{job}/result", headers=w, data={"engine": "sam-body4d", "fps": 10, "dancers": 1, "frames": 4},
                    files={"joints": ("joints.json", b'{"people":[]}', "application/json"), "meta": ("meta.json", b"{}", "application/json"),
                           "mesh": ("mesh.bin", b"\x00" * 8, "application/octet-stream")})
    assert r.status_code == 200, r.text
    page = client.get(f"/v/{vid}", cookies={"ds_session": zoe}).text
    assert 'data-layer="body"' in page and 'id="body3d"' in page and "mountBody3D" in page
    # the app's link: a signed token that opens the viewer without a session
    track = client.get(f"/v1/videos/{vid}/body", headers=hdr(zoe)).json()["track"]
    assert track["view_url"].startswith(f"/body/{job}/view?t=")
    view = client.get(track["view_url"])
    assert view.status_code == 200 and "mountBody3D" in view.text
    tok = track["view_url"].split("t=")[1]
    assert client.get(f"/body/{job}/mesh.bin?t={tok}").status_code == 200
    assert client.get(f"/body/{job}/view").status_code == 404          # no token, no session
    assert client.get(f"/body/{job}/view?t=bogus").status_code == 404


def test_a_delivered_body_becomes_an_app_pose_track():
    zoe = _user("refzoe2", plan="pro")
    vid = _post_video(zoe, "Cross body lead", couple=True)
    assert client.post(f"/v1/videos/{vid}/refine", json={"tier": "3d"}, headers=hdr(zoe)).json()["status"] == "queued"
    w = {"X-Worker-Token": "worker-secret"}
    job = client.get("/v1/refine/next", headers=w).json()["job"]
    while job and job["video_id"] != vid:
        job = client.get("/v1/refine/next", headers=w).json()["job"]
    # two dancers, three frames, 70 MHR points each; dancer 2 missing in the middle frame
    frame = [[0.01 * i, 0.02 * i, 3.0 + 0.001 * i] for i in range(70)]
    joints = {"fps": 30, "frames": 3, "people": [[frame, frame, frame], [frame, None, frame]]}
    r = client.post(f"/v1/refine/{job['id']}/result", headers=w,
                    data={"engine": "sam-body4d-lite", "fps": 30, "dancers": 2, "frames": 3},
                    files={"joints": ("joints.json", json.dumps(joints).encode(), "application/json"),
                           "meta": ("meta.json", b"{}", "application/json")})
    assert r.status_code == 200, r.text
    body = client.get(f"/v1/videos/{vid}/body", headers=hdr(zoe)).json()
    key = body["track"]["pose_key"]
    assert key.endswith(f"-body{job['id']}") and body["summary"]["3d"]["pose_key"] == key
    track = client.get(f"/pose/{key}.json", headers=hdr(zoe)).json()
    assert track["dancers"] == 2 and track["frames"] == 3 and len(track["j"][0][0]) == 33
    assert track["j"][1][1] == track["j"][1][0]                 # the missing frame holds the last pose
    assert track["j"][0][0][15] == [round(0.01 * 62, 3), round(0.02 * 62, 3), round(3.0 + 0.001 * 62, 3)]   # left wrist = MHR 62
