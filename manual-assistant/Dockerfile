# Manual Assistant. Non-root, pinned dependencies, healthcheck.
FROM python:3.12-slim

# curl is only here for the healthcheck below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes=false -r requirements.txt

# Only what the app needs to run. No manual is ever baked into the image:
# manuals live on the data volume and are uploaded through the admin page.
COPY app/ ./app/
COPY static/ ./static/
COPY config.yaml ./
COPY eval/ ./eval/

# The data volume is mounted here. Owned by the unprivileged user so the app can
# write the database, the vectors and the page cache.
RUN mkdir -p /data/manuals /data/vectors /data/pagecache \
 && chown -R appuser:appuser /data /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    DB_PATH=/data/app.db \
    USERS_FILE=/data/users.yaml \
    HOST=0.0.0.0 \
    PORT=8000

USER appuser
EXPOSE 8000

# The app is healthy when its own database answers. A language model that is down
# degrades the service but does not make the container unhealthy, so the
# healthcheck reads the status field rather than the model flag.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz | grep -q '"status": "ok"'

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-config", "/dev/null"]
