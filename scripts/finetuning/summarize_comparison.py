"""Summarize preserved paired experiments without relabeling any examples."""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from run_experiment import percentile, rows_for, sha256, write_json


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(args):
    paths = [args.runs / name for name in (args.base_run, args.sft_run)]
    summaries = [read(p / "summary.json") for p in paths]
    provenance = [read(p / "provenance.json") for p in paths]
    for info in provenance:
        if info.get("git_dirty") is not False:
            raise ValueError("formal comparison requires clean source")
        if info["arguments"]["split"] != "test" or info["arguments"]["limit"]:
            raise ValueError("formal comparison requires the complete test split")
    for key in ("max_new_tokens", "seed"):
        if provenance[0]["arguments"][key] != provenance[1]["arguments"][key]:
            raise ValueError(f"paired generation mismatch: {key}")
    for key in (
        "git_commit",
        "revision",
        "dataset_manifest_sha256",
        "quantization",
        "gpu",
        "packages",
    ):
        if provenance[0][key] != provenance[1][key]:
            raise ValueError(f"paired provenance mismatch: {key}")
    outputs = []
    for path, summary in zip(paths, summaries, strict=True):
        if sha256(path / "outputs.jsonl") != summary["raw_outputs_sha256"]:
            raise ValueError("raw output checksum mismatch")
        records = list(
            map(
                json.loads,
                (path / "outputs.jsonl").read_text(encoding="utf-8").splitlines(),
            )
        )
        indexed = {row["id"]: row for row in records}
        if len(indexed) != len(records):
            raise ValueError("duplicate paired case IDs")
        outputs.append(indexed)
    gold = {r["id"]: r for r in rows_for(args.dataset, "test")}
    if set(gold) != set(outputs[0]) or set(gold) != set(outputs[1]):
        raise ValueError("incomplete paired test")
    groups = defaultdict(list)
    wins, losses = [], []
    for key, row in gold.items():
        before, after = [int(o[key]["scores"]["task_success_proxy"]) for o in outputs]
        groups[row["group"]].append(after - before)
        if after > before:
            wins.append(key)
        if after < before:
            losses.append(key)
    rng = random.Random(20260911)
    names, samples = sorted(groups), []
    for _ in range(10000):
        values = [v for name in rng.choices(names, k=len(names)) for v in groups[name]]
        samples.append(sum(values) / len(values))
    samples.sort()
    full_chain = {}
    for alias in ("base", "sft"):
        reports = [
            read(
                args.agent
                / f"sft-paired-cp-demo-{course}-coursepilot-qwen3-{alias}.json"
            )
            for course in ("ds", "os")
        ]
        if any(r["dataset"]["case_count"] != 40 for r in reports):
            raise ValueError("incomplete course QA")
        latency = [
            row["latency_ms"] for r in reports for row in r["case_results"].values()
        ]
        full_chain[alias] = {
            "cases": 80,
            "unanswerable_refused": sum(
                r["metrics"]["abstention"]["refused_unanswerable_count"]
                for r in reports
            ),
            "unanswerable_count": 40,
            "false_refusals": sum(
                r["metrics"]["abstention"]["falsely_refused_count"] for r in reports
            ),
            "answerable_count": 40,
            "gold_alignment_pairs": sum(
                r["metrics"]["citations"]["accurate_citation_count"] for r in reports
            ),
            "all_citation_pairs": sum(
                r["metrics"]["citations"]["citation_count"] for r in reports
            ),
            "latency_ms_p50": percentile(latency, 0.5),
            "latency_ms_p95": percentile(latency, 0.95),
            "total_seconds": sum(latency) / 1000,
        }
    result = {
        "schema": "coursepilot.qwen3-paired-summary/1",
        "given_evidence": {"base": summaries[0], "sft": summaries[1]},
        "paired_lexical_proxy": {
            "wins": wins,
            "regressions": losses,
            "delta": (len(wins) - len(losses)) / len(gold),
            "groups": len(groups),
            "cluster_bootstrap_95_percentile": [samples[249], samples[9749]],
            "seed": 20260911,
            "resamples": 10000,
        },
        "full_chain": full_chain,
        "source_provenance": provenance,
        "inference": read(args.agent / "comparison-index.json")["inference_provenance"],
        "release_decision": args.release_decision,
        "limitations": [
            "AI-assisted synthetic source-group holdout, not teacher blind labels",
            "lexical matching is not semantic faithfulness or answer correctness",
            "original course QA gold labels omit some valid source facts",
            "full-chain comparison is a regression test, not an independent external benchmark",
            "one seed; single concurrency; unmerged LoRA latency reported separately",
        ],
    }
    write_json(args.output, result)
    print(
        json.dumps(
            {
                "paired_delta": result["paired_lexical_proxy"]["delta"],
                "full_chain": full_chain,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--agent", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-run", default="base-test-v1")
    parser.add_argument("--sft-run", default="sft-test-v1")
    parser.add_argument(
        "--release-decision", default="pending evidence review; not promoted"
    )
    summarize(parser.parse_args())
