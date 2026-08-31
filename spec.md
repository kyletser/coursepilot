# CoursePilot 产品与技术规格

> 面向计算机专业课程的知识图谱增强个性化学习 Agent

| 字段 | 内容 |
|---|---|
| 文档版本 | v0.1.0 |
| 产品阶段 | 秋招作品集 MVP |
| 实施节奏 | 连续推进，不按周切分 |
| 首版课程 | 数据结构、操作系统 |
| 目标用户 | Student、Teacher |
| 标准部署 | Docker Compose 单机部署 |
| 文档状态 | Approved for implementation |

## 1. 产品概述

### 1.1 产品定位

CoursePilot 是一个面向高校计算机专业课程的知识图谱增强个性化学习 Agent。它以教师提供的教材、课件、教学大纲、实验指导书和题库为唯一权威知识来源，为学生提供带原文引用的课程问答、概念比较、知识缺口诊断、诊断测验和个性化学习路径。

CoursePilot 不试图替代教师，也不把大模型生成内容直接视为正确知识。所有影响课程知识图谱和学生掌握度的内容必须经过确定性规则或教师审核。

CoursePilot 与 Coderook 形成互补：

- Coderook 证明 Agent Runtime、工具调用、上下文治理、代码执行和任务恢复能力。
- CoursePilot 证明垂直领域 Agentic RAG、知识工程、学习者建模、业务权限和效果评测能力。

### 1.2 核心问题

CoursePilot 解决以下问题：

1. 课程资料分散在教材、课件、教学大纲和实验文档中，学生难以快速定位权威答案。
2. 普通知识库问答只回答当前问题，不知道学生缺少哪些前置知识。
3. 通用大模型的回答缺少课程范围、版本和引用约束，容易产生无法验证的内容。
4. 学生通常不知道自己真正薄弱的知识点，也不知道下一步应该复习什么。
5. 教师缺少对 AI 回答、知识关系、题目质量和学生薄弱点的可治理入口。

### 1.3 产品原则

- **证据优先**：课程结论必须绑定可展示的原文证据。
- **不确定即拒答**：没有充分证据时明确说明资料不足。
- **教师治理**：知识关系和诊断题必须经教师审核后才能影响学生。
- **结构化记忆**：只保存可解释的掌握度、错误类型和学习目标，不从闲聊自动推断长期事实。
- **可评测**：每项核心能力必须有冻结数据集、基线和回归门槛。
- **范围克制**：首版只覆盖两门课程，不实现代码执行、网页研究和学校级多租户。

## 2. 目标与成功标准

### 2.1 MVP 目标

MVP 必须完成以下闭环：

1. 教师创建课程并上传开放样例或自有课程资料。
2. 系统完成解析、结构感知切片、向量索引和词法索引。
3. 系统抽取知识点与关系候选，教师审核后发布知识图谱。
4. 学生通过邀请码加入课程并进行带引用的课程问答。
5. 学生完成教师审核的诊断题，系统更新知识点掌握度。
6. 系统结合目标知识点、前置依赖和掌握度生成学习路径。
7. 教师查看 Bad Case、学生薄弱点和离线评测结果。

### 2.2 质量门槛

以下指标使用冻结评测集计算，达标后方可将 MVP 标记为完成：

| 指标 | 门槛 |
|---|---:|
| Retrieval Recall@5 | ≥ 0.80 |
| Retrieval MRR@5 | ≥ 0.70 |
| 引用准确率 | ≥ 0.90 |
| 不可回答问题拒答率 | ≥ 0.90 |
| 意图路由 Macro-F1 | ≥ 0.90 |
| 学习路径前置依赖合法率 | 100% |
| 学习路径与教师标注一致率 | ≥ 0.80 |
| 跨用户、跨课程越权用例 | 0 通过漏洞 |
| 干净环境 Docker Compose 启动 | 通过 |

这些是验收目标，不是预先声明的项目成果。简历只能引用实际评测报告中已经获得的数值。

### 2.3 非目标

MVP 不包含：

- 代码执行、代码修改、仓库分析或编程题自动解答。
- 网页搜索、Deep Research、并行 Researcher 或开放域问答。
- 扫描 PDF OCR、语音、视频理解、移动端应用。
- 计算机网络、数据库等第三和第四门课程。
- LMS/LTI、学校/院系级多租户、付费订阅与运营系统。
- Kubernetes、跨区域容灾或大规模在线推理集群。
- 未经教师审核便自动发布知识点关系或诊断题。

