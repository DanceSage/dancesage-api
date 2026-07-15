# dancesage-api

FastAPI service for receiving DanceSage keypoint sequences. Pose refinement,
move classification, and coaching analysis are not implemented yet; unfinished
analysis endpoints return HTTP 501 rather than placeholder results.

## Run locally

```bash
conda activate dance_sage
pip install -r requirements-dev.txt
uvicorn api.main:app --reload
pytest
```

The health endpoint is `GET /health`, and the iOS ingestion endpoint is
`POST /api/refine-pose`.

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
