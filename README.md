# MathModel-Pipeline-Agent

国赛数学建模端到端管道。本仓库接任并继续完善 [MathModelAgent](https://github.com/jihe520/MathModelAgent) 的 **WebUI**（`backend/` + `frontend/`）；`skills/` 跟随上游。

拷贝自 `83d8783`。不是官方续作。许可沿用原仓库：个人免费、禁止闭源分发、不可提供商业服务。详见 `LICENSE`、`NOTICE`。

| 目录 | 谁维护 |
|---|---|
| `backend/` `frontend/` | 本仓库接任，继续优化 |
| `skills/` | 定时从上游拉，开 PR，不自动合 |

## 跑起来

需要 Python 3.12+、Node.js、pnpm、Redis。或 Docker（先复制环境文件）：

```bash
cp backend/.env.example backend/.env.dev
docker-compose up
```

- 前端 http://localhost:5173
- 后端 http://localhost:8000

本地：侧边栏配置四个 Agent 的 API Key，粘贴题目，上传 csv/xlsx，提交后看 `/task/{id}`。

Windows 也可双击 `win_start.bat`。

> **安全提示**：后端默认只监听 `127.0.0.1`。本服务没有鉴权，且会在本机执行 LLM 生成的 Python 代码——把端口暴露到局域网等于把执行权交出去。确需局域网访问时，将 `win_start.bat` 中的 `--host` 改为 `0.0.0.0` 前请自行确认网络环境可信。

## 质量门与审批体系

流水线在四个 Agent（Coordinator 拆题 → Modeler 建模 → Coder 编码 → Writer 写作）之间布设四道质量门与人工检查点，设计规格见 `docs/quality-gates-plan.md` 与 `docs/adr/`：

- **G1 数据完备性门**：拆题后校验题面声明附件与工作目录实存文件，防"演示数据论文"
- **G2 代码质量门**：每问编码后两层把关（L1 脚本检查 + L2 AI 评审），MATERIAL 触发 ≤3 轮定向修复
- **G3 文本门**：每节写作后查内部文案泄露、路径、占位符、引用完整性
- **G4 终审**：七维分类判据 + 数值重算 + 机械裁决，遗留问题如实写入论文"局限性"章节

检查点①②④支持人工三分支审批（approve / revise / reject）；`AUTO_MODE=true` 时门耗尽自动降级放行并记录 `auto_degraded` 审计。`QUALITY_GATES_ENABLED` / `AGENT_CONTRACTS_ENABLED` 可整体关闭还原上游基线，用于 A/B 对照。任务全程状态机持久化（`work_dir/task_state.json`），全部门报告落盘 `verify_report.md`。

术语表见 `CONTEXT.md`；三期 Agent 方法论契约（建模蓝图 / 论证纪律 / 图表 DoD）见 `docs/agent-methodology-upgrade.md`。

## CI

PR / push 到 `backend/`、`frontend/` 时跑：

- backend：`uv sync` 后 `ruff check app` + `pytest -q`
- frontend：`pnpm install` 后 `biome check --formatter-enabled=false src` + `vue-tsc -b`

## 上游同步

- `skills/`：`.github/workflows/sync-skills.yml` 每天拉一次，有 diff 就开 PR
- `backend/` `frontend/`：`.github/workflows/watch-webui.yml` 只开 issue，不覆盖本地

本地手动同步 skills：

```bash
bash scripts/sync-upstream-skills.sh
```
