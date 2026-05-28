---
name: teardown-schedule
description: 关闭个人定时上报，暂停或删除所有已创建的周报/月报/季报/年报自动化任务
---

# teardown-schedule

关闭当前用户的所有定时上报自动化任务。

## 执行方式

1. 检查 `$CODEX_HOME/automations/` 目录是否存在。若不存在则告知用户无任务可关闭并结束。

2. 遍历 `$CODEX_HOME/automations/*/automation.toml`，查找 `name` 匹配以下之一的自动化：
   - `提交个人周报`
   - `提交个人月报`
   - `提交个人季报`
   - `提交个人年报`

3. 对每个匹配到的任务，使用 `codex_app__automation_update` 工具暂停：
   - `mode`: `update`
   - `id`: 从 toml 文件中获取
   - `kind`: `cron`
   - `status`: `PAUSED`

4. 告知用户已暂停的任务数量和名称。询问是否需完全删除（`mode=delete`）。

## 回退方案

若 `codex_app__automation_update` 不可用（如 Claude Code 环境），回退至系统调度：
`python3 scripts/schedule_teardown.py`（自动检测 macOS launchd / Linux crontab / Windows schtasks 并移除对应任务）。
