#!/usr/bin/env python3
"""以 Claude Code Insights HTML 为基础，嵌入 Codex/OpenCode 数据生成合并报告。"""

import datetime
import json
import os
import re
import sys
from html import escape
from pathlib import Path


def parse_embedded_json(path, div_id):
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    match = re.search(rf'id="{re.escape(div_id)}"[^>]*>(.*?)</div>', html, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except Exception:
        return None


def parse_codex_report(path):
    return parse_embedded_json(path, "codex-raw-data")


def parse_opencode_report(path):
    return parse_embedded_json(path, "opencode-raw-data")


def parse_cursor_report(path):
    return parse_embedded_json(path, "cursor-raw-data")


def parse_trae_report(path):
    return parse_embedded_json(path, "trae-raw-data")


def parse_claude_report(path):
    return parse_embedded_json(path, "claude-raw-data")


def parse_claude_stats(html):
    sessions = 0
    match = re.search(r"across (\d+) sessions", html)
    if match:
        sessions = int(match.group(1))

    vals = re.findall(r'class="stat-value"[^>]*>([^<]*)', html)
    labs = re.findall(r'class="stat-label"[^>]*>([^<]*)', html)
    stats = dict(zip(labs, vals))
    return sessions, stats


def insert_at_container_top(html, banner):
    if '<div class="container">' in html:
        return html.replace('<div class="container">', '<div class="container">\n' + banner, 1)
    return banner + html


def parse_lines_value(text):
    match = re.match(r"\+?([\d,]+)/-([\d,]+)", str(text or ""))
    if not match:
        return 0, 0
    return ___safe_int(match.group(1).replace(",", "")), ___safe_int(match.group(2).replace(",", ""))


def _fmt(n):
    value = int(n or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def ___safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


_PROJECT_ALIASES = {
    "agent-twitter": "agent",
}


def _path_label(path_value):
    normalized = str(path_value or "").rstrip("/\\")
    if not normalized:
        return ""
    name = Path(normalized).name or normalized
    return _PROJECT_ALIASES.get(name, name)


def _render_pills(items, name_key="name", count_key="count", limit=8):
    if not items:
        return '<span class="merge-empty">暂无</span>'
    pills = []
    for item in items[:limit]:
        name = escape(str(item.get(name_key, "")))
        count = escape(str(item.get(count_key, "")))
        pills.append(f'<span class="merge-pill">{name} <b>{count}</b></span>')
    return "".join(pills)


def _render_text_cards(items, limit=4):
    if not items:
        return '<div class="merge-card"><div class="merge-card-title">暂无</div><div class="merge-card-text">本周没有足够样本生成该区块。</div></div>'
    cards = []
    for item in items[:limit]:
        title = escape(str(item.get("title", "")))
        detail = escape(str(item.get("detail") or item.get("summary") or item.get("desc") or ""))
        cards.append(
            f"""<div class="merge-card">
  <div class="merge-card-title">{title}</div>
  <div class="merge-card-text">{detail}</div>
</div>"""
        )
    return "".join(cards)


def _render_usage_cards(items, limit=4):
    if not items:
        return '<div class="merge-card"><div class="merge-card-title">暂无</div><div class="merge-card-text">本周没有足够样本生成该区块。</div></div>'
    cards = []
    for item in items[:limit]:
        title = escape(str(item.get("title", "")))
        value = escape(str(item.get("value", "")))
        detail = escape(str(item.get("detail") or item.get("desc") or ""))
        cards.append(
            f"""<div class="merge-card">
  <div class="merge-split">
    <div class="merge-card-title">{title}</div>
    <div class="merge-meta">{value}</div>
  </div>
  <div class="merge-card-text">{detail}</div>
</div>"""
        )
    return "".join(cards)


def _render_work_on(items, limit=5):
    if not items:
        return '<p class="merge-empty">本周暂无项目分布数据。</p>'
    blocks = []
    for item in items[:limit]:
        name = escape(str(item.get("name", "")))
        sessions = ___safe_int(item.get("sessions"))
        tokens = _fmt(item.get("tokens"))
        desc = escape(str(item.get("desc") or ""))
        blocks.append(
            f"""<div class="merge-card">
  <div class="merge-split">
    <div class="merge-card-title">{name}</div>
    <div class="merge-meta">{sessions} 会话 · {tokens} tokens</div>
  </div>
  <div class="merge-card-text">{desc}</div>
</div>"""
        )
    return "".join(blocks)


def _render_daily(items, limit=7):
    if not items:
        return '<p class="merge-empty">本周没有每日数据。</p>'
    blocks = []
    for item in items[:limit]:
        day = escape(str(item.get("day", "")))
        sessions = ___safe_int(item.get("sessions"))
        tokens = _fmt(item.get("tokens"))
        blocks.append(
            f"""<div class="merge-list-row">
  <span>{day}</span>
  <span>{sessions} 会话 · {tokens} tokens</span>
</div>"""
        )
    return "".join(blocks)


def _render_opencode_areas(items, limit=6):
    if not items:
        return '<p class="merge-empty">本周暂无 OpenCode 项目数据。</p>'
    blocks = []
    for item in items[:limit]:
        blocks.append(
            f"""<div class="merge-card">
  <div class="merge-split">
    <div class="merge-card-title">{escape(str(_path_label(item.get('cwd', '')) or item.get('cwd', '')))}</div>
    <div class="merge-meta">{___safe_int(item.get('sessions'))} 会话 · {_fmt(item.get('tokens'))} tokens</div>
  </div>
</div>"""
        )
    return "".join(blocks)


def _render_notes(notes):
    if not notes:
        return '<p class="merge-empty">暂无采集说明。</p>'
    return "".join(f'<div class="merge-note">{escape(str(note))}</div>' for note in notes[:6])


def _collect_active_days(*daily_groups):
    days = set()
    for items in daily_groups:
        for item in items or []:
            day = str((item or {}).get("day") or "").strip()
            if day:
                days.add(day)
    return days


def _banner_subrows(*rows):
    parts = []
    for row in rows:
        if row:
            parts.append(f'<div class="merge-banner-subrow">{escape(str(row))}</div>')
    return "".join(parts)


def _build_combined_banner(week, cc, cx, oc, cu, tu):
    cc_sessions = ___safe_int((cc or {}).get("cc_sessions"))
    cc_messages = ___safe_int((cc or {}).get("cc_messages"))
    cc_lines_added = ___safe_int((cc or {}).get("cc_lines_added"))
    cc_lines_removed = ___safe_int((cc or {}).get("cc_lines_removed"))
    cc_tokens = ___safe_int((cc or {}).get("cc_tokens"))
    cc_days = ___safe_int((cc or {}).get("cc_days"))
    cc_files = ___safe_int((cc or {}).get("cc_files"))
    cx_sessions = ___safe_int((cx or {}).get("total_sessions"))
    cx_messages = ___safe_int((cx or {}).get("user_messages") or (cx or {}).get("total_messages"))
    cx_message_events = ___safe_int((cx or {}).get("message_events"))
    cx_lines_added = ___safe_int((cx or {}).get("lines_added"))
    cx_lines_removed = ___safe_int((cx or {}).get("lines_removed"))
    cx_tokens = ___safe_int((cx or {}).get("total_tokens"))
    cx_days = ___safe_int((cx or {}).get("active_days"))
    cx_file_impact = ___safe_int(sum((item.get("patch_files") or 0) for item in ((cx or {}).get("thread_details") or [])))
    oc_sessions = ___safe_int((oc or {}).get("total_sessions"))
    oc_messages = ___safe_int((oc or {}).get("total_messages"))
    oc_tokens = ___safe_int((oc or {}).get("total_tokens"))
    oc_lines_added = ___safe_int((oc or {}).get("lines_added"))
    oc_lines_removed = ___safe_int((oc or {}).get("lines_removed"))
    oc_file_impact = ___safe_int((oc or {}).get("files_modified"))
    cu_sessions = ___safe_int((cu or {}).get("total_sessions"))
    cu_messages = ___safe_int((cu or {}).get("total_messages"))
    cu_lines_added = ___safe_int((cu or {}).get("lines_added") or (cu or {}).get("total_lines_added"))
    cu_lines_removed = ___safe_int((cu or {}).get("lines_removed") or (cu or {}).get("total_lines_removed"))
    cu_tokens = ___safe_int((cu or {}).get("total_tokens"))
    cu_days = ___safe_int((cu or {}).get("active_days"))
    tu_tokens = ___safe_int((tu or {}).get("total_tokens"))
    cu_file_impact = ___safe_int((cu or {}).get("total_files"))
    tu_sessions = ___safe_int((tu or {}).get("total_sessions"))
    tu_messages = ___safe_int((tu or {}).get("total_messages"))
    tu_lines_added = ___safe_int((tu or {}).get("total_lines_added"))
    tu_lines_removed = ___safe_int((tu or {}).get("total_lines_removed"))
    tu_days = ___safe_int((tu or {}).get("active_days"))
    tu_file_impact = ___safe_int((tu or {}).get("total_files"))
    tu_agent = ___safe_int((tu or {}).get("agent_count"))
    cc_daily = (cc or {}).get("cc_daily") or []
    cx_daily = (cx or {}).get("daily") or []
    oc_daily = (oc or {}).get("daily") or []
    cu_daily = (cu or {}).get("daily") or []
    tu_daily = (tu or {}).get("daily") or []
    active_day_set = _collect_active_days(cc_daily, cx_daily, oc_daily, cu_daily, tu_daily)
    total_days = len(active_day_set)
    cc_days = len(_collect_active_days(cc_daily))
    cx_days = len(_collect_active_days(cx_daily))
    oc_days = len(_collect_active_days(oc_daily))
    cu_days = len(_collect_active_days(cu_daily))
    tu_days = len(_collect_active_days(tu_daily))
    total_sessions = cc_sessions + cx_sessions + oc_sessions + cu_sessions + tu_sessions
    total_messages = cc_messages + cx_messages + oc_messages + cu_messages + tu_messages
    total_lines_added = cc_lines_added + cx_lines_added + oc_lines_added + cu_lines_added + tu_lines_added
    total_lines_removed = cc_lines_removed + cx_lines_removed + oc_lines_removed + cu_lines_removed + tu_lines_removed
    total_tokens = cc_tokens + cx_tokens + oc_tokens + cu_tokens + tu_tokens
    total_files = cc_files + cx_file_impact + oc_file_impact + cu_file_impact + tu_file_impact
    cc_msgs_per_day = round(cc_messages / cc_days, 1) if cc_days else 0
    cx_msgs_per_day = round(cx_messages / cx_days, 1) if cx_days else 0
    oc_msgs_per_day = round(oc_messages / oc_days, 1) if oc_days else 0
    cu_msgs_per_day = round(cu_messages / cu_days, 1) if cu_days else 0
    msgs_per_day = round(total_messages / total_days, 1) if total_days else 0
    return f"""<div class="merge-banner">
  <div class="merge-banner-head">Claude Code + Codex + OpenCode + Cursor + Trae 合并统计 · {escape(week)}</div>
  <div class="merge-banner-grid">
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_sessions}</div>
      <div class="merge-banner-label">总 SESSIONS</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_sessions}", f"CX {cx_sessions}", f"OC {oc_sessions}", f"CU {cu_sessions}", f"TU {tu_sessions}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{_fmt(total_tokens)}</div>
      <div class="merge-banner-label">总 Tokens</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {_fmt(cc_tokens)}", f"CX {_fmt(cx_tokens)}", f"OC {_fmt(oc_tokens)}", f"CU {_fmt(cu_tokens)}", f"TU {_fmt(tu_tokens)}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_messages}</div>
      <div class="merge-banner-label">总 MESSAGES</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_messages}", f"CX {cx_messages}", f"OC {oc_messages}", f"CU {cu_messages}", f"TU {tu_messages}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">+{total_lines_added:,}/-{total_lines_removed:,}</div>
      <div class="merge-banner-label">总 LINES</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC +{cc_lines_added:,}/-{cc_lines_removed:,}", f"CX +{cx_lines_added:,}/-{cx_lines_removed:,}", f"OC +{oc_lines_added:,}/-{oc_lines_removed:,}", f"CU +{cu_lines_added:,}/-{cu_lines_removed:,}", f"TU +{tu_lines_added:,}/-{tu_lines_removed:,}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_files}</div>
      <div class="merge-banner-label">总 FILES</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_files}", f"CX {cx_file_impact}", f"OC {oc_file_impact}", f"CU {cu_file_impact}", f"TU {tu_file_impact}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_days}</div>
      <div class="merge-banner-label">总 Days</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_days}", f"CX {cx_days}", f"OC {oc_days}", f"CU {cu_days}", f"TU {tu_days}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{msgs_per_day}</div>
      <div class="merge-banner-label">Messages/Day</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_msgs_per_day}", f"CX {cx_msgs_per_day}", f"OC {oc_msgs_per_day}", f"CU {cu_msgs_per_day}")}</div>
    </div>
  </div>
</div>"""


def _build_codex_section(cx):
    if not cx:
        return ""
    insights = cx.get("insights") or {}
    at_a_glance = insights.get("at_a_glance") or {}
    return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>Codex 深度洞察</h2>
  <p class="merge-section-sub">按行为模式重写，只分析工作方式、摩擦点和建议，不展示具体项目名。</p>
    </div>
    <div class="merge-section-metric">{___safe_int(cx.get("total_sessions"))} 会话 · {_fmt(cx.get("total_tokens"))} tokens</div>
  </div>

  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(at_a_glance.get("working", "暂无")))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(at_a_glance.get("hindering", "暂无")))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(at_a_glance.get("quick_win", "暂无")))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(at_a_glance.get("ambitious", "暂无")))}</div>
  </div>

  <h3 class="merge-subhead">What You Work On</h3>
  <div class="merge-grid merge-grid-2">
    {_render_work_on(insights.get("work_on") or [])}
  </div>

  <h3 class="merge-subhead">How You Use Codex</h3>
  <div class="merge-card merge-narrative">
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or [""])[0]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", ""])[1]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", "", ""])[2]))}</div>
    <div class="merge-key">{escape(str(insights.get("key_insight") or ""))}</div>
  </div>
  <div class="merge-grid merge-grid-2">
    {_render_usage_cards(insights.get("usage_cards") or [], limit=4)}
  </div>

  <div class="merge-grid merge-grid-4">
    <div class="merge-card">
      <div class="merge-card-title">活跃天数</div>
      <div class="merge-kpi">{___safe_int(cx.get("active_days"))}</div>
      <div class="merge-card-text">本周有 Codex 会话的天数</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">交互式会话</div>
      <div class="merge-kpi">{___safe_int(cx.get("interactive"))}</div>
      <div class="merge-card-text">需要人工审批/确认的会话数</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">全自动会话</div>
      <div class="merge-kpi">{___safe_int(cx.get("full_auto"))}</div>
      <div class="merge-card-text">approval=never 的会话数</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">主力模型</div>
      <div class="merge-kpi">{escape(str((insights.get("top_model") or {}).get("name") or ((cx.get("models") or [{}])[0].get("model") or "N/A")))}</div>
      <div class="merge-card-text">本周使用最重的 Codex 模型</div>
    </div>
  </div>

  <div class="merge-grid merge-grid-3">
    <div class="merge-card">
      <div class="merge-card-title">高频工具</div>
      <div class="merge-pill-row">{_render_pills(insights.get("top_tools") or [])}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">高频命令</div>
      <div class="merge-pill-row">{_render_pills(insights.get("top_commands") or [])}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">高频主题</div>
      <div class="merge-pill-row">{_render_pills(insights.get("top_topics") or [])}</div>
    </div>
  </div>

  <h3 class="merge-subhead">Impressive Things You Did</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("wins") or [])}</div>

  <h3 class="merge-subhead">Where Things Go Wrong</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("friction") or [])}</div>

  <h3 class="merge-subhead">Features to Try</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("features") or [])}</div>

  <h3 class="merge-subhead">New Ways to Use Codex</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("patterns") or [])}</div>

  <h3 class="merge-subhead">On the Horizon</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("horizon") or [])}</div>
