"""Freeze unseen short-answer CMRC cases plus explicitly missing-evidence pairs."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from run_experiment import normalized, sha256, write_json
from run_external_qa import SOURCE_SHA

# Standalone imports are initialized above.
# isort: split
from app.agent.schemas import Evidence


def prepare(args):
    if sha256(args.source) != SOURCE_SHA:
        raise ValueError("upstream CMRC bytes differ")
    source = sorted(
        json.loads(args.source.read_text(encoding="utf-8")),
        key=lambda r: r["context_id"],
    )
    keys = json.loads(args.answer_keys.read_text(encoding="utf-8"))
    previous_manifest = json.loads(
        (args.previous / "manifest.json").read_text(encoding="utf-8")
    )
    if sha256(args.previous / "cases.jsonl") != previous_manifest["cases_sha256"]:
        raise ValueError("previous frozen cases changed")
    prior = list(
        map(
            json.loads,
            (args.previous / "cases.jsonl").read_text(encoding="utf-8").splitlines(),
        )
    )
    excluded = {r["context_id"] for r in source[:50]}
    excluded.update(e["chunk_id"] for r in prior for e in r["evidence"])
    selected = []
    for context in source:
        if (
            context["context_id"] in excluded
            or not 100 <= len(context["context_text"]) <= 1200
        ):
            continue
        for query in sorted(context["qas"], key=lambda q: q["query_id"]):
            answers = query["answers"]
            if answers and all(
                2 <= len(normalized(a)) <= 12 and a in context["context_text"]
                for a in answers
            ):
                selected.append((context, query))
                break
        if len(selected) == args.count:
            break
    if len(selected) != args.count:
        raise ValueError(f"only {len(selected)} eligible unseen contexts")
    if set(keys) != {q["query_id"] for _, q in selected}:
        raise ValueError("answer-key IDs do not exactly match the selected questions")
    rng, rows = random.Random(20260914), []
    for target, query in selected:
        groups = keys[query["query_id"]]
        if not groups or not all(
            any(normalized(a) in normalized(target["context_text"]) for a in aliases)
            for aliases in groups
        ):
            raise ValueError("answer keys are not anchored in the source")
        distractors = [
            r
            for r in source
            if r["context_id"] != target["context_id"]
            and 100 <= len(r["context_text"]) <= 1200
            and not any(
                normalized(a) in normalized(r["context_text"])
                for aliases in groups
                for a in aliases
            )
        ]
        if len(distractors) < 2:
            raise ValueError("missing unrelated passages")
        for answerable in (True, False):
            passages = [target, distractors[0]] if answerable else distractors[:2]
            rng.shuffle(passages)
            evidence = [
                Evidence(
                    course_id="external-cmrc",
                    index_version="fixed-cmrc-trial",
                    chunk_id=p["context_id"],
                    document=p["title"] or p["context_id"],
                    document_version=SOURCE_SHA[:12],
                    section="CMRC unseen contrast",
                    page=i + 1,
                    text=p["context_text"],
                    score=0.9,
                ).model_dump()
                for i, p in enumerate(passages)
            ]
            rows.append(
                {
                    "id": query["query_id"]
                    + ("-present" if answerable else "-missing"),
                    "group": target["context_id"],
                    "query": query["query_text"],
                    "answers": query["answers"],
                    "answer_groups": groups,
                    "is_answerable": answerable,
                    "gold_label": next(
                        i + 1 for i, p in enumerate(passages) if p is target
                    )
                    if answerable
                    else None,
                    "evidence": evidence,
                }
            )
    rng.shuffle(rows)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "cases.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
        newline="\n",
    )
    write_json(
        args.output / "manifest.json",
        {
            "source": "https://github.com/ymcui/cmrc2018",
            "source_sha256": SOURCE_SHA,
            "license": "CC-BY-SA-4.0",
            "count": len(rows),
            "groups": len(selected),
            "cases_sha256": sha256(args.output / "cases.jsonl"),
            "excluded_context_ids": sorted(excluded),
            "previous_cases_sha256": sha256(args.previous / "cases.jsonl"),
            "answer_keys_sha256": sha256(args.answer_keys),
            "answer_key_origin": "AI-reviewed atomic keys from official reference answers, fixed before any model output; not independent human review",
            "selection": "sorted unseen contexts; length100..1200; first query with all reference answers 2..12 normalized chars present verbatim; one query per context",
            "negative_construction": "replace target by two unrelated passages containing none of the reference answers; NOT naturally unanswerable CMRC labels",
            "scope": "given-evidence short answers plus constructed missing-evidence pairs; not official CMRC; not whole-answer semantic accuracy",
            "primary_metric": "positive: all predeclared atomic answer groups in gold-source claims and ANSWERED; negative: INSUFFICIENT_EVIDENCE",
            "seed": 20260914,
            "training_used": False,
        },
    )
    print(
        json.dumps({"count": len(rows), "sha256": sha256(args.output / "cases.jsonl")})
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument(
        "--answer-keys",
        type=Path,
        default=Path(__file__).with_name("cmrc_contrast_answer_keys.json"),
    )
    prepare(parser.parse_args())
