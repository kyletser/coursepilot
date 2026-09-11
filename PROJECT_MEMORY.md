# CoursePilot 项目记忆

> 本文件保存 CoursePilot 从选题到产品规格阶段形成的长期上下文，供后续开发任务、项目复盘和简历整理使用。产品需求以 `spec.md` 为准；若两者冲突，以最新用户指令和 `spec.md` 为准。

## 1. 项目由来

用户是教育领域的计算机科学与技术专业学生，目标方向是秋招 Agent 开发。现有项目 **Coderook** 是一个编程智能体，重点展示 Agent Runtime、工具调用、上下文管理、任务恢复和代码执行能力。

因此第二个项目不再重复 Coding Agent，而选择教育垂直领域的 **Agentic RAG**：

- Coderook 证明“Agent 怎么可靠地运行和执行工具”。
- CoursePilot 证明“Agent 如何在垂直业务中使用可信知识、用户状态和评测闭环解决问题”。
- 两个项目共同覆盖 Agent Runtime、RAG、知识工程、后端工程、安全和效果评测。

CoursePilot 的产品定位最终确定为：

> 面向高校计算机专业课程的知识图谱增强个性化学习 Agent。

## 2. 已锁定的产品决策

| 决策项 | 结论 |
|---|---|
| 产品名称 | CoursePilot |
| 目标用户 | Student、Teacher |
| 首版课程 | 数据结构、操作系统 |
| 实施节奏 | 连续推进，不按周切分 |
| 前端 | Next.js、TypeScript、Tailwind CSS |
| 后端 | FastAPI、Pydantic、SQLAlchemy、Alembic；Agent 编排为自研核心（LangGraph 取舍见决策记录 2026-08-31） |
| 异步任务 | Celery、Redis |
| 业务与向量数据库 | PostgreSQL、pgvector |
| 课程知识图谱 | Neo4j |
| Lexical 检索 | bm25s 为目标方案，当前使用自研等价实现（见决策记录 2026-08-31），每个课程版本独立索引 |
| Embedding | 本地 BGE-M3 |
| Reranker | 本地 BGE-Reranker-v2-m3 |
| Chat Model | OpenAI 兼容 API，通过环境变量配置 |
| 认证 | JWT；`STUDENT`、`TEACHER` 双角色 |
| 部署 | Docker Compose |
| 样例语料 | 仓库只提交开放许可的小型样例；完整教材由教师上传 |

## 3. 核心产品闭环

### Teacher

1. 创建课程并生成邀请码。
2. 上传 PDF、DOCX、PPTX、Markdown 或 TXT 资料。
3. 查看解析、切片、Embedding 和索引状态。
4. 审核模型抽取的知识点、关系和诊断题。
5. 发布审核通过的课程版本。
6. 查看学生薄弱知识分布，标注 Bad Case 并运行回归评测。

### Student

1. 通过邀请码加入课程。
2. 基于课程资料进行问答、概念比较和跨章节分析。
3. 查看文档、章节、页码和原文级引用。
4. 完成教师审核的诊断测验。
5. 查看知识点掌握度、薄弱点和个性化学习路径。
6. 回看学习记录与历史回答。

系统的可信边界是：未经教师审核的知识关系和题目不能影响学生问答、掌握度或学习路径；证据不足时必须拒答，不能用模型常识伪装成课程答案。

## 4. Agentic RAG 的差异化设计

核心意图：

- `TUTOR_QA`
- `CONCEPT_COMPARE`
- `DIAGNOSE`
- `QUIZ`
- `LEARNING_PATH`

核心工具：

- `search_course_material`
- `query_concept_graph`
- `get_learner_profile`
- `get_approved_quiz`
- `update_mastery_state`

检索主链路：安全检查 → 意图路由 → 学习者状态 → 审核知识图谱 → Dense/BM25 双路召回 → RRF → Cross-Encoder Rerank → 证据判断 → 至多一次 Query Rewrite → 带引用生成 → Grounding 校验 → 回答或拒答。

个性化不是让 LLM 随意记录聊天印象，而是使用可审计的学习状态：每名学生、每个知识点维护 Beta-Bernoulli 掌握度，只有教师审核题目的有效作答能够更新状态。

