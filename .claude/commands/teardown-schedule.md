---
description: 关闭个人定时上报，自动选择合适方案（Codex Automation 或系统调度）
allowed-tools: Bash, Read
---

关闭当前用户的所有定时上报任务（提交个人周报、月报、季报、年报）。

## 执行步骤（不要询问确认，直接执行）

### 方案选择

优先检测 `codex_app__automation_update` 工具是否可用：
- **可用（Codex 环境）**：走方案 A，通过 MCP 工具暂停/删除自动化
- **不可用（Claude Code / 其他）**：走方案 B，移除系统 crontab / launchd 任务

### 方案 A：Codex Automation（codex_app__automation_update 可用）

1. 检查 `$CODEX_HOME/automations/` 目录是否存在，若不存在则告知用户没有已创建的定时上报任务并结束。

2. 遍历 `$CODEX_HOME/automations/*/automation.toml`，查找 `name` 匹配以下任一名称的自动化任务：
   - `提交个人周报`
   - `提交个人月报`
   - `提交个人季报`
   - `提交个人年报`

3. 对每个匹配到的任务，使用 `codex_app__automation_update` 工具以 `mode=update`、`status=PAUSED` 暂停该任务：
   - `id`: 从 `automation.toml` 中获取的任务 ID
   - `kind`: `cron`
   - `status`: `PAUSED`

4. 告知用户已暂停的自动化数量和名称。询问是否需要完全删除（`mode=delete`）。

### 方案 B：系统调度（codex_app__automation_update 不可用）

直接运行回退脚本：

```bash
python3 scripts/schedule_teardown.py
```

该脚本自动检测操作系统，移除对应的 launchd / crontab / schtasks 定时任务。
