"""Audit raw versus guarded external outputs and prompt cost, without relabeling."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from run_experiment import normalized, percentile, sha256, write_json

# run_experiment establishes standalone imports.
# isort: split
from app.agent.schemas import AnswerDraft


def summarize(args):
    manifest = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    if sha256(args.dataset / "cases.jsonl") != manifest["cases_sha256"]:
        raise ValueError("external dataset changed")
    gold = {
        r["id"]: r
        for r in map(
            json.loads,
            (args.dataset / "cases.jsonl").read_text(encoding="utf-8").splitlines(),
        )
    }
    provenance = json.loads((args.run / "provenance.json").read_text(encoding="utf-8"))
    if provenance["source"]["git_dirty"] is not False:
        raise ValueError("dirty evaluation source")
    rows = list(
        map(
            json.loads,
            (args.run / "outputs.jsonl").read_text(encoding="utf-8").splitlines(),
        )
    )
    summaries = {}
    success_ids = {}
    for variant in provenance["variants"]:
        selected = [r for r in rows if r["model"] == variant]
        if len(selected) != len(gold) or {r["id"] for r in selected} != set(gold):
            raise ValueError("incomplete or duplicate external cases")
        valid, raw_span, raw_empty, prompt_tokens, completion_tokens = 0, 0, 0, 0, 0
        for row in selected:
            payloads = []
            for call in row["model_calls"]:
                if call["status_code"] == 200:
                    payload = json.loads(call["body"])
                    payloads.append(payload)
                    prompt_tokens += payload.get("usage", {}).get("prompt_tokens", 0)
                    completion_tokens += payload.get("usage", {}).get(
                        "completion_tokens", 0
                    )
            if not payloads:
                continue
            try:
                draft = AnswerDraft.model_validate_json(
                    payloads[0]["choices"][0]["message"]["content"]
                )
            except (ValueError, KeyError, TypeError, IndexError):
                continue
            valid += 1
            raw_empty += not draft.claims
            target = gold[row["id"]]
            raw_span += any(
                target["gold_label"] in claim.citation_labels
                and any(
                    normalized(a) in normalized(claim.text) for a in target["answers"]
                )
                for claim in draft.claims
            )
        success_ids[variant] = {
            r["id"]
            for r in selected
            if r["response"]["status"] == "ANSWERED"
            and r["reference_span_with_gold_source"]
        }
        summaries[variant] = {
            "count": len(selected),
            "first_output_schema_valid": valid,
            "first_output_empty_claims": raw_empty,
            "first_output_span_with_gold_source": raw_span,
            "answered_span_with_gold_source": len(success_ids[variant]),
            "status_counts": dict(Counter(r["response"]["status"] for r in selected)),
            "prompt_tokens_including_retries": prompt_tokens,
            "completion_tokens_including_retries": completion_tokens,
            "latency_ms_p50": percentile([r["latency_ms"] for r in selected], 0.5),
            "latency_ms_p95": percentile([r["latency_ms"] for r in selected], 0.95),
        }
    base = success_ids["coursepilot-qwen3-base"]
    paired = {
        variant: {"wins": sorted(ids - base), "regressions": sorted(base - ids)}
        for variant, ids in success_ids.items()
        if variant != "coursepilot-qwen3-base"
    }
    result = {
        "schema": "coursepilot.external-generation-audit/1",
        "summaries": summaries,
        "paired_answered_span_vs_base": paired,
        "provenance": provenance,
        "raw_outputs_sha256": sha256(args.run / "outputs.jsonl"),
        "limitations": [
            "50 selected evidence-conditioned CMRC trial questions; not official EM/F1",
            "reference-span inclusion with source label does not prove whole-answer correctness",
            "no unanswerable external cases; cannot estimate external abstention recall",
            "few-shot uses longer context; timings single concurrency and include HTTP/Agent overhead",
        ],
    }
    write_json(args.output, result)
    print(json.dumps(summaries, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    summarize(parser.parse_args())