## 3. 用户与权限

### 3.1 Teacher

Teacher 可以：

- 创建、编辑和归档自己拥有的课程。
- 生成、撤销和重新生成课程邀请码。
- 上传、更新、停用课程资料并查看入库状态。
- 审核、编辑或拒绝知识点和关系候选。
- 审核、编辑或拒绝 AI 生成的诊断题。
- 查看加入课程的学生、薄弱知识分布和测验汇总。
- 创建冻结评测集、运行评测和标注 Bad Case。

Teacher 不可以访问其他教师拥有的课程、文档、学生记录或评测结果。

### 3.2 Student

Student 可以：

- 使用有效邀请码加入课程。
- 在已加入课程中创建学习会话。
- 发起课程问答、概念比较、诊断、测验和学习路径请求。
- 查看回答引用、自己的掌握度、测验结果和学习历史。
- 主动退出课程；退出后保留历史记录但不再允许访问课程资料。

Student 不可以上传课程资料、审核图谱、创建题目或查看其他学生数据。

### 3.3 权限规则

- 所有资源查询必须在服务端按 `user_id`、`course_id` 和角色进行鉴权，不能依赖前端隐藏入口。
- Teacher 资源访问必须满足 `course.owner_id == current_user.id`。
- Student 访问课程资源必须存在状态为 `ACTIVE` 的 Enrollment。
- 文档原文和引用接口沿用相同课程权限，不提供公共静态下载地址。
- 邀请码以哈希形式存储，支持失效时间和主动撤销。

## 4. 信息架构与用户流程

### 4.1 Student 页面

| 页面 | 主要内容 |
|---|---|
| 登录/注册 | 邮箱、密码、角色选择 |
| 加入课程 | 输入邀请码并展示课程确认信息 |
| 课程首页 | 课程简介、学习进度、推荐下一步 |
| 学习对话 | 流式回答、检索状态、引用面板、意图切换 |
| 知识图谱 | 知识点、前置关系、个人掌握度覆盖层 |
| 诊断测验 | 题目、作答、解析、对应知识点 |
| 学习路径 | 当前目标、待补前置知识、推荐顺序 |
| 学习历史 | 会话、测验、掌握度变化记录 |

### 4.2 Teacher 页面

| 页面 | 主要内容 |
|---|---|
| 课程管理 | 课程信息、邀请码、学生数量、状态 |
| 资料管理 | 上传、文档版本、处理进度、失败原因 |
| 图谱审核 | 候选知识点、候选关系、来源证据、批量审核 |
| 题库审核 | AI 候选题、正确答案、解析、来源和知识点 |
| 学生概览 | 掌握度分布、薄弱知识点、测验汇总 |
| Bad Case | 问题、回答、引用、教师标签和修复状态 |
| 评测中心 | 数据集、实验配置、运行状态、指标对比 |

### 4.3 教师资料发布流程

```text
教师上传文件
  → 文件类型、大小与权限校验
  → SHA-256 幂等检查
  → 创建 DocumentVersion 与 IngestionJob
  → 异步解析并生成结构化中间表示
  → 结构感知切片
  → 生成 Dense 向量与 Lexical 索引
  → 抽取知识点和关系候选
  → 执行索引冒烟测试
  → 教师审核图谱候选
  → 原子发布新课程索引版本
```

新版本未完成或冒烟测试失败时，线上查询继续使用旧的 `ACTIVE` 版本。

### 4.4 学生学习流程

```text
选择课程和目标
  → 提问或选择学习动作
  → Agent 路由意图
  → 获取学习者状态和知识图谱上下文
  → 检索并验证课程证据
  → 返回回答、诊断题或学习路径
  → 学生完成审核题目
  → 幂等更新掌握度
  → 重新计算推荐学习路径
```

## 5. 功能需求

### 5.1 账户与课程

#### FR-AUTH-001 注册和登录

- 用户使用邮箱和密码注册。
- 角色创建后不可由用户自行从 Student 切换为 Teacher；MVP 可在注册时选择角色。
- 密码使用 Argon2id 哈希。
- Access Token 有效期 30 分钟，Refresh Token 有效期 14 天。
- Refresh Token 支持轮换和注销失效。
- 浏览器端通过同源 BFF 使用 `Secure`、`HttpOnly`、`SameSite` Cookie；不得把 Access Token 或 Refresh Token 持久化到 `localStorage`。

#### FR-COURSE-001 创建课程

