"""Single-GPU QLoRA training and paired, evidence-controlled Agent evaluation.

Training never reads test.jsonl. Evaluation writes every raw output, including
malformed JSON, and the real TrustedAgentCore result. This isolates generation
from retrieval; a separate retrieval/end-to-end experiment is still required.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
REVISION = "1cfa9a7208912126459214e8b04321603b3df60c"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def source_state():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            )
        )
        if dirty:
            raise ValueError("formal experiments require a clean source tree")
        return {"git_commit": commit, "git_dirty": False}
    except subprocess.CalledProcessError:
        manifest = json.loads((ROOT / "source_provenance.json").read_text())
        if manifest.get("git_dirty") is not False or not manifest.get("files"):
            raise ValueError("unverified source export")
        for name, expected in manifest["files"].items():
            if sha256(ROOT / name) != expected:
                raise ValueError(f"exported source changed: {name}")
        return {
            "git_commit": manifest["git_commit"],
            "git_dirty": False,
            "exported_files_verified": len(manifest["files"]),
            "source_manifest_sha256": sha256(ROOT / "source_provenance.json"),
        }


def rows_for(dataset, split):
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    path = dataset / f"{split}.jsonl"
    if sha256(path) != manifest["splits"][split]["sha256"]:
        raise ValueError(f"frozen {split} data hash mismatch")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(rows) != manifest["splits"][split]["count"]:
        raise ValueError("row count mismatch")
    return rows


def load_model(args, training=False):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for this experiment")
    torch.set_num_threads(4)
    random.seed(args.seed)
    torch.manual_seed(args.seed)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    tokenizer.pad_token = tokenizer.eos_token
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quant,
        device_map={"": 0},
        torch_dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
        local_files_only=True,
    )
    if args.adapter:
        if training:
            raise ValueError(
                "this protocol trains a fresh adapter; no test-driven continuation"
            )
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, args.adapter, is_trainable=False)
    return model, tokenizer


def prompt_text(tokenizer, messages):
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )


def provenance(args):
    import torch

    return {
        **source_state(),
        "base_model": "Qwen/Qwen3-4B",
        "revision": REVISION,
        "arguments": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "dataset_manifest_sha256": sha256(args.dataset / "manifest.json"),
        "python": platform.python_version(),
        "host": platform.node(),
        "gpu": torch.cuda.get_device_name(0),
        "torch_cuda": torch.version.cuda,
        "bnb_cuda_version_override": os.environ.get("BNB_CUDA_VERSION"),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "transformers", "peft", "accelerate", "bitsandbytes")
        },
        "quantization": "4-bit NF4 double quantization; BF16 compute",
        "external_model_api_used": False,
        "download_manifest_sha256": sha256(args.model / "download_manifest.json"),
    }


def train(args):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments

    train_rows, dev_rows = (
        rows_for(args.dataset, "train"),
        rows_for(args.dataset, "dev"),
    )
    if {r["group"] for r in train_rows} & {r["group"] for r in dev_rows}:
        raise ValueError("source group leakage")
    model, tokenizer = load_model(args, training=True)
    model = prepare_model_for_kbit_training(
        model,
        use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
    )
    model = get_peft_model(
        model,
        LoraConfig(
            r=args.rank,
            lora_alpha=2 * args.rank,
            lora_dropout=0.05,
            target_modules="all-linear",
            bias="none",
            task_type="CAUSAL_LM",
        ),
    )
    model.config.use_cache = False
    info = provenance(args)
    info["trainable_parameters"], info["total_parameters"] = (
        model.get_nb_trainable_parameters()
    )
    write_json(args.output / "provenance.json", info)

    def encode(row):
        prefix = tokenizer(
            prompt_text(tokenizer, row["messages"]), add_special_tokens=False
        )["input_ids"]
        answer = tokenizer(
            json.dumps(row["answer"], ensure_ascii=False, separators=(",", ":")),
            add_special_tokens=False,
        )["input_ids"] + [tokenizer.eos_token_id]
        ids = prefix + answer
        if len(ids) > args.max_length:
            raise ValueError(
                f"{row['id']} has {len(ids)} tokens; refusing silent target truncation"
            )
        return {
            "input_ids": ids,
            "attention_mask": [1] * len(ids),
            "labels": [-100] * len(prefix) + answer,
        }

    encoded_train, encoded_dev = (
        list(map(encode, train_rows)),
        list(map(encode, dev_rows)),
    )
    settings = TrainingArguments(
        output_dir=str(args.output / "checkpoints"),
        per_device_train_batch_size=1,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=8,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        warmup_ratio=0.05,
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        optim="paged_adamw_8bit",
        bf16=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        logging_steps=5,
        report_to="none",
        seed=args.seed,
        data_seed=args.seed,
        dataloader_num_workers=0,
        dataloader_pin_memory=False,
        remove_unused_columns=False,
        disable_tqdm=True,
    )
    trainer = Trainer(
        model=model,
        args=settings,
        train_dataset=encoded_train,
        eval_dataset=encoded_dev,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer, padding=True, pad_to_multiple_of=8
        ),
    )
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    result = trainer.train()
    adapter = args.output / "adapter"
    trainer.save_model(str(adapter))
    tokenizer.save_pretrained(adapter)
    result.metrics.update(
        {
            "wall_seconds": time.perf_counter() - started,
            "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
            "best_checkpoint": trainer.state.best_model_checkpoint,
            "best_dev_loss": trainer.state.best_metric,
            "adapter_sha256": sha256(adapter / "adapter_model.safetensors"),
            "train_count": len(train_rows),
            "dev_count": len(dev_rows),
            "selection": "lowest development loss; test data not loaded",
        }
    )
    write_json(args.output / "training_result.json", result.metrics)
    print("TRAINING_COMPLETE " + json.dumps(result.metrics), flush=True)


def normalized(text):
    return "".join(re.findall(r"[\w]", text.casefold()))


def char_f1(expected, actual):
    left, right = Counter(normalized(expected)), Counter(normalized(actual))
    overlap = sum((left & right).values())
    return (
        2 * overlap / (sum(left.values()) + sum(right.values()))
        if left and right
        else 0.0
    )


def score_output(raw, row):
    from app.agent.schemas import AnswerDraft

    try:
        parsed = AnswerDraft.model_validate_json(raw)
    except ValueError:
        return {
            "schema_valid": False,
            "refused": False,
            "exact_match": False,
            "claim_citation_pairs": 0,
            "supported_pairs_proxy": 0,
            "citation_set_match": False,
            "answer_char_f1": 0.0,
            "task_success_proxy": False,
        }
    expected = row["answer"]["claims"]
    allowed = {i + 1 for i in range(len(row["evidence"]))}
    pairs = [
        (claim.text, label)
        for claim in parsed.claims
        for label in claim.citation_labels
    ]
    # All pairs count. A source must be correct and the answer must match its gold
    # statement with char-F1 >= .7. This remains a lexical proxy, not an NLI score.
    supported = sum(
        any(
            label in gold["citation_labels"] and char_f1(gold["text"], text) >= 0.7
            for gold in expected
        )
        for text, label in pairs
    )
    actual_labels = {label for _, label in pairs}
    expected_labels = {label for gold in expected for label in gold["citation_labels"]}
    label_match = actual_labels == expected_labels
    f1 = char_f1(
        "".join(c["text"] for c in expected), "".join(c.text for c in parsed.claims)
    )
    exact = sorted(
        (normalized(c.text), tuple(sorted(c.citation_labels))) for c in parsed.claims
    ) == sorted(
        (normalized(c["text"]), tuple(sorted(c["citation_labels"]))) for c in expected
    )
    success = (
        (not parsed.claims)
        if not expected
        else (bool(pairs) and supported == len(pairs) and label_match and f1 >= 0.7)
    )
    return {
        "schema_valid": True,
        "refused": not parsed.claims,
        "exact_match": exact,
        "claim_citation_pairs": len(pairs),
        "supported_pairs_proxy": supported,
        "citation_labels_valid": actual_labels.issubset(allowed),
        "citation_set_match": label_match,
        "answer_char_f1": f1,
        "task_success_proxy": success,
    }


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def evaluate(args):
    import torch

    from app.agent import AgentRequest, TrustedAgentCore
    from app.agent.schemas import Evidence

    rows = rows_for(args.dataset, args.split)
    if args.limit:
        rows = rows[: args.limit]
    model, tokenizer = load_model(args)
    model.eval()
    write_json(args.output / "provenance.json", provenance(args))

    def generate(messages):
        inputs = tokenizer(prompt_text(tokenizer, messages), return_tensors="pt").to(
            "cuda:0"
        )
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
                eos_token_id=tokenizer.eos_token_id,
                use_cache=True,
            )
        torch.cuda.synchronize()
        tokens = generated[0, inputs["input_ids"].shape[1] :]
        return {
            "raw": tokenizer.decode(tokens, skip_special_tokens=True),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "input_tokens": inputs["input_ids"].shape[1],
            "output_tokens": len(tokens),
        }

    # Fixed warmup is excluded from latency statistics, included in execution log.
    generate(rows[0]["messages"])
    torch.cuda.reset_peak_memory_stats()
    results = []
    with (args.output / "outputs.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            first = generate(row["messages"])
            calls = [first]

            class Adapter:
                def __init__(self, first_result, generation_calls):
                    self.count = 0
                    self.first_result = first_result
                    self.generation_calls = generation_calls

                def generate(self, prompt):
                    if self.count == 0:
                        self.count += 1
                        return self.first_result["raw"]
                    self.count += 1
                    messages = [
                        {"role": "system", "content": prompt.system_instruction},
                        {"role": "user", "content": prompt.user_payload},
                    ]
                    if prompt.grounding_feedback:
                        messages[0]["content"] += (
                            "\nPrevious grounding errors: "
                            + json.dumps(prompt.grounding_feedback)
                        )
                    result = generate(messages)
                    self.generation_calls.append(result)
                    return result["raw"]

            core = TrustedAgentCore(chat_adapter=Adapter(first, calls))
            response = asyncio.run(
                core.run(
                    AgentRequest(
                        course_id="sft-course",
                        active_index_version="sft-v1",
                        query=row["query"],
                        requested_intent="TUTOR_QA",
                    ),
                    evidence=[Evidence.model_validate(e) for e in row["evidence"]],
                )
            )
            scored = score_output(first["raw"], row)
            result = {
                "id": row["id"],
                "category": row["category"],
                "is_answerable": row["is_answerable"],
                **first,
                "scores": scored,
                "agent_status": response.status.value,
                "agent_answer": response.answer,
                "agent_calls": len(calls),
                "agent_generation_ms": sum(c["latency_ms"] for c in calls),
                "all_generation_calls": calls,
                "agent_scores": score_output(
                    json.dumps(
                        {"claims": [c.model_dump() for c in response.claims]},
                        ensure_ascii=False,
                    ),
                    row,
                ),
            }
            if response.status.value not in {
                "ANSWERED",
                "EVIDENCE_SUMMARY",
                "INSUFFICIENT_EVIDENCE",
            }:
                # An operational failure with no claims is not a correct refusal.
                result["agent_scores"].update(
                    refused=False, exact_match=False, task_success_proxy=False
                )
            results.append(result)
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            handle.flush()
            print(
                f"EVAL {args.split} {len(results)}/{len(rows)} {row['id']} {response.status.value}",
                flush=True,
            )

    def aggregate(selected, field):
        scores = [r[field] for r in selected]
        negatives = [r for r in selected if not r["is_answerable"]]
        positives = [r for r in selected if r["is_answerable"]]
        pairs = sum(s["claim_citation_pairs"] for s in scores)
        supported = sum(s["supported_pairs_proxy"] for s in scores)
        return {
            "count": len(selected),
            "schema_valid_rate": sum(s["schema_valid"] for s in scores) / len(scores),
            "exact_match_rate": sum(s["exact_match"] for s in scores) / len(scores),
            "task_success_proxy_rate": sum(s["task_success_proxy"] for s in scores)
            / len(scores),
            "supported_pairs_proxy": supported,
            "claim_citation_pairs": pairs,
            "citation_support_proxy_rate": supported / pairs if pairs else None,
            "unanswerable_count": len(negatives),
            "answerable_count": len(positives),
            "refused_unanswerable_count": sum(r[field]["refused"] for r in negatives),
            "false_refusal_count": sum(r[field]["refused"] for r in positives),
            "mean_answer_char_f1": sum(r[field]["answer_char_f1"] for r in positives)
            / len(positives)
            if positives
            else None,
        }

    latencies = [r["latency_ms"] for r in results]
    summary = {
        "schema": "coursepilot.sft-evidence-eval/1",
        "scope": "given-evidence real Agent; not retrieval end-to-end",
        "raw_model": aggregate(results, "scores"),
        "guarded_agent": aggregate(results, "agent_scores"),
        "categories": {
            category: aggregate(
                [r for r in results if r["category"] == category], "scores"
            )
            for category in sorted({r["category"] for r in results})
        },
        "agent_status_counts": dict(Counter(r["agent_status"] for r in results)),
        "latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "total_output_tokens": sum(r["output_tokens"] for r in results),
        "total_generation_seconds": sum(latencies) / 1000,
        "peak_gpu_allocated_bytes": torch.cuda.max_memory_allocated(),
        "raw_outputs_sha256": sha256(args.output / "outputs.jsonl"),
        "limitations": [
            "AI-assisted synthetic source-group holdout",
            "lexical support proxy, not semantic proof",
            "single concurrency; no retrieval timing; no external model API",
        ],
    }
    write_json(args.output / "summary.json", summary)
    print("EVALUATION_COMPLETE " + json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["train", "eval"])
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    parser.add_argument(
        "--limit", type=int, default=0, help="smoke only; never a full report"
    )
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--max-length", type=int, default=1536)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--seed", type=int, default=20260911)
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=False)
    if arguments.mode == "train":
        train(arguments)
    else:
        evaluate(arguments)
