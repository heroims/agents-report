# Agents Team Usage Collector

团队成员执行一条命令，自动采集 Claude Code + Codex + OpenCode + Cursor + Trae 使用数据，支持按周/月/季/年归档。

## 使用方法

### 前置条件
- 已安装并可使用 Claude Code / Codex / OpenCode / Cursor / Trae 中至少一个
- Python 3.12+（脚本仅依赖标准库，无需额外安装）
- 当前目录为仓库根目录

### 环境变量（可选）

项目根目录创建 `.envrc` 文件，`getagt`、`analyzeagt`、`dashboard-uv` 启动时会自动加载：

```bash
export AGENTS_REPORT_URL=http://localhost:8080  # 设为 Dashboard 地址后，报告直接上传而非本地 git
export DASHBOARD_PORT=8080                      # Dashboard 监听端口
```

### 采集个人数据

在 Claude Code / Codex 中执行：

```text
/getagt
```

命令行执行：

```bash
./getagt                        # macOS/Linux
python scripts/getagt.py        # Windows
```

说明：`/getagt` 是自定义命令，只在支持 slash command 的工具内可用；普通 Terminal 需要使用 `./getagt` 或 `python3 scripts/getagt.py`。

#### 指定报告周期

默认生成**周报**。可通过 `--period` / `-p` 指定周期类型：

```bash
./getagt --period monthly    # 月报 (2026-05)
./getagt --period quarterly  # 季报 (2026-Q2)
./getagt --period annual     # 年报 (2026)
./getagt --period weekly     # 周报 (默认)
```

### 生成团队报告

在 Claude Code / Codex 中执行：

```text
/analyzeagt
```

命令行执行：

```bash
python3 scripts/analyze.py                        # 周报 (默认)
python3 scripts/analyze.py --period monthly       # 月报
python3 scripts/analyze.py --period quarterly     # 季报
python3 scripts/analyze.py --period annual        # 年报
```

- 若设置了 `AGENTS_REPORT_URL`：通过 `POST /api/analyze` 委托 Dashboard 在服务端执行分析
- 未设置时：本地生成团队报告

### 启动 Dashboard

Dashboard 依赖 `flask` + `requests`，推荐使用 [uv](https://docs.astral.sh/uv/) 管理依赖：

```bash
uv sync              # 安装依赖（仅首次）
./dashboard-uv       # 启动 dashboard，读取本地 reports/
```

Docker 方式（无需安装 Python/uv）：

```bash
docker compose up -d
```

Dashboard 默认监听 `http://localhost:8080`，可通过 `DASHBOARD_PORT` 环境变量修改。不配 `GITLAB_TOKEN` 时自动读取本地 `reports/` 目录。

### 完整流程（自动执行）
`/getagt`：
1. 生成最新 insights 报告
2. 采集 Codex/OpenCode/Cursor/Trae 数据（可选，失败不阻断）
3. 按周期归档到 `reports/{period}/{group}/`

`/analyzeagt`：
1. 读取 `reports/` 历史报告
2. 若指定周期无直接匹配数据，自动回退聚合下级周期（年报→季报→月报→周报）
3. 若设置了 `AGENTS_REPORT_URL`，委托 Dashboard 服务端分析；否则本地生成
4. 输出到 `reports/{period}/team-report.html`

### 预期输出
- 个人报告：`reports/{period}/{group}/{name}-{period}-report.html`
  - 周报：`reports/2026-W22/group/heroims-2026-W22-report.html`
  - 月报：`reports/2026-05/group/heroims-2026-05-report.html`
- 团队报告：`reports/{period}/team-report.html`
  - 周报：`reports/2026-W22/team-report.html`
  - 月报：`reports/2026-05/team-report.html`

### 分组说明
- 分组由 `scripts/members.json` 定义，key 为分组名，value 为成员列表
- 不在 `members.json` 中的成员默认归入 `group` 分组

## 目录与命名规范

- 所有数据统一存放在 `reports/`
  - 第一层：`{period}/`（时间段，如 `2026-W22`）
  - 第二层：`{group}/`（团队分组） | 团队报告 `team-report.html`
  - 第三层：个人报告 `{name}-{period}-report.html`
- 团队报告命名：`team-report.html`
- 成员报告命名：`{name}-{period}-report.html`
- 成员映射文件：`scripts/members.json`
  - key: 文件名中的 `{name}` 部分
  - value: 团队展示名

## 常见问题

### insights 报告生成失败
- 现象：执行 `/getagt` 后提示 Claude 报告生成失败
- 原因：`scripts/generate_insights_from_stats.py` 执行异常，常见为 `~/.claude/usage-data/session-meta/` 或 `~/.claude/projects/**/*.jsonl` 缺失/损坏
- 处理：
  1. 检查 `~/.claude/usage-data/session-meta/` 与 `~/.claude/projects/` 是否存在且本周有数据
  2. 单独跑脚本看报错：`python3 scripts/generate_insights_from_stats.py 2026-W19 --output=/tmp/test.html`
  3. **不要** 改用 Claude Code 内置 `/insights` 命令来兜底——`/insights` 输出的是滚动 ~30 天数据（不按周），归档后会出现"45 sessions / 24 active days"这类月度数字假冒成周报的情况
- 跨期补跑：`./getagt` 默认取当前周期；要生成历史报告，直接调底层脚本并显式指定周期号
  ```bash
  # 周报
  python3 scripts/generate_insights_from_stats.py 2026-W19 --output=$HOME/.claude/usage-data/report.html
  python3 scripts/collect_codex.py 2026-W19 --output=/tmp/codex.html
  python3 scripts/merge_reports.py $HOME/.claude/usage-data/report.html /tmp/codex.html "" "" "" reports/2026-W19/group/{name}-2026-W19-report.html 2026-W19

  # 月报
  python3 scripts/generate_insights_from_stats.py 2026-05 --output=$HOME/.claude/usage-data/report.html
  python3 scripts/collect_codex.py 2026-05 --output=/tmp/codex.html
  python3 scripts/merge_reports.py $HOME/.claude/usage-data/report.html /tmp/codex.html "" "" "" reports/2026-05/group/{name}-2026-05-report.html 2026-05
  ```

### OpenCode 报告采集失败
- 现象：`/getagt` 过程中 OpenCode 部分没有合并
- 原因：`opencode db path` 不可用、OpenCode 数据库不可读、或当周没有会话
- 处理：OpenCode 为可选数据源，主流程会继续；如需排查可单独运行
  `python3 scripts/collect_opencode.py 2026-W15 --output=/tmp/opencode-test.html`

### 报告命名或映射异常
- 现象：报告生成但无法正确映射成员显示名
- 原因：文件名或 `members.json` key 不匹配
- 处理：确认文件名为 `{name}-{period}-report.html` 且 `members.json` key 与 `{name}` 一致

### 生成团队报告
- 使用：执行 `/analyzeagt` 或 `python3 scripts/analyze.py --period <type>`
- 命令行执行：Windows 用 `python scripts/analyze.py`，macOS 用 `python3 scripts/analyze.py`
- 输出：`reports/{period}/team-report.html`
- Dashboard 端：也可通过 `POST /api/analyze?period_type=weekly` 触发服务端分析
