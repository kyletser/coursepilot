"""Frozen CMRC evidence-conditioned transfer check, never training data.

Not official CMRC EM/F1: success is reference-span inclusion with the right
source label. It does not certify every generated claim's semantic correctness.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from pathlib import Path

import httpx
from run_experiment import normalized, sha256, source_state, write_json

from app.agent import AgentRequest, OpenAICompatibleChatAdapter, TrustedAgentCore
from app.agent.schemas import Evidence

SOURCE_SHA = "a976d1fd5efc173bd58ff1c57e958de5f49fed633a7bfb8e0e402e5490d75f5e"


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
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    if sha256(args.dataset / "cases.jsonl") != manifest["cases_sha256"]:
        raise ValueError("frozen external data changed")
    rows = list(
        map(
            json.loads,
            (args.dataset / "cases.jsonl").read_text(encoding="utf-8").splitlines(),
        )
    )
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
            {"source": state, "dataset": manifest, "deployment": deployment},
        )
        results = []
        with (args.output / "outputs.jsonl").open("x", encoding="utf-8") as handle:
            for index, row in enumerate(rows):
                aliases = ["coursepilot-qwen3-base", "coursepilot-qwen3-sft"]
                if index % 2:
                    aliases.reverse()
                for alias in aliases:
                    adapter = OpenAICompatibleChatAdapter(
                        base_url=args.base_url,
                        api_key=token,
                        model=alias,
                        timeout_seconds=60,
                    )
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
                    supported_span = any(
                        row["gold_label"] in claim.citation_labels
                        and any(
                            normalized(a) in normalized(claim.text)
                            for a in row["answers"]
                        )
                        for claim in response.claims
                    )
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
                        "reference_span_with_gold_source": supported_span,
                        "literal_supported_pairs": literal,
                        "all_pairs": len(pairs),
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                    results.append(record)
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                    handle.flush()
                    print(f"EXTERNAL {index + 1}/50 {alias}", flush=True)
        summary = {
            alias: {
                "count": 50,
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
            for alias in ("coursepilot-qwen3-base", "coursepilot-qwen3-sft")
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
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare(args.source, args.output)
    else:
        asyncio.run(evaluate(args))
