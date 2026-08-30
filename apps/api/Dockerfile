FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY . /app
RUN pip install --upgrade pip \
    && pip install . \
    && groupadd --gid 10001 coursepilot \
    && useradd --uid 10001 --gid coursepilot --no-create-home coursepilot \
    && mkdir -p /data/uploads /data/indexes /data/models /tmp \
    && chown -R coursepilot:coursepilot /data /tmp \
    && chmod 1777 /tmp

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
