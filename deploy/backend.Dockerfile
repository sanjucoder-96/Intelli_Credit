# syntax=docker/dockerfile:1
# ------------------------------------------------------------------------------
# IntelliCredit backend image (FastAPI API + Celery worker/beat share this image;
# docker-compose.yml overrides the command per service).
#
# Build context is the REPOSITORY ROOT (not backend/):
#   docker build -f deploy/backend.Dockerfile -t intellicredit-backend:dev .
#
# System packages: curl only (used by HEALTHCHECK). WeasyPrint's system
# dependencies (Pango, Cairo, GDK-Pixbuf) arrive with report rendering in
# Phase 5 — the image is kept lean until then.
# ------------------------------------------------------------------------------

# --- builder: resolve and install all Python dependencies into a venv ---------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /srv/backend
# Dependency layer first: resolving requirements only re-runs when
# pyproject.toml changes, not on every source edit.
COPY backend/pyproject.toml ./
RUN pip install .

# Source layer: reinstalling the package itself is fast (deps are cached).
COPY backend/ .
RUN pip install --no-deps .

# --- runtime: minimal image, non-root, venv + source only ---------------------
FROM python:3.12-slim AS runtime

# curl: HEALTHCHECK. libgl1/libglib2.0-0/libxcb1: OpenCV's shared-library
# needs (RapidOCR imports cv2; the wheel is the full build — swapping to
# opencv-python-headless upstream of rapidocr is a noted later optimization).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgl1 libglib2.0-0 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system intellicredit \
    && useradd --system --gid intellicredit --create-home intellicredit \
    # Blob-storage mount point (ADR-0016): pre-owned by the app user so the
    # named volume inherits writable ownership on first mount.
    && mkdir -p /srv/storage \
    && chown intellicredit:intellicredit /srv/storage

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv/backend
# Source tree is copied alongside the installed package so alembic.ini and the
# migrations directory are available at their repo-relative paths.
COPY --from=builder --chown=intellicredit:intellicredit /srv/backend /srv/backend

USER intellicredit
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
