from __future__ import annotations


class GraphCoreError(ValueError):
    """Base error for deterministic graph validation failures."""

    code = "GRAPH_VALIDATION_ERROR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SourceChunkRequiredError(GraphCoreError):
    code = "GRAPH_SOURCE_CHUNK_REQUIRED"


class InvalidRelationTypeError(GraphCoreError):
    code = "GRAPH_RELATION_TYPE_INVALID"


class GraphSelfLoopError(GraphCoreError):
    code = "GRAPH_PREREQUISITE_SELF_LOOP"


class GraphCycleError(GraphCoreError):
    code = "GRAPH_PREREQUISITE_CYCLE"


class InvalidGraphQueryError(GraphCoreError):
    code = "GRAPH_QUERY_INVALID"


class InvalidOutboxEventError(GraphCoreError):
    code = "GRAPH_OUTBOX_EVENT_INVALID"
