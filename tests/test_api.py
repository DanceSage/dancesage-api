from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_refine_pose_accepts_frames_with_and_without_people():
    response = client.post(
        "/api/refine-pose",
        json={
            "name": "basic step",
            "keypoints": [[], [[[0.25, 0.75], [0.5, 0.5]]]],
        },
    )
    assert response.status_code == 200
    assert response.json()["frame_count"] == 2


def test_refine_pose_rejects_invalid_coordinate_shape():
    response = client.post(
        "/api/refine-pose",
        json={"name": "invalid", "keypoints": [[[[0.25]]]]},
    )
    assert response.status_code == 422


def test_unimplemented_analysis_is_explicit():
    payload = {"name": "basic step", "keypoints": [[]]}
    assert client.post("/api/classify-move", json=payload).status_code == 501
    assert client.post("/api/analyze-sequence", json=payload).status_code == 501