- Teacher 填写名称、课程代码、简介和学期。
- 首版课程模板只提供“数据结构”和“操作系统”，Teacher 仍可修改显示名称。
- 创建后生成一次性可重置的课程邀请码。

#### FR-COURSE-002 加入课程

- Student 输入邀请码加入课程。
- 重复加入返回已有 Enrollment，不创建重复记录。
- 失效或撤销的邀请码返回明确错误。

### 5.2 文档与入库

#### FR-INGEST-001 支持格式

MVP 支持：

- 文本型 PDF
- DOCX
- PPTX
- Markdown
- TXT

扫描 PDF、受密码保护文档、损坏文件和无法提取文本的文件必须失败并显示原因，不得假装入库成功。

#### FR-INGEST-002 文档版本

- 同一逻辑文档可以有多个 DocumentVersion。
- 上传内容 SHA-256 相同的文件时返回已存在版本。
- 新版本完成前旧版本继续服务。
- 发布新版本后，新会话使用新版本；历史消息保留原引用版本。

#### FR-INGEST-003 结构感知切片

切片保留：

- 文档、版本、页码、章节路径和标题层级。
- 段落类型：正文、列表、表格、代码块、公式描述、例题、答案、实验步骤。
- 前后相邻 Chunk ID。

默认目标 Chunk 为 300–600 中文字符，最大 900 字符；代码块和表格优先保持原子性。章节标题作为上下文前缀参与 Embedding，但不重复展示在正文中。

#### FR-INGEST-004 入库任务

IngestionJob 状态固定为：

- `QUEUED`
- `PARSING`
- `CHUNKING`
- `EMBEDDING`
- `LEXICAL_INDEXING`
- `GRAPH_EXTRACTING`
- `VALIDATING`
- `READY_FOR_REVIEW`
- `PUBLISHED`
- `FAILED`
- `CANCELLED`

任务重试必须复用同一 DocumentVersion，并从最后一个可安全重试阶段继续。

### 5.3 混合检索

#### FR-RETRIEVE-001 Dense 检索

- 使用 BGE-M3 生成 Chunk 和查询向量。
- 向量写入 PostgreSQL pgvector。
- 只检索当前课程当前 `ACTIVE` 索引版本。
- 默认召回 Top 20。

#### FR-RETRIEVE-002 Lexical 检索

- 使用 `bm25s` 构建课程版本级索引。
- 中文文本在入索引前完成统一分词、大小写和标点归一化。
- 索引工件以 `course_id/index_version` 路径持久化。
- Worker 完成新索引后原子切换服务端缓存。
- 默认召回 Top 20。

#### FR-RETRIEVE-003 融合与重排

- Dense 与 Lexical 使用 RRF 融合，默认 `k=60`。
- 融合后保留 Top 20。
- 使用 BGE-Reranker-v2-m3 重排。
- 最终返回 Top 5–8 条证据，具体数量受证据分数和上下文预算控制。
- 所有检索步骤记录候选 ID、分数、耗时和索引版本。

#### FR-RETRIEVE-004 检索降级

- Dense 检索不可用时允许降级为 Lexical-only，并在 Trace 中记录。
- Lexical 索引不可用时允许降级为 Dense-only。
- Reranker 超时后使用 RRF 顺序，不阻断回答。
- 两路检索都不可用时停止生成并返回错误，不允许直接调用 LLM 回答。

### 5.4 知识图谱

#### FR-GRAPH-001 图谱模型

Neo4j 节点：

- `Course {course_id, name}`
- `Concept {concept_id, course_id, name, description, status}`

关系：

- `PREREQUISITE_OF`
- `RELATED_TO`
- `PART_OF`
- `CONTRASTS_WITH`

每个概念和关系关联来源 Chunk、抽取模型、置信度、审核状态、审核人和审核时间。

#### FR-GRAPH-002 候选抽取

- Worker 从发布候选版本的 Chunk 中提取概念、别名和关系。
- LLM 必须为每个候选提供来源 Chunk 和简短证据说明。
- 没有来源证据的候选直接丢弃。
- 候选只能写入 PostgreSQL 审核表，不能直接写入正式 Neo4j 图谱。

#### FR-GRAPH-003 教师审核

Teacher 可以：

- 批准、拒绝或编辑概念名称、描述和关系类型。
- 合并同义概念。
- 查看来源原文和所在章节。
- 批量处理同一章节的候选。

批准操作以 Outbox 事件同步到 Neo4j。失败事件可重试，PostgreSQL 审核记录为事实来源。

#### FR-GRAPH-004 学习路径约束

