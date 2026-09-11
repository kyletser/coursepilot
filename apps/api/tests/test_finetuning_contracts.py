"""Regressions required before comparing original and fine-tuned generators."""

from types import SimpleNamespace

import pytest

from app.agent import AgentRequest, AgentStatus, TrustedAgentCore
from app.agent.schemas import ClaimDraft
from app.evaluation.runner import _LabeledClaim, _qa_judgments
from tests.test_agent_core import evidence_record


async def test_explicit_model_abstention_does_not_retry_or_leak_summary():
    class AbstainingModel:
        calls = 0

        def generate(self, prompt):
            self.calls += 1
            return {"claims": []}

    model = AbstainingModel()
    response = await TrustedAgentCore(chat_adapter=model).run(
        AgentRequest(
            course_id="course-os",
            query="资料没有给出的答案？",
            active_index_version="index-v3",
        ),
        evidence=[evidence_record()],
    )
    assert model.calls == 1
    assert response.status == AgentStatus.INSUFFICIENT_EVIDENCE
    assert response.claims == [] and response.citations == []
    assert "MODEL_ABSTAINED" in response.warnings


@pytest.mark.parametrize("label,chunk", [(2, "wrong"), (3, None)])
def test_wrong_or_unknown_citation_is_counted_in_denominator(label, chunk):
    response = SimpleNamespace(
        claims=[ClaimDraft(text="A queue is FIFO.", citation_labels=[1, label])],
        evidence=[
            SimpleNamespace(chunk_id="right", text="A queue is FIFO."),
            SimpleNamespace(chunk_id="wrong", text="A stack is LIFO."),
        ],
        citations=[SimpleNamespace(label=1, chunk_id="right", claim_indices=[0])]
        + (
            [SimpleNamespace(label=label, chunk_id=chunk, claim_indices=[0])]
            if chunk
            else []
        ),
    )
    rows = _qa_judgments(
        response,
        labeled_claims={
            "fifo": _LabeledClaim("A queue is FIFO.", frozenset({"right"}))
        },
        scope=frozenset({"right"}),
    )
    assert len(rows) == 2
    assert rows[0]["supports_claim"] is True
    assert rows[1]["supports_claim"] is False


def test_right_source_id_cannot_make_unrelated_generated_claim_correct():
    response = SimpleNamespace(
        claims=[ClaimDraft(text="Earth has two moons.", citation_labels=[1])],
        evidence=[SimpleNamespace(chunk_id="right", text="A queue is FIFO.")],
        citations=[SimpleNamespace(label=1, chunk_id="right", claim_indices=[0])],
    )
    rows = _qa_judgments(
        response,
        labeled_claims={
            "fifo": _LabeledClaim("A queue is FIFO.", frozenset({"right"}))
        },
        scope=frozenset({"right"}),
    )
    assert len(rows) == 1
    assert rows[0]["supports_claim"] is False
