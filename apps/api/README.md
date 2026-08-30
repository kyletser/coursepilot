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

## Evaluation case templates

Datasets are typed (`RETRIEVAL`, `INTENT_ROUTING`, `LEARNING_PATH`,
`END_TO_END_QA`). Cases are `{case_key, input, expected, labels}` JSON objects;
the metric implementations in `app/evaluation/service.py` and
`app/evaluation/metrics.py` read the fields below. The demo datasets created by
`scripts/seed_demo.py` follow exactly these shapes and stay DRAFT; frozen
acceptance datasets must be built separately per `spec.md` §11.1.

```jsonc
// RETRIEVAL — ranked ids are graded against expected.relevant_chunk_ids
// (optional graded relevance: {"<chunk_id>": 0-3}).
{
  "case_key": "retrieval-页面置换",
  "input": {"query": "为什么需要页面置换算法？"},
  "expected": {"relevant_chunk_ids": ["<real-chunk-uuid>"]},
  "labels": {"concept": "页面置换"}
}

// INTENT_ROUTING — graded against expected.expected_intent
// (or an allowed set: {"allowed_intents": ["TUTOR_QA", "QUIZ"]}).
{
  "case_key": "routing-01",
  "input": {"query": "给我出几道关于栈的练习题"},
  "expected": {"expected_intent": "QUIZ"},
  "labels": {}
}

// END_TO_END_QA — refusal recall / false-refusal rate use is_answerable;
// citation precision uses required_claim_ids.
{
  "case_key": "refusal-01",
  "input": {"query": "课程资料没有覆盖的问题"},
  "expected": {"is_answerable": false},
  "labels": {}
}

// LEARNING_PATH — expected path edges come from approved PREREQUISITE_OF
// relations; teacher_concept_ids is required for the teacher-consistency
// acceptance metric. Each edge is a [from_concept_id, to_concept_id] pair.
{
  "case_key": "path-01",
  "input": {"target_concept_id": "<concept-uuid>", "mastered_concept_ids": []},
  "expected": {
    "approved_prerequisite_edges": [["<from-uuid>", "<to-uuid>"]],
    "teacher_concept_ids": ["<prerequisite-uuid>", "<target-uuid>"]
  },
  "labels": {}
}
```