</section>"""


def _build_opencode_section(oc):
    if not oc:
        return """<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>OpenCode 深度洞察</h2>
      <p class="merge-section-sub">本周仍保留空态区块，避免“看不到就像没这个工具”。</p>
    </div>
    <div class="merge-section-metric">无数据</div>
  </div>
  <div class="merge-card">
    <div class="merge-card-title">本周未采集到 OpenCode 会话</div>
    <div class="merge-card-text">这不代表你没用 OpenCode，只代表当前机器在本周没有可读的 OpenCode 本地数据。请检查 `opencode db path` 指向的数据库是否包含本周会话。</div>
  </div>
</section>"""
    source = oc.get("source") or {}
    return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>OpenCode 深度洞察</h2>
      <p class="merge-section-sub">按行为模式拉齐 OpenCode 洞察，不展示具体项目名，只看节奏、摩擦和建议。</p>
    </div>
    <div class="merge-section-metric">{___safe_int(oc.get("total_sessions"))} 会话 · {_fmt(oc.get("total_tokens"))} tokens</div>
  </div>
  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("working", "暂无")))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("hindering", "暂无")))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("quick_win", "暂无")))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("ambitious", "暂无")))}</div>
  </div>

  <h3 class="merge-subhead">What You Work On</h3>
  <div class="merge-grid merge-grid-2">{_render_work_on(((oc.get("insights") or {}).get("work_on") or []))}</div>

  <h3 class="merge-subhead">How You Use OpenCode</h3>
  <div class="merge-card merge-narrative">
    <div class="merge-card-text">{escape(str((((oc.get("insights") or {}).get("narrative_parts") or [""])[0])))}</div>
    <div class="merge-card-text">{escape(str((((oc.get("insights") or {}).get("narrative_parts") or ["", ""])[1])))}</div>
    <div class="merge-card-text">{escape(str((((oc.get("insights") or {}).get("narrative_parts") or ["", "", ""])[2])))}</div>
    <div class="merge-key">{escape(str(((oc.get("insights") or {}).get("key_insight") or "")) )}</div>
  </div>
  <div class="merge-grid merge-grid-2">{_render_usage_cards(((oc.get("insights") or {}).get("usage_cards") or []), limit=4)}</div>

  <div class="merge-grid merge-grid-3">
    <div class="merge-card">
      <div class="merge-card-title">总会话</div>
      <div class="merge-kpi">{___safe_int(oc.get("total_sessions"))}</div>
      <div class="merge-card-text">本周 OpenCode 会话总数</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">活跃天数</div>
      <div class="merge-kpi">{___safe_int(oc.get("active_days"))}</div>
      <div class="merge-card-text">基于 session 更新时间聚合</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">数据源</div>
      <div class="merge-kpi">{escape(str(source.get('db_path', 'N/A')))}</div>
      <div class="merge-card-text">本地 sqlite 快照</div>
    </div>
  </div>

  <h3 class="merge-subhead">Impressive Things You Did</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("wins") or []))}</div>

  <h3 class="merge-subhead">Where Things Go Wrong</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("friction") or []))}</div>

  <h3 class="merge-subhead">Features to Try</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("features") or []))}</div>

  <h3 class="merge-subhead">New Ways to Use OpenCode</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("patterns") or []))}</div>

  <h3 class="merge-subhead">On the Horizon</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("horizon") or []))}</div>
</section>"""



