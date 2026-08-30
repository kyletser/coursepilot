# CoursePilot 开放演示语料

本目录提供两份可直接入库的微型中文课程资料：

- `data-structures.md`：数据结构
- `operating-systems.md`：操作系统

两份资料均为 CoursePilot 项目原创演示文本，不摘录教材、讲义或题库。内容用于验证解析、检索、引用、图谱审核、来源题目审核和课程发布闭环，不代表完整课程，也不包含预制评测结果。

## 许可

除另有说明外，本目录中的课程资料采用 [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)（SPDX：`CC-BY-4.0`）许可。建议署名：`CoursePilot contributors, CoursePilot Open Course Samples, 2026`。完整许可说明见 `LICENSE.md`。

## 一键入库

先启动 API、Worker、PostgreSQL、Redis 和 Neo4j，并确保 Worker 能加载真实 BGE-M3 模型。首次拉取模型时可在本地 `.env` 中设置 `MODEL_ALLOW_DOWNLOAD=true`，再构建并启动 Compose：

```powershell
docker compose up --build -d
python scripts/seed_demo.py
```

脚本默认连接 `http://127.0.0.1:8000`。账号、密码、API 地址和轮询超时均可通过 `python scripts/seed_demo.py --help` 查看或覆盖。脚本不会伪造向量、发布状态或评测指标；模型或 Worker 不可用时会停止并给出排查提示。
