# CoursePilot 代码审查报告（完整版）

> 审查日期：2026-08-30 至 2026-08-31 · 审查基线：main @ 6a61018 · 终审定稿
> 方法：五轮独立审查交叉验证——① 后端核心（agent/retrieval/learning/graph/ingestion/tasks）② 路由与安全层 ③ 前端与工程配置 ④ 逐条验证 + 盲挖 + spec 逐条对账 + 安全专项（prompt injection/授权矩阵/依赖许可）+ 运行时可观测性专项 + 前端细节/文档/样例专项 ⑤ 终审（P1 全部由审查者亲验 + 第三轮结论对抗性复核）。过程与局限见第九节。
> 所有问题均经读码验证并标注 `file:line`；全程经对抗性复核修正/驳回 6 项结论，已在文中注明。

## 总体评价

代码基础质量显著高于同类作品平均水平：权限模型闭环（服务层独立复核归属）、掌握度幂等事务完整、agent 证据信任边界设计正确（schema-only 输出 + citation 白名单 + 预算硬上限 + fail-closed 检索）、上传解析器防护到位（zip 炸弹/XXE/宏/路径穿越）、outbox 与 PG 事实源同事务、评测 runner 强制语料边界、无 XSS 渲染通道、SQL/Cypher 全参数化。**未发现可直接越权泄漏或掌握度错乱的 P0 缺陷。**

但对照 spec 的完成度有明显缺口：**78 项 spec 条目中 48 项实现、23 项部分实现、4 项缺失、3 项偏离**。最关键的三条主线问题：版本隔离系统性缺失、证据分数门槛被架空、冻结评测数据集完全缺失（导致全部验收门槛不可判定）。

---

## 一、P1 —— 破坏产品约束 / 验收目标，必须修复

### A. 正确性与 spec 约束

**P1-1 版本隔离系统性缺失（多环节叠加）**
- `apps/api/app/agent/service.py:70-106`：`DatabaseLexicalRetriever._load()` 只按 course + PUBLISHED 过滤，不按 index_version 覆盖范围。旧版本发布后永远保持 PUBLISHED（`graph/service.py:473-494`），因此 v1+v2 内容会同时进入兜底 BM25 索引：近似重复挤占 top-k，过期内容可凭高分成为引用，且 `Evidence.index_version` 固定标注为会话版本（`agent/service.py:297`）——引用版本归属错误。
- `apps/api/app/graph/service.py:473-508`：`publish()` 无条件翻转课程内全部 READY_FOR_REVIEW 版本，不校验其 chunk 是否在目标 index 语料内。
- `apps/api/app/routers/chat.py:298-304`：索引查询只按 course+version，不校验 status，v1 被 ARCHIVED 后旧会话继续服务。
- 连主词法工件构建（`ingestion/pipeline.py:504-513`）也按"每文档取最新已发布/待审版本"，与 dense 路由（`retrieval/adapters.py:299-332`，正确取最新发布版本）不一致。
- **修复**：ingestion 完成时把 index 覆盖的 `document_version_id` 列表写入 `CourseIndex`；检索、publish、chat 全部按该列表过滤；补"双版本发布后不返回旧版本 chunk"集成测试。

**P1-2 证据分数门槛被架空，"不足即拒答"只剩空结果触发**
- `agent/service.py:347`：无归一化分的候选一律 `max(0.56, 1.0-(rank-1)*0.06)` ≥ `minimum_score=0.55`（`grounding.py:64`）——词法/RRF 路径任何命中必过门槛。
- `retrieval/adapters.py:350`：dense 归一化 `(sim+1)/2` 使 cosine 0.1 即映射到 0.55——对 BGE-M3 归一化向量几乎不设防。
- reranker 降级（spec 9.2：0 重试直接降级）时整链门槛失效，直接威胁 spec §2.2 拒答率 ≥0.90。
- **修复**：去掉 0.56 兜底分；BM25/RRF 分数显式归一化；dense 阈值改为与相似度分布校准的独立阈值；低于门槛不伪造分数。

**P1-3 冻结评测数据集完全缺失，全部验收门槛不可判定**（spec 对账 #66）
- `scripts/seed_demo.py` 不创建 eval 数据集；仓库无任何 eval case 数据。spec §11.1 要求四类冻结数据集（200/80/50/30 条），是 §2.2 全部质量门槛与 §17 MVP 完成定义第 7 条的前置条件。当前所有指标（Recall@5/MRR/拒答率/Macro-F1 等）只有实现、从未在真实语料上产出过报告——简历/演示无法引用任何真实数字，且 AGENTS.md 约束"所有数字必须可追溯到冻结评测报告"因此无法满足。
- **修复**：为 seed 增加评测数据集构建步骤（人工标注真实 case，不预填指标），跑通 runner 三基线并产出冻结报告。

