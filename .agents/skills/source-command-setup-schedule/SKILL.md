---
name: setup-schedule
description: 开启个人定时上报（周报/月报/季报/年报），通过 Codex Automation 自动提交
---

# setup-schedule

为当前用户创建四个定时上报自动化任务，每个任务会在对应周期自动运行 `./getagt` 采集数据并归档提交。

## 执行方式

Agent 使用 `codex_app__automation_update` 工具调用。

## 共用参数

- `cwds`: `["{项目根目录绝对路径}"]`
- `kind`: `cron`
- `executionEnvironment`: `local`
- `model`: `gpt-5.1`
- `reasoningEffort`: `low`
- `status`: `ACTIVE`
- `mode`: `suggested_create`

## 四个任务

### 1. 提交个人周报
- `name`: `提交个人周报`
- `rrule`: `FREQ=WEEKLY;BYDAY=MO`
- `prompt`: `运行 ./getagt --period weekly 采集本周 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

### 2. 提交个人月报
- `name`: `提交个人月报`
- `rrule`: `FREQ=MONTHLY;BYMONTHDAY=1`
- `prompt`: `运行 ./getagt --period monthly 采集本月 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

### 3. 提交个人季报
- `name`: `提交个人季报`
- `rrule`: `FREQ=MONTHLY;BYMONTH=1,4,7,10;BYMONTHDAY=1`
- `prompt`: `运行 ./getagt --period quarterly 采集本季度 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

### 4. 提交个人年报
- `name`: `提交个人年报`
- `rrule`: `FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1`
- `prompt`: `运行 ./getagt --period annual 采集本年度 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据并归档提交`

## 完成后

告知用户需要在 Codex 桌面端右侧面板确认每个自动化卡片以激活。可运行 `/teardown-schedule` 随时暂停或删除。

## 回退方案

若 `codex_app__automation_update` 不可用（如 Claude Code 环境），回退至系统调度：
`python3 scripts/schedule_setup.py`（macOS 用 launchd，Linux 用 crontab，Windows 用 schtasks）。
