# Qwen3 experiment execution record

Updated 2026-09-11. In progress; not a final report or resume evidence.

## V2 candidate fixed before new test generation

`runs/qlora-v3` completed 1 epoch / 181 optimizer steps on 1,448 training examples.
Wall time 598.124 seconds; peak allocated GPU memory 6,640,988,160 bytes; development
loss 0.0030913625. Saved adapter SHA256:
`c7f642d6d89a6551b3d13f64a459673b87512ca5212b7833af58f223c18b92c3`.
Dataset manifest SHA256:
`cc886080e4c6f609792285e0c7466f7ad5689746b84b1c62679bba202f5f8cf0`.

The 236-case development generation completed: 231 lexical-proxy successes,
64/64 unanswerable refusals, 2/172 false refusals, all schema-valid. Five proxy
failures were inspected: two abstentions, one wrong fact selection, and two extra
fact/label mismatches. These are development observations, not independent results.
No training/prompt edits are planned for this candidate. Fix its hash now and run
the new 180-case base/SFT test with `source-v6`, followed by the original 80-case
course regression and the frozen external 50-case transfer check. If it fails,
retain results and do not promote by selecting favorable cases.

Training/dev process has exited and released GPU 0. Historical live-process notes
below remain only as an execution history, not current state.

Latest: v1 given-evidence and full-chain runs are now complete and archived under
`evaluation-reports/2026-09-11`. See `docs/evaluation/qwen3-v1-results.md` for the
verified results and decision **not to promote v1**. Process IDs below are history.
The next work is a better long-evidence/paraphrase train/dev distribution plus a
new independent test; old tests remain regression data, not new blind evidence.

V2 update: source `d3dbcefbfaa1e54464473384a0952a1f2a5dcdfd` exported to remote
`source-v6`; dataset has 1,448 train / 236 dev / 180 test. Train/dev token-length
preflight checked 1,684 examples, maximum 952 tokens, zero above the 2,048 budget.
`runs/qlora-v3` starts fresh from base with one epoch (181 optimizer steps), not
from v1's adapter. Then `runs/sft-v2-dev-v1` evaluates development generation.
Log: `runs/qlora-v3-and-dev.log`. New test generation has not been scheduled yet.
V1 inference process was stopped to release memory; the default Ollama was not
changed. The original artifacts remain available for rollback and comparison.

External generation check prepared before any v2 test results: 50 CMRC trial
questions, excluding the old retrieval benchmark's first 50 context questions.
Ignored dataset path `tmp/finetuning/cmrc-generation-v1`; cases SHA256
`6f98b74ae05dde4e36ccc0830d72543325f932db243703da05437018e45cc0be`.
Source SHA256 `a976d1fd5efc173bd58ff1c57e958de5f49fed633a7bfb8e0e402e5490d75f5e`,
CC BY-SA 4.0. `run_external_qa.py` reconstructs it deterministically from the local
upstream file, records endpoint adapter provenance, and alternates model order.
It reports span inclusion with the correct source label, not official CMRC EM/F1;
summary fallbacks are separately counted so extractive fallback does not masquerade
as successful model generation. This dataset is never used to train or select a
checkpoint. No results exist yet.

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

## Verified training completion

`qlora-v2` finished both epochs in 497.04 seconds. Development losses were
0.00553608 (epoch 1) and 0.00562742 (epoch 2); the saved adapter comes from
checkpoint-86 (epoch 1), chosen by the predeclared lowest-loss rule. Peak allocated
GPU memory was 5,792,137,216 bytes. Adapter SHA256:
`dd13c58d13bc6b052c75dd1ab9ac0383e9bdc2301840f33b69aa3a8b50600f85`.
Training result and provenance have been copied to local ignored `tmp/finetuning/qlora-v2`.

Saved-adapter development generation completed successfully. Paired test runs
are live under parent PID 5654, log `runs/paired-evaluation-v1.log`, using source-v4
for both models. Full test conclusions are pending; do not tune using test outputs.

The original frozen evaluation database was found in Ubuntu WSL's native Docker,
distinct from Docker Desktop's empty default database. It was backed up and restored
to a **new** Docker Desktop database `coursepilot_sft_eval`; original data was not
overwritten. Windows lexical-index paths still exist. BGE caches were not found, so
fixed-revision, hash-verified downloads are in progress in `data/models/`:
BGE-M3 `5617a9f61b028005a4858fdac845db406aefb181` and reranker
`953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`. Metadata is retained in ignored
`tmp/finetuning`. Do not claim the restored retrieval environment is ready yet.

## Remaining gates

1. Training/checkpoint-selection gate passed; preserve its logs and hashes.
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
