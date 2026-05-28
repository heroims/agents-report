#!/usr/bin/env python3
"""采集 OpenClaw 使用数据，基于 commands.log 生成周报 HTML。"""

import argparse
import datetime
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import sys as _sys
_scripts_dir = str((__import__('pathlib').Path(__file__).resolve().parent))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
from period_utils import period_start_end

OPENCLAW_COMMANDS_LOG = Path.home() / ".openclaw" / "logs" / "commands.log"


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


def _extract_agent_name(session_key):
    """从 sessionKey 提取 agent 名称。格式: agent:{name}:..."""
    parts = (session_key or "").split(":")
    if len(parts) >= 2 and parts[0] == "agent":
        return parts[1]
    return "unknown"


def _extract_agent_source(session_key):
    """从 sessionKey 提取来源信息。"""
    parts = (session_key or "").split(":")
    if len(parts) >= 3:
        return ":".join(parts[2:])
    return ""


def collect(period_str):
    """采集指定周期 OpenClaw 使用数据。"""
    period_start, period_end = period_start_end(period_str)
    start_dt = datetime.datetime(period_start.year, period_start.month, period_start.day, 0, 0, 0, tzinfo=datetime.timezone.utc)
    end_dt = datetime.datetime(period_end.year, period_end.month, period_end.day, 23, 59, 59, 999999, tzinfo=datetime.timezone.utc)

    if not OPENCLAW_COMMANDS_LOG.exists():
        raise RuntimeError(f"OpenClaw commands.log 不存在: {OPENCLAW_COMMANDS_LOG}")

    events = []
    with open(OPENCLAW_COMMANDS_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_str = obj.get("timestamp")
            if not ts_str:
                continue
            try:
                ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            if ts < start_dt or ts > end_dt:
                continue

            session_key = obj.get("sessionKey", "")
            events.append({
                "timestamp": ts,
                "action": obj.get("action", ""),
                "agent_name": _extract_agent_name(session_key),
                "agent_source": _extract_agent_source(session_key),
                "source": obj.get("source", ""),
                "sender_id": obj.get("senderId", ""),
            })

    if not events:
        raise RuntimeError(f"OpenClaw 在 {period_str} 无会话数据")

    # Count sessions (unique session creation "new" events)
    new_events = [e for e in events if e["action"] == "new"]
    total_sessions = len(new_events)
    reset_events = len([e for e in events if e["action"] == "reset"])

    # Active days
    days = set()
    for e in events:
        days.add(e["timestamp"].strftime("%Y-%m-%d"))
    active_days = len(days)

    # Daily distribution
    daily_counter = defaultdict(lambda: {"new": 0, "reset": 0})
    for e in events:
        day = e["timestamp"].strftime("%Y-%m-%d")
        daily_counter[day][e["action"]] += 1

    daily = []
    for day in sorted(daily_counter.keys()):
        daily.append({
            "day": day,
            "sessions": daily_counter[day]["new"],
            "resets": daily_counter[day]["reset"],
        })

    # Agent distribution
    agent_counter = Counter()
    for e in new_events:
        agent_counter[e["agent_name"]] += 1

    agents_list = []
    for name, count in agent_counter.most_common(10):
        agents_list.append({"name": name, "sessions": count})

    # Source distribution
    source_counter = Counter()
    for e in new_events:
        source_counter[e["source"]] += 1

    sources_list = []
    for name, count in source_counter.most_common(5):
        sources_list.append({"name": name, "sessions": count})

    return {
        "week": period_str,
        "period_start": str(period_start),
        "period_end": str(period_end),
        "total_sessions": total_sessions,
        "reset_events": reset_events,
        "active_days": active_days,
        "total_events": len(events),
        "daily": daily,
        "agents": agents_list,
        "sources": sources_list,
        "source": {
            "log_path": str(OPENCLAW_COMMANDS_LOG),
            "notes": ["sessions parsed from commands.log new/reset events"],
        },
    }


def build_insights(data):
    total_sessions = int(data.get("total_sessions") or 0)
    active_days = int(data.get("active_days") or 0)
    agents = data.get("agents") or []
    sources = data.get("sources") or []
    daily = data.get("daily") or []

    top_agent = agents[0]["name"] if agents else "未知"
    top_source = sources[0]["name"] if sources else "未知"
    peak_day = max(daily, key=lambda d: d["sessions"], default=None)

    agent_names = ", ".join(a["name"] for a in agents[:3]) or "多个 agent"

    at_a_glance = {
        "working": (
            f"OpenClaw 本周启动了 {total_sessions} 个新会话、{data.get('reset_events', 0)} 次重置，分布在 {active_days} 个活跃日上。"
            f"主力 agent 是 {top_agent}，主要通过 {top_source} 触发，说明你的多 agent 编排已经开始常态化。"
            if total_sessions
            else "本周 OpenClaw 无活动。"
        ),
        "hindering": (
            "当前 OpenClaw 的采集数据仅限于会话创建和重置事件，缺乏 token 用量、工具调用、代码改动等细粒度指标，"
            "无法像 Claude Code 或 Codex 那样做深度行为分析。如有更多日志源可考虑扩展采集。"
        ),
        "quick_win": (
            f"把最活跃的 agent（{top_agent}）的行为模式固化下来，"
            "对照它的触发方式看是不是已经有稳定的入口和任务轮廓。"
        ),
        "ambitious": (
            "如果后续 OpenClaw 可以输出更细粒度的执行日志（token、工具、文件改动），"
            "这里就能从活跃度报告升级为执行质量报告。"
        ),
    }

    sessions_per_day = round(total_sessions / active_days, 1) if active_days else 0

    narrative_parts = [
        f"你这周通过 OpenClaw 触发了 {total_sessions} 次会话，主要是通过 {top_source} 入口，agent 集中在 {agent_names}。",
        f"活跃天数 {active_days} 天，平均每天 {sessions_per_day} 个新会话。"
        + (f"高峰出现在 {peak_day['day']}，当天 {peak_day['sessions']} 个新会话。" if peak_day else ""),
        f"从 agent 分布来看，{top_agent} 是最活跃的 agent。OpenClaw 目前承担的是多 agent 编排和会话管理角色。",
    ]

    key_insight = "OpenClaw 的会话创建和重置频率说明你已经在用它做多 agent 编排，但目前数据层面只能看到启动行为，还看不到执行细节。"

    usage_cards = [
        {
            "title": "会话密度",
            "value": f"{sessions_per_day}/day",
            "desc": f"平均每天 {sessions_per_day} 个新会话，说明 OpenClaw 已经成为常用入口。",
        },
        {
            "title": "Agent 多样性",
            "value": str(len(agents)),
            "desc": f"本周使用了 {len(agents)} 个不同的 agent，编排能力比较活跃。",
        },
        {
            "title": "触发来源",
            "value": top_source,
            "desc": f"主要触发来源是 {top_source}，说明你习惯从特定渠道启动 agent。",
        },
        {
            "title": "重置频率",
            "value": str(data.get("reset_events", 0)),
            "desc": f"本周发生了 {data.get('reset_events', 0)} 次会话重置，可能反映了一些方向调整或上下文刷新。",
        },
    ]

    wins = [
        {
            "title": "多 agent 编排已进入日常",
            "detail": f"{total_sessions} 个新会话分布在 {active_days} 天，不是偶尔测试，是真实的日常使用。",
        },
        {
            "title": f"{top_agent} 已经成为主力 agent",
            "detail": f"从分布看，{top_agent} 承担了最多的会话量，说明你在这个 agent 上已经找到了适合的使用模式。",
        },
    ]

    friction = [
        {
            "title": "数据局限于启动事件",
            "detail": "目前只能看到会话创建和重置，看不到 token 消耗、工具调用、文件改动，所以无法做深度分析。",
        },
        {
            "title": "需要更细的执行数据",
            "detail": "如果后续 OpenClaw 能输出类似 rollout 的执行日志，就能从活跃度报告升级成执行质量报告。",
        },
    ]

    features = [
        {
            "title": "让每个 agent 都有明确的职责边界",
            "detail": "既然多 agent 编排已经是常态，就把每个 agent 负责什么、不负责什么写清楚，减少重合和浪费。",
        },
        {
            "title": "把高频 agent 的启动方式固化",
            "detail": f"既然 {top_agent} 是最活跃的，就把它最常用的触发方式、输入格式、预期输出标准化。",
        },
    ]

    patterns = [
        {
            "title": "OpenClaw 更像编排层，而不是单一执行工具",
            "summary": "你主要用它管理多个 agent 的会话，而不是把它本身当作一个编码 agent 来用。",
        },
        {
            "title": "你已经形成了稳定的 agent 组合",
            "summary": f"集中在 {agent_names}，说明你已经在根据自己的需求筛选和搭配 agent。",
        },
    ]

    horizon = [
        {
            "title": "从活跃监控升级到质量监控",
            "detail": "一旦补上 token 和工具调用数据，OpenClaw 就可以开始分析执行效率和问题定位。",
        },
        {
            "title": "把 agent 编排策略沉淀成规则",
            "detail": "既然 agent 使用模式已经成型，下一阶段就是把选择规则和编排策略固化成可复用的配置。",
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
    active_days = int(data.get("active_days") or 0)
    reset_events = int(data.get("reset_events") or 0)
    daily = data.get("daily") or []
    agents = data.get("agents") or []
    sources = data.get("sources") or []
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
    max_agent = max((a["sessions"] for a in agents), default=1) or 1

    work_on_html = ""
    for idx, agent in enumerate(agents[:5]):
        name = _safe_text(agent["name"])
        sessions = agent["sessions"]
        work_on_html += f"""      <div class="project-area">
        <div class="area-header">
          <span class="area-name">Agent: {name}</span>
          <span class="area-count">{sessions} 会话</span>
        </div>
        <div class="area-desc">本周在 OpenClaw 中主要通过 {name} agent 处理任务。</div>
      </div>
"""
    if not work_on_html:
        work_on_html = '      <p class="empty">本周暂无 agent 分布数据。</p>\n'

    html_str = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>OpenClaw Insights</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(180deg, #faf5ff 0%, #f3e8ff 100%); color: #334155; line-height: 1.65; padding: 48px 24px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ font-size: 32px; font-weight: 700; color: #4c1d95; margin-bottom: 8px; }}
    h2 {{ font-size: 20px; font-weight: 600; color: #0f172a; margin-top: 42px; margin-bottom: 14px; }}
    .subtitle {{ color: #7c3aed; font-size: 15px; margin-bottom: 28px; }}
    .hero {{ background: linear-gradient(135deg, #faf5ff 0%, #ede9fe 45%, #f5f3ff 100%); border: 1px solid #c4b5fd; border-radius: 16px; padding: 22px 24px; margin-bottom: 24px; }}
    .glance-title {{ font-size: 16px; font-weight: 700; color: #6d28d9; margin-bottom: 14px; }}
    .glance-sections {{ display: flex; flex-direction: column; gap: 10px; }}
    .glance-section {{ font-size: 14px; color: #4c1d95; }}
    .glance-section strong {{ color: #6d28d9; }}
    .stats-row {{ display: flex; gap: 24px; margin-bottom: 28px; padding: 20px 0; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; flex-wrap: wrap; }}
    .stat {{ text-align: center; }}
    .stat-value {{ font-size: 24px; font-weight: 700; color: #0f172a; }}
    .stat-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
    .section-intro {{ font-size: 14px; color: #64748b; margin-bottom: 14px; }}
    .project-area {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; margin-bottom: 10px; }}
    .area-header {{ display: flex; justify-content: space-between; gap: 8px; align-items: center; }}
    .area-name {{ font-weight: 600; color: #0f172a; }}
    .area-count {{ font-size: 12px; color: #64748b; }}
    .area-desc {{ font-size: 13px; color: #475569; margin-top: 8px; line-height: 1.6; }}
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
    .bar-track {{ flex: 1; height: 8px; border-radius: 999px; background: #ede9fe; margin: 0 10px; overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: 999px; background: linear-gradient(90deg, #8b5cf6 0%, #a78bfa 100%); }}
    .bar-value {{ width: 70px; font-size: 11px; color: #64748b; text-align: right; }}
    .raw-data {{ display: none; }}
    .empty {{ color: #94a3b8; font-size: 13px; }}
    @media (max-width: 760px) {{ .cards, .charts-row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="container">
    <h1>OpenClaw Insights</h1>
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
      <div class="stat"><div class="stat-value">{total_sessions}</div><div class="stat-label">新会话</div></div>
      <div class="stat"><div class="stat-value">{reset_events}</div><div class="stat-label">重置事件</div></div>
      <div class="stat"><div class="stat-value">{active_days}</div><div class="stat-label">活跃天数</div></div>
    </div>

    <h2>Agent 分布</h2>
    <p class="section-intro">按 agent 类型统计本周新会话数。</p>
    <div class="project-areas">
{work_on_html}    </div>

    <h2>How You Use OpenClaw</h2>
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
        <div class="chart-title">每日新会话</div>
{_render_bar_rows(daily, 'day', 'sessions', max_daily, 'x')}
      </div>
      <div class="chart-card">
        <div class="chart-title">Agent 排名</div>
{_render_bar_rows(agents, 'name', 'sessions', max_agent, 'x')}
      </div>
    </div>

    <h2>Impressive Things You Did</h2>
    <p class="section-intro">基于本周会话创建模式，识别已经稳定形成的使用优势。</p>
    <div class="cards">
{_render_cards(insights['wins'])}
    </div>

    <h2>Where Things Go Wrong</h2>
    <p class="section-intro">当前主要摩擦来自数据的可见性 —— 能看到启动行为，但看不到执行细节。</p>
    <div class="cards">
{_render_cards(insights['friction'])}
    </div>

    <h2>Features to Try</h2>
    <div class="cards">
{_render_cards(insights['features'])}
    </div>

    <h2>New Ways to Use OpenClaw</h2>
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
        "reset_events": reset_events,
        "active_days": active_days,
        "daily": daily,
        "agents": agents,
        "sources": sources,
        "insights": insights,
        "source": data.get("source") or {},
    }

    html_str += f"""
    <div class="raw-data" id="openclaw-raw-data">{json.dumps(raw_data, ensure_ascii=False)}</div>
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

    parser = argparse.ArgumentParser(description="采集 OpenClaw 使用数据")
    parser.add_argument("week", nargs="?", default=default_week, help="ISO 周标识，如 2026-W22")
    parser.add_argument("--output", "-o", metavar="PATH", help="输出 HTML 路径")
    args = parser.parse_args()

    try:
        data = collect(args.week)
    except Exception as exc:
        print(f"OpenClaw 采集失败: {exc}", file=sys.stderr)
        return 1

    if not data or int(data.get("total_sessions") or 0) <= 0:
        print(f"OpenClaw 在 {args.week} 无可用数据", file=sys.stderr)
        return 1

    if args.output:
        generate_html(data, args.output)
        print(f"OpenClaw report: {args.output}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