**P1-4 Agent 五工具缺四，个性化链路未接入问答**（spec 对账 #33/#34）
- 仅实现 `search_course_material`（`agent/core.py:39-47`）；`query_concept_graph` / `get_learner_profile` / `get_approved_quiz` / `update_mastery_state` 作为工具接口均不存在。学习者状态从未进入问答上下文，"Agentic RAG + 学习者建模"核心卖点在问答路径上未兑现。spec 假设的 LangGraph 11 节点中 `load_learner_context` / `expand_graph_context` / `persist_turn` 不存在（实现为自研 `TrustedAgentCore` 等价序列）。
- **修复**：接入学习者状态与图谱扩展进问答链路；或按 AGENTS.md 规则把架构决策变更记入 PROJECT_MEMORY 并同步 spec/文档（当前文档与代码矛盾，见 P2-D1）。

**P1-5 Chat 请求跨 LLM 调用持有 DB 连接，默认连接池撑不住 20 并发**（spec §10）
- `apps/api/app/db.py:18-28`：未配置 `pool_size/max_overflow`，默认 5+10。
- `routers/chat.py:337-341`：同一 `AsyncSession` 传入 `run_trusted_turn`（`agent/service.py:389-429`），整个检索 + 最多 2×45s LLM + grounding 重试期间事务保持打开；dense 检索还要再借第二个连接（`retrieval/adapters.py:341`）。20 并发提问 → 队列等待 30s 后 `QueuePool limit reached`，直接击穿"20 并发在线"与"问答 P95<15s"目标。
- **修复**：PENDING 消息先 commit 落库，LLM 生成阶段用独立短会话，结束后短会话写回；显式配置连接池。

**P1-6 SSE 伪流式：TTFT 目标不可达，且 LLM 重试不符合 spec 9.2**（spec 9.2/10）
- `routers/chat.py:343-462`：在返回 StreamingResponse 前同步跑完整轮 Agent，整段答案塞进单个 `token` 事件（`:437`）。首字节时间 = 完整答案生成时间，TTFT P95<3.0s 必然超；grounding 重试翻倍。
- `agent/adapters.py:147-157`：每次 LLM 调用新建 `httpx.AsyncClient`，无连接复用。重试方面（终审更正）：`agent/core.py:206-216` 的 grounding 循环会在适配器抛异常时把异常转为 feedback 重试一次，并非完全无重试；但无指数退避、最多 2 次尝试即降级为证据摘要，不满足 spec 9.2"45s + 2 次指数退避"。由于全量生成后才发事件，加重试完全安全。
- **修复**：真流式（检索完成后转发 LLM delta，重试仅限首个 delta 前，补指数退避）；进程级复用 AsyncClient。

**P1-7 入库任务每次重新加载 BGE-M3（约 2.3GB）**（spec §10 入库目标）
- `tasks/ingestion.py:26-37` 每任务新建 engine，`pipeline.py:102-106` 每任务新建 embedding adapter，模型权重不跨任务缓存。连续入库 10 个文档 = 10 次模型加载（每次数十秒）；`--concurrency=2` 时内存尖峰约 5-6GB，compose 无 memory limit。
- **修复**：celery `worker_process_init` 信号预热进程级单例 adapter。

**P1-8 未认证端点滥用防护整体缺失**
- `routers/auth.py` 全文件无限流（每请求在 threadpool 跑 Argon2id 64MB×3 轮，高频请求占满线程池拖垮全部依赖 threadpool 的路由）。
- `main.py:83-90` 无请求体大小上限：匿名者向 `/login` POST 数百 MB JSON 即占满 worker 内存（文档上传有 50MB 上限，JSON 端点没有）。
- **修复**：IP/账号维度限流 + 失败锁定；全局 body 大小中间件（JSON 1MB 上限，SSE/上传例外）。

**P1-9 前端 quiz 幂等键按每次提交生成，违背 FR-QUIZ-002**
- `apps/web/components/student-course-tabs.tsx:420`：`idempotency_key: crypto.randomUUID()` 在 `submitAttempt` 内生成；提交超时后重试即新 key，服务端唯一约束（`models.py:985-989`）无法去重 → 重复 attempt + 二次掌握度更新。服务端全库无其他去重防线。
- **修复**：`loadNext` 取题时生成一次存入 state，跨重试复用。

**P1-10 网络瞬断清空登录态**
- `apps/web/lib/api.ts:155-160`：`refreshTokens` catch 无条件 `setStoredSession(null)`；`auth-provider.tsx:54-57` 的 `syncCurrentUser` 同样。API 短暂不可达（代理 502）即登出并丢弃 14 天 refresh token。
- **修复**：仅 401 / `AUTH_REQUIRED` / `INVALID_REFRESH_TOKEN` 清会话，网络类错误保留 token。

