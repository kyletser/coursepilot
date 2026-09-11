# CoursePilot Agent 评测报告（2026-09-05）

> 历史报告保留：后续审计发现旧引用指标漏计部分错误引用，因此本页“引用准确率 100%”不再用于简历。检索 Recall/MRR 与引用评分是不同口径；本轮模型实验及修正说明见 `qwen3-final-report.md`。

## 结论

CoursePilot 已在两门内部课程的冻结数据集上完成真实本地模型评测，并在 CMRC 2018 固定子集上完成外部检索迁移测试。内部结果达到项目规格中的检索、引用、拒答、路由和学习路径门槛；但本报告不把演示语料结果外推为生产效果。

最适合写入简历的结论是：

- 200 条内部检索中，Dense-only 到 Hybrid + Reranker 的 Recall@5 从 94.5% 提升到 100%，MRR@5 从 88.4% 提升到 98.6%。
- 80 条可信问答中，不可回答拒答率 100%，引用准确率 100%，引用覆盖率 90%，可回答问题误拒答率 7.5%。
- 50 条意图路由 Macro-F1 为 1.0；30 条学习路径的前置依赖合法率和教师路径一致率均为 100%。
- 在完全相同的 40 条数据结构问答上，将检索候选从 `20/20/10` 裁剪到 `10/8/5` 后，总运行时间从 567.3 秒降至 402.7 秒，下降 29.0%，质量指标不变。

## 环境与模型

| 项目 | 实际配置 |
|---|---|
| 操作系统 | Windows 11，基础设施运行在本机 WSL2 |
| CPU | AMD Ryzen 7 5700X，16 logical CPUs |
| GPU | AMD Radeon RX 7650 GRE 4GB；Ollama 使用 GPU |
| Python | 3.12.13 |
| LLM | Ollama `qwen3:4b`，本地运行 |
| Embedding | `BAAI/bge-m3`，本地缓存，PyTorch CPU |
| Reranker | `BAAI/bge-reranker-v2-m3`，本地缓存，PyTorch CPU |
| 数据库 | PostgreSQL 16 + pgvector；Redis；Neo4j |
| 外部模型 API | 未使用 |

正式内部报告绑定 Git 提交 `85f67a403ec2f3f2d45baabf4abf8fc99d451e4a`，外部报告绑定 `60ba63109b4417111b4c73d0804b9eda24e7f286`，两者均为 `git_dirty=false`。

## 数据集与方法

内部冻结集覆盖两门课程：数据结构与操作系统。

| 类型 | 数据结构 | 操作系统 | 合计 |
|---|---:|---:|---:|
| Retrieval | 100 | 100 | 200 |
| End-to-End QA | 40 | 40 | 80 |
| Intent Routing | 25 | 25 | 50 |
| Learning Path | 15 | 15 | 30 |

问答集每门课程包含 20 条可回答和 20 条不可回答问题。正式问答逐条执行 Guard、路由、Dense/BM25、RRF、Reranker、证据门控、一次 Query Rewrite、`qwen3:4b` 结构化 Claim 生成、Citation 白名单和 Grounding 校验。

三组内部检索基线为：

1. `dense_only`：只使用 BGE-M3 向量召回。
2. `hybrid_rerank`：Dense + BM25、RRF 融合、BGE Cross-Encoder 重排。
3. `kg_personalized`：在同一份 Hybrid 结果上，根据已审核前置关系和真实 MasteryState 重排。

KG 评测复用 Hybrid 基础结果，仅执行个性化部分；报告同时记录等效端到端耗时和实际增量耗时，不把复用伪装成在线延迟。

## 内部结果

### 检索

| 课程 | 基线 | Recall@5 | MRR@5 | nDCG@10 |
|---|---|---:|---:|---:|
| 数据结构 | Dense-only | 0.970 | 0.917 | 0.930 |
| 数据结构 | Hybrid + Reranker | 1.000 | 0.977 | 0.983 |
| 数据结构 | KG Personalized | 1.000 | 0.982 | 0.986 |
| 操作系统 | Dense-only | 0.920 | 0.851 | 0.868 |
| 操作系统 | Hybrid + Reranker | 1.000 | 0.995 | 0.996 |
| 操作系统 | KG Personalized | 1.000 | 0.995 | 0.996 |
| 两课宏平均 | Dense-only | 0.945 | 0.884 | 0.899 |
| 两课宏平均 | Hybrid + Reranker | 1.000 | 0.986 | 0.989 |
| 两课宏平均 | KG Personalized | 1.000 | 0.988 | 0.991 |

Hybrid 的收益在两门课上都成立；KG 仅在数据结构上有小幅 MRR 增益，在操作系统上没有增益。因此可以说“实现并验证了个性化重排”，不能说“KG 对所有场景都显著提升”。

### 可信问答

| 课程 | 不可回答拒答率 | 误拒答率 | 引用准确率 | 引用覆盖率 |
|---|---:|---:|---:|---:|
| 数据结构 | 1.000 | 0.050 | 1.000 | 0.900 |
| 操作系统 | 1.000 | 0.100 | 1.000 | 0.900 |
| 合计 | 1.000 | 0.075 | 1.000 | 0.900 |

3 个误拒答案例的正确 Chunk 均排在 Hybrid 第 1 名，但 Reranker 分数为 0.46、0.47、0.53，低于 EvidencePolicy 的 0.55 门槛。这说明瓶颈是证据分数校准，不是召回。当前保留保守阈值：优先维持 40/40 不可回答问题全部拒答，而不为追求覆盖率盲目放宽门槛。

### 路由与学习路径

