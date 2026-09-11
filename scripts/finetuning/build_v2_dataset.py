"""Long-evidence augmentation using only v1 train/dev plus new source groups.

V1 test outputs and original course QA labels are never loaded by this builder.
V1 tests are nevertheless considered development-observed after the v1 audit.
"""

import argparse
import copy
import csv
import json
import random
from pathlib import Path

from run_experiment import ROOT, rows_for, sha256

from app.agent.grounding import build_generation_prompt
from app.agent.schemas import Evidence, Intent, IntentRoute


def refresh(row):
    prompt = build_generation_prompt(
        query=row["query"],
        route=IntentRoute(
            intent=Intent.TUTOR_QA,
            target_concepts=[],
            needs_retrieval=True,
            reason="frozen experiment",
        ),
        evidence=[Evidence.model_validate(e) for e in row["evidence"]],
    )
    row["messages"] = [
        {"role": "system", "content": prompt.system_instruction},
        {"role": "user", "content": prompt.user_payload},
    ]
    return row


def build(output):
    if output.exists():
        raise FileExistsError("never overwrite frozen datasets")
    rng = random.Random(20260912)
    old = ROOT / "experiments/qwen3-sft-v1"
    source = Path(__file__).with_name("curriculum_v2.tsv")
    with source.open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle, delimiter="\t"))
    with (
        Path(__file__)
        .with_name("curriculum.tsv")
        .open(encoding="utf-8", newline="") as handle
    ):
        original = list(csv.DictReader(handle, delimiter="\t"))
    old_topics = {r["topic"] for r in original}
    if old_topics & {r["topic"] for r in records if r["split"] == "test"}:
        raise ValueError("new test topic overlaps v1 source catalog")
    splits = {"train": [], "dev": [], "test": []}
    for split in ("train", "dev"):
        facts = {
            f"{split}-{r['topic']}-": (r["answer1"], r["answer2"])
            for r in original
            if r["split"] == split
        }
        for row in rows_for(old, split):
            original_row = copy.deepcopy(row)
            original_row["id"] = "v2-original-" + row["id"]
            splits[split].append(original_row)
            expanded = copy.deepcopy(row)
            expanded["id"] = "v2-long-" + row["id"]
            expanded["category"] = "long_" + row["category"]
            for evidence in expanded["evidence"]:
                content = evidence["text"]
                for prefix, pair in facts.items():
                    if evidence["chunk_id"].startswith(prefix):
                        content = " ".join(dict.fromkeys([content, *pair]))
                        break
                evidence["text"] = (
                    "本节从数据组织与执行条件两方面讨论课程中的机制。"
                    + content
                    + " 分析时要区分接口语义、实现过程以及成立条件，不能把某一种实现的特征直接推广到所有场景。"
                    "课堂讨论还应明确输入规模、状态是否已经建立以及哪些准备工作计入代价；材料未规定的具体实验参数仍然未知。"
                )
            splits[split].append(refresh(expanded))

    for record in records:
        split, topic = record["split"], record["topic"]
        peers = [r for r in records if r["split"] == split and r["topic"] != topic]
        noise = rng.sample(peers, 2)
        for mode in ("short", "long", "missing", "unspecified", "multi"):
            for n in (1, 2):
                # No arbitrary question-prefix expansion: the two queries target
                # distinct facts. Long passages include both facts and commentary.
                own = (
                    record[f"answer{n}"]
                    if mode == "short"
                    else (
                        record["answer1"]
                        + record["answer2"]
                        + "以上性质需要结合各自前提理解。讨论实现时应注明对象、状态与操作范围，不能用未写入材料的课程安排或机器配置补全回答。"
                    )
                )
                own_id = f"v2-{topic}-{mode}-{n}"
                passages = [(own_id, own)]
                passages += [
                    (f"v2-{p['topic']}-noise", p["answer1"] + p["answer2"])
                    for p in noise
                ]
                if mode == "missing":
                    passages = passages[1:]
                rng.shuffle(passages)
                evidence = [
                    Evidence(
                        course_id="sft-course",
                        index_version="sft-v1",
                        chunk_id=pid,
                        document="原创 v2 条件与机制资料",
                        document_version="2",
                        section=topic,
                        page=i + 1,
                        text=text,
                        score=0.9,
                    ).model_dump()
                    for i, (pid, text) in enumerate(passages)
                ]
                labels = {pid: i + 1 for i, (pid, _) in enumerate(passages)}
                query = record[f"question{n}"]
                claims = []
                if mode not in {"missing", "unspecified"}:
                    claims = [
                        {
                            "text": record[f"answer{k}"],
                            "citation_labels": [labels[own_id]],
                        }
                        for k in ((1, 2) if mode == "multi" else (n,))
                    ]
                if mode == "unspecified":
                    query = (
                        f"材料中规定{topic}本次实验的确切运行时间是多少？"
                        if n == 1
                        else f"这份材料能确定{topic}在下次考试中的分值吗？"
                    )
                elif mode == "multi":
                    if n == 2:
                        continue  # Do not duplicate identical multi-question rows.
                    query = record["question1"] + record["question2"]
                row = {
                    "id": f"v2-{split}-{len(splits[split]):04d}",
                    "group": topic,
                    "category": "new_" + mode,
                    "query": query,
                    "evidence": evidence,
                    "answer": {"claims": claims},
                    "is_answerable": bool(claims),
                }
                splits[split].append(refresh(row))
    groups = {split: {r["group"] for r in rows} for split, rows in splits.items()}
    if any(
        groups[a] & groups[b]
        for a, b in (("train", "dev"), ("train", "test"), ("dev", "test"))
    ):
        raise ValueError("source group leakage")
    output.mkdir(parents=True)
    manifest = {
        "version": "coursepilot-sft-v2",
        "seed": 20260912,
        "label_origin": "AI-assisted synthetic; not independent teacher annotation",
        "parent_manifest_sha256": sha256(old / "manifest.json"),
        "source_sha256": sha256(source),
        "splits": {},
    }
    for split, rows in splits.items():
        rng.shuffle(rows)
        path = output / f"{split}.jsonl"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest["splits"][split] = {
            "count": len(rows),
            "sha256": sha256(path),
            "groups": sorted(groups[split]),
        }
    with (output / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k: len(v) for k, v in splits.items()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