**P1-11 refresh token 存 localStorage**
- `apps/web/lib/api.ts:9,47,62`：XSS 即可窃取 14 天 refresh token。
- **修复**：refresh token 改 HttpOnly Cookie（经 Next 代理 Set-Cookie），access token 留内存。

### B. 部署与验收前置

**P1-12 CI 不闭环 + 构建不可复现**
- `.github/workflows/ci.yml:107` 只 build api 镜像：web 镜像、worker、web→api 代理链路从未在 CI 验证（spec §2.2"干净环境 Compose 启动"门槛未闭环）；backend job 未跑 ruff（`pyproject.toml:39` 已声明依赖）。
- `apps/api/Dockerfile:11`：`pip install ".[ml]"` 不使用已存在的 `uv.lock`，构建依赖未锁定。
- **修复**：CI 增加 web 构建 + `up --wait web worker` + ruff；Dockerfile 改 `uv sync --frozen` 或 pip 约束文件。

---

## 二、P2 —— 明确缺陷，条件触发即出错 / spec 明确要求未满足

### 后端正确性

| # | 问题 | 位置 | 说明与修复 |
|---|---|---|---|
| P2-1 | 取消后的入库版本永久卡死且阻断重传 | `routers/documents.py:564-570, 613-619, 263-278` | cancel 后 version 回 UPLOADED、retry 仅允许 FAILED、重复 sha256 检查匹配任意状态版本——同文件永远无法重新入库。允许 CANCELLED job 重试，或重复检查跳过 CANCELLED 版本 |
| P2-2 | 旧会话 dense 检索静默变空且 trace 记 success | `retrieval/adapters.py:310-317` | 会话钉住的 index 归档后 `active_index_exists` 恒假 → 0 行"成功"结果，静默退化为纯词法，不可观测。exists 放宽为 `IN (ACTIVE, ARCHIVED)` 或空结果时记 `index_archived` 降级 |
| P2-3 | 引用持久化丢弃多 claim 绑定 | `routers/chat.py:398-404` + `models.py:922` | `uq_citations_message_label` 使一个 label 只存一行，同引用支持多 claim 时只存 `claim_indices[0]`，历史回读审计链断裂（spec 12.1）。唯一键改 `(message_id, label, claim_index)` |
| P2-4 | 聊天无幂等机制 | `routers/chat.py:54-67` | 重试产生重复消息与重复 token 消费。加 `client_message_id` + `(session_id, client_message_id)` 唯一约束 |
| P2-5 | 并发首次作答同概念不同题目 → 未捕获 IntegrityError → 500 | `learning/service.py:461-523` | except 分支只处理幂等键冲突；按约束名区分并重读 mastery 返回 200 |
| P2-6 | ingestion 候选查重-插入 TOCTOU | `ingestion/pipeline.py:615-649` + `models.py:758-766` | `ConceptCandidate` 无 `(source_chunk_id, name)` 唯一约束；acks_late 重投递时可产生重复候选。加唯一约束 + advisory lock |
| P2-7 | Celery visibility_timeout=900s + acks_late 可并发重入 | `worker.py:22` + `tasks/ingestion.py:49-55` | 大批量 embedding 超 900s 后 broker 重投递，`_persist_chunks` delete+re-insert 无互斥 → `uq_chunks_version_ordinal` 冲突打成假 FAILED。设 `task_time_limit < visibility_timeout` 或运行租约 |
| P2-8 | 超时重试矩阵大半缺失（spec 9.2） | `pipeline.py:411`（embedding 无超时）、`graph/neo4j.py`（无 3s 超时）、解析（无 5min 控制） | 慢模型/挂死的 Neo4j 拖垮 worker 与请求 |
| P2-9 | 学生数据无删除通道与保留策略（spec §8.3 违约） | `routers/auth.py`（无 delete-account）、`models.py:898-915` | Message/QuizAttempt/MasteryState/usage.retrieval_traces 无限期持久化。补 `DELETE /users/me`（撤销 token + 软删）+ 定期清理；trace 瘦身 |
| P2-10 | Neo4j 一致性审计缺失（spec §6.2 每小时审计+重放） | 全仓无 audit 实现 | 图/库漂移静默积累且不可重放——图是掌握度与路径的信任来源 |
| P2-11 | 账户删除外另一隐私面：usage 中完整检索 trace 原样返回学生并存库 | `chat.py:275,381-386` | 含完整查询与 5 段 × 20 候选 trace，存储膨胀 + 泄漏内部结构。trace 瘦身或入独立表 |
| P2-12 | publish 的 PENDING 检查按整课程计数 | `graph/service.py:417-444` | 旧 index 遗留候选永久阻塞新版本发布。按 `index_id == target.id` 过滤 |
| P2-13 | 评测口径：allowed_intents 扭曲混淆矩阵（第一轮判 P1，复核降级——spec §11.1 认可"预测 ∈ 允许集即正确"语义，且无冻结数据集使用该字段） | `evaluation/service.py:832-846` | 预测不在允许集时 expected 被记为 `allowed_intents[0]`，FN 归因到任意类。allowed 仅用于归一化 predicted |
| P2-14 | 评测 git_dirty 在显式传 commit 时丢失 | `evaluation/runner.py:783-785`（CLI 无 `--git-dirty`） | 破坏 provenance 可追溯。显式传 commit 时也探测 dirty |
| P2-15 | 评测运行 API 与 CLI 语义分裂（spec 7.7） | `routers/evaluation.py:283-298` | API 只记录外部 case_results，不"启动评测"；前端评测中心无法真正运行实验。补执行端点或明示 CLI-only |
| P2-16 | 生产配置校验缺项 | `config.py:61-75` | `neo4j_password`/`database_url` 默认口令、空 `llm_api_key` 在 production 不报错 |
| P2-17 | 评测 JSON 输入无大小/深度限制 + `extra="allow"` 直存 | `routers/evaluation.py:37-41, 73-75` + `service.py:1139-1156` | 深嵌套触发 RecursionError 500；任意键永久入库（终审附缓解说明：合并前经 `_json_digest` 可序列化校验，保留实验参数为有意设计）。改 `extra="forbid"` 或显式白名单 + 深度/节点数预算 |
| P2-18 | 跨课程前置关系零实现（spec FR-GRAPH-004 评测问题类型之一） | `models.py:816`、`graph/query.py:45` | 单课程硬约束，属能力性缺口，需产品决策：实现或显式移出 MVP 范围 |
| P2-19 | 递归 DFS 环检测超深链 500 | `learning/prerequisites.py:126-149` | 改显式栈（`graph/review.py:136-152` 已是迭代版可参照） |