- 路径只能使用 `APPROVED` 的 `PREREQUISITE_OF` 关系。
- 图中不得出现自环。
- 发布前检测课程内 prerequisite 环；发现环时阻止相关边发布并要求教师处理。
- 跨课程关系允许存在，但两端课程必须均为当前用户已加入课程。

### 5.5 Agent 工作流

#### FR-AGENT-001 意图类型

Agent 仅支持以下意图：

- `TUTOR_QA`：课程事实和概念解释。
- `CONCEPT_COMPARE`：比较两个或多个课程概念。
- `DIAGNOSE`：识别学生可能缺失的前置知识并提出诊断题。
- `QUIZ`：获取审核题目并组织测验。
- `LEARNING_PATH`：根据目标和掌握度生成学习顺序。

路由器输出必须符合 Pydantic Schema，包含 `intent`、`target_concepts`、`needs_retrieval` 和 `reason`。

#### FR-AGENT-002 工具

工具接口固定为：

1. `search_course_material(course_id, query, filters, top_k)`
2. `query_concept_graph(course_id, concept_ids, relation_types, max_depth)`
3. `get_learner_profile(course_id, student_id, concept_ids)`
4. `get_approved_quiz(course_id, concept_ids, difficulty, count)`
5. `update_mastery_state(attempt_id)`

工具均执行独立鉴权和输入校验，不能依赖调用工具的 Agent 已经完成鉴权。

#### FR-AGENT-003 Agent 图

Agent 编排节点固定为（实现形态为自研 `TrustedAgentCore` 等价序列，不依赖
LangGraph；取舍记录见 `PROJECT_MEMORY.md` 决策记录 2026-08-31）：

```text
input_guard
→ route_intent
→ load_learner_context
→ expand_graph_context
→ retrieve_evidence
→ grade_evidence
→ rewrite_query（最多一次）
→ generate_response
→ verify_grounding
→ persist_turn
→ END
```

Quiz 和 Learning Path 在路由后进入各自确定性子图，不经过开放式 ReAct 循环。

#### FR-AGENT-004 停止条件

- Query Rewrite 最多执行一次。
- 单次请求工具调用总数最多 8 次。
- Agent 总步骤最多 12 步。
- 超过限制时返回已收集到的可靠证据或明确失败，不继续循环。

#### FR-AGENT-005 回答和引用

- 关键课程事实使用 `[1]`、`[2]` 形式绑定 Citation。
- Citation 包含文档名、版本、章节、页码、Chunk ID 和原文片段。
- `verify_grounding` 检查引用是否存在、是否属于当前课程版本、是否支持对应陈述。
- 引用校验失败时允许重新生成一次；再次失败则返回证据摘要而不是自由生成答案。

### 5.6 学习者模型与测验

#### FR-MASTERY-001 掌握度

每个 Student–Concept 保存：

- `alpha`
- `beta`
- `mastery = alpha / (alpha + beta)`
- `attempt_count`
- `last_assessed_at`

初始值为 `alpha=1`、`beta=1`。正确答案增加 `alpha`，错误答案增加 `beta`；更新权重由题目难度固定映射，不能由 LLM 自由决定。

建议权重：

| 难度 | 正确/错误增量 |
|---|---:|
| EASY | 0.75 |
| MEDIUM | 1.00 |
| HARD | 1.25 |

#### FR-QUIZ-001 题目候选

- LLM 可以生成单选题候选，但必须给出来源 Chunk、目标知识点、难度、唯一正确答案和解释。
- 系统校验选项数量、答案唯一性、引用存在性和课程版本。
- 候选题必须经 Teacher 审核后进入 `APPROVED` 题库。
- 只有 `APPROVED` 题目可以更新 MasteryState。

#### FR-QUIZ-002 作答幂等

- 每次 QuizAttempt 使用客户端生成或服务端下发的 idempotency key。
- 重复提交返回原结果，不重复更新掌握度。
- 更新 QuizAttempt 和 MasteryState 必须位于同一 PostgreSQL 事务。

#### FR-PATH-001 学习路径

- 输入目标 Concept 或课程目标。
- 查询最多 4 层前置关系。
- 优先选择 mastery `< 0.60` 的前置节点。
- 按图拓扑顺序输出学习步骤。
- 每一步包含推荐原因、相关资料和可选诊断题。
- 若图中存在未解决的环或目标概念未审核，则拒绝生成路径。

## 6. 数据模型

### 6.1 PostgreSQL 核心表