## 5. 明确不做的内容

- 不实现代码执行和代码修改，这些继续由 Coderook 覆盖。
- 不实现网页 Deep Research 或并行 Researcher。
- MVP 不做扫描 PDF OCR、语音、移动端和 LMS 集成。
- MVP 不扩到四门课程、学校级多租户和 Kubernetes。
- 不让未经审核的知识关系、题目或普通聊天推断写入正式学习状态。
- 不把大型开源项目换皮后当作原创项目。

## 6. 开源项目的参考边界

- **DeepTutor**：参考教育产品能力、知识空间、多 Agent 解题和长期记忆的产品设计；不是运行依赖。
- **RAGFlow**：参考文档入库、解析状态和知识库工程；不直接二次开发成 CoursePilot。
- **LightRAG / GraphRAG**：参考跨文档关系检索和图增强检索思想；不是完整应用框架依赖。
- **Open Deep Research**：参考规划、搜索和评测思路，但其网页研究场景不进入 MVP。

任何复用的第三方代码都必须检查许可证、保留必要声明，并记录到 NOTICE。仅参考产品思想不等于复制实现。

## 7. 秋招简历研究得到的约束

此前扫描了 `C:\Users\Administrator\Desktop\秋招\简历参考` 中 19 张简历图片，约对应 16 份独立简历。与 CoursePilot 最相关的结论是：

1. 有竞争力的项目必须回答“为什么做、个人做了什么、怎么验证、结果如何”。
2. RAG 项目应能说明解析与切片、Dense/BM25、RRF、Rerank、Query Rewrite、引用和评测，而不是只写“使用 LangChain 搭建知识库”。
3. 工程型 Agent 项目必须解释失败场景：幂等、重试、降级、索引切换、断线重连、权限隔离和回归测试。
4. 指标必须有冻结数据集、样本量、基线、定义和实验条件。
5. 简历最终只保留 1–2 个主项目。CoursePilot 与 Coderook 应分别承担“垂直 Agentic RAG”和“Agent Runtime”的证据角色。
6. 不能借用他人的模板数字，也不能在项目完成前预填 Recall、MRR 或性能提升。

原始横向分析仍保存在：

`C:\Users\Administrator\Desktop\秋招\简历参考\秋招Agent简历横向分析.md`

该文件是求职研究资料，不是 CoursePilot 的运行依赖，因此不复制原始简历图片或 OCR 数据到项目仓库。

## 8. 实施与验收原则

- `spec.md` 是当前产品和技术实现的唯一规格源。
- 所有服务端接口必须校验用户、角色、课程归属和资源归属；前端隐藏按钮不算权限控制。
- 文档、Chunk、Embedding、BM25 索引和发布版本必须可追溯。
- 新索引未就绪时继续服务旧版本，发布切换必须原子化并可回滚。
- 测验提交和掌握度更新必须幂等、事务化且可审计。
- 每个回答必须有可点击引用，或者明确说明课程资料证据不足。
- 评测集在调参前冻结，三组对照实验使用相同数据划分和判定口径。
- 所有简历指标只允许来自实际评测报告；没有实验结果就写工程事实，不造数字。

## 9. 当前状态

2026-09-11 Qwen3-4B QLoRA v1 已训练并完成 172 条给定证据测试及 80 条真实课程 QA 对照。
结果见 `docs/evaluation/qwen3-v1-results.md`：合成集拒答 30/40→40/40，但课程可回答误拒答
3/40→4/40，暂不批准默认模型替换。适配器已通过 SSH 隧道接入现有 OpenAI-compatible
Adapter；后续需改善长证据/转述分布并使用新的未调参测试。不能把 170/172 词法匹配称为
真实问答准确率，也不能把全链路的参考 claim 对齐指标称为语义引用准确率。

2026-09-11 新增进行中目标：在用户授权服务器 `/home/ccnu/Code/LXP/coursepilot-qwen3-sft` 微调 Qwen3-4B，改善当前 Agent 并产出可追溯简历实验。协议见 `docs/evaluation/qwen3-finetuning-protocol.md`。2026-09-07 复核发现旧引用指标存在遗漏未归因引用的偏差，旧“引用准确率 100%”暂不再用于简历。v1 训练和对照已完成，默认模型发布门槛尚未通过，具体状态以上方最新记录为准。