### 安全专项（本轮新增）

| # | 问题 | 位置 | 说明与修复 |
|---|---|---|---|
| P2-20 | 生成 payload 中 document/section 元数据未标记为不可信 | `agent/grounding.py:150-161` | 文档名/章节标题与 quote 平级进入 prompt，可携带注入文本。metadata 归入子对象并声明不可信；`logical_name` 字符白名单（`documents.py:77-86`） |
| P2-21 | 二阶注入：校验异常文本回流 system prompt | `agent/core.py:216-218` + `adapters.py:124-133` | pydantic ValidationError 文本（含模型原文）作为 grounding_feedback 拼进第二轮 system 消息。feedback 只发结构化字段，绝不透传 `str(exc)` |
| P2-22 | 输出侧无指令样式过滤 | `agent/grounding.py:289-307` | 文档中指令句（"请立即访问 xxx"）可作"引用"原样直达学生回答。指令样式检测命中则折叠引用并标注"文档原文摘录" |
| P2-23 | 自助注册即 TEACHER，削弱教师审核信任锚 | `schemas.py:37` + `auth.py:81-87` + `auth-screen.tsx:166-202` | 任何人可建钓鱼课程、消耗 LLM/embedding 资源。邀请/审批制，或管理端开关 |
| P2-24 | NOTICE 缺失，违背 AGENTS.md 许可合规 | 仓库根 | compose 引入 Neo4j(GPLv3)、Redis(RSALv2/SSPLv1 双许可) 使 NOTICE 必要。补 NOTICE + 依赖清单 |
| P2-25 | seed 脚本默认公开教师口令 | `scripts/seed_demo.py:609-632` | 对公网实例执行后任何人可登录教师账号。未显式传参时拒绝运行或随机生成打印一次 |
| P2-26 | `/health/ready` 未认证暴露组件状态与异常类型 | `routers/health.py:87-89, 92-122` | 外网可探测依赖拓扑。明细仅鉴权后返回 |

### 运行时 / 可观测性（本轮新增）