| 表 | 关键字段 |
|---|---|
| `users` | id, email, password_hash, role, status, created_at |
| `refresh_tokens` | id, user_id, token_hash, expires_at, revoked_at |
| `courses` | id, owner_id, code, name, description, semester, status |
| `course_invites` | id, course_id, code_hash, expires_at, revoked_at |
| `enrollments` | id, course_id, student_id, status, joined_at |
| `documents` | id, course_id, logical_name, status |
| `document_versions` | id, document_id, sha256, version, file_path, status |
| `chunks` | id, version_id, content, page, section_path, chunk_type, embedding |
| `ingestion_jobs` | id, version_id, stage, progress, error_code, retry_count |
| `course_indexes` | id, course_id, version, dense_status, lexical_path, status |
| `concept_candidates` | id, course_id, name, description, source_chunk_id, confidence, status |
| `relation_candidates` | id, from_candidate_id, to_candidate_id, type, source_chunk_id, status |
| `graph_outbox` | id, event_type, payload, status, retry_count |
| `chat_sessions` | id, course_id, student_id, title, summary, index_version |
| `messages` | id, session_id, role, content, intent, trace_id, created_at |
| `citations` | id, message_id, chunk_id, quote, claim_index |
| `quiz_items` | id, course_id, concept_id, question, options, answer, difficulty, status |
| `quiz_attempts` | id, quiz_item_id, student_id, answer, correct, idempotency_key |
| `mastery_states` | id, course_id, student_id, concept_id, alpha, beta, attempt_count |
| `eval_datasets` | id, course_id, type, version, frozen_at |
| `eval_cases` | id, dataset_id, input, expected, labels |
| `eval_runs` | id, dataset_id, config, status, metrics, trace_id |
| `bad_cases` | id, course_id, source_type, source_id, category, status, notes |

所有业务表使用 UUID 主键、UTC 时间和软删除/状态字段。唯一约束至少包括：

- `users.email`
- `enrollments(course_id, student_id)`
- `document_versions(document_id, sha256)`
- `quiz_attempts(student_id, idempotency_key)`
- `mastery_states(course_id, student_id, concept_id)`

### 6.2 Neo4j 与 PostgreSQL 一致性

- PostgreSQL 中审核记录是事实来源。
- 审核事务写入 `graph_outbox`。
- Worker 幂等消费 Outbox 并以稳定 `concept_id` MERGE Neo4j 节点和关系。
- 每小时运行一致性审计，发现缺失或多余图数据时记录告警并支持重放。
- 学生查询只读取 Neo4j 中 `status='APPROVED'` 的数据。

## 7. API 契约

所有接口统一返回：

```json
{
  "data": {},
  "error": null,
  "request_id": "uuid"
}
```

错误结构：

```json
{
  "data": null,
  "error": {
    "code": "COURSE_ACCESS_DENIED",
    "message": "You do not have access to this course",
    "details": {}
  },
  "request_id": "uuid"
}
```

### 7.1 Auth

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/register` | 注册Student或Teacher |
| POST | `/api/v1/auth/login` | 获取Access和Refresh Token |
| POST | `/api/v1/auth/refresh` | 轮换Token |
| POST | `/api/v1/auth/logout` | 撤销Refresh Token |
| GET | `/api/v1/auth/me` | 当前用户 |

### 7.2 Course 与 Enrollment

| 方法 | 路径 | 角色 |
|---|---|---|
| POST | `/api/v1/courses` | Teacher |
| GET | `/api/v1/courses` | Student/Teacher |
| GET | `/api/v1/courses/{course_id}` | 已授权用户 |
| PATCH | `/api/v1/courses/{course_id}` | Owner Teacher |
| POST | `/api/v1/courses/{course_id}/invite/reset` | Owner Teacher |
| POST | `/api/v1/courses/join` | Student |
| GET | `/api/v1/courses/{course_id}/students` | Owner Teacher |

### 7.3 Document 与 Ingestion

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/courses/{course_id}/documents` | 上传文档 |
| GET | `/api/v1/courses/{course_id}/documents` | 文档列表 |
| GET | `/api/v1/documents/{document_id}/versions` | 版本列表 |
| GET | `/api/v1/ingestion-jobs/{job_id}` | 任务状态 |
| POST | `/api/v1/ingestion-jobs/{job_id}/retry` | 重试失败任务 |
| POST | `/api/v1/ingestion-jobs/{job_id}/cancel` | 取消任务 |
| GET | `/api/v1/chunks/{chunk_id}` | 获取有权限的引用原文 |