def _build_cursor_section(cu):
    if not cu:
        return """<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>Cursor 深度洞察</h2>
      <p class="merge-section-sub">本周仍保留空态区块，避免"看不到就像没这个工具"。</p>
    </div>
    <div class="merge-section-metric">无数据</div>
  </div>
  <div class="merge-card">
    <div class="merge-card-title">本周未采集到 Cursor 会话</div>
    <div class="merge-card-text">这不代表你没用 Cursor，只代表当前机器在本周没有可读的 Cursor 本地数据。请检查 state.vscdb 是否包含本周 composer 会话。</div>
  </div>
</section>"""
    insights = cu.get("insights") or {}
    at_a_glance = insights.get("at_a_glance") or {}
    return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>Cursor 深度洞察</h2>
      <p class="merge-section-sub">基于 composer 会话数据，分析 Cursor Agent/Chat 使用模式和代码落地情况。</p>
    </div>
    <div class="merge-section-metric">{___safe_int(cu.get("total_sessions"))} 会话 · {___safe_int(cu.get("total_messages"))} 消息</div>
  </div>
  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(at_a_glance.get("working", "暂无")))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(at_a_glance.get("hindering", "暂无")))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(at_a_glance.get("quick_win", "暂无")))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(at_a_glance.get("ambitious", "暂无")))}</div>
  </div>

  <h3 class="merge-subhead">What You Work On</h3>
  <div class="merge-grid merge-grid-2">{_render_work_on((insights.get("work_on") or []))}</div>

  <h3 class="merge-subhead">How You Use Cursor</h3>
  <div class="merge-card merge-narrative">
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or [""])[0]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", ""])[1]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", "", ""])[2]))}</div>
    <div class="merge-key">{escape(str(insights.get("key_insight") or ""))}</div>
  </div>
  <div class="merge-grid merge-grid-2">{_render_usage_cards((insights.get("usage_cards") or []), limit=4)}</div>

  <div class="merge-grid merge-grid-4">
    <div class="merge-card">
      <div class="merge-card-title">Agent 会话</div>
      <div class="merge-kpi">{___safe_int(cu.get("agent_count"))}</div>
      <div class="merge-card-text">Agent 模式会话数</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">Chat 会话</div>
      <div class="merge-kpi">{___safe_int(cu.get("chat_count"))}</div>
      <div class="merge-card-text">Chat 模式会话数</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">活跃天数</div>
      <div class="merge-kpi">{___safe_int(cu.get("active_days"))}</div>
      <div class="merge-card-text">本周有 Cursor 会话的天数</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">AI 代码块</div>
      <div class="merge-kpi">{___safe_int(cu.get("ai_hashes_count"))}</div>
      <div class="merge-card-text">AI 生成代码块数</div>
    </div>
  </div>

  <h3 class="merge-subhead">Impressive Things You Did</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("wins") or []))}</div>

  <h3 class="merge-subhead">Where Things Go Wrong</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("friction") or []))}</div>

  <h3 class="merge-subhead">Features to Try</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("features") or []))}</div>

  <h3 class="merge-subhead">New Ways to Use Cursor</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("patterns") or []))}</div>

  <h3 class="merge-subhead">On the Horizon</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("horizon") or []))}</div>
