"""A summary fallback must not count as a generated correct answer."""

import json
from argparse import Namespace

from run_experiment import sha256
from summarize_external import summarize


def test_fallback_separate_from_first_generation(tmp_path):
    dataset, run = tmp_path / "dataset", tmp_path / "run"
    dataset.mkdir()
    run.mkdir()
    case = {"id": "fixture", "answers": ["answer"], "gold_label": 1}
    (dataset / "cases.jsonl").write_text(json.dumps(case) + "\n", encoding="utf-8")
    (dataset / "manifest.json").write_text(
        json.dumps({"cases_sha256": sha256(dataset / "cases.jsonl")}), encoding="utf-8"
    )
    variants = ["coursepilot-qwen3-base", "coursepilot-qwen3-sft"]
    (run / "provenance.json").write_text(
        json.dumps({"source": {"git_dirty": False}, "variants": variants}),
        encoding="utf-8",
    )
    rows = []
    for variant in variants:
        tuned = variant.endswith("-sft")
        content = (
            json.dumps({"claims": [{"text": "answer", "citation_labels": [1]}]})
            if tuned
            else "invalid JSON"
        )
        rows.append(
            {
                "id": "fixture",
                "model": variant,
                "response": {"status": "ANSWERED" if tuned else "EVIDENCE_SUMMARY"},
                "reference_span_with_gold_source": True,
                "latency_ms": 10,
                "model_calls": [
                    {
                        "status_code": 200,
                        "body": json.dumps(
                            {
                                "choices": [{"message": {"content": content}}],
                                "usage": {
                                    "prompt_tokens": 100,
                                    "completion_tokens": 10,
                                },
                            }
                        ),
                    }
                ],
            }
        )
    (run / "outputs.jsonl").write_text(
        "\n".join(map(json.dumps, rows)) + "\n", encoding="utf-8"
    )
    output = tmp_path / "summary.json"
    summarize(Namespace(dataset=dataset, run=run, output=output))
    result = json.loads(output.read_text(encoding="utf-8"))
    base, tuned = [result["summaries"][v] for v in variants]
    assert base["first_output_schema_valid"] == 0
    assert base["answered_span_with_gold_source"] == 0
    assert base["status_counts"] == {"EVIDENCE_SUMMARY": 1}
    assert tuned["answered_span_with_gold_source"] == 1
    assert tuned["first_output_span_with_gold_source"] == 1
    assert tuned["prompt_tokens_including_retries"] == 100
