---
name: analyzeagt
description: 读取 reports/ 中的历史报告，生成团队汇总报告，支持周/月/季/年并自动回退聚合下级周期数据
---

# analyzeagt

读取 `reports/` 中的历史报告，生成团队汇总报告。

## 用法

直接调用脚本：

```bash
python3 scripts/analyze.py                        # 周报（默认）
python3 scripts/analyze.py --period monthly       # 月报
python3 scripts/analyze.py --period quarterly     # 季报
python3 scripts/analyze.py --period annual        # 年报
```

## 自动流程

1. 遍历 `reports/` 收集所有成员报告
2. 按周期类型筛选并确定当前周期和对比周期
3. 若指定周期无直接匹配数据，自动回退聚合下级周期：
   - 年报 → 季报 → 月报 → 周报
   - 季报 → 月报 → 周报
   - 月报 → 周报
4. 聚合时数值字段累加、days 取最大值、tools/languages 合并计数
5. 若设置了 `AGENTS_REPORT_URL`，委托 Dashboard 服务端分析；否则本地生成
6. 输出到 `reports/{period}/team-report.html`

## Dashboard API

也可通过 Dashboard 端点触发服务端分析：

```bash
curl -X POST 'http://localhost:8880/api/analyze?period_type=weekly'
```
