"""Ensure audit capture preserves malformed outputs without recording credentials."""

import asyncio
from unittest.mock import patch

import httpx
from run_external_qa import FewShotAdapter, RecordingTransport, training_demonstrations

# Standalone helper establishes the application import path.
# isort: split
from app.agent.grounding import GenerationPrompt


def test_capture_is_transparent_and_excludes_authorization():
    async def check():
        upstream = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not valid model JSON")
        )
        transport = RecordingTransport()
        with patch("run_external_qa.httpx.AsyncHTTPTransport", return_value=upstream):
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.post(
                    "http://127.0.0.1:18080/v1/chat/completions",
                    headers={"Authorization": "Bearer test-secret-not-for-log"},
                    json={"model": "fake"},
                )
        assert response.content == b"not valid model JSON"
        assert transport.calls == [{"status_code": 200, "body": "not valid model JSON"}]
        assert "test-secret-not-for-log" not in repr(transport.calls)

    asyncio.run(check())


def test_fewshot_only_changes_system_demonstrations():
    class Capture:
        async def generate(self, prompt):
            self.prompt = prompt
            return "sentinel"

    original = GenerationPrompt("rules", "actual evidence", (3,), ("retry reason",))
    capture = Capture()
    result = asyncio.run(FewShotAdapter(capture, " demo").generate(original))
    assert result == "sentinel"
    assert capture.prompt.system_instruction == "rules demo"
    assert capture.prompt.user_payload == original.user_payload
    assert capture.prompt.allowed_citation_labels == (3,)
    assert capture.prompt.grounding_feedback == ("retry reason",)
    assert original.system_instruction == "rules"


def test_demonstrations_only_read_train():
    rows = [
        {
            "id": "b",
            "category": "single_evidence",
            "query": "q",
            "evidence": [],
            "answer": {},
        },
        {
            "id": "a",
            "category": "single_evidence",
            "query": "q",
            "evidence": [],
            "answer": {},
        },
        {
            "id": "c",
            "category": "missing_evidence",
            "query": "q",
            "evidence": [],
            "answer": {},
        },
    ]
    from pathlib import Path

    with (
        patch("run_external_qa.rows_for", return_value=rows) as loader,
        patch("run_external_qa.sha256", return_value="frozen-hash"),
    ):
        _, info = training_demonstrations(Path("unused"))
    loader.assert_called_once_with(Path("unused"), "train")
    assert info["example_ids"] == ["a", "c"]
