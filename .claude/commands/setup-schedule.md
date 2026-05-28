---
description: 开启个人定时上报（周报/月报/季报/年报），自动选择合适方案
allowed-tools: Bash, Read
---

为当前用户创建四个定时上报任务（周报、月报、季报、年报），每个任务会在对应周期自动运行 `./getagt` 采集数据并归档提交。

## 执行步骤（不要询问确认，直接执行）

### 方案选择

优先检测 `codex_app__automation_update` 工具是否可用：
- **可用（Codex 环境）**：走方案 A，使用 MCP 工具创建自动化
- **不可用（Claude Code / 其他）**：走方案 B，使用系统 crontab / launchd 安装

### 方案 A：Codex Automation（codex_app__automation_update 可用）

使用 `codex_app__automation_update` 工具创建以下四个 Cron 自动化，全部使用 `mode=suggested_create`：

**共用参数**：
- `cwds`: `["{项目根目录绝对路径}"]`
- `kind`: `cron`
- `executionEnvironment`: `local`
- `model`: `gpt-5.1`
- `reasoningEffort`: `low`
- `status`: `ACTIVE`

**任务一：提交个人周报**
- `name`: `提交个人周报`
- `rrule`: `FREQ=WEEKLY;BYDAY=MO`
- `prompt`: `运行 ./getagt --period weekly 采集本周 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

**任务二：提交个人月报**
- `name`: `提交个人月报`
- `rrule`: `FREQ=MONTHLY;BYMONTHDAY=1`
- `prompt`: `运行 ./getagt --period monthly 采集本月 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

**任务三：提交个人季报**
- `name`: `提交个人季报`
- `rrule`: `FREQ=MONTHLY;BYMONTH=1,4,7,10;BYMONTHDAY=1`
- `prompt`: `运行 ./getagt --period quarterly 采集本季度 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

**任务四：提交个人年报**
- `name`: `提交个人年报`
- `rrule`: `FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1`
- `prompt`: `运行 ./getagt --period annual 采集本年度 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

全部创建完成后告知用户：需在 Codex 桌面端右侧面板确认每个自动化卡片以激活。

### 方案 B：系统调度（codex_app__automation_update 不可用）

直接运行回退脚本：

```bash
python3 scripts/schedule_setup.py
```

该脚本自动检测操作系统（macOS → launchd，Linux → crontab，Windows → schtasks），安装四个定时任务，每天 09:00 执行。

告知用户调度规则：
- 周报 — 每周一 09:00
- 月报 — 每月 1 日 09:00
- 季报 — 1/4/7/10 月 1 日 09:00
- 年报 — 每年 1 月 1 日 09:00