| # | 问题 | 位置 | 说明与修复 |
|---|---|---|---|
| P2-27 | 日志体系缺位 | `errors.py:130-132`、`main.py:83-88` | 无 logging 配置，request_id/job_id 被丢弃，`X-Request-ID` 与日志对不上号；pipeline/worker 零日志；无任何 metrics 端点。"入库失败 100% 可诊断"只靠 DB error_code 单腿支撑，慢请求/错误回答从日志不可诊断。dictConfig JSON 格式 + 访问日志 + `/metrics` |
| P2-28 | LLM token/延迟未记录（spec 9.1），grounding 结果不落库 | `agent/adapters.py:157` | `response.usage` 全部丢弃；未来 Faithfulness/System 指标无数据来源。adapter 返回 (draft, usage, latency) 并入 trace |
| P2-29 | 静默降级三连 | `agent/service.py:129-140`、`pipeline.py:421-438`、`health.py:66-74` | 词法 fallback 掩盖 artifact 故障且 trace 记 success（每请求全课程重建 BM25，秒级 CPU）；embedding 不可用静默变纯词法；readiness 不检查模型可加载性。三者叠加 = "系统绿灯、回答质量劣化"且无处可见。fallback 加 trace 标记 + 规模上限 |
| P2-30 | outbox 每事件新建 Neo4j driver，beat 每 5s 新建/销毁 DB engine | `tasks/graph.py:190-198, 39-43` | 100 事件 = 100 次握手。进程级单例 driver/engine |
| P2-31 | 词法 artifact 只增不删 | `retrieval/index.py:192-195` | 每次入库生成全课程语料全量副本，N 次入库 = N 份 JSON，磁盘线性膨胀。ACTIVE 切换后 GC 旧版本（保留 N-1） |
| P2-32 | 双 worker 并发入库同课程时 artifact 可能缺 chunk | `pipeline.py:562-573` | B 的语料查询时 A 的版本仍 PROCESSING，B 产出 artifact 不含 A 的 chunk。publish 时重建 artifact 或语料查询纳入全部非 FAILED 版本 |

### 前端 / 数据质量（本轮新增）

| # | 问题 | 位置 | 说明与修复 |
|---|---|---|---|
| P2-33 | 切换页签丢失整个课程问答会话 | `student-course-tabs.tsx:47, 102-106` | Chat 状态全是组件本地 state，条件渲染即卸载；切回后对话消失且无恢复路径（后端 `GET /chat/sessions/{id}/messages` 存在）。状态提升到 CourseWorkspace 或保持挂载 |
| P2-34 | 样例文档元信息污染演示图谱与检索 | `ingestion/parsers.py:612-730`（无 front-matter 处理）+ `samples/*.md` + `seed_demo.py:427-471` | front matter 入库参与检索；文档标题与"## 自检问题"成为概念候选并被 seed 无条件批准，学生图谱出现"自检问题"伪知识点及配套伪测验。剥离 front matter + 调整样例标题 + seed 过滤 |
| P2-35 | 确定性兜底测验是"找原文"模式题，系统性抬高掌握度 | `learning/service.py:283-305` | 题目与干扰项句式固定可被模式识别，学生必对 → 掌握度只升不降，薄弱概念/学习路径信号失真。改基于概念描述的实质四选一 |
| P2-36 | 评测 Case JSON 契约无任何示例 | README、评测中心 UI | 三类数据集字段结构无文档无模板，教师无法自助完成评测（与 P1-3 叠加）。附最小 Case 模板 |
| P2-37 | LangGraph 文档与实现矛盾且缺决策记录 | `PROJECT_MEMORY.md:28`、`spec.md:362,846` vs `pyproject.toml`/`agent/core.py` | 文档称已锁定 LangGraph，实际为自研等价实现且无依赖。按 AGENTS.md 补决策记录并同步 spec（秋招评审硬伤） |

---

## 三、P3 —— 次要 / 防御纵深 / 打磨

**后端**：`learning/service.py:95-115` 服务层缺 teacher 角色校验（当前不可达，防御纵深）；`graph/query.py:42-45` Cypher 不校验 relation course_id（节点级过滤已防泄漏，且 compile() 不在请求路径）；`tasks/graph.py:171-176` PROCESSING 无租约检查重取（dispatch 租约 + MERGE 幂等缓解）；`grounding.py:276,285` 词法支撑阈值偏松（EN 0.5 / CN bigram 0.35）、`:219` expected_version 回退（主流程不可达）；`core.py:154-182` rewrite 轮整体替换证据（默认 minimum_count=1 下无损）；`core.py:413` + `chat.py:274` 异常类名经 usage 泄漏给学生；`learning/history.py` mastery 重放近似 + join 缺 deleted_at 过滤（无软删代码路径，潜在）；`core.py:106-113` + `chat.py:370` QUIZ/LEARNING_PATH 占位文案持久化为 COMPLETED（学生 UI 未暴露该意图，API 调用方受影响）；`agent/routing.py:16-54` 关键词路由可预期误判（"软件测试"→QUIZ），建议加入冻结路由评测集；`agent/service.py:414-423` target_concept_ids 不校验课程归属/审核状态（无泄漏，污染改写查询）；多个列表无分页（`chat.py:199-229`、`documents.py:460-500`、`learning.py:262-279`、`graph/service.py:58-139`、`history.py:135-218`）；`chunks.embedding` 无 pgvector ANN 索引（`models.py:680-682`、`0002_mvp_core.py:591-595`），与 P95<2s 目标冲突；`relation_candidates.to_candidate_id`/`merged_into_candidate_id` 无索引；embedding 逐行 UPDATE、逐行 DELETE（`pipeline.py:306-307, 451-452`）；大文档嵌入无中间 checkpoint；共享模型推理无信号量/合批（`retrieval/adapters.py:121-136`）；`parsers.py:160-171` GBK/GB18030 txt 被拒（Windows 记事本高频坑）；uvicorn 未开 `--proxy-headers`（compose `:163-169`）；`register` 409 邮箱枚举（`auth.py:73-79`）；`/health/ready` 探针每次新建 Redis client/Neo4j driver（`health.py:36-53`）；无 CORS/TrustedHost 中间件（当前同源架构不需要）；DEBUG 未纳入生产校验；孤儿上传文件无清理（`documents.py:406-424`）；DEAD_LETTER 无运维面（`tasks/graph.py:212-213`）；compose `--beat` 内嵌 worker，扩容即重复调度（`compose.yaml:208`）；每个 API 副本各持 BGE-M3+Reranker 约 4-5GB 且 compose 无 memory limit；driver 6.x vs server 5.26 兼容矩阵需复核。

