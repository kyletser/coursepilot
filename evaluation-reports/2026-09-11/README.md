# Qwen3 v1 measured results

- `qwen3-v1-summary.json`: verified paired summary and release decision.
- `qwen3-v1-raw.tar.gz`: original given-evidence train/dev/test records and four
  real retrieval QA reports, including deployment provenance and negative cases.
- Interpretation: `docs/evaluation/qwen3-v1-results.md`.

The synthetic lexical metric improves, but original course QA has one additional
false refusal. **Not approved as the default model.** The legacy field named
`citation_accuracy` is a gold-text alignment proxy, not semantic accuracy.

Reproduce the summary after extracting the archive to an empty directory:

```text
python scripts/finetuning/summarize_comparison.py --runs EXTRACTED/evidence-evaluation-v1/runs --agent EXTRACTED/agent-paired-v1 --dataset experiments/qwen3-sft-v1 --output NEW-SUMMARY.json
```

Reports contain synthetic/seed-course text. Original course sample data is CC BY
4.0; the synthetic experiment is labeled AI-assisted and uses the same attribution
license. Model weights are not included. No SSH or inference credentials are stored.
