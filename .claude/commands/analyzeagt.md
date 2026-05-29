---
description: 生成团队分析报告（支持周/月/季/年）
allowed-tools: Bash, Read, Glob
---

读取 `reports/` 中的历史报告，生成团队汇总报告。

## 执行步骤（不要询问确认，直接执行）

1. **运行分析脚本**：
   ```bash
   python3 scripts/analyze.py
   ```
   如需指定周期类型：
   ```bash
   python3 scripts/analyze.py --period monthly    # 月报
   python3 scripts/analyze.py --period quarterly  # 季报
   python3 scripts/analyze.py --period annual     # 年报
   ```

   **语言选择**：根据用户提问语言自动传递 `--lang` 参数（中文提问 → `--lang zh`，英文提问 → `--lang en`）。

2. **执行逻辑**：
   - 若设置了 `AGENTS_REPORT_URL`：通过 `POST /api/analyze` 委托 Dashboard 在服务端执行分析
   - 未设置时：本地遍历 `reports/`，聚合生成并存储团队报告
   - 若指定周期无直接匹配数据，自动回退聚合下级周期（年报→季报→月报→周报）

3. **输出结果**：成功时打印 "团队报告已通过 Dashboard 生成" 或 "报告已生成: {路径}"