**前端**：Tabs ARIA 结构不完整（aria-controls 悬空、无方向键导航，`course-workspace.tsx:213-234`）；一组次要文字色不达 WCAG AA（3.08:1-4.41:1，`ui.tsx:9`、`teacher-course-tabs.tsx:1077` 等，统一加深灰阶）；枚举状态直出英文 + 师生选项标号不一致（`ui.tsx:112-134`、`:746` vs `student:366`）；聊天流不自动滚动、引用锚点不 focus（`student-course-tabs.tsx:226, 253-258`）；学习路径图谱为空时要求学生手输知识点 UUID（`:1036-1045`）；教师资料管理无入库进度轮询（`teacher-course-tabs.tsx:278-299`）；换模板静默覆盖已填代码/名称（`dashboard.tsx:225-237`）；Bad Case 硬编码 limit=100 无分页、评测命令 `--index-version` 可自动带入（`teacher-governance-tabs.tsx:111, 310`）；代理透传客户端 `x-forwarded-*`/cookie、剥 content-encoding 留 accept-encoding 隐患（`route.ts:23-34, 56-59`）；`lib/types.ts:154` mastery 类型与后端不符；死 CSS `.status-dot`；skip-link 仅落地页有。

**文档/样例/seed**：README 缺 Node 24 前置说明（`README.md:88-97`）；AGENTS.md 仍写"周里程碑"（spec 已改连续推进，`AGENTS.md:23`）；seed 脚本 401 不重登（长演示可能中断，`seed_demo.py:87-163`）；学生图谱为列表渲染非可视化（spec §14 里程碑措辞为"可视化初版"，`student-course-tabs.tsx:526-660`）；LLM 出题未实现（`EXPLICIT_GENERATION_MODEL` 仅透传标记，兜底题为拼装题）。

---

## 四、spec 逐条对账摘要（78 项）

**统计：IMPLEMENTED 48 · PARTIAL 23 · MISSING 4 · DEVIATION 3**

**MISSING（4 项）**：
1. §6.2 每小时图/库一致性审计 + 重放（#44）
2. §8.3 账户删除 + 数据匿名化（#57，另见 P2-9）
3. §9.1 Langfuse 环境变量开关（#61，可选能力，未留接口）
4. §11.1 四类冻结评测数据集（#66，见 P1-3）

**DEVIATION（3 项）**：
1. FR-RETRIEVE-002 指定 bm25s 库 → 自研 `LightweightBM25Index`（`retrieval/index.py:50-160`，注释自认预留 bm25s 适配位）
2. FR-GRAPH-002 假设 LLM 抽取 → `deterministic-heading-v1` 确定性标题抽取（`pipeline.py:593-663`，aliases 恒空）
3. FR-AGENT-003 LangGraph 11 节点 → 自研 TrustedAgentCore 等价序列（缺 3 个节点，见 P1-4/P2-37）

**PARTIAL 中最重要的**（其余见上文 P1/P2 对应项）：评测运行 API 语义分裂（#53）；教师批量审核缺失（#30，FR-GRAPH-003 一半——批准/拒绝/编辑/来源/Outbox 均已实现；终审确认批量与合并均为零实现）；Neo4j `Course` 节点无写入（#28，终审确认全仓 Cypher 无 `:Course`）；Trace 七项缺 LLM 模型/延迟/Token 与 grounding 结果（#60）；§11.3 指标缺 Faithfulness / System 指标（TTFT/P50/P95/Token/失败率）与 LLM-as-Judge（#70；终审更正：**教师路径一致率与路径长度已实现**于 `evaluation/service.py:932-936` 并接入报告，此前误报为缺失）；端到端无浏览器级测试（#73）；12.4 安全测试缺 SQL/NoSQL 注入、XSS/Markdown 危险链接、真实并发竞争用例（#74）；§17 MVP 完成定义 10 条中第 7 条（门槛）因无数据集不可判定、第 10 条缺架构图与评测报告。

