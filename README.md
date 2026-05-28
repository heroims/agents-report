# Agents Team Usage Collector

> **Cross-agent usage analytics**: collect, merge, and report data from Claude Code, Codex CLI, OpenCode, Cursor, Trae, OpenClaw, and Hermes — seven AI coding agents in one unified dashboard. Zero Python dependencies for core scripts.

团队成员通过 agent slash command 即可采集 7 款主流 AI Coding Agent 的使用数据，合并为统一报告并按周/月/季/年归档。当团队同时使用多款 AI 编程工具时，这是目前公开可检索到的唯一跨 agent 使用统计方案。核心采集脚本纯 Python 标准库实现，无需安装任何第三方依赖。

可选附带 Flask Dashboard 用于团队报告汇总与 AI 摘要（Dashboard 需要额外安装依赖）。

---

## 环境要求

- Python 3.12+（**核心采集仅需标准库，无需 pip/uv 安装任何包**）
- 至少安装 Claude Code / Codex CLI / OpenCode / Cursor / Trae / OpenClaw / Hermes 其中之一

## 可选：环境变量

在项目根目录创建 `.envrc` 文件（已被 `.gitignore` 忽略），`./getagt`、`./analyzeagt`、`./dashboard-uv` 启动时会自动 `source`：

```bash
# Dashboard 地址 —— 设置后 getagt 直接 HTTP PUT 上传报告，analyzeagt 委托服务端分析
# 不设置时：getagt 通过 git pull --rebase && git add/commit/push 归档，analyzeagt 本地生成团队报告
export AGENTS_REPORT_URL=http://localhost:8880

# Dashboard 监听端口（仅 Dashboard 使用）
export DASHBOARD_PORT=8880

# GitLab 集成（可选 —— 不设置时 Dashboard 读取本地 reports/ 目录）
# export GITLAB_URL=https://gitlab.example.com
# export GITLAB_TOKEN=glpat-xxxx
# export GITLAB_PROJECT=my-group/my-project

# AI 摘要功能（仅 Dashboard /api/ai/summary 和 /api/ai/chat 使用）
export AI_PROVIDER=openai_chat       # 或 anthropic_messages
export AI_API_KEY=sk-xxxx
export AI_BASE_URL=https://api.openai.com
export AI_MODEL=gpt-4o
```

---

## 采集个人报告 (`getagt`)

支持主流agent的skill模式,在目录里打开agent即可

<img src="/screenshot/trae.png" width="30%"><img src="/screenshot/claude.png" width="50%">

### Claude Code / Codex 中执行

```text
/getagt
```

该命令由 [`.claude/commands/getagt.md`](.claude/commands/getagt.md) 和 [`.agents/skills/source-command-getagt/SKILL.md`](.agents/skills/source-command-getagt/SKILL.md) 驱动。Agent 会自动运行 `./getagt` 并展示结果。

### CLI 执行

```bash
./getagt                        # 周报（默认）
./getagt --period monthly       # 月报
./getagt --period quarterly     # 季报
./getagt --period annual        # 年报
```

`./getagt` 是 shell wrapper，等价于 `python3 scripts/getagt.py [args]`。Windows 下可直接运行 `python scripts\getagt.py`。

### 自动流程

1. 通过 `git config user.name` 识别成员标识（slug 化后作为文件名前缀）
2. 从 [`scripts/members.json`](scripts/members.json) 查找所属分组（key 为分组名，value 为成员列表；未匹配的默认为 `group`）
3. 调用 `scripts/generate_insights_from_stats.py` 生成 Claude Code insights 报告
4. 依次采集 Codex / OpenCode / Cursor / Trae / OpenClaw / Hermes 数据（可选数据源，任一失败不阻断主流程）
5. 采集本机环境信息（JDK 版本、网络接口 IP）
6. 通过 `scripts/merge_reports.py` 合并为单一 HTML
7. 输出到 `reports/{period}/{group}/{name}-{period}-report.html`
8. 若设置了 `AGENTS_REPORT_URL`：通过 `PUT /api/report/upload` 上传到 Dashboard；否则 `git pull --rebase && git add/commit/push`

---

## 生成团队报告 (`analyzeagt`)

### Claude Code / Codex 中执行

```text
/analyzeagt
```

### CLI 执行

```bash
./analyzeagt                        # 周报（默认）
./analyzeagt --period monthly       # 月报
./analyzeagt --period quarterly     # 季报
./analyzeagt --period annual        # 年报
```

等价于 `python3 scripts/analyze.py [args]`。

### 自动流程

1. 遍历 `reports/` 收集所有成员报告（通过 `scripts/period_utils.py` 解析文件名中的周期标识）
2. 按周期类型筛选，确定当前周期和上一对比周期
3. **自动回退聚合**：若指定周期无直接匹配数据，自动回退聚合下级周期：
   - 年报 → 季报 → 月报 → 周报
   - 季报 → 月报 → 周报
   - 月报 → 周报