### 7.4 Graph Review

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/courses/{course_id}/graph/candidates` | 候选列表 |
| POST | `/api/v1/graph/concepts/{id}/approve` | 批准概念 |
| POST | `/api/v1/graph/concepts/{id}/reject` | 拒绝概念 |
| PATCH | `/api/v1/graph/concepts/{id}` | 编辑候选 |
| POST | `/api/v1/graph/relations/{id}/approve` | 批准关系 |
| POST | `/api/v1/graph/relations/{id}/reject` | 拒绝关系 |
| GET | `/api/v1/courses/{course_id}/graph` | 获取审核图谱 |

### 7.5 Chat SSE

创建会话：

`POST /api/v1/courses/{course_id}/chat/sessions`

发送消息：

`POST /api/v1/chat/sessions/{session_id}/messages`

请求：

```json
{
  "content": "为什么虚拟内存需要页面置换算法？",
  "requested_intent": null,
  "target_concept_ids": []
}
```

响应为 `text/event-stream`，事件结构固定为：

```text
event: status
data: {"stage":"retrieving","trace_id":"..."}

event: retrieval
data: {"query":"...","candidate_count":20,"index_version":"..."}

event: token
data: {"text":"虚拟内存的物理页框数量是有限的"}

event: citation
data: {"citation_id":"...","label":1,"document":"操作系统课件","page":42}

event: done
data: {"message_id":"...","intent":"TUTOR_QA","usage":{}}
```

错误事件：

```text
event: error
data: {"code":"INSUFFICIENT_EVIDENCE","message":"课程资料中没有足够依据"}
```

客户端断开不会自动重放已经发送的Token；服务端仍保存最终完成或失败状态，客户端重连后通过消息查询接口获取最终结果。

### 7.6 Quiz、Mastery 与 Path

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/courses/{course_id}/quizzes/generate-candidates` | Teacher生成候选 |
| GET | `/api/v1/courses/{course_id}/quizzes/review` | Teacher审核队列 |
| POST | `/api/v1/quizzes/{id}/approve` | 批准题目 |
| POST | `/api/v1/quizzes/{id}/reject` | 拒绝题目 |
| GET | `/api/v1/courses/{course_id}/quizzes/next` | Student获取题目 |
| POST | `/api/v1/quizzes/{id}/attempts` | Student提交答案 |
| GET | `/api/v1/courses/{course_id}/mastery` | Student掌握度 |
| POST | `/api/v1/courses/{course_id}/learning-path` | 生成路径 |

