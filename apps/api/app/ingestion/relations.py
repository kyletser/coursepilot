from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from app.models import RelationType

EXTRACTION_MODEL = "deterministic-document-signals-v1"

_PREREQUISITE_MARKER_RE = re.compile(
    r"(?P<evidence>"
    r"(?P<label>前置知识|prerequisites?)"
    r"\s*[:：]\s*"
    r"(?P<target>[^\r\n。；;.]+)"
    r")",
    re.IGNORECASE,
)
_SPACE_RE = re.compile(r"\s+")
_TARGET_WRAPPERS = " \t*_`'\"“”‘’《》〈〉<>[]【】()（）"


@dataclass(frozen=True, slots=True)
class HeadingCandidateSignal:
    candidate_id: uuid.UUID
    name: str
    section_path: tuple[str, ...]
    source_chunk_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ChunkRelationSignal:
    chunk_id: uuid.UUID
    ordinal: int
    content: str
    section_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RelationCandidateDraft:
    from_candidate_id: uuid.UUID
    to_candidate_id: uuid.UUID
    relation_type: RelationType
    source_chunk_id: uuid.UUID
    evidence: str
    extraction_model: str
    confidence: float

    @property
    def identity(
        self,
    ) -> tuple[uuid.UUID, uuid.UUID, RelationType, uuid.UUID]:
        return (
            self.from_candidate_id,
            self.to_candidate_id,
            self.relation_type,
            self.source_chunk_id,
        )


def extract_explicit_relation_candidates(
    chunks: list[ChunkRelationSignal],
    headings: list[HeadingCandidateSignal],
) -> list[RelationCandidateDraft]:
    """Extract only relations directly stated by document structure or markers.

    Heading order is deliberately ignored. ``PART_OF`` comes from a complete
    parent/child heading path, while ``PREREQUISITE_OF`` requires an explicit
    marker whose value resolves to exactly one heading candidate.
    """

    headings_by_path: dict[tuple[str, ...], list[HeadingCandidateSignal]] = {}
    headings_by_name: dict[str, list[HeadingCandidateSignal]] = {}
    for heading in headings:
        path = _normalize_path(heading.section_path)
        if not path:
            continue
        headings_by_path.setdefault(path, []).append(heading)
        normalized_name = _normalize_name(heading.name)
        if normalized_name:
            headings_by_name.setdefault(normalized_name, []).append(heading)

    drafts: dict[
        tuple[uuid.UUID, uuid.UUID, RelationType, uuid.UUID],
        RelationCandidateDraft,
    ] = {}

    for path in sorted(headings_by_path):
        children = headings_by_path[path]
        if len(path) < 2 or len(children) != 1:
            continue
        parents = headings_by_path.get(path[:-1], [])
        if len(parents) != 1:
            continue
        child = children[0]
        parent = parents[0]
        if child.candidate_id == parent.candidate_id:
            continue
        draft = RelationCandidateDraft(
            from_candidate_id=child.candidate_id,
            to_candidate_id=parent.candidate_id,
            relation_type=RelationType.PART_OF,
            source_chunk_id=child.source_chunk_id,
            evidence=" > ".join(path),
            extraction_model=EXTRACTION_MODEL,
            confidence=1.0,
        )
        drafts[draft.identity] = draft

    for chunk in sorted(chunks, key=lambda item: item.ordinal):
        current_candidates = headings_by_path.get(
            _normalize_path(chunk.section_path), []
        )
        if len(current_candidates) != 1:
            continue
        current = current_candidates[0]
        for match in _PREREQUISITE_MARKER_RE.finditer(chunk.content):
            if _marker_is_negated(chunk.content, match.start("label")):
                continue
            target_name = _normalize_name(match.group("target"))
            targets = headings_by_name.get(target_name, [])
            if len(targets) != 1:
                continue
            prerequisite = targets[0]
            if prerequisite.candidate_id == current.candidate_id:
                continue
            draft = RelationCandidateDraft(
                from_candidate_id=prerequisite.candidate_id,
                to_candidate_id=current.candidate_id,
                relation_type=RelationType.PREREQUISITE_OF,
                source_chunk_id=chunk.chunk_id,
                evidence=match.group("evidence").strip(),
                extraction_model=EXTRACTION_MODEL,
                confidence=1.0,
            )
            drafts[draft.identity] = draft

    return list(drafts.values())


def _normalize_path(path: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        normalized for item in path if (normalized := _normalize_display_text(item))
    )


def _normalize_name(value: str) -> str:
    return _normalize_display_text(value).strip(_TARGET_WRAPPERS).casefold()


def _normalize_display_text(value: str) -> str:
    return _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _marker_is_negated(content: str, marker_start: int) -> bool:
    line_prefix = content[
        max(0, content.rfind("\n", 0, marker_start) + 1) : marker_start
    ]
    normalized = _normalize_display_text(line_prefix).casefold()
    return normalized.endswith(("no", "not a", "not an", "无", "无需", "没有"))
