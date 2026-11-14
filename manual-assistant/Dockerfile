# Manual Assistant. Non-root, pinned dependencies, healthcheck.
FROM python:3.12-slim

# curl serves the healthcheck below. sqlite3 is for reading the question log and
# the feedback on a running server, which DEPLOY.md's operations section does.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl sqlite3 \
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
COPY scripts/ ./scripts/
# The tests and their pytest settings travel with the image, so the suite can be
# run against the container that is actually deployed, which DEPLOY.md does after
# every update. They generate their own PDFs and need no manual and no model.
COPY tests/ ./tests/
COPY pyproject.toml ./

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

# app.main installs the JSON log handler on uvicorn's own loggers when it is
# imported, which happens after uvicorn has configured logging, so uvicorn needs
# no log options here. Passing --log-config /dev/null makes uvicorn refuse to
# start, because it rejects an empty configuration file.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
