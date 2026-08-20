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

## CI

PR / push 到 `backend/`、`frontend/` 时跑：

- `ruff check app`（backend）
- `biome check --formatter-enabled=false src`（frontend；忽略 `components/ui` 与大资源）

## 上游同步

- `skills/`：`.github/workflows/sync-skills.yml` 每天拉一次，有 diff 就开 PR
- `backend/` `frontend/`：`.github/workflows/watch-webui.yml` 只开 issue，不覆盖本地

本地手动同步 skills：

```bash
bash scripts/sync-upstream-skills.sh
```
