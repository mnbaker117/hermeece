# Hermeece Docker image — Phase 1 production build.
#
# Phase 1 ships without calibre-bin bundled because the post-download
# pipeline (calibredb add) lands in Phase 2. The image stays small
# (~150MB) and boots fast. Phase 2 will add the calibre-bin layer
# behind a build arg so users who only want the IRC → qBit pipeline
# can keep the slim image.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HERMEECE_MODE=docker \
    DATA_DIR=/app/data

WORKDIR /app

# OS deps:
#   - sqlite3: ad-hoc DB inspection during ops debugging
#     (`docker compose exec hermeece sqlite3 /app/data/hermeece.db ...`).
#     ~1MB extra; the convenience pays for itself the first time
#     you need to look at a `grabs` row in production.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python runtime dependencies first so the layer cache stays
# warm across code changes. Test deps live in requirements-dev.txt
# and are deliberately NOT installed in the production image.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# App source. Tests, previous-stuff/, and the venv are all excluded
# via .dockerignore.
COPY app ./app
COPY pyproject.toml ./

# Mount target for the data dir (settings.json, hermeece.db). Make
# sure /app/data exists at image build time so the first boot under
# a non-mounted setup still works.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# WebUI port — NOT 8787 (that's AthenaScout). 8788 is Hermeece's
# default and matches docker-compose.example.yml.
EXPOSE 8788

# Liveness probe — uses /api/health, which reports both the service
# status and whether the dispatcher singleton has been built.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8788/api/health').status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8788"]
