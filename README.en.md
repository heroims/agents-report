# Agents Team Usage Collector

> **Cross-agent usage analytics**: collect, merge, and report data from Claude Code, Codex CLI, OpenCode, Cursor, Trae, OpenClaw, and Hermes — seven AI coding agents in one unified dashboard. Zero Python dependencies for core scripts.

<p align="center">
  <a href="README.md">中文</a> ·
  <b>English</b>
</p>

Team members run a single agent slash command to collect usage data from 7 major AI Coding Agents, merge everything into a unified report, and archive by week, month, quarter, or year. When your team uses multiple AI coding tools simultaneously, this is currently the only publicly discoverable open-source solution for cross-agent usage analytics. Core collection scripts are written in pure Python standard library — no third-party dependencies required.

An optional Flask Dashboard is available for team report aggregation and AI-powered summaries (Dashboard requires additional dependencies).

---

## Requirements

- Python 3.12+ (**core collection uses only the standard library — no pip/uv package installation needed**)
- At least one of these installed: Claude Code / Codex CLI / OpenCode / Cursor / Trae / OpenClaw / Hermes

## Optional: Environment Variables

Create an `.envrc` file in the project root (gitignored). It will be automatically sourced when `./getagt`, `./analyzeagt`, or `./dashboard-uv` starts:

```bash
# Dashboard URL — when set, getagt uploads via HTTP PUT and analyzeagt delegates analysis to the server.
# When unset: getagt uses git pull --rebase && git add/commit/push; analyzeagt generates reports locally.
export AGENTS_REPORT_URL=http://localhost:8880

# Dashboard listen port (Dashboard only)
export DASHBOARD_PORT=8880

# GitLab integration (optional — when unset, Dashboard reads local reports/ directory)
# export GITLAB_URL=https://gitlab.example.com
# export GITLAB_TOKEN=glpat-xxxx
# export GITLAB_PROJECT=my-group/my-project

# AI summary (used by Dashboard /api/ai/summary and /api/ai/chat only)
export AI_PROVIDER=openai_chat       # or anthropic_messages
export AI_API_KEY=sk-xxxx
export AI_BASE_URL=https://api.openai.com
export AI_MODEL=gpt-4o
```

---

## Collecting Personal Reports (`getagt`)

Supports skill mode in major agents — just open the project directory in your agent and run the command.

<img src="/screenshot/trae.png" width="30%"><img src="/screenshot/claude.png" width="50%">

### Run inside Claude Code / Codex

```text
/getagt
```

Driven by [`.claude/commands/getagt.md`](.claude/commands/getagt.md) and [`.agents/skills/source-command-getagt/SKILL.md`](.agents/skills/source-command-getagt/SKILL.md). The agent will automatically run `./getagt` and display the results.

### CLI

```bash
./getagt                        # Weekly (default)
./getagt --period monthly       # Monthly
./getagt --period quarterly     # Quarterly
./getagt --period annual        # Annual
```

`./getagt` is a shell wrapper, equivalent to `python3 scripts/getagt.py [args]`. On Windows, run `python scripts\getagt.py` directly.

### Automated Flow

1. Identifies the member via `git config user.name` (slugified as the filename prefix)
2. Looks up the group assignment from [`scripts/members.json`](scripts/members.json) (keys are group names, values are member lists; unmatched members default to `group`)
3. Calls `scripts/generate_insights_from_stats.py` to generate the Claude Code insights report
4. Sequentially collects Codex / OpenCode / Cursor / Trae / OpenClaw / Hermes data (optional sources — any single failure does not block the pipeline)
5. Collects local environment info (JDK version, network interface IPs)
6. Merges everything into a single HTML via `scripts/merge_reports.py`
7. Outputs to `reports/{period}/{group}/{name}-{period}-report.html`
8. If `AGENTS_REPORT_URL` is set: uploads to Dashboard via `PUT /api/report/upload`; otherwise `git pull --rebase && git add/commit/push`

---

## Generating Team Reports (`analyzeagt`)

### Run inside Claude Code / Codex

```text
/analyzeagt
```

### CLI

```bash
./analyzeagt                        # Weekly (default)
./analyzeagt --period monthly       # Monthly
./analyzeagt --period quarterly     # Quarterly
./analyzeagt --period annual        # Annual
```

Equivalent to `python3 scripts/analyze.py [args]`.

### Automated Flow

1. Traverses `reports/` to collect all member reports (parsing period identifiers from filenames via `scripts/period_utils.py`)
2. Filters by period type, determines the current period and the previous comparison period
3. **Auto roll-up aggregation**: if the requested period has no direct data, automatically falls back to aggregate from lower periods:
   - Annual → Quarterly → Monthly → Weekly
   - Quarterly → Monthly → Weekly
   - Monthly → Weekly