</section>"""


def _build_trae_section(tu):
    if not tu or not tu.get("total_sessions"):
        return """    <div class="merge-section">
      <h2>Trae</h2>
      <div class="merge-card">
        <div class="merge-card-title">No Trae sessions this week</div>
        <div class="merge-card-text">Check workspaceStorage for Trae session data.</div>
      </div>
    </div>"""

    total_sessions = ___safe_int(tu.get("total_sessions", 0))
    total_messages = ___safe_int(tu.get("total_messages", 0))
    agent_count = ___safe_int(tu.get("agent_count", 0))
    chat_count = ___safe_int(tu.get("chat_count", 0))
    areas = tu.get("areas", [])[:5]
    insights = tu.get("insights") or {}

    area_pills = "".join(
        f'<span class="merge-pill">{escape(str(a.get("cwd", "")))} <b>{a.get("sessions", "")}</b></span>'
        for a in areas
    ) or '<span class="merge-empty">N/A</span>'

    usage_cards = ""
    for item in (insights.get("usage_cards") or [])[:3]:
        title = escape(str(item.get("title", "")))
        value = escape(str(item.get("value", "")))
        desc = escape(str(item.get("desc", "")))
        usage_cards += '      <div class="merge-card">\n' \
            f'        <div class="merge-card-title">{title}</div>\n' \
            f'        <div class="merge-card-value">{value}</div>\n' \
            f'        <div class="merge-card-text">{desc}</div>\n' \
            '      </div>\n'

    wins_html = ""
    for item in (insights.get("wins") or [])[:3]:
        wins_html += f'<div class="merge-card"><div class="merge-card-title">{escape(str(item.get("title", "")))}</div><div class="merge-card-text">{escape(str(item.get("detail", "")))}</div></div>'

    section = f"""    <div class="merge-section">
      <h2>Trae</h2>
      <p class="merge-section-sub">WorkspaceStorage memento data, Builder/Chat mode analysis.</p>

      <div class="merge-stat-row">
        <div class="merge-stat">
          <div class="merge-stat-value">{total_sessions}</div>
          <div class="merge-stat-label">Sessions</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{total_messages}</div>
          <div class="merge-stat-label">Messages</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{agent_count}/{chat_count}</div>
          <div class="merge-stat-label">Builder/Chat</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{len(areas)}</div>
          <div class="merge-stat-label">Projects</div>
        </div>
      </div>

      <h3 class="merge-subhead">How You Use Trae</h3>
      <div class="merge-card-grid">
{usage_cards}
      </div>

      <h3 class="merge-subhead">Active Projects</h3>
      <div class="merge-pill-row">{area_pills}</div>