### 7.7 Evaluation

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/courses/{course_id}/eval-datasets` | 创建数据集 |
| POST | `/api/v1/eval-datasets/{id}/freeze` | 冻结版本 |
| POST | `/api/v1/eval-datasets/{id}/runs` | 启动服务端真实三基线评测；请求只接受运行参数，不接受客户端提交的 Case 结果 |
| GET | `/api/v1/eval-runs/{id}` | 查看进度和指标 |
| POST | `/api/v1/bad-cases` | 标注Bad Case |
| PATCH | `/api/v1/bad-cases/{id}` | 更新修复状态 |

## 8. 安全与隐私

### 8.1 文件安全

- 根据文件头验证格式，不只依赖扩展名。
- 文件名随机化，原始名称仅作为元数据。
- 单文件默认限制 50 MB，可通过配置降低但不得提高到无限制。
- 解析过程在 Worker 中执行，不执行文档内宏、脚本或外部链接。
- 文档下载必须经过应用鉴权。

### 8.2 Prompt Injection

- 检索文档视为不可信数据，不能覆盖系统指令。
- Tool描述、系统提示和课程文档使用明确分隔。
- 文档中的“忽略此前指令”等内容只能作为引用文本，不得改变Agent行为。
- Agent不得调用规格外工具或访问课程外资源。

### 8.3 数据隐私

- 不记录密码、API Key、Refresh Token原文。
- 日志默认不记录完整文档和学生回答正文，只记录ID、长度和分类信息。
- 教师只能查看课程级聚合薄弱点；MVP中不提供其他学生的完整对话浏览入口。
- 删除账户时撤销Token并异步删除或匿名化用户学习数据。

## 9. 可观测性与可靠性

### 9.1 Trace

每个请求生成 `request_id`，每个Agent运行生成 `trace_id`。Agent Trace 至少记录：

- 意图路由结果。
- 查询改写前后文本。
- 图谱查询节点和耗时。
- Dense、Lexical、RRF、Rerank候选和分数。
- LLM调用模型、延迟、Token和错误类型。
- Grounding校验结果。
- 最终回答状态。

内置结构化Trace为必需能力；Langfuse通过环境变量启用，默认关闭，不作为系统正确性的依赖。

### 9.2 超时与重试

| 操作 | 默认超时 | 重试 |
|---|---:|---:|
| Chat LLM | 45s | 2次指数退避 |
| Embedding批次 | 60s | 3次 |
| Reranker | 15s | 0次，直接降级 |
| Neo4j查询 | 3s | 1次 |
| 文档解析 | 5min | Worker人工/自动重试 |

对外请求重试只允许发生在尚未向客户端发送首个Token之前。

### 9.3 健康检查

- `/health/live`：进程存活。
- `/health/ready`：PostgreSQL、Redis、Neo4j、模型适配器和索引缓存均可用。
- Readiness失败的实例不得接收新请求。

## 10. 性能目标

在规格中记录参考硬件、模型供应商和语料规模后测量：

| 指标 | MVP目标 |
|---|---:|
| 非LLM API P95 | < 500ms |
| 混合检索+重排 P95 | < 2.0s |
| Chat TTFT P95 | < 3.0s |
| 完整问答 P95 | < 15s |
| 单课程语料 | 1,000–10,000 Chunks |
| 同时在线演示用户 | 20 |
| 入库任务失败可诊断率 | 100%有错误码 |

性能目标不作为无硬件条件的绝对承诺；每份评测报告必须记录运行环境。

## 11. 评测方案

### 11.1 冻结数据集

| 数据集 | 数量 | 标注内容 |
|---|---:|---|
| Retrieval | 每门100，共200 | 相关Chunk、知识点、问题类型 |
| End-to-End QA | 每门40，共80 | 参考答案、必需证据、可回答性 |
| Intent Routing | 50 | 唯一意图或允许意图集合 |
| Learning Path | 30 | 目标、初始掌握度、教师路径 |

问题类型至少覆盖：

- 单一知识点。
- 跨章节问题。
- 数据结构与操作系统跨课程关系。
- 精确术语、算法名和缩写。
- 易混淆概念比较。
- 前置知识缺失。
- 资料中不存在的问题。
- Prompt Injection和越权诱导。

### 11.2 实验配置

必须保留三组可复现实验：

1. `dense_only`：BGE-M3+pgvector。
2. `hybrid_rerank`：Dense+Lexical+RRF+BGE Reranker。
3. `kg_personalized`：Hybrid+审核图谱+学习者状态。

每次 EvalRun 固化：

- Git commit。
- 数据集版本。
- 文档索引版本。
- 模型、Embedding和Reranker版本。
- Prompt版本。
- 检索参数。
- 运行时间、成本和硬件环境。

### 11.3 指标

- Retrieval：Recall@5、MRR@5、nDCG@10。
- Answer：引用准确率、引用覆盖率、Faithfulness、Answer Relevancy。
- Abstention：不可回答召回率和误拒答率。
- Routing：Macro-F1和混淆矩阵。
- Path：前置依赖合法率、教师路径一致率、路径长度。
- System：TTFT、P50/P95延迟、Token和失败率。

LLM-as-Judge只作为辅助指标；引用准确率、可回答性、图路径合法性必须使用确定性或人工标注计算。

## 12. 测试计划

### 12.1 单元测试

- RRF分数、排序和去重。
- Chunk边界和元数据保留。
- Beta-Bernoulli掌握度更新。
- 邀请码、JWT和权限判断。
- 路由Schema和输入校验。
- 图环检测与学习路径排序。
- 引用与Claim绑定。

### 12.2 集成测试

- 文档上传到索引发布完整链路。
- PostgreSQL Outbox到Neo4j幂等同步。
- Dense、Lexical和Reranker降级。
- QuizAttempt事务和重复提交。
- 新索引失败时旧版本继续服务。
- Agent查询只使用当前用户有权限的课程数据。

### 12.3 端到端测试

- Teacher建课、上传、审核图谱和审核题目。
- Student邀请码入课、问答、查看引用、完成测验、获取路径。
- 文档更新后新会话使用新版本，旧消息仍能查看原引用。
- SSE正常完成、服务端失败、客户端中断和重连。
- Prompt Injection无法改变系统工具和课程边界。

### 12.4 安全测试

- Student访问Teacher API。
- 用户修改URL中的course_id访问其他课程。
- 引用接口绕过会话访问文档原文。
- 恶意文件名、双扩展名、超大文件和错误MIME。
- SQL/NoSQL注入、XSS和Markdown危险链接。
- 同一QuizAttempt并发重复提交。

## 13. Docker Compose与配置

标准服务：

- `web`
- `api`
- `worker`
- `postgres`
- `redis`
- `neo4j`

必需环境变量：

```text
DATABASE_URL
REDIS_URL
NEO4J_URI
NEO4J_USERNAME
NEO4J_PASSWORD
JWT_SECRET
LLM_BASE_URL
LLM_API_KEY
LLM_MODEL
EMBEDDING_MODEL=BAAI/bge-m3
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
UPLOAD_ROOT=/data/uploads
INDEX_ROOT=/data/indexes
```

仓库提交 `.env.example`，不得提交任何真实密钥。模型权重使用缓存Volume，不打包进应用镜像。

## 14. 实施顺序

以下能力按依赖顺序连续推进，不绑定周次；稳定切片通过核心验证后即可进入下一项。

### 基础能力

- 初始化前后端、Docker Compose和CI。
- 完成JWT、双角色、课程、邀请码和Enrollment。
- 建立PostgreSQL迁移框架和统一错误结构。

### 入库链路

- 完成五种文档格式解析。
- 完成结构感知切片、版本和IngestionJob。
- 实现Celery任务、幂等和失败重试。

### 检索与引用

- 完成BGE-M3、pgvector和bm25s。
- 实现RRF、Rerank、降级和索引版本切换。
- 完成引用数据模型与原文面板。

### Agent与图谱

- 完成Agent路由、检索、证据校验和拒答（自研编排核心，见决策记录）。
- 完成候选抽取、教师审核、Outbox和Neo4j。
- 完成知识图谱可视化初版。

### 个性化学习

- 完成题目候选与审核。
- 完成QuizAttempt、掌握度和学习路径。
- 补充图环检测和跨课程前置关系。

### 产品界面

- 完成Student全部核心页面。
- 完成Teacher资料、图谱、题库和学生概览。
- 完成SSE状态和错误体验。

### 评测与治理

- 构建并冻结四类评测集。
- 跑完三组基线实验。
- 完成Trace、Bad Case和评测中心。

### 交付

- 完成安全、端到端和故障注入测试。
- 验证干净环境Docker Compose启动。
- 编写README、架构图、演示脚本和真实评测报告。
- 根据真实评测数据生成简历项目描述，禁止使用预估指标。

## 15. 风险与缓解

| 风险 | 缓解措施 |
|---|---|
| 课程资料版权不清 | 仓库只提交开放许可样例，完整资料由教师上传 |
| LLM抽取图谱错误 | 候选进入审核表，Teacher批准后才发布 |
| Neo4j与Postgres不一致 | Outbox、稳定ID、幂等MERGE和一致性审计 |
| 题目质量影响掌握度 | 只有审核题可更新MasteryState |
| 项目范围失控 | 首版固定两门课和五个Agent工具 |
| 本地模型资源不足 | Chat走API，Embedding/Reranker支持批处理和CPU降级 |
| 指标看似漂亮但不可复现 | 冻结数据、记录Git/模型/索引/Prompt版本 |
| 被认为是开源项目换皮 | 不依赖DeepTutor/RAGFlow运行，突出自己的数据模型、审核闭环和评测 |

## 16. 开源参考边界

- DeepTutor：仅参考产品能力拆分、知识空间和记忆治理。
- RAGFlow：仅参考文档入库、版本和检索工程。
- LightRAG/GraphRAG：仅参考图检索与关系建模思想。
- Open Deep Research：不进入MVP运行链路。

实现中若复制任何第三方代码，必须检查许可证、保留版权声明并在NOTICE中记录。产品设计相似不等于可以复制实现。

## 17. MVP完成定义

只有同时满足以下条件，CoursePilot MVP才算完成：

1. Teacher和Student核心流程均可从浏览器完成。
2. 两门课程均有开放样例数据和可复现入库脚本。
3. 所有课程问答均带引用或明确拒答。
4. 未审核图谱和题目无法影响Student。
5. 掌握度更新具备事务、幂等和审计记录。
6. 三组对照实验可一键运行并输出版本化报告。
7. 达到本规格第2.2节质量门槛。
8. 权限、安全和故障场景测试通过。
9. Docker Compose可在干净环境启动并完成演示脚本。
10. README、架构图、API文档和评测报告齐全。