4. 聚合规则：数值字段累加、days 取最大值、tools/languages 合并计数
5. 若设置了 `AGENTS_REPORT_URL`：通过 `POST /api/analyze` 委托 Dashboard 服务端执行；否则本地生成
6. 输出到 `reports/{period}/team-report.html`

## 定时上报管理 (`setup-schedule` / `teardown-schedule`)

团队每个成员都可以通过指令开启或关闭定时自动上报。指令会根据运行环境自动选择最佳方案：
- **Codex**：通过 `codex_app__automation_update` 创建托管自动化
- **Claude Code / 其他**：回退至系统调度（macOS launchd / Linux crontab / Windows schtasks）

### 在 Agent 中执行

```text
/setup-schedule      # 一键创建周报+月报+季报+年报四个定时任务
/teardown-schedule    # 暂停或删除所有已创建的定时上报任务
```

- Codex：指令由 [`.agents/skills/source-command-setup-schedule/SKILL.md`](.agents/skills/source-command-setup-schedule/SKILL.md) 驱动
- Claude Code：指令由 [`.claude/commands/setup-schedule.md`](.claude/commands/setup-schedule.md) 驱动，含自动回退逻辑

### 直接运行脚本（跳过 Agent）

```bash
python3 scripts/schedule_setup.py     # 安装系统调度定时任务
python3 scripts/schedule_teardown.py  # 移除系统调度定时任务
```

### 调度规则

| 任务 | 频率 | 执行时间 |
|------|------|----------|
| 提交个人周报 | 每周 | 周一 09:00 |
| 提交个人月报 | 每月 | 1 日 09:00 |
| 提交个人季报 | 每季度 | 1/4/7/10 月 1 日 09:00 |
| 提交个人年报 | 每年 | 1 月 1 日 09:00 |
## 启动 Dashboard（可选）

Dashboard 提供 Web 界面浏览团队报告，支持 AI 摘要和问答。仅 Dashboard 需要额外安装依赖。

### 安装依赖

```bash
uv sync              # 安装 pyproject.toml 中声明的依赖（flask, requests, openai, anthropic）
```

Dashboard 依赖：`flask`, `requests`, `openai`, `anthropic`。核心采集脚本（`getagt.py`, `analyze.py` 等）仅用 Python 标准库，无需此步骤。

### 启动

```bash
./dashboard-uv       # 等价于 uv run python dashboard/server.py
```

Docker 方式：

```bash
docker build -t agents-report-dashboard .
docker run -p 8880:8880 agents-report-dashboard
```

默认监听 `http://localhost:8880`（可通过 `DASHBOARD_PORT` 修改）。不配 `GITLAB_TOKEN` 时自动读取本地 `reports/` 目录。

### Dashboard API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Dashboard 前端页面 |
| `/api/data` | GET | 获取聚合数据（支持 `?group=&period=&period_type=` 过滤） |
| `/api/report` | GET | 代理报告 HTML（支持 `?path=` 或 `?name=&period=&group=&kind=`） |
| `/api/report/upload` | PUT | 接收 `getagt` 上传的个人报告 |
| `/api/analyze` | POST | 触发服务端团队分析（`?period_type=weekly`） |
| `/api/refresh` | POST | 刷新缓存 |
| `/api/ai/summary` | POST | 生成 AI 摘要（需配置 AI 环境变量） |
| `/api/ai/summary/stream` | POST | 流式 AI 摘要 |
| `/api/ai/chat` | POST | AI 问答 |

---

## 目录与命名规范

```
reports/
├── {period}/                      # 如 2026-W22 / 2026-05 / 2026-Q2 / 2026
│   ├── team-report.html           # 团队汇总报告
│   ├── {group}/                   # 分组目录（如 group / dex）
│   │   └── {name}-{period}-report.html   # 个人报告
│   └── ...
```

### 周期标识格式

| 周期类型 | 标识格式 | 示例 |
|----------|----------|------|
| 周报 | `YYYY-WNN` | `2026-W22` |
| 月报 | `YYYY-MM` | `2026-05` |
| 季报 | `YYYY-QN` | `2026-Q2` |
| 年报 | `YYYY` | `2026` |

### 分组

- 分组由 [`scripts/members.json`](scripts/members.json) 定义，key 为分组名，value 为成员标识列表
- 不在 `members.json` 中的成员默认归入 `group` 分组
- 成员标识来源于 `git config user.name` 的 slug 化结果（小写、空格转连字符、去特殊符号）

---

## 项目结构

