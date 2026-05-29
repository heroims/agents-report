#!/usr/bin/env python3
"""采集 Hermes 使用数据，基于 state.db 生成带洞察的 HTML 报告。"""

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
from i18n import T as _I18nT
_LANG = _I18nT.detect()
_I18N = _I18nT(_LANG)
_t = _I18N

HERMES_DB = Path.home() / ".hermes" / "state.db"


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fmt(n):
    value = int(n or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _safe_text(value):
    return html.escape(str(value or ""))


def _to_date(epoch_sec):
    """Unix timestamp -> date string."""
    try:
        return datetime.datetime.fromtimestamp(float(epoch_sec)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def _parse_tool_calls(tool_calls_raw):
    """Parse tool_calls JSON field from messages table."""
    if not tool_calls_raw:
        return []
    try:
        parsed = json.loads(tool_calls_raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, list):
        return [item.get("function", {}).get("name", item.get("name", "")) for item in parsed if isinstance(item, dict)]
    return []


def _parse_content_text(content_raw):
    """Extract plain text from content JSON."""
    if not content_raw:
        return ""
    try:
        parsed = json.loads(content_raw)
    except (json.JSONDecodeError, TypeError):
        return str(content_raw)[:200]
    if isinstance(parsed, str):
        return parsed[:200]
    if isinstance(parsed, list):
        texts = []
        for block in parsed:
            if isinstance(block, dict):
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        return " ".join(texts)[:200]
    return ""


def collect(period_str):
    """采集指定周期 Hermes 使用数据。"""
    period_start, period_end = period_start_end(period_str)
    start_ts = datetime.datetime(period_start.year, period_start.month, period_start.day, 0, 0, 0).timestamp()
    end_ts = datetime.datetime(period_end.year, period_end.month, period_end.day, 23, 59, 59, 999999).timestamp()

    if not HERMES_DB.exists():
        raise RuntimeError(f"Hermes state.db 不存在: {HERMES_DB}")

    conn = sqlite3.connect(str(HERMES_DB))
    conn.row_factory = sqlite3.Row

    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "sessions" not in tables:
            raise RuntimeError("Hermes schema 缺少 sessions 表")

        session_rows = conn.execute(
            "SELECT * FROM sessions WHERE started_at >= ? AND started_at <= ? ORDER BY started_at DESC",
            (start_ts, end_ts),
        ).fetchall()

        if not session_rows:
            # Also try sessions that may have ended in this period but started earlier
            session_rows = conn.execute(
                "SELECT * FROM sessions WHERE ended_at >= ? AND ended_at <= ? AND started_at < ? ORDER BY ended_at DESC",
                (start_ts, end_ts, start_ts),
            ).fetchall()

        if not session_rows:
            raise RuntimeError(f"Hermes 在 {period_str} 无会话数据")

        # Collect session-level metrics
        session_ids = [row["id"] for row in session_rows]
        total_sessions = len(session_ids)

        total_input_tokens = sum(_safe_int(row["input_tokens"]) for row in session_rows)
        total_output_tokens = sum(_safe_int(row["output_tokens"]) for row in session_rows)
        total_cache_read = sum(_safe_int(row["cache_read_tokens"]) for row in session_rows)
        total_cache_write = sum(_safe_int(row["cache_write_tokens"]) for row in session_rows)
        total_reasoning = sum(_safe_int(row["reasoning_tokens"]) for row in session_rows)
        total_tokens = total_input_tokens + total_output_tokens
        total_message_count = sum(_safe_int(row["message_count"]) for row in session_rows)
        total_tool_call_count = sum(_safe_int(row["tool_call_count"]) for row in session_rows)

        # Active days from started_at
        days = set()
        daily_sessions = defaultdict(int)
        for row in session_rows:
            day = _to_date(row["started_at"])
            if day:
                days.add(day)
                daily_sessions[day] += 1

        daily = []
        for day in sorted(days):
            daily.append({"day": day, "sessions": daily_sessions[day]})

        active_days = len(days)

        # Model distribution
        model_counter = Counter()
        model_tokens = defaultdict(int)
        for row in session_rows:
            model = row["model"] or "unknown"
            model_counter[model] += 1
            model_tokens[model] += _safe_int(row["input_tokens"]) + _safe_int(row["output_tokens"])

        models = []
        for name, count in model_counter.most_common(10):
            models.append({"model": name, "sessions": count, "tokens": model_tokens[name]})

        # Source distribution
        source_counter = Counter()
        for row in session_rows:
            source = row["source"] or "unknown"
            source_counter[source] += 1

        sources = [{"name": name, "count": cnt} for name, cnt in source_counter.most_common(5)]

        # End reason distribution
        end_reason_counter = Counter()
        for row in session_rows:
            reason = row["end_reason"] or "unknown"
            end_reason_counter[reason] += 1

        # Collect tool usage from messages table
        tools_counter = Counter()
        if "messages" in tables:
            placeholders = ",".join("?" for _ in session_ids)
            message_rows = conn.execute(
                f"SELECT tool_name, tool_calls, role, content FROM messages WHERE session_id IN ({placeholders})",
                session_ids,
            ).fetchall()

            total_messages = len(message_rows)
            user_messages = 0

            for m in message_rows:
                if m["role"] == "user":
                    user_messages += 1

                # Tool calls from tool_calls JSON
                tc_names = _parse_tool_calls(m["tool_calls"])
                for name in tc_names:
                    if name:
                        tools_counter[name] += 1

                # Tool calls from tool_name field
                if m["tool_name"]:
                    tools_counter[m["tool_name"]] += 1
        else:
            total_messages = total_message_count
            user_messages = 0

        top_tools = [{"name": name, "count": cnt} for name, cnt in tools_counter.most_common(10)]

        source_notes = [
            "sessions from state.db sessions table (started_at filter)",
            "tokens from sessions.input_tokens + sessions.output_tokens",
            f"messages from messages table" if "messages" in tables else "messages count from sessions.message_count",
            f"top_tools from messages.tool_calls + messages.tool_name" if "messages" in tables else "tool calls from sessions.tool_call_count (no per-tool detail)",
        ]

        return {
            "week": period_str,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "user_messages": user_messages,
            "total_tokens": total_tokens,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "reasoning_tokens": total_reasoning,
            "active_days": active_days,
            "tool_call_count": total_tool_call_count,
            "daily": daily,
            "models": models,
            "sources": sources,
            "top_tools": top_tools,
            "end_reasons": [{"reason": k, "count": v} for k, v in end_reason_counter.most_common(5)],
            "source_info": {
                "db_path": str(HERMES_DB),
                "notes": source_notes,
            },
        }
    finally:
        conn.close()


def build_insights(data):
    total_sessions = int(data.get("total_sessions") or 0)
    total_messages = int(data.get("total_messages") or 0)
    total_tokens = int(data.get("total_tokens") or 0)
    active_days = int(data.get("active_days") or 0)
    tool_call_count = int(data.get("tool_call_count") or 0)
    models = data.get("models") or []
    daily = data.get("daily") or []
    top_tools = data.get("top_tools") or []

    avg_tokens = round(total_tokens / total_sessions) if total_sessions else 0
    avg_messages = round(total_messages / total_sessions, 1) if total_sessions else 0
    sessions_per_day = round(total_sessions / active_days, 1) if active_days else 0
    top_model = models[0]["model"] if models else "unknown"
    tool_names = ", ".join(t["name"] for t in top_tools[:3]) or "工具调用"
    peak_day = max(daily, key=lambda d: d["sessions"], default=None)

    at_a_glance = {
        "working": (
            f"Hermes 这周承担了 {total_sessions} 次会话，分布在 {active_days} 个活跃日，"
            f"总 token 消耗 {_fmt(total_tokens)}，说明它已经进入你的日常工具箱。"
            f"主力模型 {top_model} 和 {tool_names} 是本周的高频操作。"
            if total_sessions
            else _t("No Hermes activity this period.")
        ),
        "hindering": (
            "当前 Hermes 数据能看到会话、消息、token 和工具调用，"
            "但缺乏具体的文件改动（lines added/removed）和补丁落地统计数据，"
            "所以执行质量的部分还比较保守。"
        ),
        "quick_win": (
            f"如果 {top_model} 是主力，对比一下它和 Claude Code/Codex 的模型选择，"
            "看看是否有重合或互补，可以帮助优化分配。"
        ),
        "ambitious": (
            "如果后续能从 messages 中解析出文件操作和代码变更，"
            "Hermes 就可以从活跃度报告升级为完整的执行质量报告。"
        ),
    }

    narrative_parts = [
        f"你这周使用 Hermes 的方式已经形成一定节奏。{total_sessions} 个会话、{total_messages} 条消息、{_fmt(total_tokens)} tokens，"
        f"平均每会话 {avg_messages} 条消息，说明它不只是单次查询工具，而是在承接连续交互。",
        f"从模型选择看，{top_model} 是主力。工具调用集中在 {tool_names}，本周共调用了 {tool_call_count} 次工具，"
        f"平均每会话 {round(tool_call_count / total_sessions, 1) if total_sessions else 0} 次工具调用。"
        + (f" 活跃高峰出现在 {peak_day['day']}，当天 {peak_day['sessions']} 个会话。" if peak_day else ""),
        f"{active_days} 个活跃日、日均 {sessions_per_day} 个会话，Hermes 更像是一个持续使用而非偶发试探的工具。",
    ]

    key_insight = "Hermes 的使用频率和工具调用密度说明它已经是你的日常执行工具，下一步是把高频场景固化，把执行质量数据补全。"

    usage_cards = [
        {
            "title": _t("Session Density"),
            "value": f"{sessions_per_day}/day",
            "desc": f"平均每天 {sessions_per_day} 个会话，Hermes 已经融入日常节奏。",
        },
        {
            "title": _t("Tool Calls"),
            "value": str(tool_call_count),
            "desc": f"本周 {tool_call_count} 次工具调用，主要集中在 {tool_names}。",
        },
        {
            "title": _t("Context Depth"),
            "value": _fmt(avg_tokens),
            "desc": f"平均每会话约 {_fmt(avg_tokens)} tokens，说明你在 Hermes 上也承担了有上下文深度的任务。",
        },
        {
            "title": _t("Primary Model"),
            "value": top_model,
            "desc": f"{top_model} 承担了最多的会话，是你本周的首选模型。",
        },
    ]

    wins = [
        {
            "title": _t("Stable usage rhythm formed"),
            "detail": f"{active_days} 个活跃日、{total_sessions} 个会话，Hermes 不再是实验性工具，而是列入日常流程。",
        },
        {
            "title": _t("Tool call density shows real work"),
            "detail": f"{tool_call_count} 次工具调用说明 Hermes 不只是问答，而是在操作文件、执行命令、调用 API。",
        },
        {
            "title": f"{top_model} {_t("is already your stable choice")}",
            "detail": f"主力模型 {top_model} 说明你已经在 Hermes 上找到了合适的模型配置。",
        },
    ]

    friction = [
        {
            "title": _t("Execution quality data insufficient"),
            "detail": "目前能看到 token 和工具调用，但看不到文件改动、行数变化、补丁成功率，执行闭环的验证还缺一块。",
        },
        {
            "title": _t("Tool division still unclear"),
            "detail": "当 Claude Code、Codex、OpenCode、Hermes 都在用的时候，需要更明确各自负责什么类型的任务。",
        },
    ]

    features = [
        {
            "title": _t("Solidify high-frequency Hermes tasks into workflows"),
            "detail": "既然工具调用已经集中在特定几个，就把这些任务的起点、边界和输出格式写进 SOUL.md 或 skills 中。",
        },
        {
            "title": _t("Strategically allocate models"),
            "detail": f"对比 Hermes 的 {top_model} 和其他工具的模型选择，看是否有机会根据任务类型做更精细的模型分配。",
        },
    ]

    patterns = [
        {
            "title": _t("Hermes handles tool-intensive tasks"),
            "summary": f"从 {tool_call_count} 次工具调用和 {tool_names} 的分布看，你主要用它做需要多条命令和多个 API 交互的任务。",
        },
        {
            "title": _t("Already building multi-tool collaboration"),
            "summary": "Claude Code、Codex、OpenCode、Hermes 各司其职，关键是让每个工具都拿到最适合它的任务。",
        },
    ]

    horizon = [
        {
            "title": _t("Complete execution quality metrics"),
            "detail": "一旦能从 messages 中解析出文件改动和补丁结果，Hermes 报告就能从'用了多少'升级成'做成了多少'。",
        },
        {
            "title": _t("Form four-tool clear division"),
            "detail": "Claude Code 重调研、Codex 重落地、OpenCode 重补位、Hermes 重编排 —— 这套分工成型后效率会有明显提升。",
        },
    ]

    return {
        "at_a_glance": at_a_glance,
        "narrative_parts": narrative_parts,
        "key_insight": key_insight,
        "usage_cards": usage_cards,
        "wins": wins,
        "friction": friction,
        "features": features,
        "patterns": patterns,
        "horizon": horizon,
    }


def generate_html(data, out_path):
    total_sessions = int(data.get("total_sessions") or 0)
    total_messages = int(data.get("total_messages") or 0)
    total_tokens = int(data.get("total_tokens") or 0)
    active_days = int(data.get("active_days") or 0)
    tool_call_count = int(data.get("tool_call_count") or 0)
    input_tokens = int(data.get("input_tokens") or 0)
    output_tokens = int(data.get("output_tokens") or 0)
    daily = data.get("daily") or []
    models = data.get("models") or []
    top_tools = data.get("top_tools") or []
    insights = data.get("insights") or build_insights(data)

    def _render_bar_rows(items, label_key, value_key, max_val, suffix=""):
        if not items:
            return '<p class="empty">暂无数据。</p>'
        rows = []
        for item in items:
            val = _safe_int(item.get(value_key))
            pct = int(val / max_val * 100) if max_val else 0
            rows.append(
                f"""      <div class="bar-row">
        <div class="bar-label" title="{_safe_text(item.get(label_key, ''))}">{_safe_text(item.get(label_key, ''))}</div>
        <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
        <div class="bar-value">{_safe_text(str(val) + suffix)}</div>
      </div>"""
            )
        return "\n".join(rows)

    def _render_cards(items):
        if not items:
            return '      <div class="card"><div class="card-title">暂无</div><div class="card-detail">本周样本不足以提炼此部分。</div></div>\n'
        parts = []
        for item in items:
            detail = item.get("detail") or item.get("summary") or item.get("desc") or ""
            parts.append(
                f"""      <div class="card">
        <div class="card-title">{_safe_text(item.get('title', ''))}</div>
        <div class="card-detail">{_safe_text(detail)}</div>
      </div>
"""
            )
        return "".join(parts)

    max_daily = max((d["sessions"] for d in daily), default=1) or 1
    max_model = max((m["sessions"] for m in models), default=1) or 1
    max_tool = max((t["count"] for t in top_tools), default=1) or 1

    html_str = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Hermes Insights</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(180deg, #f0fdf4 0%, #dcfce7 100%); color: #334155; line-height: 1.65; padding: 48px 24px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ font-size: 32px; font-weight: 700; color: #166534; margin-bottom: 8px; }}
    h2 {{ font-size: 20px; font-weight: 600; color: #0f172a; margin-top: 42px; margin-bottom: 14px; }}
    .subtitle {{ color: #16a34a; font-size: 15px; margin-bottom: 28px; }}
    .hero {{ background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 45%, #ecfdf5 100%); border: 1px solid #86efac; border-radius: 16px; padding: 22px 24px; margin-bottom: 24px; }}
    .glance-title {{ font-size: 16px; font-weight: 700; color: #15803d; margin-bottom: 14px; }}
    .glance-sections {{ display: flex; flex-direction: column; gap: 10px; }}
    .glance-section {{ font-size: 14px; color: #14532d; }}
    .glance-section strong {{ color: #15803d; }}
    .stats-row {{ display: flex; gap: 24px; margin-bottom: 28px; padding: 20px 0; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; flex-wrap: wrap; }}
    .stat {{ text-align: center; }}
    .stat-value {{ font-size: 24px; font-weight: 700; color: #0f172a; }}
    .stat-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
    .section-intro {{ font-size: 14px; color: #64748b; margin-bottom: 14px; }}
    .narrative {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 18px; }}
    .narrative p {{ margin-bottom: 12px; font-size: 14px; color: #475569; line-height: 1.7; }}
    .key-insight {{ background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px 14px; font-size: 14px; color: #166534; }}
    .cards {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; }}
    .card-title {{ font-size: 15px; font-weight: 600; color: #0f172a; margin-bottom: 8px; }}
    .card-detail {{ font-size: 13px; color: #475569; line-height: 1.6; }}
    .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 18px; }}
    .chart-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; }}
    .chart-title {{ font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase; margin-bottom: 12px; }}
    .bar-row {{ display: flex; align-items: center; margin-bottom: 8px; }}
    .bar-label {{ width: 120px; font-size: 12px; color: #334155; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ flex: 1; height: 8px; border-radius: 999px; background: #dcfce7; margin: 0 10px; overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: 999px; background: linear-gradient(90deg, #22c55e 0%, #10b981 100%); }}
    .bar-value {{ width: 70px; font-size: 11px; color: #64748b; text-align: right; }}
    .raw-data {{ display: none; }}
    .empty {{ color: #94a3b8; font-size: 13px; }}
    @media (max-width: 760px) {{ .cards, .charts-row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Hermes Insights</h1>
    <p class="subtitle">{data['period_start']} ~ {data['period_end']} | {data['week']}</p>

    <div class="hero">
      <div class="glance-title">At a Glance</div>
      <div class="glance-sections">
        <div class="glance-section"><strong>What's working:</strong> {_safe_text(insights['at_a_glance']['working'])}</div>
        <div class="glance-section"><strong>What's hindering you:</strong> {_safe_text(insights['at_a_glance']['hindering'])}</div>
        <div class="glance-section"><strong>Quick wins to try:</strong> {_safe_text(insights['at_a_glance']['quick_win'])}</div>
        <div class="glance-section"><strong>Ambitious workflows:</strong> {_safe_text(insights['at_a_glance']['ambitious'])}</div>
      </div>
    </div>

    <div class="stats-row">
      <div class="stat"><div class="stat-value">{total_sessions}</div><div class="stat-label">' + _t("Sessions") + '</div></div>
      <div class="stat"><div class="stat-value">{total_messages}</div><div class="stat-label">' + _t("Messages") + '</div></div>
      <div class="stat"><div class="stat-value">{_fmt(total_tokens)}</div><div class="stat-label">' + _t("Token Usage") + '</div></div>
      <div class="stat"><div class="stat-value">{active_days}</div><div class="stat-label">' + _t("Active Days") + '</div></div>
      <div class="stat"><div class="stat-value">{tool_call_count}</div><div class="stat-label">' + _t("Tool Calls") + '</div></div>
    </div>

    <h2>How You Use Hermes</h2>
    <div class="narrative">
      <p>{_safe_text(insights['narrative_parts'][0])}</p>
      <p>{_safe_text(insights['narrative_parts'][1])}</p>
      <p>{_safe_text(insights['narrative_parts'][2])}</p>
      <div class="key-insight"><strong>Key pattern:</strong> {_safe_text(insights['key_insight'])}</div>
    </div>

    <div class="cards" style="margin-top:18px;">
{_render_cards(insights['usage_cards'])}
    </div>

    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">' + _t("Daily Sessions") + '</div>
{_render_bar_rows(daily, 'day', 'sessions', max_daily, 'x')}
      </div>
      <div class="chart-card">
        <div class="chart-title">' + _t("Model Distribution") + '</div>
{_render_bar_rows(models, 'model', 'sessions', max_model, 'x')}
      </div>
    </div>

    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">' + _t("Top Tools") + '</div>
{_render_bar_rows(top_tools, 'name', 'count', max_tool, 'x')}
      </div>
      <div class="chart-card">
        <div class="chart-title">' + _t("Token Detail") + '</div>
        <div class="bar-row"><div class="bar-label">Input</div><div class="bar-track"><div class="bar-fill" style="width:100%"></div></div><div class="bar-value">{_fmt(input_tokens)}</div></div>
        <div class="bar-row"><div class="bar-label">Output</div><div class="bar-track"><div class="bar-fill" style="width:{int(output_tokens / max(input_tokens, 1) * 100)}%"></div></div><div class="bar-value">{_fmt(output_tokens)}</div></div>
      </div>
    </div>

    <h2>Impressive Things You Did</h2>
    <p class="section-intro">基于本周会话模式识别已经形成的稳定优势。</p>
    <div class="cards">
{_render_cards(insights['wins'])}
    </div>

    <h2>Where Things Go Wrong</h2>
    <p class="section-intro">主要摩擦来自数据可见性和工具间分工。</p>
    <div class="cards">
{_render_cards(insights['friction'])}
    </div>

    <h2>Features to Try</h2>
    <div class="cards">
{_render_cards(insights['features'])}
    </div>

    <h2>New Ways to Use Hermes</h2>
    <div class="cards">
{_render_cards(insights['patterns'])}
    </div>

    <h2>On the Horizon</h2>
    <div class="cards">
{_render_cards(insights['horizon'])}
    </div>
"""

    raw_data = {
        "week": data.get("week"),
        "period_start": data.get("period_start"),
        "period_end": data.get("period_end"),
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "user_messages": data.get("user_messages", 0),
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": data.get("cache_read_tokens", 0),
        "cache_write_tokens": data.get("cache_write_tokens", 0),
        "reasoning_tokens": data.get("reasoning_tokens", 0),
        "active_days": active_days,
        "tool_call_count": tool_call_count,
        "daily": daily,
        "models": models,
        "top_tools": top_tools,
        "sources": data.get("sources") or [],
        "insights": insights,
        "source_info": data.get("source_info") or {},
    }

    html_str += f"""
    <div class="raw-data" id="hermes-raw-data">{json.dumps(raw_data, ensure_ascii=False)}</div>
  </div>
</body>
</html>
"""

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_str)


def main():
    today = datetime.date.today()
    iso = today.isocalendar()
    default_week = f"{iso[0]}-W{iso[1]:02d}"

    parser = argparse.ArgumentParser(description="采集 Hermes 使用数据")
    parser.add_argument("week", nargs="?", default=default_week, help="ISO 周标识，如 2026-W22")
    parser.add_argument("--output", "-o", metavar="PATH", help="输出 HTML 路径")
    args = parser.parse_args()

    try:
        data = collect(args.week)
    except Exception as exc:
        print(f"Hermes 采集失败: {exc}", file=sys.stderr)
        return 1

    if not data or int(data.get("total_sessions") or 0) <= 0:
        print(f"Hermes 在 {args.week} 无可用数据", file=sys.stderr)
        return 1

    if args.output:
        generate_html(data, args.output)
        print(f"Hermes report: {args.output}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
