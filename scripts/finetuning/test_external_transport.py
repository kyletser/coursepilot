"""Ensure audit capture preserves malformed outputs without recording credentials."""

import asyncio
from unittest.mock import patch

import httpx
from run_external_qa import RecordingTransport


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
