#!/usr/bin/env python3
"""采集 Cursor 使用数据，生成 HTML 报告。

数据源:
  1. ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
     - cursorDiskKV: composer 会话与 bubble 消息
     - ItemTable: aiCodeTracking.dailyStats 每日统计
  2. ~/.cursor/ai-tracking/ai-code-tracking.db
     - ai_code_hashes: AI 代码块生成跟踪
     - scored_commits: commit 级 AI 归因

跨平台路径说明:
  - macOS:  ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
  - Linux:  ~/.config/Cursor/User/globalStorage/state.vscdb
  - Windows: %APPDATA%/Cursor/User/globalStorage/state.vscdb

  ai-tracking 在所有平台均为 ~/.cursor/ai-tracking/ai-code-tracking.db
"""

import argparse
import datetime
import html
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import sys as _sys
_scripts_dir = str((__import__('pathlib').Path(__file__).resolve().parent))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
from period_utils import period_start_end


def _cursor_state_db():
    """返回 Cursor globalStorage state.vscdb 路径（跨平台）。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Cursor"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "")) / "Cursor"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Cursor"
    return base / "User" / "globalStorage" / "state.vscdb"


def _cursor_ai_tracking_db():
    """返回 Cursor ai-tracking sqlite 路径。"""
    return Path.home() / ".cursor" / "ai-tracking" / "ai-code-tracking.db"


def parse_week(period_str):
    m = re.match(r"^(\d{4})-W(\d{1,2})$", str(period_str or "").strip())
    if not m:
        raise ValueError(f"无效周标识: {period_str}，应为 YYYY-WNN")
    year = int(m.group(1))
    week = int(m.group(2))
    period_start = datetime.date.fromisocalendar(year, week, 1)
    period_end = datetime.date.fromisocalendar(year, week, 7)
    return f"{year}-W{week:02d}", period_start, period_end


def epoch_ms(dt_obj):
    return int(dt_obj.timestamp() * 1000)


def format_tokens(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def safe_text(value):
    return html.escape(str(value or ""))


def safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _path_label(path_value):
    normalized = str(path_value or "").rstrip("/\\")
    if not normalized:
        return "(unknown)"
    return Path(normalized).name or "(unknown)"


def _parse_iso_datetime(text):
    """解析 ISO 8601 时间字符串 (如 2026-03-10T13:40:51.341Z) 返回 epoch ms。"""
    if not text:
        return 0
    try:
        text = str(text).replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(text)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def _bubble_count(composer_json):
    """统计一个 composer 会话中的消息数。"""
    headers = composer_json.get("fullConversationHeadersOnly") or []
    return len(headers)


def _collect_composer_sessions(state_db_path, period_start, period_end):
    """从 cursorDiskKV 采集指定周的 composer 会话数据。"""
    start_ms = epoch_ms(datetime.datetime(period_start.year, period_start.month, period_start.day, 0, 0, 0))
    end_ms = epoch_ms(datetime.datetime(period_end.year, period_end.month, period_end.day, 23, 59, 59, 999000))

    if not state_db_path.exists():
        return [], {}

    conn = sqlite3.connect(str(state_db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
        ).fetchall()
    finally:
        conn.close()

    sessions = []
    sessions_by_day = defaultdict(set)
    areas = defaultdict(lambda: {"sessions": 0, "messages": 0})

    for row in rows:
        try:
            data = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue

        created_ms = safe_int(data.get("createdAt"))
        if created_ms < start_ms or created_ms > end_ms:
            continue

        composer_id = data.get("composerId", "")
        mode = data.get("unifiedMode") or data.get("forceMode") or ""
        is_agentic = bool(data.get("isAgentic"))
        messages = _bubble_count(data)
        lines_added = safe_int(data.get("totalLinesAdded"))
        lines_removed = safe_int(data.get("totalLinesRemoved"))
        files_changed = safe_int(data.get("filesChangedCount"))
        subtitle = data.get("subtitle") or ""
        title = data.get("text") or subtitle or ""

        day_key = datetime.datetime.fromtimestamp(created_ms / 1000).date().isoformat()

        # 从 subtitle 或 file paths 推断项目
        project = "(unknown)"
        if subtitle:
            # subtitle 格式如 "Edited file1.md, file2.json, ..."
            paths = [p.strip() for p in subtitle.split(",")]
            if paths:
                project = _path_label(paths[0])

        sessions.append({
            "composer_id": composer_id,
            "day": day_key,
            "created_at": created_ms,
            "mode": mode,
            "is_agentic": is_agentic,
            "messages": messages,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "files_changed": files_changed,
            "title": title[:90] if title else "未命名会话",
            "project": project,
        })
        sessions_by_day[day_key].add(composer_id)
        areas[project]["sessions"] += 1
        areas[project]["messages"] += messages

    return sessions, areas, sessions_by_day


def _collect_daily_stats(state_db_path, period_start, period_end):
    """从 ItemTable 采集每日统计。"""
    if not state_db_path.exists():
        return []

    conn = sqlite3.connect(str(state_db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT key, value FROM ItemTable WHERE key LIKE 'aiCodeTracking.dailyStats%'"
        ).fetchall()
    finally:
        conn.close()

    stats = []
    for row in rows:
        try:
            data = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            continue

        date_str = data.get("date", "")
        if not date_str:
            continue
        try:
            day = datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            continue

        if period_start <= day <= period_end:
            stats.append({
                "day": date_str,
                "tab_suggested": safe_int(data.get("tabSuggestedLines")),
                "tab_accepted": safe_int(data.get("tabAcceptedLines")),
                "composer_suggested": safe_int(data.get("composerSuggestedLines")),
                "composer_accepted": safe_int(data.get("composerAcceptedLines")),
            })

    stats.sort(key=lambda x: x["day"])
    return stats


def _collect_ai_code_hashes(ai_db_path, period_start, period_end):
    """从 ai_code_hashes 表采集 AI 代码生成数据。"""
    start_ms = epoch_ms(datetime.datetime(period_start.year, period_start.month, period_start.day, 0, 0, 0))
    end_ms = epoch_ms(datetime.datetime(period_end.year, period_end.month, period_end.day, 23, 59, 59, 999000))

    if not ai_db_path.exists():
        return [], {}

    conn = sqlite3.connect(str(ai_db_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "ai_code_hashes" not in tables:
            return [], {}

        rows = conn.execute(
            "SELECT * FROM ai_code_hashes WHERE timestamp >= ? AND timestamp <= ?",
            (start_ms, end_ms),
        ).fetchall()
    finally:
        conn.close()

    hashes = []
    models = Counter()
    files = Counter()

    for row in rows:
        ts = safe_int(row["timestamp"])
        h = {
            "hash": row["hash"],
            "source": row["source"],
            "file_extension": row["fileExtension"],
            "file_name": row["fileName"],
            "conversation_id": row["conversationId"],
            "timestamp": ts,
            "model": row["model"],
        }
        hashes.append(h)
        models[row["model"] or "unknown"] += 1
        name = _path_label(row["fileName"])
        files[name] += 1

    return hashes, dict(models.most_common(10)), dict(files.most_common(15))


def _collect_scored_commits(ai_db_path, period_start, period_end):
    """从 scored_commits 采集 commit 级别 AI 归因。"""
    start_str = period_start.isoformat()
    end_str = period_end.isoformat()

    if not ai_db_path.exists():
        return []

    conn = sqlite3.connect(str(ai_db_path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "scored_commits" not in tables:
            return []

        rows = conn.execute(
            "SELECT * FROM scored_commits WHERE commitDate >= ? AND commitDate <= ?",
            (start_str, end_str),
        ).fetchall()
    finally:
        conn.close()

    commits = []
    for row in rows:
        commits.append({
            "commit_hash": row["commitHash"],
            "branch": row["branchName"],
            "date": row["commitDate"],
            "lines_added": safe_int(row["linesAdded"]),
            "lines_deleted": safe_int(row["linesDeleted"]),
            "composer_lines_added": safe_int(row["composerLinesAdded"]),
            "composer_lines_deleted": safe_int(row["composerLinesDeleted"]),
            "human_lines_added": safe_int(row["humanLinesAdded"]),
            "human_lines_deleted": safe_int(row["humanLinesDeleted"]),
            "ai_percentage_v2": row.get("v2AiPercentage") or "",
        })
    return commits


def _summarize_insights(sessions, areas, daily_stats, ai_hashes, models, top_files):
    """生成与 Codex/OpenCode 报告一致的洞察结构。"""
    total_sessions = len(sessions)
    total_messages = sum(s["messages"] for s in sessions)
    total_lines_added = sum(s["lines_added"] for s in sessions)
    total_lines_removed = sum(s["lines_removed"] for s in sessions)
    total_files = sum(s["files_changed"] for s in sessions)

    agent_count = sum(1 for s in sessions if s["mode"] == "agent")
    chat_count = sum(1 for s in sessions if s["mode"] == "chat")
    plan_count = sum(1 for s in sessions if s["mode"] == "plan")

    active_days = len(set(s["day"] for s in sessions))
    avg_messages = round(total_messages / total_sessions, 1) if total_sessions else 0

    # 项目分布
    ranked_areas = sorted(areas.items(), key=lambda x: x[1]["sessions"], reverse=True)
    area_items = [
        {"cwd": name, "sessions": data["sessions"], "tokens": 0}
        for name, data in ranked_areas[:10]
    ]

    # 每日分布
    day_counter = Counter(s["day"] for s in sessions)
    daily = [
        {"day": day, "sessions": cnt, "tokens": 0}
        for day, cnt in sorted(day_counter.items())
    ]

    # 模式分布
    model_list = [{"model": m, "cnt": c, "tokens": 0} for m, c in (models or {}).items()]

    # 工作类型
    work_on = []
    for name, data in ranked_areas[:5]:
        work_on.append({
            "name": name,
            "sessions": data["sessions"],
            "tokens": 0,
            "desc": (
                f"约 {data['sessions']} 次会话、{data['messages']} 条消息。"
                f" 主要在该项目中进行代码编辑和 AI 辅助开发。"
            ),
        })

    # At a glance
    dominant_mode = "Agent 模式为主" if agent_count >= chat_count else "Chat 模式为主"
    working = (
        f"本周 Cursor 使用以 {dominant_mode}，"
        f"共 {total_sessions} 次会话覆盖 {active_days} 个活跃天。"
    ) if total_sessions else "本周暂无 Cursor 使用数据。"

    hindering = "暂无显著摩擦数据。" if total_sessions else ""
    if total_sessions and avg_messages < 3:
        hindering = "平均每个会话消息数偏低，可能存在较多一次性查询或中断。"

    quick_win = "尝试将高频任务固定为 Agent 工作流，减少手动重复操作。" if total_sessions else ""
    ambitious = "探索 Cursor Rules 和 .cursorrules 让 Agent 行为更可预测。" if total_sessions else ""

    # Wins
    wins = []
    if total_sessions >= 5:
        wins.append({
            "title": "Cursor 已成为日常开发主力",
            "detail": f"本周 {total_sessions} 次会话，覆盖 {active_days} 天，Cursor 深度融入日常工作流。",
        })
    if agent_count > 0:
        wins.append({
            "title": "Agent 模式使用积极",
            "detail": f"{agent_count} 次 Agent 会话说明你已开始将 AI 作为自主代理使用，而不只是问答。",
        })
    if total_lines_added + total_lines_removed > 0:
        wins.append({
            "title": "AI 辅助代码落地",
            "detail": f"涉及 +{total_lines_added}/-{total_lines_removed} 行代码变更，AI 辅助有实质产出。",
        })

    # Friction
    friction = []
    if chat_count > agent_count and total_sessions >= 3:
        friction.append({
            "title": "Chat 模式比例偏高",
            "detail": f"Chat 会话 {chat_count} 次高于 Agent {agent_count} 次，部分工作可能未充分发挥 Agent 自主能力。",
        })
    if avg_messages < 5 and total_sessions >= 3:
        friction.append({
            "title": "会话深度偏浅",
            "detail": f"平均每会话仅 {avg_messages} 条消息，可能存在较多未完成的探索性会话。",
        })

    # Features
    features = [
        {
            "title": "把稳定任务前置成 Agent 工作流",
            "detail": "对反复做的任务，直接在 Agent 模式下描述完整上下文和预期输出，减少来回确认。",
        },
        {
            "title": "用 .cursorrules 固化项目规范",
            "detail": "把团队代码规范、测试路径、文件组织约定写进 .cursorrules，让 Agent 行为更一致。",
        },
        {
            "title": "善用 Tab 补全减少重复输入",
            "detail": "Cursor Tab 是高频提效点，保持上下文干净可以让补全更精准。",
        },
    ]

    # Patterns
    patterns = [
        {
            "title": "Cursor 已是你的操作型 AI 工具",
            "summary": f"从 {agent_count} 次 Agent 和 {chat_count} 次 Chat 的分布看，你在用 Cursor 做实际编码和修改，而不是纯聊天。",
        },
    ]

    usage_cards = [
        {
            "title": "会话密度",
            "value": f"{avg_messages} msgs/session",
            "desc": f"平均每会话 {avg_messages} 条消息，{'深度交互良好' if avg_messages >= 5 else '存在优化空间'}。",
        },
        {
            "title": "Agent 采纳率",
            "value": f"{agent_count}/{total_sessions}",
            "desc": f"{total_sessions} 次会话中 {agent_count} 次使用 Agent 模式，{'Agent 已成为默认选择' if agent_count >= chat_count else 'Chat 仍占主导'}。",
        },
        {
            "title": "落地强度",
            "value": f"+{total_lines_added}/-{total_lines_removed}",
            "desc": f"涉及 {total_files} 个文件、+{total_lines_added}/-{total_lines_removed} 行变更，AI 编辑有实质落地。",
        },
        {
            "title": "覆盖广度",
            "value": f"{active_days} 天",
            "desc": f"本周有 {active_days} 个活跃天，{'高频使用' if active_days >= 5 else '使用频率适中'}。",
        },
    ]

    horizon = [
        {
            "title": "从 Chat 到 Agent 的全面迁移",
            "detail": "当前仍有 Chat 模式会话，尝试把更多任务用 Agent 模式完成，享受自主执行的效率提升。",
        },
    ]

    return {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_lines_added": total_lines_added,
        "total_lines_removed": total_lines_removed,
        "total_files": total_files,
        "active_days": active_days,
        "agent_count": agent_count,
        "chat_count": chat_count,
        "plan_count": plan_count,
        "avg_messages": avg_messages,
        "areas": area_items,
        "daily": daily,
        "models": model_list,
        "ai_hashes_count": len(ai_hashes),
        "top_files": top_files,
        "work_on": work_on,
        "at_a_glance": {
            "working": working,
            "hindering": hindering,
            "quick_win": quick_win,
            "ambitious": ambitious,
        },
        "wins": wins,
        "friction": friction,
        "features": features,
        "patterns": patterns,
        "usage_cards": usage_cards,
        "horizon": horizon,
        "narrative_parts": [
            f"本周 Cursor 使用以 {dominant_mode}，覆盖 {active_days} 天、{total_sessions} 次会话。",
            f"共 {total_messages} 条消息、涉及 {total_files} 个文件、+{total_lines_added}/-{total_lines_removed} 行代码变更。",
            f"AI 代码哈希记录了 {len(ai_hashes)} 个 AI 生成代码块。",
        ],
        "key_insight": (
            f"Cursor {total_sessions} 次会话中 Agent 占 {agent_count} 次（{round(agent_count/max(total_sessions,1)*100)}%），"
            f"平均每会话 {avg_messages} 条消息，{'Agent 使用充分' if agent_count >= chat_count else '仍有 Chat→Agent 迁移空间'}。"
        ),
        "top_model": model_list[0] if model_list else {"name": "N/A"},
        "top_tools": [],
        "top_commands": [],
        "top_topics": [],
    }


def _render_bar_rows(items, label_key, value_key, max_val, suffix="", limit=8):
    if not items:
        return '<p class="empty">暂无数据。</p>'
    rows = []
    for item in items[:limit]:
        label = safe_text(str(item.get(label_key, "")))
        value = safe_int(item.get(value_key))
        pct = int(value / max(max_val, 1) * 100)
        rows.append(
            f"""<div class="bar-row">
          <div class="bar-label" title="{label}">{label}</div>
          <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
          <div class="bar-value">{safe_text(str(value) + suffix)}</div>
        </div>"""
        )
    return "\n".join(rows)


def _render_pills(items, name_key="name", count_key="count", limit=8):
    if not items:
        return '<span class="empty">暂无</span>'
    pills = []
    for item in items[:limit]:
        name = safe_text(str(item.get(name_key, "")))
        count = safe_text(str(item.get(count_key, "")))
        pills.append(f'<span class="pill">{name} <b>{count}</b></span>')
    return "".join(pills)


def generate_html(report, out_path):
    s = report["summary"]
    insights = report["insights"]

    html_parts = [f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Cursor Insights</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%); color: #1f2937; line-height: 1.65; padding: 40px 20px 72px; }}
    .container {{ max-width: 980px; margin: 0 auto; }}
    h1 {{ font-size: 36px; font-weight: 800; color: #0f172a; margin-bottom: 8px; }}
    h2 {{ font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 44px; margin-bottom: 14px; }}
    h3 {{ font-size: 16px; font-weight: 700; color: #0f172a; margin-top: 22px; margin-bottom: 10px; }}
    .subtitle {{ color: #64748b; font-size: 15px; margin-bottom: 28px; }}
    .hero {{ background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 45%, #e0e7ff 100%); border: 1px solid #86efac; border-radius: 20px; padding: 24px 24px 18px; box-shadow: 0 12px 40px rgba(15, 23, 42, 0.06); }}
    .hero-grid {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px; }}
    .glance-title {{ font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #166534; font-weight: 700; margin-bottom: 14px; }}
    .glance-item {{ font-size: 14px; color: #14532d; margin-bottom: 10px; }}
    .glance-item strong {{ color: #166534; }}
    .hero-side {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .hero-metric {{ background: rgba(255,255,255,0.72); border: 1px solid rgba(34, 197, 94, 0.25); border-radius: 14px; padding: 14px; }}
    .hero-metric-value {{ font-size: 24px; font-weight: 800; color: #0f172a; }}
    .hero-metric-label {{ font-size: 11px; color: #6b7280; text-transform: uppercase; }}
    .stats-row {{ display: flex; gap: 22px; margin: 30px 0 18px; padding: 18px 0; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; flex-wrap: wrap; }}
    .stat {{ text-align: center; min-width: 90px; }}
    .stat-value {{ font-size: 24px; font-weight: 800; color: #0f172a; }}
    .stat-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
    .section-intro {{ font-size: 14px; color: #64748b; margin-bottom: 14px; }}
    .cards {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 14px; padding: 16px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04); }}
    .card-title {{ font-size: 15px; font-weight: 700; color: #0f172a; margin-bottom: 8px; }}
    .card-detail {{ font-size: 13px; color: #475569; }}
    .pill {{ display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; background: #f0fdf4; color: #166534; padding: 5px 10px; font-size: 12px; }}
    .pill b {{ color: #14532d; }}
    .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 18px; }}
    .chart-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 14px; padding: 16px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04); }}
    .chart-title {{ font-size: 12px; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: 12px; }}
    .bar-row {{ display: flex; align-items: center; margin-bottom: 8px; }}
    .bar-label {{ width: 120px; font-size: 12px; color: #334155; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ flex: 1; height: 8px; background: #f0fdf4; border-radius: 999px; margin: 0 10px; overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: 999px; background: linear-gradient(90deg, #22c55e 0%, #6366f1 100%); }}
    .bar-value {{ width: 74px; font-size: 11px; font-weight: 600; color: #64748b; text-align: right; }}
    .empty {{ color: #94a3b8; font-size: 13px; }}
    .raw-data {{ display: none; }}
    @media (max-width: 760px) {{
      .hero-grid, .cards, .charts-row {{ grid-template-columns: 1fr; }}
      .hero-side {{ grid-template-columns: 1fr 1fr; }}
      .stats-row {{ justify-content: center; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Cursor Insights</h1>
    <p class="subtitle">{safe_text(report.get('period_start', ''))} ~ {safe_text(report.get('period_end', ''))} &nbsp;|&nbsp; {safe_text(report.get('week', ''))}</p>

    <div class="hero">
      <div class="hero-grid">
        <div>
          <div class="glance-title">At a Glance</div>
          <div class="glance-item"><strong>What's working:</strong> {safe_text(insights['at_a_glance']['working'])}</div>
          <div class="glance-item"><strong>What's hindering you:</strong> {safe_text(insights['at_a_glance']['hindering'])}</div>
          <div class="glance-item"><strong>Quick wins to try:</strong> {safe_text(insights['at_a_glance']['quick_win'])}</div>
          <div class="glance-item"><strong>On the horizon:</strong> {safe_text(insights['at_a_glance']['ambitious'])}</div>
        </div>
        <div class="hero-side">
          <div class="hero-metric"><div class="hero-metric-value">{s['total_sessions']}</div><div class="hero-metric-label">Sessions</div></div>
          <div class="hero-metric"><div class="hero-metric-value">{s['total_messages']}</div><div class="hero-metric-label">Messages</div></div>
          <div class="hero-metric"><div class="hero-metric-value">{s['total_files']}</div><div class="hero-metric-label">Files Changed</div></div>
          <div class="hero-metric"><div class="hero-metric-value">{s['active_days']}</div><div class="hero-metric-label">Active Days</div></div>
        </div>
      </div>
    </div>

    <div class="stats-row">
      <div class="stat"><div class="stat-value">{s['total_sessions']}</div><div class="stat-label">会话数</div></div>
      <div class="stat"><div class="stat-value">{s['total_messages']}</div><div class="stat-label">消息数</div></div>
      <div class="stat"><div class="stat-value">+{s['total_lines_added']}/-{s['total_lines_removed']}</div><div class="stat-label">代码行</div></div>
      <div class="stat"><div class="stat-value">{s['total_files']}</div><div class="stat-label">改动文件</div></div>
      <div class="stat"><div class="stat-value">{s['active_days']}</div><div class="stat-label">活跃天数</div></div>
      <div class="stat"><div class="stat-value">{s['agent_count']}</div><div class="stat-label">Agent</div></div>
      <div class="stat"><div class="stat-value">{s['chat_count']}</div><div class="stat-label">Chat</div></div>
      <div class="stat"><div class="stat-value">{s['ai_hashes_count']}</div><div class="stat-label">AI 代码块</div></div>
    </div>

    <h2>What You Work On</h2>
    <p class="section-intro">按项目目录聚合的会话分布，不展示具体文件路径。</p>
    <div class="cards">
"""]
    if insights["work_on"]:
        for item in insights["work_on"]:
            html_parts.append(
                f"""      <div class="card">
        <div class="card-title">{safe_text(item['name'])}</div>
        <div class="card-detail">{safe_text(item['desc'])}</div>
      </div>
"""
            )
    else:
        html_parts.append('      <div class="card"><div class="card-title">暂无项目数据</div><div class="card-detail">本周没有解析到足够的项目分布信息。</div></div>\n')
    html_parts.append("    </div>\n")

    html_parts.append("""    <h2>How You Use Cursor</h2>
    <div class="card" style="margin-bottom: 18px;">
      <div class="card-detail" style="font-size:14px;line-height:1.7;">""")
    for idx, paragraph in enumerate(insights["narrative_parts"]):
        if idx:
            html_parts.append("<br><br>")
        html_parts.append(safe_text(paragraph))
    html_parts.append(
        f"""<div style="margin-top:14px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:12px 14px;color:#166534;"><strong>Key insight:</strong> {safe_text(insights['key_insight'])}</div>
      </div>
    </div>
    <div class="cards">
""")
    for card in insights["usage_cards"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{safe_text(card['title'])}: {safe_text(card['value'])}</div>
        <div class="card-detail">{safe_text(card['desc'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    # Daily charts
    if s["daily"]:
        max_daily = max(item["sessions"] for item in s["daily"]) or 1
        html_parts.append("""    <h2>每日会话数</h2>
    <div class="chart-card">
""")
        html_parts.append(_render_bar_rows(s["daily"], "day", "sessions", max_daily))
        html_parts.append("\n    </div>\n")

    # Mode distribution
    html_parts.append(f"""    <h2>使用模式</h2>
    <div class="stats-row" style="margin-top: 16px;">
      <div class="stat"><div class="stat-value">{s['agent_count']}</div><div class="stat-label">Agent 模式</div></div>
      <div class="stat"><div class="stat-value">{s['chat_count']}</div><div class="stat-label">Chat 模式</div></div>
      <div class="stat"><div class="stat-value">{s['plan_count']}</div><div class="stat-label">Plan 模式</div></div>
      <div class="stat"><div class="stat-value">{s['avg_messages']}</div><div class="stat-label">Avg msgs/session</div></div>
    </div>
""")

    # AI code hashes
    if insights["ai_hashes_count"] > 0 and insights["top_files"]:
        max_file = max(insights["top_files"].values()) or 1
        file_items = [{"name": k, "count": v} for k, v in insights["top_files"].items()]
        html_parts.append("""    <h2>AI 代码生成文件</h2>
    <p class="section-intro">Cursor AI 生成代码块所涉及的文件分布。</p>
    <div class="chart-card">
""")
        html_parts.append(_render_bar_rows(file_items, "name", "count", max_file, " blocks"))
        html_parts.append("\n    </div>\n")

    if insights["models"]:
        max_model = max(item["cnt"] for item in insights["models"]) or 1
        html_parts.append("""    <h2>模型分布</h2>
    <div class="chart-card">
""")
        for r in insights["models"]:
            pct = int(r["cnt"] / max_model * 100)
            html_parts.append(
                f"""      <div class="bar-row">
        <div class="bar-label">{safe_text(r['model'])}</div>
        <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
        <div class="bar-value">{r['cnt']}x</div>
      </div>
"""
            )
        html_parts.append("    </div>\n")

    # Wins
    html_parts.append("""    <h2>Impressive Things You Did</h2>
    <div class="cards">
""")
    if insights["wins"]:
        for item in insights["wins"]:
            html_parts.append(
                f"""      <div class="card">
        <div class="card-title">{safe_text(item['title'])}</div>
        <div class="card-detail">{safe_text(item['detail'])}</div>
      </div>
"""
            )
    else:
        html_parts.append('      <div class="card"><div class="card-title">数据不足</div><div class="card-detail">本周 Cursor 使用量偏少，暂时不足以提炼稳定优势。</div></div>\n')
    html_parts.append("    </div>\n")

    # Friction
    html_parts.append("""    <h2>Where Things Go Wrong</h2>
    <div class="cards">
""")
    if insights["friction"]:
        for item in insights["friction"]:
            html_parts.append(
                f"""      <div class="card">
        <div class="card-title">{safe_text(item['title'])}</div>
        <div class="card-detail">{safe_text(item['detail'])}</div>
      </div>
"""
            )
    else:
        html_parts.append('      <div class="card"><div class="card-title">摩擦不明显</div><div class="card-detail">本周没有解析到显著的中断或效率下滑问题。</div></div>\n')
    html_parts.append("    </div>\n")

    # Features
    html_parts.append("""    <h2>Features to Try</h2>
    <div class="cards">
""")
    for item in insights["features"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{safe_text(item['title'])}</div>
        <div class="card-detail">{safe_text(item['detail'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    # Patterns
    html_parts.append("""    <h2>New Ways to Use Cursor</h2>
    <div class="cards">
""")
    for item in insights["patterns"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{safe_text(item['title'])}</div>
        <div class="card-detail">{safe_text(item['summary'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    # Horizon
    html_parts.append("""    <h2>On the Horizon</h2>
    <div class="cards">
""")
    for item in insights["horizon"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{safe_text(item['title'])}</div>
        <div class="card-detail">{safe_text(item['detail'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    # Raw data
    raw_data = {
        "week": report["week"],
        "period_start": report["period_start"],
        "period_end": report["period_end"],
        "total_sessions": s["total_sessions"],
        "total_messages": s["total_messages"],
        "total_lines_added": s["total_lines_added"],
        "total_lines_removed": s["total_lines_removed"],
        "total_files": s["total_files"],
        "active_days": s["active_days"],
        "agent_count": s["agent_count"],
        "chat_count": s["chat_count"],
        "plan_count": s["plan_count"],
        "daily": s["daily"],
        "areas": s["areas"],
        "models": s["models"],
        "insights": insights,
    }
    html_parts.append(
        f"""
    <div class="raw-data" id="cursor-raw-data">{json.dumps(raw_data, ensure_ascii=False)}</div>
  </div>
</body>
</html>
"""
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))
    print(f"Cursor report: {out_path}", file=sys.stderr)


def collect(period_str):
    period_start, period_end = period_start_end(period_str)
    week_label = period_str  # keep backward compat label

    state_db = _cursor_state_db()
    ai_db = _cursor_ai_tracking_db()

    if not state_db.exists():
        raise RuntimeError(f"Cursor state DB 不存在: {state_db}")

    sessions, areas, sessions_by_day = _collect_composer_sessions(state_db, period_start, period_end)
    daily_stats = _collect_daily_stats(state_db, period_start, period_end)
    ai_hashes, models, top_files = _collect_ai_code_hashes(ai_db, period_start, period_end)
    scored_commits = _collect_scored_commits(ai_db, period_start, period_end)

    if not sessions and not daily_stats and not ai_hashes:
        raise RuntimeError(f"Cursor 在 {week_label} 无使用数据")

    insights = _summarize_insights(sessions, areas, daily_stats, ai_hashes, models, top_files)

    report = {
        "week": week_label,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "source": {
            "state_db": str(state_db),
            "ai_db": str(ai_db),
        },
        "summary": {
            "total_sessions": insights["total_sessions"],
            "total_messages": insights["total_messages"],
            "total_lines_added": insights["total_lines_added"],
            "total_lines_removed": insights["total_lines_removed"],
            "total_files": insights["total_files"],
            "active_days": insights["active_days"],
            "agent_count": insights["agent_count"],
            "chat_count": insights["chat_count"],
            "plan_count": insights["plan_count"],
            "avg_messages": insights["avg_messages"],
            "ai_hashes_count": insights["ai_hashes_count"],
            "daily": insights["daily"],
            "areas": insights["areas"],
            "models": insights["models"],
        },
        "insights": insights,
        "daily_stats": daily_stats,
        "ai_hashes_count": len(ai_hashes),
        "scored_commits_count": len(scored_commits),
    }

    return report


def main():
    today = datetime.date.today()
    iso_cal = today.isocalendar()
    default_week = f"{iso_cal[0]}-W{iso_cal[1]:02d}"

    parser = argparse.ArgumentParser(description="采集 Cursor 使用数据")
    parser.add_argument("week", nargs="?", default=default_week, help="ISO 周标识，如 2026-W20")
    parser.add_argument("--output", "-o", metavar="PATH", help="输出 HTML 路径")
    args = parser.parse_args()

    try:
        report = collect(args.week)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if args.output:
        generate_html(report, args.output)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