**反向核对**：routers 中 spec §7 未定义的端点均可在 spec 其他章节找到依据，无越界能力。

---

## 五、安全专项：已核查无问题的归档面

SQL/ILIKE 全参数化；Cypher 全参数化 + 关系类型枚举白名单 + 深度 int 约束；Redis 无用户输入 key、Celery JSON-only（禁 pickle，无反序列化 RCE）；lexical artifact 与 eval 报告路径有穿越防护；subprocess 仅固定参数 git 命令且 CLI-only；无 YAML/CSV/pickle；CSRF 面不存在（纯 Bearer、无 cookie）；无 open redirect（returnTo 为死代码）；无 XSS 渲染通道（无 dangerouslySetInnerHTML/markdown 渲染器）；服务层对 body 传入 ID 的归属复核完整（mass-assignment 无——schema `extra="forbid"` + 白名单 setattr）；membership 状态机自洽；无默认"无课程过滤"查询；源码无硬编码密钥；日志无 PII。系统指令层级 + evidence JSON 转义 + citation 白名单 + temperature=0 + json_object 的 agent 边界设计有效。

## 六、运行时专项：已核查无问题的归档面

API 无状态化良好（无进程内后台循环、租约用 `skip_locked`），横向扩 API/worker 大体安全；优雅关闭健康（stop_grace_period + acks_late + finally 释放）；retrieval trace 计时准确；chat 单轮 DB 查询 7-9 次无 N+1；worker_prefetch=1 正确；UTC 时区纪律贯彻全栈；compose 环境变量与 config 字段逐项对得上；migrate 一次性容器 + readiness 固定 revision 校验闭环。

---

## 七、测试缺口（按验收路径优先）

1. 真实并发幂等竞争（spec 12.4 点名）：现有 replay 测试为顺序执行，`submit_attempt` IntegrityError 分支（`learning/service.py:512-536`）零覆盖
2. 发布后旧会话版本隔离（spec 12.2）：P1-1/P2-1 两个场景均无测试
3. 跨课程 quiz 作答、评测/学习路径跨课程访问
4. 上传 413、refresh token 过期路径、chat API 层 InputGuard 拒绝路径
5. `DatabaseLexicalRetriever` / `FallbackLexicalRetriever` 无任何直接测试
6. 浏览器级 E2E（Playwright/Cypress）不存在
7. CI 外本地跑 pytest 时 postgres marker 用例的 skip 行为需确认

---

## 八、修复路线图

**第一批（恢复产品约束与验收前提，1-2 周）**
P1-1 版本隔离、P1-2 分数门槛、P1-3 冻结评测数据集 + 冻结报告、P1-7 模型单例、P1-9 quiz 幂等键、P1-10 会话误清、P2-34 样例污染（演示第一印象）、P2-37 LangGraph 决策记录。

**第二批（性能与安全，2-3 周）**
P1-5 连接池、P1-6 真流式 + LLM 重试、P1-8 限流 + body 上限、P1-11 token Cookie 化、P1-12 CI/Dockerfile、P2-1/2/3/4/5 后端正确性批、P2-20/21/22 注入三连、P2-27/28/29 可观测性批。

**第三批（spec 收口）**
P1-4 Agent 工具链、P2-9 数据删除、P2-10 一致性审计、P2-15 评测 API、P2-18 跨课程前置（产品决策）、批量审核、剩余 P3 与测试缺口。

**需要产品决策的项**：P2-18（跨课程前置实现或移出范围）、#8 退出学生历史访问语义（有测试固化的现状解读 vs spec 措辞，建议澄清 spec）、P2-23（教师注册是否开放）、自研 BM25/deterministic 抽取是否补齐为 spec 指定方案或在 spec/文档中正式记为决策。

---

## 九、终审记录（2026-08-31）

本报告经五轮审查与两轮对抗性复核收敛：

1. **第一轮**（三路并行）：后端核心 / 路由与安全 / 前端与工程配置。
2. **第二轮**：逐条验证第一轮 18 项结论（4 项降级或修正定性）+ 盲挖新问题；关键 P1 由审查者本人读码复核。
3. **第三轮**（四专项）：spec 逐条对账（78 项）、安全专项（prompt injection / 授权矩阵 / 注入面 / 依赖许可）、运行时可观测性专项、前端细节 / 文档 / 样例专项。
4. **终审**：审查者本人亲验全部 P1（版本隔离、分数门槛、评测数据集缺失、Agent 工具缺失、连接池、伪流式、模型重载、body 上限、token 存储、quiz 幂等键、会话误清、CI/Dockerfile 均逐一读码确认）+ 独立代理对第三轮 18 项高危结论做对抗性复核。

