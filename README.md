# dancesage-api

FastAPI service for DanceSage event discovery and keypoint ingestion. Event search
uses the OpenAI Responses API web-search tool, and rejects results whose URLs are
not present in the tool's source list. Pose refinement, move classification, and
coaching analysis remain unfinished; those analysis endpoints return HTTP 501.

## Run locally

```bash
conda activate dance_sage
pip install -r requirements-dev.txt
export OPENAI_API_KEY="..."
uvicorn api.main:app --reload
pytest
```

The health endpoint is `GET /health`. Event discovery is
`POST /api/events/search`; the iOS ingestion endpoint is `POST /api/refine-pose`.
`OPENAI_EVENT_SEARCH_MODEL` can override the default `gpt-5.6` model. Never ship
the OpenAI key in the iOS application.

```
dancesage-api/
├── .github/
│   └── workflows/
│       └── build-and-push.yml    # Build Docker, push to GCR
├── src/
│   ├── __init__.py
│   ├── main.py                   # FastAPI app
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py             # /analyze-video endpoint
│   ├── models/
│   │   ├── __init__.py
│   │   └── pose_detector.py      # MMPose wrapper class
│   ├── services/
│   │   ├── __init__.py
│   │   ├── video_processor.py    # Video handling
│   │   └── keypoint_exporter.py  # Export results
│   └── config/
│       ├── __init__.py
│       └── settings.py           # App config
├── tests/
├── Dockerfile
├── requirements.txt
├── .dockerignore
├── .gitignore
└── README.md
```
