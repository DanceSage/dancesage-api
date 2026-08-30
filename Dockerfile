FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY dsplatform ./dsplatform

# SQLite lives on a mounted volume, not in the image — a machine can be
# replaced at any time and the database has to survive that.
ENV DATABASE_URL=sqlite:////data/dancesage.db \
    STORAGE_BACKEND=r2

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
