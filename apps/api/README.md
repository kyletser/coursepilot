# CoursePilot API

FastAPI service for identity, refresh-token rotation, dual roles, courses,
hashed invites, and enrollments.

## Local commands

```bash
python -m pip install -e ".[test]"
alembic upgrade head
uvicorn app.main:app --reload
pytest
```

Production uses PostgreSQL through `DATABASE_URL`. The fast SQLite suite is for
contract and authorization feedback; PostgreSQL remains required for migration,
row-lock, concurrent refresh, and concurrent enrollment verification.

The API entry point is `app.main:app`. The worker entry point is
`app.worker:celery_app`, and its smoke task is named `coursepilot.ping`.

All REST endpoints return `{data, error, request_id}` and also expose the same
request id in `X-Request-ID`. Access tokens are 30-minute HS256 JWTs. Refresh
tokens are opaque, stored only as SHA-256 digests, rotated in a lineage, and have
a fixed 14-day family lifetime. Replaying a consumed token revokes the active
token in that family.

`/health/ready` always checks the database and, by default, the Alembic revision,
Redis, Neo4j, and `INDEX_ROOT`. OpenAI-compatible `/models` probing is separately
enabled with `READINESS_CHECK_LLM=true` once real model credentials are present.