截至 2026-09-05：

- 已完成产品与技术规格，并将实施节奏调整为连续推进。
- 已初始化 Git、Next.js Web、FastAPI API、Celery Worker、Docker Compose 与 CI。
- 已实现统一响应契约、JWT 双角色、Refresh Token 轮换、课程、邀请码和 Enrollment 权限闭环及首个 Alembic 迁移。
- 已实现文档安全入库与分块、pgvector/BGE/词法混合检索及降级、证据关系抽取、候选图谱审核与 Outbox 发布、带引用可信问答、测验审核与幂等作答、掌握度、学习路径和冻结评测数据闭环。
- 已实现教师与学生双角色 Web 工作台，包含课程发布、知识图谱、学习历史、Bad Case、评测中心和学生学习汇总；本地关键回归包含 126 项后端测试以及前端 lint、类型检查和生产构建。
- 已提供两门原创 CC-BY-4.0 开放样例、一键真实入库脚本和三组检索基线运行器；脚本遇到模型或基础设施缺失时明确失败。种子脚本额外创建演示规模、人工标注的 DRAFT 评测数据集（检索用例绑定真实 chunk ID），不冻结、不产出任何指标。
- 2026-08-31 修复审查确认的高优先缺陷：`CourseIndex` 记录语料覆盖集（迁移 0003）并贯通检索、发布与旧会话服务（版本隔离）；证据分数门槛改为真实校准（BM25 覆盖分、dense 相似度锚定、rerank sigmoid），移除按排名伪造的兜底分；Worker 进程级复用 BGE-M3；前端测验幂等键随题生成、网络瞬断不再清除登录态；Markdown 前置元数据入库即剥离；文档与决策记录同步（LangGraph/bm25s/确定性抽取）。
- 2026-09-01 完成代码与功能审查报告中全部高/中/低优先项修复：评测 Runner 扩展至意图路由、学习路径与端到端问答；图谱候选批量审核端点；LLM 调用指数退避重试；前端 CSRF/Secure Cookie/CSP/超时/断流恢复加固；Dockerfile 多阶段与非 root 运行、`.dockerignore`、compose 弱凭据说明；CI 引入 ruff check、ruff format 校验与 mypy。本地回归 146 项后端测试通过（另 1 项未配置 PostgreSQL 测试库而跳过），ruff 与 mypy 全绿。
- 已在本机 PostgreSQL/pgvector、Redis、Neo4j 和 Ollama 环境完成真实入库与正式冻结评测：内部 200 条 Retrieval、80 条 End-to-End QA、50 条 Intent Routing、30 条 Learning Path；外部使用 CMRC 2018 固定 50 问题/200 passage 子集做检索迁移测试。原始报告均绑定干净 Git 提交并归档在 `evaluation-reports/2026-09-05/`。
- 内部宏平均结果：Hybrid + Reranker 相对 Dense-only 的 Recall@5 从 0.945 提升到 1.0，MRR@5 从 0.884 提升到 0.986；不可回答拒答率与引用准确率均为 1.0，引用覆盖率 0.9，误拒答率 0.075；路由、路径合法性和教师一致率均为 1.0。所有结论必须连同样本量和限制表述。
- 在相同 40 条 DS QA 上把候选参数从 20/20/10 裁剪到 10/8/5，总耗时由 567.3s 降到 402.7s（-29.0%），质量指标不变；KG 评测复用 Hybrid 基础结果，只单独测个性化增量。
- CMRC 外部子集上 Dense 已达到 Recall@5/MRR@5=1.0，CPU Reranker 没有准确率增益且显著更慢；该结果只作为跨域检索 sanity check 和“选择性重排”依据，不称为 CMRC 官方成绩。

## 10. 决策记录维护规则

后续出现会影响产品范围、数据契约、核心架构或验收指标的决定时，在下表追加一行；日常实现细节不需要记录。