| 指标 | 结果 |
|---|---:|
| Intent Routing Macro-F1（50 条） | 1.000 |
| 前置依赖合法率（34 条被评估依赖） | 1.000 |
| 完整合法路径率（27 条受约束路径） | 1.000 |
| 教师路径一致率（30 条） | 1.000 |

路由 v2 曾将“为什么我学不会数据结构”误判为普通问答，因为规则只匹配连续的“为什么学不会”。将诊断特征改为“学不会”并加入回归样本后，Macro-F1 从 0.9596 提升到 1.0。

学习路径 v2 的 0.875 合法率来自评测标签错误：深度为 1 的路径被拿去与深度为 2 的完整边集合比较。修复为“只评估当前 teacher path 范围内的边”后为 1.0。这是评测可信度修复，不应包装成学习路径算法提升。

## 性能与取舍

| 场景 | 数据结构 | 操作系统 | 合计均值 |
|---|---:|---:|---:|
| Dense-only 检索 | 920 ms/题 | 200 ms/题 | 560 ms/题 |
| Hybrid + Reranker | 2197 ms/题 | 1815 ms/题 | 2006 ms/题 |
| KG 个性化增量 | 3.65 ms/题 | 2.43 ms/题 | 3.04 ms/题 |
| 完整可信问答 | 10.07 s/题 | 13.14 s/题 | 11.60 s/题 |

以上是单机顺序执行的总耗时均值，不是并发压测 P95。问答原始报告尚未保存逐案例 TTFT/P95；简历和面试中不能将均值表述为 P95。

在完全相同的 40 条 DS QA 上：

| 版本 | 检索候选参数 | 总耗时 | 质量 |
|---|---|---:|---|
| Before，`0e54edd` | 20 / 20 / 10 | 567.3 s | 拒答、引用指标与 After 相同 |
| After，`85f67a4` | 10 / 8 / 5 | 402.7 s | 不变 |

总耗时下降 29.0%。另外，KG 评测不再重复执行相同 Hybrid 基础检索，200 条内部案例只增加约 3.04 ms/题的个性化计算成本。

## 外部迁移测试

外部数据选用 [CMRC 2018 官方仓库](https://github.com/ymcui/cmrc2018) 的 trial 文件，固定上游提交 `c0eb1b6ba219847457e6af3180da722bbeb656af`，源文件 SHA256 为 `a976d1fd5efc173bd58ff1c57e958de5f49fed633a7bfb8e0e402e5490d75f5e`，许可证为 CC BY-SA 4.0。CMRC 2018 是公开中文抽取式阅读理解数据集，论文见 [ACL Anthology](https://aclanthology.org/D19-1600/)。

选择规则固定为：按 `context_id` 排序，取前 200 个可用 passage 作为候选池，从前 50 个 passage 各取第一个问题。该测试只衡量 CoursePilot 检索组件的跨域迁移，不是 CMRC 官方榜单或完整 QA 成绩。

| 基线 | Recall@5 | MRR@5 | 平均耗时 |
|---|---:|---:|---:|
| Dense-only | 1.000 | 1.000 | 122 ms/题 |
| Hybrid + Reranker | 1.000 | 1.000 | 9029 ms/题 |

结论不是“外部数据满分”，而是这个固定子集对 BGE-M3 已经容易，Cross-Encoder 没有带来额外准确率，却在 CPU 上显著增加成本。生产策略应根据查询难度或 Dense 置信度选择性启用 Reranker，并优先使用 GPU/批处理。

## 评测过程中修复的问题

1. 删除按排名制造的证据兜底分，避免普通词法命中越过证据门槛。
2. 修复旧会话的词法回退可能读到新发布文档，所有路线统一绑定索引覆盖版本集合。
3. 修复 BGE 模型缓存路径误用 `HF_HOME` 而非 `HF_HOME/hub`。
4. 支持 Dense 降级入库任务在模型恢复后原地重试，不创建重复文档版本或索引。
5. 修复正式评测的引用归因，使“模型忠实改写”按冻结 supporting Chunk 判断，同时保留运行时 Claim 与原文 Grounding。
6. 修复路由中的“为什么我学不会”漏判。
7. 修复学习路径评测边范围错误。
8. 修复三基线报告汇总读取错误，并避免 KG 评测重复 Hybrid 计算。

## 复现

内部评测需要本地 PostgreSQL/pgvector、已发布的两门演示课程、本地 BGE 模型缓存和 Ollama `qwen3:4b`：

```powershell
python scripts/seed_frozen_evaluations.py
python scripts/run_frozen_evaluations.py --output-dir <empty-output-directory>
```

外部检索迁移测试：

```powershell
python scripts/run_cmrc_external_retrieval.py `
  --source <cmrc2018_trial.json> `
  --source-commit c0eb1b6ba219847457e6af3180da722bbeb656af `
  --sample-count 50 `
  --candidate-count 200 `
  --output <new-report.json>
```

机器可读摘要、原始 Trace 压缩包及校验值见 [评测证据目录](../../evaluation-reports/2026-09-05/README.md)。

## 不能忽略的局限

- 内部数据来自两门演示课程，不代表生产流量分布。
- 外部测试只覆盖 50 个问题和 200 个候选 passage，且只测检索迁移。
- 当前 BGE/Reranker 使用 CPU，Hybrid 延迟不能代表 GPU 部署。
- `qwen3:4b` 用于本地可复现演示，不代表生产大模型质量。
- 完整问答只记录顺序执行总耗时与均值，尚无并发、TTFT 和逐案例 P95。
- 证据阈值仍需更大校准集；当前选择体现“宁可少答，也不无证据生成”。
