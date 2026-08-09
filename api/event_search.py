from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Literal, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, HttpUrl, field_validator


DanceStyle = Literal["salsa", "bachata", "latin"]
EventStatus = Literal["scheduled", "cancelled", "postponed", "unknown"]


class EventSearchRequest(BaseModel):
    city: str = Field(min_length=2, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    country: Optional[str] = Field(default=None, max_length=100)
    date: date
    styles: list[DanceStyle] = Field(default_factory=lambda: ["salsa", "bachata", "latin"])

    @field_validator("city", "region", "country")
    @classmethod
    def clean_location(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value else value

    @field_validator("styles")
    @classmethod
    def require_style(cls, value: list[DanceStyle]) -> list[DanceStyle]:
        if not value:
            raise ValueError("select at least one dance style")
        return list(dict.fromkeys(value))


class DanceEvent(BaseModel):
    id: str
    name: str
    styles: list[DanceStyle]
    event_type: str
    start_time: datetime
    end_time: Optional[datetime] = None
    timezone: str
    venue_name: str
    address: Optional[str] = None
    city: str
    summary: str
    source_url: HttpUrl
    source_title: str
    confidence: Literal["high", "medium", "low"]
    status: EventStatus = "scheduled"


class EventSearchResponse(BaseModel):
    events: list[DanceEvent]
    checked_at: datetime
    query: EventSearchRequest
    cached: bool = False


class EventSearchProvider:
    async def search(self, request: EventSearchRequest) -> list[DanceEvent]:
        raise NotImplementedError


class OpenAIEventSearchProvider(EventSearchProvider):
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, api_key: str, model: str = "gpt-5.6") -> None:
        self.api_key = api_key
        self.model = model

    async def search(self, request: EventSearchRequest) -> list[DanceEvent]:
        location = ", ".join(part for part in [request.city, request.region, request.country] if part)
        prompt = f"""
Find public social dance events in {location} on {request.date.isoformat()} for these styles:
{', '.join(request.styles)}. Search official organizer, venue, ticketing, Facebook, and Instagram
pages. Return only events with evidence that the specific event occurs on that exact date. Do not
infer an occurrence from an old recurring listing. Prefer official sources and omit uncertain,
private, sold-out, or cancelled events. Times must be ISO 8601 with their UTC offset. Return at most
12 results. The source_url must be a URL you actually consulted with web search.
""".strip()
        payload = {
            "model": self.model,
            "tools": [{"type": "web_search", "search_context_size": "medium"}],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
            "input": prompt,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "dance_event_search",
                    "strict": True,
                    "schema": _event_search_schema(),
                }
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _openai_error(exc.response)
            raise HTTPException(status_code=502, detail=f"Event search provider error: {detail}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="Event search is temporarily unavailable") from exc

        body = response.json()
        searched_urls = _source_urls(body)
        raw_text = _output_text(body)
        try:
            raw_events = json.loads(raw_text)["events"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=502, detail="Event search returned an invalid response") from exc

        events: list[DanceEvent] = []
        for raw_event in raw_events:
            try:
                event = DanceEvent.model_validate(raw_event)
            except ValueError:
                continue
            if _canonical_url(str(event.source_url)) not in searched_urls:
                continue
            if event.start_time.date() != request.date or event.status != "scheduled":
                continue
            event.id = sha256(
                f"{event.name}|{event.start_time.isoformat()}|{event.source_url}".encode()
            ).hexdigest()[:20]
            events.append(event)
        return events


class EventSearchService:
    def __init__(self, provider: EventSearchProvider, ttl: timedelta = timedelta(minutes=15)) -> None:
        self.provider = provider
        self.ttl = ttl
        self._cache: dict[str, tuple[datetime, list[DanceEvent]]] = {}
        self._lock = threading.Lock()

    async def search(self, request: EventSearchRequest) -> EventSearchResponse:
        key = request.model_dump_json()
        now = datetime.now(timezone.utc)
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.ttl:
                return EventSearchResponse(events=cached[1], checked_at=cached[0], query=request, cached=True)

        events = await self.provider.search(request)
        checked_at = datetime.now(timezone.utc)
        with self._lock:
            self._cache[key] = (checked_at, events)
        return EventSearchResponse(events=events, checked_at=checked_at, query=request)


_service: Optional[EventSearchService] = None


def get_event_search_service() -> EventSearchService:
    global _service
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Event search is not configured")
    if _service is None:
        model = os.getenv("OPENAI_EVENT_SEARCH_MODEL", "gpt-5.6")
        _service = EventSearchService(OpenAIEventSearchProvider(api_key, model))
    return _service


def _canonical_url(value: str) -> str:
    parts = urlsplit(value)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _source_urls(body: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for item in body.get("output", []):
        action = item.get("action") or {}
        for source in action.get("sources") or []:
            if source.get("url"):
                urls.add(_canonical_url(source["url"]))
        for content in item.get("content") or []:
            for annotation in content.get("annotations") or []:
                url = annotation.get("url")
                if url:
                    urls.add(_canonical_url(url))
    return urls


def _output_text(body: dict[str, Any]) -> str:
    if body.get("output_text"):
        return body["output_text"]
    for item in body.get("output", []):
        if item.get("type") == "message":
            for content in item.get("content") or []:
                if content.get("type") == "output_text":
                    return content.get("text", "")
    return ""


def _openai_error(response: httpx.Response) -> str:
    try:
        return response.json().get("error", {}).get("message", f"HTTP {response.status_code}")
    except ValueError:
        return f"HTTP {response.status_code}"


def _event_search_schema() -> dict[str, Any]:
    event_properties = {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "styles": {"type": "array", "items": {"type": "string", "enum": ["salsa", "bachata", "latin"]}},
        "event_type": {"type": "string"},
        "start_time": {"type": "string", "format": "date-time"},
        "end_time": {"type": ["string", "null"], "format": "date-time"},
        "timezone": {"type": "string"},
        "venue_name": {"type": "string"},
        "address": {"type": ["string", "null"]},
        "city": {"type": "string"},
        "summary": {"type": "string"},
        "source_url": {"type": "string", "format": "uri"},
        "source_title": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "status": {"type": "string", "enum": ["scheduled", "cancelled", "postponed", "unknown"]},
    }
    return {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "properties": event_properties,
                    "required": list(event_properties.keys()),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["events"],
        "additionalProperties": False,
    }