```
.
├── getagt                    # 个人采集入口（shell wrapper → scripts/getagt.py）
├── analyzeagt                # 团队分析入口（shell wrapper → scripts/analyze.py）
├── dashboard-uv              # Dashboard 入口（shell wrapper → dashboard/server.py）
├── pyproject.toml            # uv 项目定义 + Dashboard 依赖
├── Dockerfile                # Dashboard 容器构建
├── scripts/
│   ├── getagt.py             # 主采集脚本（标准库）
│   ├── analyze.py            # 团队聚合分析（标准库）
│   ├── period_utils.py       # 周期工具函数（标准库）
│   ├── generate_insights_from_stats.py  # Claude Code insights 生成（标准库）
│   ├── gen_report_from_sessions.py      # 从 sessions 文件生成 Claude 报告（标准库，备选）
│   ├── collect_codex.py      # Codex CLI 数据采集（标准库）
│   ├── collect_opencode.py   # OpenCode 数据采集（标准库）
│   ├── collect_cursor.py     # Cursor 数据采集（标准库）
│   ├── collect_trae.py       # Trae 数据采集（标准库）
│   ├── collect_openclaw.py   # OpenClaw 数据采集（标准库）
│   ├── collect_hermes.py     # Hermes 数据采集（标准库）
│   ├── merge_reports.py      # 多工具报告合并（标准库）
│   ├── schedule_setup.py      # 定时上报安装（launchd/crontab/schtasks）
│   ├── schedule_teardown.py   # 定时上报卸载
│   ├── members.json          # 成员→分组映射
│   └── exclude_paths.json    # 路径排除配置
├── dashboard/
│   ├── server.py             # Flask 服务（需 flask, requests, openai, anthropic）
│   ├── dashboard.html        # 前端页面
│   ├── cache.py              # 数据缓存
│   ├── gitlab_client.py      # GitLab API 客户端
│   └── requirements.txt      # pip 替代依赖声明
├── .claude/commands/
│   ├── getagt.md
│   ├── analyzeagt.md
│   ├── setup-schedule.md     # 一键开启定时上报
│   └── teardown-schedule.md  # 一键关闭定时上报
├── .agents/skills/
│   ├── source-command-getagt/
│   ├── source-command-analyzeagt/
│   ├── source-command-setup-schedule/
│   └── source-command-teardown-schedule/
└── reports/                  # 报告存档目录（git tracked）
```

---

## 常见问题

### Claude Code insights 生成失败

- **现象**：执行 `/getagt` 后提示 Claude 报告生成失败
- **原因**：`scripts/generate_insights_from_stats.py` 执行异常，常见为 `~/.claude/usage-data/session-meta/` 或 `~/.claude/projects/**/*.jsonl` 缺失或损坏
- **处理**：
  1. 检查 `~/.claude/usage-data/session-meta/` 与 `~/.claude/projects/` 是否存在且本期有数据
  2. 单独运行看报错：`python3 scripts/generate_insights_from_stats.py 2026-W19 --output=/tmp/test.html`
  3. **不要**改用 Claude Code 内置 `/insights` 命令来兜底——`/insights` 输出滚动 ~30 天数据（不按周），归档后会出现月度数字假冒周报的情况

### 跨期补跑历史报告

`./getagt` 默认取当前周期；要生成历史报告，直接调底层脚本并显式指定周期号：

```bash
# 周报
python3 scripts/generate_insights_from_stats.py 2026-W19 --output=$HOME/.claude/usage-data/report.html
python3 scripts/collect_codex.py 2026-W19 --output=/tmp/codex.html
python3 scripts/merge_reports.py $HOME/.claude/usage-data/report.html /tmp/codex.html "" "" "" \
    reports/2026-W19/group/{name}-2026-W19-report.html 2026-W19

# 月报
python3 scripts/generate_insights_from_stats.py 2026-05 --output=$HOME/.claude/usage-data/report.html
python3 scripts/collect_codex.py 2026-05 --output=/tmp/codex.html
python3 scripts/merge_reports.py $HOME/.claude/usage-data/report.html /tmp/codex.html "" "" "" \
    reports/2026-05/group/{name}-2026-05-report.html 2026-05
```

### OpenClaw 数据采集失败

- OpenClaw 数据来自 `~/.openclaw/logs/commands.log`，该文件记录 session 创建和重置事件
- 目前采集的指标：会话数、活跃天数、agent 分布、触发来源
- 单独排查：`python3 scripts/collect_openclaw.py 2026-W22 --output=/tmp/test.html`
- 注意：OpenClaw 目前仅跟踪会话启动事件，无法获取 token 消耗等执行细节

### Hermes 数据采集失败

- Hermes 数据来自 `~/.hermes/state.db`，包含 sessions 和 messages 两张表
- 采集的指标：会话数、消息数、token（输入/输出/缓存/推理）、活跃天数、模型分布、工具调用
- 单独排查：`python3 scripts/collect_hermes.py 2026-W22 --output=/tmp/test.html`

### OpenCode / Cursor / Trae 数据采集失败

- OpenCode、Cursor、Trae 均为可选数据源，主流程在它们失败时会继续执行
- 单独排查：`python3 scripts/collect_opencode.py 2026-W15 --output=/tmp/test.html`
- Codex 依赖 `~/.codex/state_5.sqlite` 数据库；OpenCode 依赖 `opencode db path` 命令可用

### 成员名称映射异常

- 确认文件名格式为 `{name}-{period}-report.html`
- `members.json` 的 key 必须与 `git config user.name` slug 化后的结果一致
- 可运行 `git config user.name` 确认当前标识