"""
    if wins_html:
        section += f"""      <h3 class="merge-subhead">Highlights</h3>
      <div class="merge-card-grid">
{wins_html}
      </div>
"""
    section += "    </div>"
    return section



def _build_merge_style():
    return """<style>
.merge-banner { margin: 0 0 24px; padding: 18px 22px; border-radius: 14px; border: 1px solid #c4b5fd; background: linear-gradient(135deg, #eef2ff 0%, #f5f3ff 100%); }
.merge-banner-head { font-size: 12px; font-weight: 700; color: #5b21b6; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; }
.merge-banner-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.merge-banner-item { background: rgba(255,255,255,0.7); border: 1px solid rgba(167,139,250,0.25); border-radius: 10px; padding: 12px; }
.merge-banner-value { font-size: 22px; font-weight: 700; color: #4c1d95; line-height: 1.2; word-break: break-word; }
.merge-banner-label { font-size: 11px; color: #6d28d9; text-transform: uppercase; margin-top: 2px; }
.merge-banner-sub { font-size: 11px; color: #7c3aed; margin-top: 4px; }
.merge-banner-subrow { line-height: 1.45; }
.merge-section { margin-top: 36px; padding-top: 6px; }
.merge-section-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
.merge-section-head-inline { margin-top: 28px; }
.merge-section-head h2 { margin: 0; }
.merge-section-sub { font-size: 13px; color: #64748b; margin-top: 4px; }
.merge-section-metric { font-size: 12px; font-weight: 600; color: #4338ca; background: #eef2ff; border-radius: 999px; padding: 6px 10px; white-space: nowrap; }
.merge-glance { background: linear-gradient(135deg, #fff7ed 0%, #ffedd5 100%); border: 1px solid #fdba74; border-radius: 14px; padding: 16px 18px; }
.merge-glance-item { font-size: 14px; color: #7c2d12; margin-bottom: 8px; line-height: 1.7; }
.merge-glance-item:last-child { margin-bottom: 0; }
.merge-subhead { margin-top: 22px; margin-bottom: 10px; font-size: 16px; color: #0f172a; }
.merge-grid { display: grid; gap: 12px; }
.merge-grid-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.merge-grid-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.merge-grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.merge-card { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px; }
.merge-card-title { font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }
.merge-card-text { font-size: 13px; color: #475569; line-height: 1.7; }
.merge-narrative { display: grid; gap: 10px; margin-bottom: 12px; }
.merge-key { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; padding: 12px 14px; font-size: 13px; color: #166534; }
.merge-split { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.merge-meta { font-size: 12px; color: #64748b; white-space: nowrap; }
.merge-pill-row { display: flex; flex-wrap: wrap; gap: 8px; }
.merge-pill { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; background: #eef2ff; color: #4338ca; padding: 5px 10px; font-size: 12px; }
.merge-pill b { color: #312e81; }
.merge-empty { font-size: 13px; color: #94a3b8; }
.merge-chip-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.merge-chip { display: inline-flex; align-items: center; padding: 4px 8px; border-radius: 999px; background: #f8fafc; color: #475569; font-size: 12px; }
.merge-kpi { font-size: 22px; font-weight: 700; color: #0f172a; margin-bottom: 4px; }
.merge-list { display: flex; flex-direction: column; gap: 8px; }
.merge-list-row, .merge-note { display: flex; justify-content: space-between; gap: 12px; padding: 10px 12px; border: 1px solid #e2e8f0; border-radius: 10px; background: #fff; font-size: 13px; color: #475569; }
.merge-note { justify-content: flex-start; }
.merge-table-wrap { overflow-x: auto; border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; }
.merge-table { width: 100%; border-collapse: collapse; min-width: 720px; }
.merge-table th, .merge-table td { padding: 12px 14px; border-bottom: 1px solid #f1f5f9; font-size: 13px; text-align: right; }
.merge-table th { color: #64748b; text-transform: uppercase; font-size: 11px; letter-spacing: 0.04em; }
.merge-table td:first-child, .merge-table th:first-child, .merge-table td:nth-child(2), .merge-table th:nth-child(2) { text-align: left; }
.merge-table tbody tr:last-child td { border-bottom: none; }
.raw-data { display: none; }
@media (max-width: 760px) {
  .merge-banner-grid, .merge-grid-2, .merge-grid-3, .merge-grid-4 { grid-template-columns: 1fr; }
  .merge-section-head { flex-direction: column; }
  .merge-list-row { flex-direction: column; }
}
</style>"""


def merge(claude_path, codex_path, opencode_path, cursor_path, trae_path, out_path, week):
    with open(claude_path, "r", encoding="utf-8") as f:
        html = f.read()

    cc_sessions, cc_stats = parse_claude_stats(html)
    cc_raw = parse_claude_report(claude_path) or {}
    cc_messages = ___safe_int(re.sub(r"[^\d]", "", cc_stats.get("Messages", "0")))
    cc_files = ___safe_int(re.sub(r"[^\d]", "", cc_stats.get("Files", "0")))
    cc_days = ___safe_int(re.sub(r"[^\d]", "", cc_stats.get("Days", "0")))
    cc_added, cc_removed = parse_lines_value(cc_stats.get("Lines", ""))
    if cc_raw:
        cc_messages = ___safe_int(cc_raw.get("cc_messages")) or cc_messages
        cc_sessions = ___safe_int(cc_raw.get("cc_sessions")) or cc_sessions
        cc_days = ___safe_int(cc_raw.get("cc_days")) or cc_days
    cc_raw.update({
        "cc_sessions": cc_sessions,
        "cc_messages": cc_messages,
        "cc_files": cc_files,
        "cc_days": cc_days,
        "cc_lines_added": cc_added,
        "cc_lines_removed": cc_removed,
    })

    cx = parse_codex_report(codex_path)
    oc = parse_opencode_report(opencode_path)
    cu = parse_cursor_report(cursor_path)
    tu = parse_trae_report(trae_path)

    style_block = _build_merge_style()
    banner = _build_combined_banner(week, cc_raw, cx, oc, cu, tu)
    codex_section = _build_codex_section(cx)
    cursor_section = _build_cursor_section(cu)
    trae_section = _build_trae_section(tu)
    opencode_section = _build_opencode_section(oc)
    has_claude_data = bool(
        ___safe_int(cc_raw.get("cc_sessions")) or ___safe_int(cc_raw.get("cc_messages")) or ___safe_int(cc_raw.get("cc_tokens"))
    )

    combined_raw = {
        "week": week,
        "cc_sessions": cc_sessions,
        "cc_messages": cc_messages,
        "cc_files": cc_files,
        "cc_days": cc_days,
        "cc_lines_added": cc_added,
        "cc_lines_removed": cc_removed,
        "cc_tokens": ___safe_int(cc_raw.get("cc_tokens")),
        "cx_sessions": ___safe_int((cx or {}).get("total_sessions")),
        "cx_tokens": ___safe_int((cx or {}).get("total_tokens")),
        "cx_days": ___safe_int((cx or {}).get("active_days")),
        "cx_data": cx,
        "oc_sessions": ___safe_int((oc or {}).get("total_sessions")),
        "oc_tokens": ___safe_int((oc or {}).get("total_tokens")),
        "oc_days": ___safe_int((oc or {}).get("active_days")),
        "oc_data": oc,
        "cu_sessions": ___safe_int((cu or {}).get("total_sessions")),
        "cu_days": ___safe_int((cu or {}).get("active_days")),
        "cu_data": cu,
        "tu_sessions": ___safe_int((tu or {}).get("total_sessions")),
        "tu_messages": ___safe_int((tu or {}).get("total_messages")),
        "tu_days": ___safe_int((tu or {}).get("active_days")),
        "tu_data": tu,
    }
    raw_block = f'<div class="raw-data" id="combined-raw-data">{json.dumps(combined_raw, ensure_ascii=False)}</div>'

    if "</head>" in html:
        html = html.replace("</head>", style_block + "\n</head>", 1)
    if has_claude_data:
        html = insert_at_container_top(html, banner)
    else:
        html = insert_at_container_top(html, banner)
        html = re.sub(
            r'(\s*<h1>Claude Code Insights</h1>[\s\S]*?)<div class="raw-data" id="claude-raw-data">[\s\S]*?</div>',
            '\n',
            html,
            count=1,
        )

    injection = "\n".join(part for part in [codex_section, opencode_section, cursor_section, trae_section, raw_block] if part)
    body_end = html.rfind("</body>")
    if body_end == -1:
        raise RuntimeError("无效的 Claude HTML：未找到 </body>")
    container_close = html.rfind("</div>", 0, body_end)
    if container_close == -1:
        raise RuntimeError("无效的 Claude HTML：未找到容器结束标签")
    html = html[:container_close] + "\n" + injection + "\n" + html[container_close:]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Combined report: {out_path}", file=sys.stderr)


if __name__ == "__main__":
    argc = len(sys.argv)
    # merge(claude, codex, opencode, cursor, trae, out, week) — 7 positional args
    if argc == 5:
        # 4 user args: claude, codex, out, week
        merge(sys.argv[1], sys.argv[2], "", "", "", sys.argv[3], sys.argv[4])
    elif argc == 6:
        # 5 user args: claude, codex, opencode, out, week
        merge(sys.argv[1], sys.argv[2], sys.argv[3], "", "", sys.argv[4], sys.argv[5])
    elif argc == 7:
        # 6 user args: claude, codex, opencode, cursor, out, week (no trae)
        merge(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], "", sys.argv[5], sys.argv[6])
    elif argc == 8:
        # 7 user args: claude, codex, opencode, cursor, trae, out, week
        merge(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7])
    else:
        print("Usage: merge_reports.py <claude.html> <codex.html> [opencode.html] [cursor.html] [trae.html] <out.html> <week>", file=sys.stderr)
        sys.exit(1)
