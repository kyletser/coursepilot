"""Frozen CMRC evidence-conditioned transfer check, never training data.

Not official CMRC EM/F1: success is reference-span inclusion with the right
source label. It does not certify every generated claim's semantic correctness.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import time
from dataclasses import replace
from pathlib import Path

import httpx
from run_experiment import normalized, rows_for, sha256, source_state, write_json

# run_experiment establishes the API import path for standalone script use.
# isort: split
from app.agent import AgentRequest, OpenAICompatibleChatAdapter, TrustedAgentCore
from app.agent.schemas import Evidence

SOURCE_SHA = "a976d1fd5efc173bd58ff1c57e958de5f49fed633a7bfb8e0e402e5490d75f5e"


def matches_reference(claims, row):
    texts = [
        claim.text for claim in claims if row["gold_label"] in claim.citation_labels
    ]
    if "answer_groups" not in row:
        return any(
            normalized(a) in normalized(text) for text in texts for a in row["answers"]
        )
    # Separate claims may cover distinct requested fields, but must cite the gold source.
    return all(
        any(
            re.search(
                r"(?<![0-9a-z])" + re.escape(normalized(a)) + r"(?![0-9a-z])",
                normalized(text),
            )
            for a in alternatives
            for text in texts
        )
        for alternatives in row["answer_groups"]
    )


class RecordingTransport(httpx.AsyncBaseTransport):
    """Capture model responses before parsing/grounding; never record auth headers."""

    def __init__(self):
        self.calls = []

    async def handle_async_request(self, request):
        async with httpx.AsyncHTTPTransport() as upstream:
            response = await upstream.handle_async_request(request)
            content = await response.aread()
            self.calls.append(
                {"status_code": response.status_code, "body": content.decode("utf-8")}
            )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=content,
                request=request,
            )


def training_demonstrations(dataset):
    """Two fixed train-only demonstrations; selection never reads model outputs."""
    rows = rows_for(dataset, "train")
    selected = [
        min((r for r in rows if r["category"] == category), key=lambda r: r["id"])
        for category in ("single_evidence", "missing_evidence")
    ]
    examples = [
        {
            "query": row["query"],
            "COURSE_EVIDENCE": [
                {"citation_label": i + 1, "quote": e["text"]}
                for i, e in enumerate(row["evidence"])
            ],
            "answer": row["answer"],
        }
        for row in selected
    ]
    suffix = (
        "\nFORMAT DEMONSTRATIONS ONLY. Their facts and citation labels are not evidence "
        "for the current question. Answer the actual user payload using only its evidence.\n"
        + json.dumps(examples, ensure_ascii=False, separators=(",", ":"))
    )
    return suffix, {
        "train_manifest_sha256": sha256(dataset / "manifest.json"),
        "example_ids": [r["id"] for r in selected],
        "prompt_suffix": suffix,
        "prompt_suffix_sha256": hashlib.sha256(suffix.encode()).hexdigest(),
        "selection": "lowest train ID for single_evidence and missing_evidence",
    }


class FewShotAdapter:
    def __init__(self, adapter, suffix):
        self.adapter, self.suffix = adapter, suffix

    async def generate(self, prompt):
        return await self.adapter.generate(
            replace(prompt, system_instruction=prompt.system_instruction + self.suffix)
        )


def prepare(source, output):
    if sha256(source) != SOURCE_SHA:
        raise ValueError("CMRC source hash mismatch")
    raw = sorted(
        json.loads(source.read_text(encoding="utf-8")), key=lambda r: r["context_id"]
    )
    # The old retrieval benchmark used the first 50 context questions; exclude
    # them before deterministic length filtering, regardless of any model output.
    pool = [
        r
        for r in raw[50:]
        if 100 <= len(r["context_text"]) <= 1200
        and r["qas"]
        and any(a in r["context_text"] for a in r["qas"][0]["answers"])
    ]
    if len(pool) < 51:
        raise ValueError("insufficient fixed eligible contexts")
    rng, rows = random.Random(20260913), []
    for index, target in enumerate(pool[:50]):
        query = target["qas"][0]
        answers = [a for a in query["answers"] if a.strip()]
        distractor = next(
            r
            for r in pool
            if r["context_id"] != target["context_id"]
            and not any(a in r["context_text"] for a in answers)
        )
        passages = [target, distractor]
        rng.shuffle(passages)
        evidence = [
            Evidence(
                course_id="external-cmrc",
                index_version="fixed-cmrc-trial",
                chunk_id=p["context_id"],
                document=p["title"] or p["context_id"],
                document_version=SOURCE_SHA[:12],
                section="CMRC trial",
                page=n + 1,
                text=p["context_text"],
                score=0.9,
            ).model_dump()
            for n, p in enumerate(passages)
        ]
        rows.append(
            {
                "id": query["query_id"],
                "query": query["query_text"],
                "answers": answers,
                "gold_label": next(
                    i + 1
                    for i, p in enumerate(passages)
                    if p["context_id"] == target["context_id"]
                ),
                "evidence": evidence,
            }
        )
    output.mkdir(parents=True, exist_ok=False)
    with (output / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(
        output / "manifest.json",
        {
            "source_sha256": SOURCE_SHA,
            "source": "https://github.com/ymcui/cmrc2018",
            "license": "CC-BY-SA-4.0",
            "count": len(rows),
            "cases_sha256": sha256(output / "cases.jsonl"),
            "selection": "sorted context_id; exclude first50; first50 of length100..1200 with answer present",
            "seed": 20260913,
            "scope": "given evidence; new generation queries; not official CMRC benchmark",
            "primary_metric": "non-refusal and normalized reference span included in a claim with gold label",
            "secondary_metric": "exact normalized quote containment for each generated claim-citation pair",
            "training_used": False,
        },
    )
    print("PREPARED 50 external cases")


async def evaluate(args):
    state = source_state()
    suffix, demonstration_info = (
        training_demonstrations(args.few_shot_training)
        if args.few_shot_training
        else ("", None)
    )
    variants = ["coursepilot-qwen3-base", "coursepilot-qwen3-sft"]
    if suffix:
        variants.append("coursepilot-qwen3-base-fewshot")
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    if sha256(args.dataset / "cases.jsonl") != manifest["cases_sha256"]:
        raise ValueError("frozen external data changed")
    rows = list(
        map(
            json.loads,
            (args.dataset / "cases.jsonl").read_text(encoding="utf-8").splitlines(),
        )
    )
    if len(rows) != manifest["count"] or len({r["id"] for r in rows}) != len(rows):
        raise ValueError("external case count or identity mismatch")
    token = os.environ["COURSEPILOT_INFERENCE_TOKEN"]
    args.output.mkdir(parents=True, exist_ok=False)
    async with httpx.AsyncClient(
        timeout=60, headers={"Authorization": "Bearer " + token}
    ) as client:
        response = await client.get(args.base_url.rstrip("/") + "/models")
        response.raise_for_status()
        deployment = response.json()["experiment_provenance"]
        write_json(
            args.output / "provenance.json",
            {
                "source": state,
                "dataset": manifest,
                "deployment": deployment,
                "demonstrations": demonstration_info,
                "variants": variants,
            },
        )
        results = []
        with (args.output / "outputs.jsonl").open("x", encoding="utf-8") as handle:
            for index, row in enumerate(rows):
                offset = index % len(variants)
                aliases = variants[offset:] + variants[:offset]
                for alias in aliases:
                    transport = RecordingTransport()
                    adapter = OpenAICompatibleChatAdapter(
                        base_url=args.base_url,
                        api_key=token,
                        model="coursepilot-qwen3-base"
                        if alias.endswith("-fewshot")
                        else alias,
                        timeout_seconds=60,
                        transport=transport,
                    )
                    if alias.endswith("-fewshot"):
                        adapter = FewShotAdapter(adapter, suffix)
                    started = time.perf_counter()
                    response = await TrustedAgentCore(chat_adapter=adapter).run(
                        AgentRequest(
                            course_id="external-cmrc",
                            active_index_version="fixed-cmrc-trial",
                            query=row["query"],
                            requested_intent="TUTOR_QA",
                        ),
                        evidence=row["evidence"],
                    )
                    supported_span = matches_reference(response.claims, row)
                    pairs = [
                        (claim.text, label)
                        for claim in response.claims
                        for label in claim.citation_labels
                    ]
                    literal = sum(
                        1 <= label <= len(row["evidence"])
                        and normalized(text)
                        in normalized(row["evidence"][label - 1]["text"])
                        for text, label in pairs
                    )
                    record = {
                        "id": row["id"],
                        "model": alias,
                        "response": response.model_dump(mode="json"),
                        "model_calls": transport.calls,
                        "reference_span_with_gold_source": supported_span,
                        "answer_check": "predeclared atomic groups"
                        if "answer_groups" in row
                        else "reference span",
                        "literal_supported_pairs": literal,
                        "all_pairs": len(pairs),
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                    results.append(record)
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    print(f"EXTERNAL {index + 1}/{len(rows)} {alias}", flush=True)
        summary = {
            alias: {
                "count": len(rows),
                "span_success_count": sum(
                    r["reference_span_with_gold_source"]
                    for r in results
                    if r["model"] == alias
                ),
                "refusals": sum(
                    r["response"]["status"] == "INSUFFICIENT_EVIDENCE"
                    for r in results
                    if r["model"] == alias
                ),
                "answered_span_success_count": sum(
                    r["reference_span_with_gold_source"]
                    and r["response"]["status"] == "ANSWERED"
                    for r in results
                    if r["model"] == alias
                ),
                "summary_fallbacks": sum(
                    r["response"]["status"] == "EVIDENCE_SUMMARY"
                    for r in results
                    if r["model"] == alias
                ),
            }
            for alias in variants
        }
        write_json(args.output / "summary.json", summary)
        print(json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "eval"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080/v1")
    parser.add_argument("--few-shot-training", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.source, args.output)
    else:
        asyncio.run(evaluate(args))
