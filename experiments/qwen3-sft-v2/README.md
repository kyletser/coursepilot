# Qwen3 v2 data

Built from v1 train/dev only and `scripts/finetuning/curriculum_v2.tsv`.
V1 test outputs motivated distribution debugging, so v1 tests are now regression
data, not untouched validation for v2. No original course QA labels are loaded.

Counts: 1,448 train, 236 dev, 180 test. The new test uses 20 new source topics
with short/long passages, missing evidence, unspecified particulars and multi-fact
requests. This is still AI-assisted synthetic data with related templates, not
teacher blind annotation. Text is CC BY 4.0, attribution CoursePilot contributors.

Predeclared run: fresh base, NF4 r16/alpha32 QLoRA, learning rate 1e-4,
batch1 × accumulation8, one epoch (approximately v1's two-epoch optimizer-step
budget), max_length2048, seed20260911. Never silently truncate targets.
Inspect dev loss and dev generation before examining new test outputs.
Original course QA remains a release regression gate. Synthetic exact match
alone cannot justify replacing the default model.
