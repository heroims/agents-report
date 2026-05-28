---
name: getagt
description: 采集当前用户的 Claude Code、Codex CLI、OpenCode、Cursor、Trae 使用数据，合并为单一报告并按周期（周/月/季/年）归档
---

# getagt

采集当前用户的 Claude Code、Codex CLI、OpenCode、Cursor、Trae 使用数据，合并为单一报告并按周期（周/月/季/年）归档。

## 用法

直接调用脚本：

```bash
./getagt                        # 周报（默认）
./getagt --period monthly       # 月报
./getagt --period quarterly     # 季报
./getagt --period annual        # 年报
```

## 自动流程

1. 根据 `git config user.name` 确定成员标识
2. 从 `scripts/members.json` 查找所属分组（默认 `group`）
3. 生成 Claude Code insights 报告
4. 采集 Codex/OpenCode/Cursor/Trae 数据（可选，失败不阻断）
5. 合并为 `reports/{period}/{group}/{name}-{period}-report.html`
6. 采集本机环境信息（JDK、网络 IP）并注入报告
7. 若设置了 `AGENTS_REPORT_URL` 环境变量，通过 HTTP PUT 上传到 dashboard；否则 git add/commit/push
