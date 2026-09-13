FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg pulls the one frame a post's still is made from; pg_dump takes the
# backups, because a managed provider's own snapshots are their safety net, not
# ours — a copy in our bucket is the one nobody else can revoke.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dsplatform ./dsplatform

# DATABASE_URL is a secret now that it carries Postgres credentials, so it is
# set on the app rather than baked in. With it unset the code falls back to a
# local SQLite file, which is what a developer wants.
ENV STORAGE_BACKEND=r2

RUN useradd --create-home appuser && mkdir -p /data && chown appuser /data

# A file restored onto the volume — by sftp, by a restore, by anything running as
# root — lands owned by root, and SQLite then fails every write while reads keep
# working. That failure is quiet in exactly the wrong way, so ownership is fixed
# at boot rather than trusted.
COPY --chmod=755 <<'SH' /usr/local/bin/start
#!/bin/sh
set -e
chown -R appuser:appuser /data 2>/dev/null || true
exec su appuser -c "uvicorn dsplatform.main:app --host 0.0.0.0 --port 8000"
SH

EXPOSE 8000
CMD ["/usr/local/bin/start"]
