---
name: getagt
description: 采集当前用户的 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据，合并为单一报告并按周期（周/月/季/年）归档
---

# getagt

采集当前用户的 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据，合并为单一报告并按周期（周/月/季/年）归档。

## 用法

直接调用脚本：

```bash
./getagt                        # 周报（默认，中文）
./getagt --period monthly       # 月报
./getagt --period quarterly     # 季报
./getagt --period annual        # 年报
./getagt --lang en              # 英文周报
./getagt --period monthly --lang en  # 英文月报
```

**语言选择**：
- 默认根据 `AGENTS_REPORT_LANG` 环境变量（`zh`/`en`），未设置时默认中文
- 通过 `--lang zh|en` 显式指定
- 代理调用时：根据用户提问语言自动传递 `--lang` 参数（中文提问 → `--lang zh`，英文提问 → `--lang en`）

## 自动流程

1. 根据 `git config user.name` 确定成员标识
2. 从 `scripts/members.json` 查找所属分组（默认 `group`）
3. 生成 Claude Code insights 报告（中文模式下自动翻译 Claude 内容）
4. 采集 Codex/OpenCode/Cursor/Trae/OpenClaw/Hermes 数据（可选，失败不阻断）
5. 采集本机环境信息（JDK、网络 IP）并注入报告
6. 合并为 `reports/{period}/{group}/{name}-{period}-report.html`
7. 若设置了 `AGENTS_REPORT_URL` 环境变量，通过 HTTP PUT 上传到 dashboard；否则 git pull --rebase && git add/commit/push