4. Aggregation rules: numeric fields are summed, `days` takes the max, `tools`/`languages` are combined counts
5. If `AGENTS_REPORT_URL` is set: delegates to Dashboard server via `POST /api/analyze`; otherwise generates locally
6. Outputs to `reports/{period}/team-report.html`

---

## Scheduled Reporting (`setup-schedule` / `teardown-schedule`)

Every team member can enable or disable automated scheduled reporting with a single command. The tool auto-detects the runtime environment and picks the best scheduling mechanism:
- **Codex**: uses `codex_app__automation_update` for managed automation
- **Claude Code / others**: falls back to system schedulers (macOS launchd / Linux crontab / Windows schtasks)

### Run inside an Agent

```text
/setup-schedule      # One-click: creates weekly + monthly + quarterly + annual tasks
/teardown-schedule   # Pauses or removes all scheduled reporting tasks
```

- Codex: driven by [`.agents/skills/source-command-setup-schedule/SKILL.md`](.agents/skills/source-command-setup-schedule/SKILL.md)
- Claude Code: driven by [`.claude/commands/setup-schedule.md`](.claude/commands/setup-schedule.md), includes auto-fallback logic

### Run Scripts Directly (skip Agent)

```bash
python3 scripts/schedule_setup.py     # Install system scheduler tasks
python3 scripts/schedule_teardown.py  # Remove system scheduler tasks
```

### Schedule Rules

| Task | Frequency | Execution Time |
|------|-----------|----------------|
| Submit weekly report | Weekly | Monday 09:00 |
| Submit monthly report | Monthly | 1st, 09:00 |
| Submit quarterly report | Quarterly | Jan/Apr/Jul/Oct 1st, 09:00 |
| Submit annual report | Yearly | January 1st, 09:00 |

---

## Starting the Dashboard (Optional)

The Dashboard provides a web UI for browsing team reports, with AI-powered summaries and Q&A. Only the Dashboard requires additional dependencies.

<img src="/screenshot/dashboard.png" width="50%">

### Install Dependencies

```bash
uv sync              # Installs dependencies declared in pyproject.toml (flask, requests, openai, anthropic)
```

Dashboard dependencies: `flask`, `requests`, `openai`, `anthropic`. Core collection scripts (`getagt.py`, `analyze.py`, etc.) use only the Python standard library — skip this step if you only need collection and analysis.

### Start

```bash
./dashboard-uv       # Equivalent to uv run python dashboard/server.py
```

Docker:

```bash
docker build -t agents-report-dashboard .
docker run -p 8880:8880 agents-report-dashboard
```

Listens on `http://localhost:8880` by default (override via `DASHBOARD_PORT`). When `GITLAB_TOKEN` is not configured, the Dashboard reads from the local `reports/` directory.

### Dashboard API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard frontend |
| `/api/data` | GET | Aggregated data (supports `?group=&period=&period_type=` filters) |
| `/api/report` | GET | Proxy report HTML (supports `?path=` or `?name=&period=&group=&kind=`) |
| `/api/report/upload` | PUT | Receive personal report uploaded by `getagt` |
| `/api/analyze` | POST | Trigger server-side team analysis (`?period_type=weekly`) |
| `/api/refresh` | POST | Refresh cache |
| `/api/ai/summary` | POST | Generate AI summary (requires AI env vars) |
| `/api/ai/summary/stream` | POST | Streaming AI summary |
| `/api/ai/chat` | POST | AI Q&A |

---

## Directory Structure & Naming Conventions

```
reports/
├── {period}/                      # e.g. 2026-W22 / 2026-05 / 2026-Q2 / 2026
│   ├── team-report.html           # Team aggregate report
│   ├── {group}/                   # Group directory (e.g. group / dex)
│   │   └── {name}-{period}-report.html   # Individual report
│   └── ...
```

### Period Identifier Format

| Period Type | Format | Example |
|-------------|--------|---------|
| Weekly | `YYYY-WNN` | `2026-W22` |
| Monthly | `YYYY-MM` | `2026-05` |
| Quarterly | `YYYY-QN` | `2026-Q2` |
| Annual | `YYYY` | `2026` |

### Groups

- Groups are defined in [`scripts/members.json`](scripts/members.json): keys are group names, values are lists of member identifiers
- Members not listed in `members.json` default to the `group` group
- Member identifiers are derived from `git config user.name` by slugifying (lowercase, spaces → hyphens, special characters removed)

---

## Project Structure

