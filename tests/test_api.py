from fastapi.testclient import TestClient

from api.main import app
from api.event_search import DanceEvent, EventSearchProvider, EventSearchService, get_event_search_service


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


class StubEventProvider(EventSearchProvider):
    def __init__(self):
        self.calls = 0

    async def search(self, request):
        self.calls += 1
        return [DanceEvent(
            id="official-event",
            name="Salsa on the Roof",
            styles=["salsa", "bachata"],
            event_type="social",
            start_time=f"{request.date.isoformat()}T20:00:00-04:00",
            end_time=f"{request.date.isoformat()}T23:30:00-04:00",
            timezone="America/Toronto",
            venue_name="Dance Hall",
            address="100 King Street",
            city=request.city,
            summary="A salsa and bachata social.",
            source_url="https://example.com/events/salsa-roof",
            source_title="Organizer event page",
            confidence="high",
            status="scheduled",
        )]


def test_event_search_returns_structured_results_and_caches():
    provider = StubEventProvider()
    service = EventSearchService(provider)
    app.dependency_overrides[get_event_search_service] = lambda: service
    payload = {"city": "Toronto", "region": "Ontario", "country": "Canada", "date": "2026-08-08", "styles": ["salsa", "bachata"]}
    try:
        first = client.post("/api/events/search", json=payload)
        second = client.post("/api/events/search", json=payload)
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == 200
    assert first.json()["events"][0]["name"] == "Salsa on the Roof"
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert provider.calls == 1


def test_event_search_requires_a_style():
    app.dependency_overrides[get_event_search_service] = lambda: EventSearchService(StubEventProvider())
    try:
        response = client.post(
            "/api/events/search",
            json={"city": "Toronto", "date": "2026-08-08", "styles": []},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
