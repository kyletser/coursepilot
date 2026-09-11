"""Loopback-only, serial OpenAI-compatible experimental inference server.

Expose through an authenticated SSH tunnel, never a public listener. Both model
aliases share the same quantized weights; the base alias disables the LoRA adapter.
JSON mode is a prompt contract, not constrained decoding. Not a production server.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import time
import uuid
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from run_experiment import load_model, prompt_text, provenance

BASE = "coursepilot-qwen3-base"
TUNED = "coursepilot-qwen3-sft"


def serve(args):
    import torch

    token = os.environ.get("COURSEPILOT_INFERENCE_TOKEN", "")
    if len(token) < 24:
        raise ValueError(
            "provide a random >=24 character inference token in environment"
        )
    model, tokenizer = load_model(args)
    model.eval()
    model_provenance = provenance(args)
    print(json.dumps({"event": "loaded", **model_provenance}), flush=True)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, fmt, *values):
            # No prompts, completion text, or authorization headers in logs.
            return

        def send_json(self, status, body):
            encoded = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def authorized(self):
            return hmac.compare_digest(
                self.headers.get("Authorization", "").encode(),
                ("Bearer " + token).encode(),
            )

        def do_GET(self):
            if not self.authorized():
                self.send_json(401, {"error": "unauthorized"})
            elif self.path == "/v1/models":
                self.send_json(
                    200,
                    {
                        "object": "list",
                        "experiment_provenance": model_provenance,
                        "data": [
                            {"id": name, "object": "model"} for name in (BASE, TUNED)
                        ],
                    },
                )
            else:
                self.send_json(404, {"error": "not found"})

        def do_POST(self):
            if not self.authorized():
                self.send_json(401, {"error": "unauthorized"})
                return
            if self.path != "/v1/chat/completions":
                self.send_json(404, {"error": "not found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("body must be between 1 and 65536 bytes")
                request = json.loads(self.rfile.read(size))
                alias = request["model"]
                if alias not in {BASE, TUNED} or request.get("stream", False):
                    raise ValueError("unknown model or unsupported streaming")
                messages = request["messages"]
                if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
                    raise ValueError("invalid messages")
                if any(
                    not isinstance(m, dict)
                    or m.get("role") not in {"system", "user"}
                    or not isinstance(m.get("content"), str)
                    for m in messages
                ):
                    raise ValueError("only system/user text messages are supported")
                limit = request.get("max_tokens", args.max_new_tokens)
                if type(limit) is not int or not 1 <= limit <= args.max_new_tokens:
                    raise ValueError("invalid output token limit")
                inputs = tokenizer(
                    prompt_text(tokenizer, messages), return_tensors="pt"
                )
                if inputs["input_ids"].shape[1] + limit > 4096:
                    raise ValueError("experimental context budget exceeded")
            except (ValueError, KeyError, TypeError):
                self.send_json(400, {"error": "invalid request or context budget"})
                return
            started = time.perf_counter()
            try:
                inputs = inputs.to("cuda:0")
                context = model.disable_adapter() if alias == BASE else nullcontext()
                with context, torch.inference_mode():
                    output = model.generate(
                        **inputs,
                        do_sample=False,
                        max_new_tokens=limit,
                        pad_token_id=tokenizer.eos_token_id,
                        eos_token_id=tokenizer.eos_token_id,
                        use_cache=True,
                    )
                generated = output[0, inputs["input_ids"].shape[1] :]
                self.send_json(
                    200,
                    {
                        "id": "chatcmpl-" + uuid.uuid4().hex,
                        "object": "chat.completion",
                        "model": alias,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": tokenizer.decode(
                                        generated, skip_special_tokens=True
                                    ),
                                },
                                "finish_reason": "stop"
                                if generated[-1].item() == tokenizer.eos_token_id
                                else "length",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": inputs["input_ids"].shape[1],
                            "completion_tokens": len(generated),
                        },
                    },
                )
                print(
                    json.dumps(
                        {
                            "event": "completed",
                            "model": alias,
                            "generation_ms": (time.perf_counter() - started) * 1000,
                        }
                    ),
                    flush=True,
                )
            except (RuntimeError, OSError, ValueError, IndexError) as exc:
                print(
                    json.dumps({"event": "failed", "type": type(exc).__name__}),
                    flush=True,
                )
                self.send_json(503, {"error": "generation unavailable"})

    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--seed", type=int, default=20260911)
    serve(parser.parse_args())
