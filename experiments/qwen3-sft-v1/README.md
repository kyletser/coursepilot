# Qwen3 SFT v1 dataset

Original AI-assisted synthetic course evidence and behavior examples, not teacher
blind annotations. Data text is provided under CC BY 4.0 (attribution: CoursePilot
contributors). Model weights are separate, under the upstream Apache 2.0 license.

`scripts/finetuning/build_dataset.py` constructs the frozen files from the original
`curriculum.tsv`. Train/dev/test contain 688/100/172 examples respectively. Source
topics and simulated machine identifiers are disjoint; paraphrase templates are
similar, so this is not a broad out-of-distribution benchmark. Exact hashes and
source groups are recorded in `manifest.json`.

Only training examples receive gradient updates. Development loss selects the
checkpoint. Test outputs must not inform training or checkpoint selection. Preserve
negative findings. Citation/text matching metrics are lexical proxies and must not
be presented as semantic entailment or production accuracy.

Run artifacts and model weights belong in the server experiment's `runs/` and
`models/`, not in this dataset directory. See the experiment protocol for the
separate real retrieval/Agent integration gate.