```
.
├── getagt                    # Personal collection entry point (shell wrapper → scripts/getagt.py)
├── analyzeagt                # Team analysis entry point (shell wrapper → scripts/analyze.py)
├── dashboard-uv              # Dashboard entry point (shell wrapper → dashboard/server.py)
├── pyproject.toml            # uv project definition + Dashboard dependencies
├── Dockerfile                # Dashboard container build
├── scripts/
│   ├── getagt.py             # Main collection script (stdlib)
│   ├── analyze.py            # Team aggregation analysis (stdlib)
│   ├── period_utils.py       # Period utility functions (stdlib)
│   ├── generate_insights_from_stats.py  # Claude Code insights generation (stdlib)
│   ├── gen_report_from_sessions.py      # Generate Claude report from sessions files (stdlib, alternative)
│   ├── collect_codex.py      # Codex CLI data collection (stdlib)
│   ├── collect_opencode.py   # OpenCode data collection (stdlib)
│   ├── collect_cursor.py     # Cursor data collection (stdlib)
│   ├── collect_trae.py       # Trae data collection (stdlib)
│   ├── collect_openclaw.py   # OpenClaw data collection (stdlib)
│   ├── collect_hermes.py     # Hermes data collection (stdlib)
│   ├── merge_reports.py      # Multi-tool report merging (stdlib)
│   ├── schedule_setup.py      # Scheduled reporting install (launchd/crontab/schtasks)
│   ├── schedule_teardown.py   # Scheduled reporting uninstall
│   ├── members.json          # Member → group mapping
│   └── exclude_paths.json    # Path exclusion config
├── dashboard/
│   ├── server.py             # Flask server (requires flask, requests, openai, anthropic)
│   ├── dashboard.html        # Frontend page
│   ├── cache.py              # Data cache
│   ├── gitlab_client.py      # GitLab API client
│   └── requirements.txt      # pip alternative dependency declaration
├── .claude/commands/
│   ├── getagt.md
│   ├── analyzeagt.md
│   ├── setup-schedule.md     # One-click scheduled reporting
│   └── teardown-schedule.md  # One-click disable scheduled reporting
├── .agents/skills/
│   ├── source-command-getagt/
│   ├── source-command-analyzeagt/
│   ├── source-command-setup-schedule/
│   └── source-command-teardown-schedule/
└── reports/                  # Report archive directory (git tracked)
```

---

## FAQ

### Claude Code insights generation fails

- **Symptom**: `/getagt` reports Claude report generation failure
- **Cause**: `scripts/generate_insights_from_stats.py` encountered an error — commonly due to missing or corrupted `~/.claude/usage-data/session-meta/` or `~/.claude/projects/**/*.jsonl`
- **Resolution**:
  1. Check that `~/.claude/usage-data/session-meta/` and `~/.claude/projects/` exist and have data for the current period
  2. Run standalone to see the error: `python3 scripts/generate_insights_from_stats.py 2026-W19 --output=/tmp/test.html`
  3. **Do NOT** fall back to Claude Code's built-in `/insights` command — `/insights` outputs a rolling ~30-day window (not aligned to weeks), which would produce misleading monthly-looking numbers in weekly reports after archival

### Backfilling historical reports

`./getagt` defaults to the current period. To generate historical reports, call the underlying scripts directly with explicit period identifiers:

```bash
# Weekly
python3 scripts/generate_insights_from_stats.py 2026-W19 --output=$HOME/.claude/usage-data/report.html
python3 scripts/collect_codex.py 2026-W19 --output=/tmp/codex.html
python3 scripts/merge_reports.py $HOME/.claude/usage-data/report.html /tmp/codex.html "" "" "" \
    reports/2026-W19/group/{name}-2026-W19-report.html 2026-W19

# Monthly
python3 scripts/generate_insights_from_stats.py 2026-05 --output=$HOME/.claude/usage-data/report.html
python3 scripts/collect_codex.py 2026-05 --output=/tmp/codex.html
python3 scripts/merge_reports.py $HOME/.claude/usage-data/report.html /tmp/codex.html "" "" "" \
    reports/2026-05/group/{name}-2026-05-report.html 2026-05
```

### OpenClaw data collection fails

- OpenClaw data comes from `~/.openclaw/logs/commands.log`, which records session creation and reset events
- Currently collected metrics: session count, active days, agent distribution, trigger sources
- Debug standalone: `python3 scripts/collect_openclaw.py 2026-W22 --output=/tmp/test.html`
- Note: OpenClaw currently only tracks session start events and cannot obtain execution details like token consumption

### Hermes data collection fails

- Hermes data comes from `~/.hermes/state.db`, which contains `sessions` and `messages` tables
- Collected metrics: session count, message count, tokens (input/output/cache/reasoning), active days, model distribution, tool calls
- Debug standalone: `python3 scripts/collect_hermes.py 2026-W22 --output=/tmp/test.html`

### OpenCode / Cursor / Trae data collection fails

- OpenCode, Cursor, and Trae are all optional data sources — the main pipeline continues if any of them fails
- Debug standalone: `python3 scripts/collect_opencode.py 2026-W15 --output=/tmp/test.html`
- Codex depends on the `~/.codex/state_5.sqlite` database; OpenCode depends on the `opencode db path` command being available

### Member name mapping is incorrect

- Verify the filename format is `{name}-{period}-report.html`
- The keys in `members.json` must match the slugified result of `git config user.name`
- Run `git config user.name` to confirm the current identifier
