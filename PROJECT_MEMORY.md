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
| 后端 | FastAPI、Pydantic、SQLAlchemy、Alembic、LangGraph |
| 异步任务 | Celery、Redis |
| 业务与向量数据库 | PostgreSQL、pgvector |
| 课程知识图谱 | Neo4j |
| Lexical 检索 | bm25s，每个课程版本独立索引 |
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

截至 2026-08-30：

- 已完成产品与技术规格，并将实施节奏调整为连续推进。
- 已初始化 Git、Next.js Web、FastAPI API、Celery Worker、Docker Compose 与 CI。
- 已实现统一响应契约、JWT 双角色、Refresh Token 轮换、课程、邀请码和 Enrollment 权限闭环及首个 Alembic 迁移。
- 后续直接推进文档入库、检索、审核图谱、可信问答、测验、掌握度、学习路径和核心界面，不按周等待。

## 10. 决策记录维护规则

后续出现会影响产品范围、数据契约、核心架构或验收指标的决定时，在下表追加一行；日常实现细节不需要记录。

| 日期 | 决策 | 原因 | 影响 |
|---|---|---|---|
| 2026-08-30 | 使用 CoursePilot 作为项目名 | 突出课程学习副驾驶定位 | 仓库、文档和产品统一命名 |
| 2026-08-30 | 首版仅做数据结构与操作系统 | 两门课程足以验证跨课程隔离和领域适配，同时符合 8 周范围 | 语料、评测集和演示围绕两门课程 |
| 2026-08-30 | 使用教师审核的 Neo4j 课程图谱 | 体现垂直知识工程，并防止候选关系直接污染学生结果 | 需要候选审核、发布和一致性机制 |
| 2026-08-30 | Coderook 与 CoursePilot 双项目互补 | 避免两个 Coding Agent 同质化 | 简历分别突出 Runtime 与 Agentic RAG |
| 2026-08-30 | 实施改为连续推进，不按周切分 | 用户要求尽快完成并避免时间节奏限制 | 规格第 14 节保留能力依赖顺序，不再绑定周次 |
