# Qwen3 experiment execution record

Updated 2026-09-11. In progress; not a final report or resume evidence.

Remote root: `/home/ccnu/Code/LXP/coursepilot-qwen3-sft`.
Base model revision: `1cfa9a7208912126459214e8b04321603b3df60c`.
Official-file SHA verification completed; `models/Qwen3-4B/download_manifest.json`
records the download. Transport used a mirror's `main` URL, but file contents were
checked against official fixed-revision hashes rather than trusting that branch.

## Runs and retained failures

- `runs/base-dev-smoke-v1`: missing pydantic-settings; no model outputs.
- `runs/base-dev-smoke-v2`: cuda126 bitsandbytes binary required GLIBC_2.34;
  no successful outputs. Per-process `BNB_CUDA_VERSION=124` resolved loading.
- `runs/base-dev-smoke-v3`: four development examples completed. Smoke only.
- `runs/base-dev-v1`: 100 development cases completed on source `d760fb6`;
  raw outputs and provenance retained. Raw schema 99/100; raw lexical task proxy
  73/100; negative refusals 19/24; false refusals 2/76. These are synthetic
  development-set findings, **not final test or semantic correctness claims**.
- `runs/qlora-v1`: deliberately stopped early after Trainer warned that PEFT
  label names were unspecified. No checkpoint from this run will be selected.
- `runs/qlora-v2`: started from the original base, not the interrupted adapter;
  explicit `label_names=["labels"]`, `prediction_loss_only=True`. Source commit
  `5a157ec009390dd96f5577060eaf672cf7204b09`. Training process observed live at
  PID 5537 with finite loss/gradient norm. PID is a historical observation, not
  future proof of liveness; recheck process and log before further action.

Environment: isolated `.venv`, inherited PyTorch 2.7.0+cu126, Transformers 4.51.3,
PEFT 0.15.2, Accelerate 1.6.0, bitsandbytes 0.45.5, pydantic 2.10.6;
single RTX 4090 D (GPU 0), NF4 double quantization, BF16 compute. Training uses
688 train / 100 dev examples, r16 all-linear LoRA, 2 epochs, batch 1 × accumulation
8, learning rate 1e-4. Untouched test contains 172 examples.

The server runs clean Git archives, not a Git checkout. The runner verifies all
exported file hashes in `source_provenance.json`; the harmless git lookup error
precedes this fallback. Future source edits must use a new archive, never patch
an already recorded experiment's source directory.

## Remaining gates

1. Confirm development eval_loss is present and checkpoint selection completes.
2. Load saved adapter and evaluate original/trained on the untouched test under
   the same source version, precision, prompt and hardware. Preserve all outputs.
3. Audit failures and paraphrases: lexical matching can undercount valid concise
   answers and can overcount semantically wrong high-overlap statements.
4. Validate the new loopback-only inference server (currently implemented but not
   runtime-verified), connect the real CoursePilot adapter over SSH, and compare
   actual retrieval QA. Do not replace this gate with supplied-evidence tests.
5. Produce final report, limitations, resume bullets and interview explanations.

Local checks: 153 backend tests passed, one independent PostgreSQL integration
test skipped without its configured test database; mypy and repository-configured
Ruff passed. These are engineering checks, not model quality results.
