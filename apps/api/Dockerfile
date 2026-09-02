# syntax=docker/dockerfile:1

# --- Builder stage -----------------------------------------------------------
# Compiles/installs all Python dependencies into an isolated virtualenv so the
# final image never carries build tooling, pip caches, or source-only debris.
FROM python:3.12.14-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && /opt/venv/bin/pip install ".[ml]"

# --- Runtime stage -----------------------------------------------------------
# Slim image with only the prebuilt virtualenv and application source.
FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

RUN groupadd --gid 10001 coursepilot \
    && useradd --uid 10001 --gid coursepilot --no-create-home coursepilot \
    && mkdir -p /data/uploads /data/indexes /data/models /tmp \
    && chown -R coursepilot:coursepilot /data /tmp \
    && chmod 1777 /tmp

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
