---
description: 采集 Claude Code + Codex + OpenCode + Cursor + Trae + OpenClaw + Hermes 使用数据并按周期归档
allowed-tools: Bash, Read, Glob
---

采集当前用户的 Claude Code、Codex CLI、OpenCode、Cursor、Trae、OpenClaw、Hermes 使用数据，合并为单一报告并按周期（周/月/季/年）归档。

## 执行步骤（不要询问确认，直接执行）

1. **运行采集脚本**：
   ```bash
   ./getagt
   ```
   如需指定周期类型：
   ```bash
   ./getagt --period monthly    # 月报
   ./getagt --period quarterly  # 季报
   ./getagt --period annual     # 年报
   ```

   **语言选择**：根据用户提问语言自动传递 `--lang` 参数（中文提问 → `--lang zh`，英文提问 → `--lang en`）。

2. **确认输出**：脚本自动完成：
   - 生成 Claude Code insights 报告（中文模式下自动翻译 Claude 内容）
   - 采集 Codex/OpenCode/Cursor/Trae/OpenClaw/Hermes 数据（可选，失败不阻断）
   - 合并报告并按 `reports/{period}/{group}/{name}-{period}-report.html` 归档
   - 若设置了 `AGENTS_REPORT_URL` 则 HTTP PUT 上传到 dashboard，否则 git pull --rebase && git add/commit/push

3. **输出结果**：脚本最后一行会打印 "已完成采集：{报告路径}" 或 "已上传到 {URL}"