**对抗性复核结果**：18 项中 16 项确认、1 项驳回（教师路径一致率与路径长度已实现，第四节的相应描述已更正）、1 项部分修正（chat LLM 存在借道 grounding 循环的单次重试，P1-6 已按实修正）。全程累计修正/驳回 6 项，其余全部结论以代码证据坐实。

**局限声明**（诚实边界）：未实际执行干净环境 Compose 启动与真实负载压测，性能结论为静态分析推断；真实评测门槛数值因 P1-3（无数据集）无法验证；SQLite 测试无法证明 PostgreSQL 下并发竞争的真实行为；依赖许可证判断基于知识而非实时数据库，compose 中 Redis 7.4（RSALv2/SSPLv1）与 Neo4j 5.26-community（GPLv3）的许可状态建议发布前复核；neo4j Python driver 6.x 与 server 5.26 的兼容矩阵需验证。

---

## 十、修复实施记录（2026-08-31，第一批）

第一批修复已实施并通过回归（后端 122 项测试通过、ruff 干净，前端 lint、类型检查和生产构建通过）：

| 项 | 修复内容 | 关键位置 |
|---|---|---|
| P1-1 版本隔离 | `CourseIndex` 新增 `covered_document_version_ids`（迁移 0003）；词法索引构建时记录覆盖集；`DatabaseLexicalRetriever` 只加载覆盖版本；`publish()` 只翻转覆盖版本；chat 路由允许 ARCHIVED 索引服务旧会话；dense 路由接受 ACTIVE/ARCHIVED 并按覆盖集过滤语料；无覆盖清单的迁移前索引检索时失败关闭，发布前必须重建 | `models.py`、`alembic/versions/0003_*`、`ingestion/pipeline.py`、`agent/service.py`、`graph/service.py`、`routers/chat.py`、`retrieval/adapters.py` |
| P1-2 分数门槛 | BM25 候选附带归一化覆盖分（raw/ΣIDF，截断 [0,1]）；dense 归一化改为锚定映射（floor 0.2，0.55 阈值 ≈ 相似度 0.64）；超出 [0,1] 的 rerank 分经 logistic 校准；删除按排名伪造的 `max(0.56, ...)` 兜底分——无校准分的候选不再越过证据门槛 | `retrieval/index.py`、`retrieval/adapters.py`、`evaluation/runner.py`、`agent/service.py` |
| P1-7 模型单例 | Worker 进程级 `shared_embedding_adapter`（懒加载 + 锁），任务不再逐个重载 BGE-M3 | `tasks/ingestion.py` |
| P1-9 quiz 幂等键 | 幂等键改为取题时生成并跨重试复用（`attemptKey` state） | `apps/web/components/student-course-tabs.tsx` |
| P1-10 会话误清 | refresh/me 失败仅在 401 时清除会话；网络/5xx 保留 token 与持久化用户快照；登录后 me 瞬时失败不再丢弃新 token | `apps/web/lib/api.ts`、`apps/web/components/auth-provider.tsx` |
| P2-34 样例污染 | Markdown 解析剥离 YAML front matter；样例"自检问题"降为粗体；seed 拒绝文档标题类候选及其关系（而非批准） | `ingestion/parsers.py`、`samples/*.md`、`scripts/seed_demo.py` |
| P2-37 文档矛盾 | PROJECT_MEMORY 决策表新增 LangGraph/bm25s/确定性抽取/覆盖列四条记录；spec FR-AGENT-003 与 §14 措辞同步 | `PROJECT_MEMORY.md`、`spec.md` |
| P1-3 评测数据集基建 | seed 新增 `[6/8]` 步骤：创建 DRAFT 演示评测集（检索 8-10 条绑定真实 chunk ID + 路由 10 条 + 拒答 4 条，可重跑幂等）；apps/api/README 增加四类 Case 模板。验收用冻结数据集仍需按 spec §11.1 单独构建 | `scripts/seed_demo.py`、`apps/api/README.md` |

**新增回归测试**：`tests/test_version_isolation.py`（4 项：DB 词法覆盖过滤、旧会话 ARCHIVED 索引服务、publish 仅翻转覆盖版本、迁移前索引失败关闭）；BM25 归一化区分部分覆盖；未校准分数不越过证据门槛；worker 单例；front matter（含 CRLF）剥离；演示评测集重跑时锁定当前文档版本并更新过期标注。

**待办**（第二、三批未动）：限流与 body 上限、真流式、连接池、token Cookie 化、CI/Dockerfile、citation 多 claim、cancel 恢复路径、prompt-injection 三项、可观测性批、其余 P2/P3。