| 日期 | 决策 | 原因 | 影响 |
|---|---|---|---|
| 2026-09-11 | 微调实验按检索、生成、安全治理、数据工程与部署分层比较技术方案 | 用户要求展示不同技术取舍及完整面试故事；避免把多个变量的联合变化归因于微调 | 配对模型使用相同量化与提示词；全参数 SFT / BF16 LoRA / DPO 未执行前不得写成项目经历；课程回归与外部迁移不一致时不默认推广 |
| 2026-09-11 | 引用评测按生成 Claim–Citation 配对计数，未归因引用判负；仅准确引用增加覆盖；允许模型用空 claims 明确拒答 | 旧归因按 gold source 先筛选会遗漏错误引用，且旧输出 Schema 无法表达模型拒答 | 保留旧报告，新指标标记 generated-pair-lexical-v2；公开 API 仍使用已有拒答状态；原模型与微调模型使用相同契约比较 |
| 2026-08-30 | 使用 CoursePilot 作为项目名 | 突出课程学习副驾驶定位 | 仓库、文档和产品统一命名 |
| 2026-08-30 | 首版仅做数据结构与操作系统 | 两门课程足以验证跨课程隔离和领域适配，同时符合 8 周范围 | 语料、评测集和演示围绕两门课程 |
| 2026-08-30 | 使用教师审核的 Neo4j 课程图谱 | 体现垂直知识工程，并防止候选关系直接污染学生结果 | 需要候选审核、发布和一致性机制 |
| 2026-08-30 | Coderook 与 CoursePilot 双项目互补 | 避免两个 Coding Agent 同质化 | 简历分别突出 Runtime 与 Agentic RAG |
| 2026-08-30 | 实施改为连续推进，不按周切分 | 用户要求尽快完成并避免时间节奏限制 | 规格第 14 节保留能力依赖顺序，不再绑定周次 |
| 2026-08-30 | 模型不可用时显式降级且禁止伪造向量 | 保持课程证据和实验结果可追溯 | Dense 状态保留为待处理，问答仅使用真实可用索引；恢复模型后重建 |
| 2026-08-30 | 确定性关系只接受标题层级和显式前置标记 | 防止章节顺序或模型常识被误当成课程依赖 | 所有关系仍进入 PENDING，由教师审核后才影响 Student |
| 2026-08-30 | 三组基线只对冻结数据和真实索引执行 | 防止工程测试或外部提交结果冒充模型效果 | EvalRun 与报告固化版本和环境；缺少个性化实绩时直接失败 |
| 2026-08-31 | Agent 编排不引入 LangGraph，使用自研 `TrustedAgentCore` 序列（guard → route → retrieve → grade → rewrite → generate → verify） | MVP 依赖最小化；预算、grounding 与信任边界的确定性控制用显式代码更易审计与测试；spec FR-AGENT-003 的节点行为已等价实现 | 第 2 节“后端”中的 LangGraph 仅作为行为规格参考；如后续需要状态机回放/检查点再评估引入 |
| 2026-08-31 | 词法检索先用自研 `LightweightBM25Index`，为 bm25s 预留适配位 | 语料规模小（单课程演示级），自研实现可持久化可复现的工件并输出带归一化覆盖分的候选；避免 MVP 阶段额外原生依赖 | 第 2 节“Lexical 检索”的 bm25s 目标不变；工件 schema 已兼容后续替换 |
| 2026-08-31 | 知识候选抽取用 `deterministic-heading-v1` 而非 LLM 抽取 | 标题结构可信、可复现、零额外成本；候选仍全部进入教师审核 | FR-GRAPH-002 的 LLM 抽取路径保留为后续增强 |
| 2026-08-31 | Student 图谱、题目、掌握度更新和学习路径均绑定当前活动索引 | 防止新版本发布后旧版已审核事实或题目继续影响正式学习状态 | 历史聊天仍可读取其固定索引证据，但新的学习动作只使用当前活动版本 |
| 2026-08-31 | Chat 中的 QUIZ、LEARNING_PATH、DIAGNOSE 由确定性业务处理器执行 | Agent 路由必须产生真实学习动作，不能返回占位提示；诊断只读取该生真实有效作答 | TUTOR_QA 与 CONCEPT_COMPARE 继续走受引用约束的检索生成链路 |
| 2026-08-31 | 浏览器令牌迁移到同源 BFF 的 HttpOnly Cookie | 避免长期 Refresh Token 被同源脚本读取，同时通过 Origin 校验和 SameSite 限制 CSRF | API 仍保留 Bearer 契约供 CLI 和服务间调用 |
| 2026-08-31 | Eval Run API 只排队服务端真实三基线运行，不接受客户端 Case 结果 | 防止外部提交结果被持久化为成功指标 | CLI 与服务端入口复用同一个真实 Runner，运行结果保留完整来源 |
| 2026-08-31 | `CourseIndex` 记录 `covered_document_version_ids`，检索与发布只作用于该覆盖集 | 修复版本隔离缺陷：旧版本会话不得引用新发布语料，发布不得翻转索引未覆盖的文档版本 | 数据契约新增可空 JSON 列（迁移 0003）；无覆盖集的迁移前索引检索时失败关闭，发布前必须重建 |
| 2026-09-01 | 新增图谱候选批量审核端点 `POST /courses/{course_id}/graph/candidates/batch-review`（每批 1–50 项） | 教师审核需一次处理多条候选，单条审核效率过低且难以统一事务语义 | 单项校验失败不中断整批，逐条返回结果与错误码；权限、归属、环检测仍在服务端逐项校验 |
| 2026-09-01 | LLM 调用对瞬时故障采用指数退避重试（408/425/429/500/502/503/504 与传输层错误），其余 4xx 视为契约错误直接失败 | 满足 spec §9.2“仅在首 token 前可重试”，避免对占位/无效配置无谓重试并防止打满上游 | `OpenAICompatibleChatAdapter` 内置退避；重试预算耗尽后失败关闭，交由确定性降级链路 |
| 2026-09-01 | 评测 Runner 扩展覆盖 `INTENT_ROUTING`、`LEARNING_PATH`、`END_TO_END_QA` 三类数据集 | 此前仅检索基线可量化，路由/路径/端到端问答缺失可复现的评测口径 | 每类数据集有独立指标与阈值校验；沿用冻结数据集与真实运行约束，不产出伪造指标 |
| 2026-09-01 | 评测运行改由 Celery 异步调度（`coursepilot.evaluation.run`），Router 通过可注入的 `evaluation_run_dispatcher` 排队 | 长耗时评测阻塞 API 请求会带来超时与资源占用问题 | 运行状态入库可查询；测试可注入同步/记录型 dispatcher，保持行为可验证 |
| 2026-09-01 | 前端安全加固：CSRF 失败关闭（Origin→Sec-Fetch-Site→Referer）、反代下按 `x-forwarded-proto` 置 `Secure`、CSP 头、30s 请求超时、SSE 连接超时与 `CHAT_STREAM_INTERRUPTED` 后按历史恢复 | 防跨站请求伪造、避免非 HTTPS 泄漏 Cookie、限制长时间挂起并保证断流可恢复 | BFF 统一处理；API 侧 Bearer 契约不变 |
| 2026-09-01 | 引入 mypy 静态类型检查（`app` 与 `tests`），并在 CI 中加入 `ruff check`、`ruff format --check`、`mypy` | 提前捕获类型与契约不一致问题，补齐质量门槛 | `pyproject.toml` 新增 `[tool.mypy]` 与 test 依赖；CI backend 作业更名为 lint+typecheck+tests+migration |
| 2026-09-05 | 正式评测固定使用本地 Ollama `qwen3:4b`，不调用外部模型 API | 保证隐私、零按次费用和可复现；4B 模型只用于作品集验证，不外推生产效果 | 报告记录模型、硬件与 `external_model_api_used=false`；服务端仍执行 Claim/Citation/Grounding 约束 |
| 2026-09-05 | 内部正式检索参数采用 10/8/5，并在 KG 基线中复用同一 Hybrid 基础结果 | 相同 DS 40 条 QA 总耗时下降 29.0% 且质量不变；避免评测重复计算 | 报告同时记录等效耗时、实际执行耗时和复用标记，不将复用伪装成在线性能 |
| 2026-09-05 | 外部测试使用 CMRC 2018 固定检索子集，明确不作为官方榜单成绩 | 检查内部优化是否只适用于自建课程，同时遵守 CC BY-SA 4.0 和结果边界 | 固定上游提交、源文件 SHA256、选择算法、50 问题/200 passage；不在仓库重新分发源数据 |
