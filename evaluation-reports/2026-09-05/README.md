# 2026-09-05 Agent 评测证据

本目录保存 CoursePilot 的正式内部评测与外部迁移测试证据。

- `summary.json`：供简历数字映射和自动检查使用的机器可读摘要。
- `internal-v3-raw.zip`：8 份内部原始 JSON 报告，包含逐案例结果、检索 Trace、数据集哈希、模型、硬件和 Git 来源。
- `cmrc-external-raw.zip`：CMRC 2018 外部检索迁移测试原始报告。

内部报告绑定干净提交 `85f67a403ec2f3f2d45baabf4abf8fc99d451e4a`；外部报告绑定干净提交 `60ba63109b4417111b4c73d0804b9eda24e7f286`。压缩包和解压后原始文件的 SHA256 记录在 `summary.json`。

外部数据来自 [CMRC 2018 官方仓库](https://github.com/ymcui/cmrc2018)，许可证为 CC BY-SA 4.0。仓库中不再分发上游数据文件，只保存测试脚本、来源提交、文件哈希和运行结果。该结果是自建固定子集的检索迁移测试，不是 CMRC 官方榜单成绩。
