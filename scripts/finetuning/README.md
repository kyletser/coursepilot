# Qwen3 证据约束微调：复现入口

这里只是实验工具，不会自动替换 CoursePilot 默认模型。真实结论和限制见
`docs/evaluation/qwen3-v1-results.md`，最新阶段见实验执行记录。

## 固定工件

远端目录：`/home/ccnu/Code/LXP/coursepilot-qwen3-sft`，大小写敏感。
模型目录 `models/Qwen3-4B`；独立环境 `.venv`；第二轮实际使用的代码归档
`source-v6` 对应提交 `d3dbcefbfaa1e54464473384a0952a1f2a5dcdfd`。
源码归档通过 `export_bundle.py` 从干净 Git 创建，运行器逐文件验证哈希。
不要修改已运行实验的归档，后续代码用新的目录导出。

第二轮数据位于 `experiments/qwen3-sft-v2`，1,448 train / 236 dev / 180 test。
manifest 记录数据字节哈希。训练只读取 train/dev，不能拿 test 选 checkpoint。

## 第二轮训练命令（远端 Bash）

先确认 GPU 0 空闲，且没有其他本实验的推理服务占用显存。以下是重跑模板，
每次 `--output` 必须使用新的目录，不覆盖已有 `runs/qlora-v3`：

```bash
cd /home/ccnu/Code/LXP/coursepilot-qwen3-sft
BNB_CUDA_VERSION=124 CUDA_VISIBLE_DEVICES=0 .venv/bin/python -u \
  source-v6/scripts/finetuning/run_experiment.py train \
  --model models/Qwen3-4B --dataset source-v6/experiments/qwen3-sft-v2 \
  --output runs/qlora-v2-reproduction-01 --epochs 1 --max-length 2048
```

本次 Ubuntu 20.04 与默认 bitsandbytes CUDA126 二进制不兼容，进程级 CUDA124
覆盖已通过实际训练验证。不要据此修改系统 glibc 或其他用户的 CUDA 环境。

`training_result.json` 保存 loss、耗时、峰值 allocated 显存和适配器哈希；
`provenance.json` 保存源码、模型 revision、依赖、硬件和参数。

## 生成评估

对开发集检查完成并固定候选后，再分别运行原模型和适配器的新测试。原模型
省略 `--adapter`；适配器指向训练输出。二者使用相同数据、解码参数和代码。
以下适配器路径为本次已完成训练的真实工件；输出目录仍需保持唯一：

```bash
BNB_CUDA_VERSION=124 CUDA_VISIBLE_DEVICES=0 .venv/bin/python -u \
  source-v6/scripts/finetuning/run_experiment.py eval \
  --model models/Qwen3-4B --dataset source-v6/experiments/qwen3-sft-v2 \
  --adapter runs/qlora-v3/adapter --split test \
  --output runs/sft-v2-test-reproduction-01
```

这是给定证据实验，不包括检索。词法匹配为代理指标，不能称语义正确率。
`--limit` 仅可用于 smoke；不接受有限样本结果作为正式全量报告。

## 接入 CoursePilot

在远端 shell 安全注入随机 `COURSEPILOT_INFERENCE_TOKEN` 环境变量，至少
24 字符；不要把 token 写到源码、提交或共享命令记录。启动实际适配器：

```bash
BNB_CUDA_VERSION=124 CUDA_VISIBLE_DEVICES=0 .venv/bin/python -u \
  source-v6/scripts/finetuning/serve_adapter.py \
  --model models/Qwen3-4B --adapter runs/qlora-v3/adapter \
  --dataset source-v6/experiments/qwen3-sft-v2
```

服务仅监听 `127.0.0.1:18080`。本机建立 SSH 隧道：

```powershell
ssh -p 131 -N -L 127.0.0.1:18080:127.0.0.1:18080 ccnu@10.131.148.21
```

通过鉴权的 `/v1/models` 检查 adapter SHA，而不仅是名称。
`coursepilot-qwen3-base` 禁用适配器，`coursepilot-qwen3-sft` 启用适配器。
这是单请求串行实验服务，不提供流式输出或生产调度保证。

在本机给评测进程配置同一 token、独立实验数据库 `DATABASE_URL`、本地
`EMBEDDING_MODEL` / `RERANKER_MODEL` 和 `INDEX_ROOT`。不要覆盖原数据库。
从干净仓库运行 `run_agent_comparison.py --output <新目录>` 执行原 80 条课程 QA。
该脚本不修改默认 Ollama 配置。

外部迁移脚本 `run_external_qa.py prepare` 校验指定 CMRC trial 源文件哈希，
`eval` 运行固定 50 条给定证据问答。原始响应、重试及最终 Agent 输出分别保留；
摘要降级单独计数。这不是官方 CMRC EM/F1，也不作为训练数据。

本轮外部三方案对照在 `eval` 时添加
`--few-shot-training experiments/qwen3-sft-v2`：原模型、QLoRA、原模型 two-shot。
示例只取训练集的两个固定 ID，增加的输入 Token 单独统计。
运行完成后用 `summarize_external.py --dataset <固定数据目录> --run <运行目录>
--output <新的汇总文件>` 审计首轮生成、最终回答、摘要降级和实际 Token 成本。

## 发布与失败处理

保留失败日志，不覆盖 run；根据阶段选择新输出目录重新运行。
不得只因 loss 降低或合成集分数提高就切换默认模型。核对课程误拒答、不可回答
拒答、外部迁移、原始生成与摘要降级的差异及延迟后，人工记录发布决策。
没有通过门槛时保留原模型，并把负结果写入报告。
