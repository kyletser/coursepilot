# Qwen3 measured results

## Final v2 evidence

- `qwen3-v2-summary.json`: 180-case given-evidence pair plus original 80-case full
  retrieval/Agent regression. Default model unchanged; adapter is experimentally verified.
- `qwen3-v2-external-span-summary.json`: first 50-case three-way transfer check.
  Its large literal-match gain mostly rewards copying, not semantic improvement.
- `qwen3-v2-contrast-summary.json`: new 50 present / 50 constructed missing-evidence
  cases with predeclared atomic answer keys; base / two-shot / QLoRA comparison.
- `qwen3-v2-raw.tar.gz`: 25 verified members; SHA256
  `a8cbbb5b7b63737f7348382086052b58b57d3473abe84b2a422e38b9a3c37fb5`.
- Interpretation: `docs/evaluation/qwen3-final-report.md`; resume and interview
  narrative: `docs/interview/coursepilot-resume-final.md`.

The v2 archive contains a per-member checksum manifest. Full external source
passages are replaced by their hashes in `outputs-compact.jsonl`; model outputs,
usage, response status and original raw-file hashes remain. Full original records
are retained under ignored `tmp/finetuning` locally. Reconstruct the fixed datasets
from the original CMRC file with the provided scripts and verify their hashes.
Then the external summarizer accepts `--outputs-name outputs-compact.jsonl` to
recompute metrics from the archive without the quoted source paragraphs.

CMRC-derived keys and excerpts follow CC BY-SA 4.0 attribution in
`scripts/finetuning/DATA_LICENSE.md`. No weights, SSH passwords or inference tokens
are included. Results are small controlled experiments, not production accuracy.

## Retained v1 evidence

- `qwen3-v1-summary.json`: verified paired summary and release decision.
- `qwen3-v1-raw.tar.gz`: original given-evidence train/dev/test records and four
  real retrieval QA reports, including deployment provenance and negative cases.
- Interpretation: `docs/evaluation/qwen3-v1-results.md`.

The synthetic lexical metric improves, but original course QA has one additional
false refusal. **Not approved as the default model.** The legacy field named
`citation_accuracy` is a gold-text alignment proxy, not semantic accuracy.

Reproduce the summary after extracting the archive to an empty directory:

```text
python scripts/finetuning/summarize_comparison.py --runs EXTRACTED/evidence-evaluation-v1/runs --agent EXTRACTED/agent-paired-v1 --dataset experiments/qwen3-sft-v1 --output NEW-SUMMARY.json --release-decision "not promoted: extra false refusal on original course QA"
```

Reports contain synthetic/seed-course text. Original course sample data is CC BY
4.0; the synthetic experiment is labeled AI-assisted and uses the same attribution
license. Model weights are not included. No SSH or inference credentials are stored.
