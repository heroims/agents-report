#!/usr/bin/env python3
"""以 Claude Code Insights HTML 为基础，嵌入 Codex/OpenCode 数据生成合并报告。"""

import datetime
import json
import os
import re
import sys
from html import escape
from i18n import T as _I18nT, _ZH_MAP
_LANG = _I18nT.detect()
_I18N = _I18nT(_LANG)
_t = _I18N
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


def parse_openclaw_report(path):
    return parse_embedded_json(path, "openclaw-raw-data")


def parse_hermes_report(path):
    return parse_embedded_json(path, "hermes-raw-data")


def parse_trae_cn_report(path):
    return parse_embedded_json(path, "trae-cn-raw-data")

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
        return '<span class="merge-empty">' + _t("N/A") + '</span>'
    pills = []
    for item in items[:limit]:
        name = escape(str(item.get(name_key, "")))
        count = escape(str(item.get(count_key, "")))
        pills.append(f'<span class="merge-pill">{name} <b>{count}</b></span>')
    return "".join(pills)


def _render_text_cards(items, limit=4):
    if not items:
        return f'<div class="merge-card"><div class="merge-card-title">{_t("N/A")}</div><div class="merge-card-text">{_t("Not enough samples to generate this block.")}</div></div>'
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
        return f'<div class="merge-card"><div class="merge-card-title">{_t("N/A")}</div><div class="merge-card-text">{_t("Not enough samples to generate this block.")}</div></div>'
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
        return f'<p class="merge-empty">{_t("No project distribution data for this period.")}</p>'
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
    <div class="merge-meta">{sessions} {_t("Sessions")} · {tokens} tokens</div>
  </div>
  <div class="merge-card-text">{desc}</div>
</div>"""
        )
    return "".join(blocks)


def _render_daily(items, limit=7):
    if not items:
        return f'<p class="merge-empty">{_t("No daily data for this period.")}</p>'
    blocks = []
    for item in items[:limit]:
        day = escape(str(item.get("day", "")))
        sessions = ___safe_int(item.get("sessions"))
        tokens = _fmt(item.get("tokens"))
        blocks.append(
            f"""<div class="merge-list-row">
  <span>{day}</span>
  <span>{sessions} {_t("Sessions")} · {tokens} tokens</span>
</div>"""
        )
    return "".join(blocks)


def _render_opencode_areas(items, limit=6):
    if not items:
        return f'<p class="merge-empty">{_t("No OpenCode project data for this period.")}</p>'
    blocks = []
    for item in items[:limit]:
        blocks.append(
            f"""<div class="merge-card">
  <div class="merge-split">
    <div class="merge-card-title">{escape(str(_path_label(item.get('cwd', '')) or item.get('cwd', '')))}</div>
    <div class="merge-meta">{___safe_int(item.get('sessions'))} {_t("Sessions")} · {_fmt(item.get('tokens'))} tokens</div>
  </div>
</div>"""
        )
    return "".join(blocks)


def _render_notes(notes):
    if not notes:
        return f'<p class="merge-empty">{_t("No collection notes.")}</p>'
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


def _build_combined_banner(week, cc, cx, oc, cu, tu, ol, hm, tc=None):
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
    ol_sessions = ___safe_int((ol or {}).get("total_sessions"))
    ol_reset_events = ___safe_int((ol or {}).get("reset_events"))
    ol_days = ___safe_int((ol or {}).get("active_days"))
    hm_sessions = ___safe_int((hm or {}).get("total_sessions"))
    hm_messages = ___safe_int((hm or {}).get("total_messages"))
    hm_tokens = ___safe_int((hm or {}).get("total_tokens"))
    hm_days = ___safe_int((hm or {}).get("active_days"))
    hm_tool_calls = ___safe_int((hm or {}).get("tool_call_count"))
    tc_sessions = ___safe_int((tc or {}).get("total_sessions"))
    tc_messages = ___safe_int((tc or {}).get("total_messages"))
    tc_agent = ___safe_int((tc or {}).get("agent_count"))
    cc_daily = (cc or {}).get("cc_daily") or []
    cx_daily = (cx or {}).get("daily") or []
    oc_daily = (oc or {}).get("daily") or []
    cu_daily = (cu or {}).get("daily") or []
    tu_daily = (tu or {}).get("daily") or []
    ol_daily = (ol or {}).get("daily") or []
    hm_daily = (hm or {}).get("daily") or []
    tc_daily = (tc or {}).get("daily") or []
    active_day_set = _collect_active_days(cc_daily, cx_daily, oc_daily, cu_daily, tu_daily, ol_daily, hm_daily, tc_daily)
    total_days = len(active_day_set)
    cc_days = len(_collect_active_days(cc_daily))
    cx_days = len(_collect_active_days(cx_daily))
    oc_days = len(_collect_active_days(oc_daily))
    cu_days = len(_collect_active_days(cu_daily))
    tu_days = len(_collect_active_days(tu_daily))
    ol_days = len(_collect_active_days(ol_daily))
    hm_days = len(_collect_active_days(hm_daily))
    total_sessions = cc_sessions + cx_sessions + oc_sessions + cu_sessions + tu_sessions + ol_sessions + hm_sessions + tc_sessions
    total_messages = cc_messages + cx_messages + oc_messages + cu_messages + tu_messages + hm_messages + tc_messages
    total_lines_added = cc_lines_added + cx_lines_added + oc_lines_added + cu_lines_added + tu_lines_added
    total_lines_removed = cc_lines_removed + cx_lines_removed + oc_lines_removed + cu_lines_removed + tu_lines_removed
    total_tokens = cc_tokens + cx_tokens + oc_tokens + cu_tokens + tu_tokens + hm_tokens
    total_files = cc_files + cx_file_impact + oc_file_impact + cu_file_impact + tu_file_impact
    cc_msgs_per_day = round(cc_messages / cc_days, 1) if cc_days else 0
    cx_msgs_per_day = round(cx_messages / cx_days, 1) if cx_days else 0
    oc_msgs_per_day = round(oc_messages / oc_days, 1) if oc_days else 0
    cu_msgs_per_day = round(cu_messages / cu_days, 1) if cu_days else 0
    msgs_per_day = round(total_messages / total_days, 1) if total_days else 0
    return f"""<div class="merge-banner">
  <div class="merge-banner-head">{_t("Combined Stats")} · {escape(week)}</div>
  <div class="merge-banner-grid">
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_sessions}</div>
      <div class="merge-banner-label">{_t("Sessions")}</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_sessions}", f"CX {cx_sessions}", f"OC {oc_sessions}", f"CU {cu_sessions}", f"TU {tu_sessions}", f"TC {tc_sessions}", f"OL {ol_sessions}", f"HM {hm_sessions}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{_fmt(total_tokens)}</div>
      <div class="merge-banner-label">{_t("Tokens")}</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {_fmt(cc_tokens)}", f"CX {_fmt(cx_tokens)}", f"OC {_fmt(oc_tokens)}", f"CU {_fmt(cu_tokens)}", f"TU {_fmt(tu_tokens)}", f"HM {_fmt(hm_tokens)}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_messages}</div>
      <div class="merge-banner-label">{_t("Messages")}</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_messages}", f"CX {cx_messages}", f"OC {oc_messages}", f"CU {cu_messages}", f"TU {tu_messages}", f"HM {hm_messages}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">+{total_lines_added:,}/-{total_lines_removed:,}</div>
      <div class="merge-banner-label">{_t("Lines")}</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC +{cc_lines_added:,}/-{cc_lines_removed:,}", f"CX +{cx_lines_added:,}/-{cx_lines_removed:,}", f"OC +{oc_lines_added:,}/-{oc_lines_removed:,}", f"CU +{cu_lines_added:,}/-{cu_lines_removed:,}", f"TU +{tu_lines_added:,}/-{tu_lines_removed:,}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_files}</div>
      <div class="merge-banner-label">{_t("Files")}</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_files}", f"CX {cx_file_impact}", f"OC {oc_file_impact}", f"CU {cu_file_impact}", f"TU {tu_file_impact}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{total_days}</div>
      <div class="merge-banner-label">{_t("Days")}</div>
      <div class="merge-banner-sub">{_banner_subrows(f"CC {cc_days}", f"CX {cx_days}", f"OC {oc_days}", f"CU {cu_days}", f"TU {tu_days}", f"OL {ol_days}", f"HM {hm_days}")}</div>
    </div>
    <div class="merge-banner-item">
      <div class="merge-banner-value">{msgs_per_day}</div>
      <div class="merge-banner-label">{_t("Message per Day")}</div>
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
      <h2>{_t("Codex Deep Insights")}</h2>
  <p class="merge-section-sub">{_t("Behavior-driven analysis of workflows, friction points, and suggestions.")}</p>
    </div>
    <div class="merge-section-metric">{___safe_int(cx.get("total_sessions"))} {_t("Sessions")} · {_fmt(cx.get("total_tokens"))} tokens</div>
  </div>

  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(at_a_glance.get("working", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(at_a_glance.get("hindering", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(at_a_glance.get("quick_win", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(at_a_glance.get("ambitious", _t("N/A"))))}</div>
  </div>

  <h3 class="merge-subhead">{_t("What You Work On")}</h3>
  <div class="merge-grid merge-grid-2">
    {_render_work_on(insights.get("work_on") or [])}
  </div>

  <h3 class="merge-subhead">{_t("How You Use Codex")}</h3>
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
      <div class="merge-card-title">{_t("Active Days")}</div>
      <div class="merge-kpi">{___safe_int(cx.get("active_days"))}</div>
      <div class="merge-card-text">{_t("Days with Codex sessions this period")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Interactive Sessions")}</div>
      <div class="merge-kpi">{___safe_int(cx.get("interactive"))}</div>
      <div class="merge-card-text">{_t("Sessions requiring manual approval")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Fully Automated Sessions")}</div>
      <div class="merge-kpi">{___safe_int(cx.get("full_auto"))}</div>
      <div class="merge-card-text">{_t("Sessions with approval=never")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Primary Model")}</div>
      <div class="merge-kpi">{escape(str((insights.get("top_model") or {}).get("name") or ((cx.get("models") or [{}])[0].get("model") or "N/A")))}</div>
      <div class="merge-card-text">{_t("Most used Codex model")}</div>
    </div>
  </div>

  <div class="merge-grid merge-grid-3">
    <div class="merge-card">
      <div class="merge-card-title">{_t("Top Tools")}</div>
      <div class="merge-pill-row">{_render_pills(insights.get("top_tools") or [])}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Top Commands")}</div>
      <div class="merge-pill-row">{_render_pills(insights.get("top_commands") or [])}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Top Topics")}</div>
      <div class="merge-pill-row">{_render_pills(insights.get("top_topics") or [])}</div>
    </div>
  </div>

  <h3 class="merge-subhead">{_t("Impressive Things You Did")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("wins") or [])}</div>

  <h3 class="merge-subhead">{_t("Where Things Go Wrong")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("friction") or [])}</div>

  <h3 class="merge-subhead">{_t("Features to Try")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("features") or [])}</div>

  <h3 class="merge-subhead">{_t("New Ways to Use Codex")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("patterns") or [])}</div>

  <h3 class="merge-subhead">{_t("On the Horizon")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("horizon") or [])}</div>
</section>"""


def _build_opencode_section(oc):
    if not oc:
        return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("OpenCode Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Empty placeholder block retained.")}</p>
    </div>
    <div class="merge-section-metric">{_t("No data")}</div>
  </div>
  <div class="merge-card">
    <div class="merge-card-title">{_t("No OpenCode sessions collected this period")}</div>
    <div class="merge-card-text">{_t("This does not mean you did not use OpenCode, only that no readable local data exists this period.")}</div>
  </div>
</section>"""
    source = oc.get("source") or {}
    return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("OpenCode Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Behavior-aligned OpenCode insights — focus on rhythm, friction, and suggestions.")}</p>
    </div>
    <div class="merge-section-metric">{___safe_int(oc.get("total_sessions"))} {_t("Sessions")} · {_fmt(oc.get("total_tokens"))} tokens</div>
  </div>
  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("working", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("hindering", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("quick_win", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(((oc.get("insights") or {}).get("at_a_glance") or {}).get("ambitious", _t("N/A"))))}</div>
  </div>

  <h3 class="merge-subhead">{_t("What You Work On")}</h3>
  <div class="merge-grid merge-grid-2">{_render_work_on(((oc.get("insights") or {}).get("work_on") or []))}</div>

  <h3 class="merge-subhead">{_t("How You Use OpenCode")}</h3>
  <div class="merge-card merge-narrative">
    <div class="merge-card-text">{escape(str((((oc.get("insights") or {}).get("narrative_parts") or [""])[0])))}</div>
    <div class="merge-card-text">{escape(str((((oc.get("insights") or {}).get("narrative_parts") or ["", ""])[1])))}</div>
    <div class="merge-card-text">{escape(str((((oc.get("insights") or {}).get("narrative_parts") or ["", "", ""])[2])))}</div>
    <div class="merge-key">{escape(str(((oc.get("insights") or {}).get("key_insight") or "")) )}</div>
  </div>
  <div class="merge-grid merge-grid-2">{_render_usage_cards(((oc.get("insights") or {}).get("usage_cards") or []), limit=4)}</div>

  <div class="merge-grid merge-grid-3">
    <div class="merge-card">
      <div class="merge-card-title">{_t("Total Sessions")}</div>
      <div class="merge-kpi">{___safe_int(oc.get("total_sessions"))}</div>
      <div class="merge-card-text">{_t("Total OpenCode sessions this period")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Active Days")}</div>
      <div class="merge-kpi">{___safe_int(oc.get("active_days"))}</div>
      <div class="merge-card-text">{_t("Aggregated from session update times")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Data Source")}</div>
      <div class="merge-kpi">{escape(str(source.get('db_path', 'N/A')))}</div>
      <div class="merge-card-text">{_t("Local sqlite snapshot")}</div>
    </div>
  </div>

  <h3 class="merge-subhead">{_t("Impressive Things You Did")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("wins") or []))}</div>

  <h3 class="merge-subhead">{_t("Where Things Go Wrong")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("friction") or []))}</div>

  <h3 class="merge-subhead">{_t("Features to Try")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("features") or []))}</div>

  <h3 class="merge-subhead">{_t("New Ways to Use OpenCode")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("patterns") or []))}</div>

  <h3 class="merge-subhead">{_t("On the Horizon")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(((oc.get("insights") or {}).get("horizon") or []))}</div>
</section>"""



def _build_cursor_section(cu):
    if not cu:
        return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("Cursor Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Empty placeholder block retained.")}</p>
    </div>
    <div class="merge-section-metric">{_t("No data")}</div>
  </div>
  <div class="merge-card">
    <div class="merge-card-title">{_t("No Cursor sessions collected this period")}</div>
    <div class="merge-card-text">{_t("This does not mean you did not use Cursor, only that no readable local data exists this period.")}</div>
  </div>
</section>"""
    insights = cu.get("insights") or {}
    at_a_glance = insights.get("at_a_glance") or {}
    return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("Cursor Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Agent/Chat usage and code delivery analysis from composer session data.")}</p>
    </div>
    <div class="merge-section-metric">{___safe_int(cu.get("total_sessions"))} {_t("Sessions")} · {___safe_int(cu.get("total_messages"))} {_t("Messages")}</div>
  </div>
  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(at_a_glance.get("working", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(at_a_glance.get("hindering", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(at_a_glance.get("quick_win", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(at_a_glance.get("ambitious", _t("N/A"))))}</div>
  </div>

  <h3 class="merge-subhead">{_t("What You Work On")}</h3>
  <div class="merge-grid merge-grid-2">{_render_work_on((insights.get("work_on") or []))}</div>

  <h3 class="merge-subhead">{_t("How You Use Cursor")}</h3>
  <div class="merge-card merge-narrative">
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or [""])[0]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", ""])[1]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", "", ""])[2]))}</div>
    <div class="merge-key">{escape(str(insights.get("key_insight") or ""))}</div>
  </div>
  <div class="merge-grid merge-grid-2">{_render_usage_cards((insights.get("usage_cards") or []), limit=4)}</div>

  <div class="merge-grid merge-grid-4">
    <div class="merge-card">
      <div class="merge-card-title">{_t("Agent Sessions")}</div>
      <div class="merge-kpi">{___safe_int(cu.get("agent_count"))}</div>
      <div class="merge-card-text">{_t("Agent mode session count")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Chat Sessions")}</div>
      <div class="merge-kpi">{___safe_int(cu.get("chat_count"))}</div>
      <div class="merge-card-text">{_t("Chat mode session count")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Active Days")}</div>
      <div class="merge-kpi">{___safe_int(cu.get("active_days"))}</div>
      <div class="merge-card-text">{_t("Days with Cursor sessions this period")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("AI Code Blocks")}</div>
      <div class="merge-kpi">{___safe_int(cu.get("ai_hashes_count"))}</div>
      <div class="merge-card-text">{_t("AI-generated code block count")}</div>
    </div>
  </div>

  <h3 class="merge-subhead">{_t("Impressive Things You Did")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("wins") or []))}</div>

  <h3 class="merge-subhead">{_t("Where Things Go Wrong")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("friction") or []))}</div>

  <h3 class="merge-subhead">{_t("Features to Try")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("features") or []))}</div>

  <h3 class="merge-subhead">{_t("New Ways to Use Cursor")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("patterns") or []))}</div>

  <h3 class="merge-subhead">{_t("On the Horizon")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards((insights.get("horizon") or []))}</div>
</section>"""


def _build_trae_section(tu):
    if not tu or not tu.get("total_sessions"):
        return f"""    <div class="merge-section">
      <h2>Trae</h2>
      <div class="merge-card">
        <div class="merge-card-title">{_t("No Trae sessions this week")}</div>
        <div class="merge-card-text">{_t("Check workspaceStorage for Trae session data.")}</div>
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
    ) or f'<span class="merge-empty">{_t("N/A")}</span>'

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
      <p class="merge-section-sub">{_t("WorkspaceStorage memento data, Builder/Chat mode analysis.")}</p>

      <div class="merge-stat-row">
        <div class="merge-stat">
          <div class="merge-stat-value">{total_sessions}</div>
          <div class="merge-stat-label">{_t("Sessions")}</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{total_messages}</div>
          <div class="merge-stat-label">{_t("Messages")}</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{agent_count}/{chat_count}</div>
          <div class="merge-stat-label">{_t("Builder/Chat")}</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{len(areas)}</div>
          <div class="merge-stat-label">{_t("Projects")}</div>
        </div>
      </div>

      <h3 class="merge-subhead">{_t("How You Use Trae")}</h3>
      <div class="merge-grid merge-grid-3">
{usage_cards}
      </div>

      <h3 class="merge-subhead">{_t("Active Projects")}</h3>
      <div class="merge-pill-row">{area_pills}</div>
"""
    if wins_html:
        section += f"""      <h3 class="merge-subhead">{_t("Highlights")}</h3>
      <div class="merge-grid merge-grid-3">
{wins_html}
      </div>
"""
    section += "    </div>"
    return section





def _build_trae_cn_section(tu):
    if not tu or not tu.get("total_sessions"):
        return f"""
    <div class="merge-section">
      <h2>Trae CN</h2>
      <div class="merge-card">
        <div class="merge-card-title">{_t("No Trae CN sessions this week")}</div>
        <div class="merge-card-text">{_t("Check workspaceStorage for Trae CN session data.")}</div>
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
    ) or f'<span class="merge-empty">{_t("N/A")}</span>'

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

    section = f"""
    <div class="merge-section">
      <h2>Trae CN</h2>
      <p class="merge-section-sub">{_t("WorkspaceStorage memento data, Builder/Chat mode analysis.")}</p>

      <div class="merge-stat-row">
        <div class="merge-stat">
          <div class="merge-stat-value">{total_sessions}</div>
          <div class="merge-stat-label">{_t("Sessions")}</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{total_messages}</div>
          <div class="merge-stat-label">{_t("Messages")}</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{agent_count}/{chat_count}</div>
          <div class="merge-stat-label">{_t("Builder/Chat")}</div>
        </div>
        <div class="merge-stat">
          <div class="merge-stat-value">{len(areas)}</div>
          <div class="merge-stat-label">{_t("Projects")}</div>
        </div>
      </div>

      <h3 class="merge-subhead">{_t("How You Use Trae CN")}</h3>
      <div class="merge-grid merge-grid-3">
{usage_cards}
      </div>

      <h3 class="merge-subhead">{_t("Active Projects")}</h3>
      <div class="merge-pill-row">{area_pills}</div>
"""
    if wins_html:
        section += f"""
      <h3 class="merge-subhead">{_t("Highlights")}</h3>
      <div class="merge-grid merge-grid-3">
{wins_html}
      </div>
"""
    section += "    </div>"
    return section
def _build_openclaw_section(ol):
    if not ol:
        return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("OpenClaw Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Empty placeholder block retained.")}</p>
    </div>
    <div class="merge-section-metric">{_t("No data")}</div>
  </div>
  <div class="merge-card">
    <div class="merge-card-title">{_t("No OpenClaw sessions collected this period")}</div>
    <div class="merge-card-text">{_t("This does not mean you did not use OpenClaw, only that no readable local data exists this period.")}</div>
  </div>
</section>"""
    insights = ol.get("insights") or {}
    at_a_glance = insights.get("at_a_glance") or {}
    agents = ol.get("agents") or []
    daily = ol.get("daily") or []

    agent_pills = "".join(
        f'<span class="merge-pill">{escape(str(a.get("name", "")))} <b>{a.get("sessions", "")}</b></span>'
        for a in agents[:6]
    ) or '<span class="merge-empty">' + _t("N/A") + '</span>'

    return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("OpenClaw Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Multi-agent orchestration patterns from commands.log session events.")}</p>
    </div>
    <div class="merge-section-metric">{___safe_int(ol.get("total_sessions"))} {_t("Sessions")} · {___safe_int(ol.get("active_days"))} {_t("Days")}</div>
  </div>
  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(at_a_glance.get("working", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(at_a_glance.get("hindering", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(at_a_glance.get("quick_win", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(at_a_glance.get("ambitious", _t("N/A"))))}</div>
  </div>

  <h3 class="merge-subhead">{_t("Agent Distribution")}</h3>
  <div class="merge-pill-row">{agent_pills}</div>

  <h3 class="merge-subhead">{_t("How You Use OpenClaw")}</h3>
  <div class="merge-card merge-narrative">
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or [""])[0]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", ""])[1]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", "", ""])[2]))}</div>
    <div class="merge-key">{escape(str(insights.get("key_insight") or ""))}</div>
  </div>
  <div class="merge-grid merge-grid-2">{_render_usage_cards(insights.get("usage_cards") or [], limit=4)}</div>

  <div class="merge-grid merge-grid-3">
    <div class="merge-card">
      <div class="merge-card-title">{_t("New Sessions")}</div>
      <div class="merge-kpi">{___safe_int(ol.get("total_sessions"))}</div>
      <div class="merge-card-text">{_t("New OpenClaw sessions this period")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Active Days")}</div>
      <div class="merge-kpi">{___safe_int(ol.get("active_days"))}</div>
      <div class="merge-card-text">{_t("OpenClaw session coverage days")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Reset Events")}</div>
      <div class="merge-kpi">{___safe_int(ol.get("reset_events"))}</div>
      <div class="merge-card-text">{_t("Session reset count")}</div>
    </div>
  </div>

  <h3 class="merge-subhead">{_t("Impressive Things You Did")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("wins") or [])}</div>

  <h3 class="merge-subhead">{_t("Where Things Go Wrong")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("friction") or [])}</div>

  <h3 class="merge-subhead">{_t("Features to Try")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("features") or [])}</div>

  <h3 class="merge-subhead">{_t("New Ways to Use OpenClaw")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("patterns") or [])}</div>

  <h3 class="merge-subhead">{_t("On the Horizon")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("horizon") or [])}</div>
</section>"""


def _build_hermes_section(hm):
    if not hm:
        return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("Hermes Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Empty placeholder block retained.")}</p>
    </div>
    <div class="merge-section-metric">{_t("No data")}</div>
  </div>
  <div class="merge-card">
    <div class="merge-card-title">{_t("No Hermes sessions collected this period")}</div>
    <div class="merge-card-text">{_t("This does not mean you did not use Hermes, only that no readable local data exists this period.")}</div>
  </div>
</section>"""
    insights = hm.get("insights") or {}
    at_a_glance = insights.get("at_a_glance") or {}
    models = hm.get("models") or []
    top_tools = hm.get("top_tools") or []

    return f"""<section class="merge-section">
  <div class="merge-section-head">
    <div>
      <h2>{_t("Hermes Deep Insights")}</h2>
      <p class="merge-section-sub">{_t("Model usage, tool calls, and execution patterns from state.db session data.")}</p>
    </div>
    <div class="merge-section-metric">{___safe_int(hm.get("total_sessions"))} {_t("Sessions")} · {_fmt(hm.get("total_tokens"))} tokens</div>
  </div>
  <div class="merge-glance">
    <div class="merge-glance-item"><strong>What's working:</strong> {escape(str(at_a_glance.get("working", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>What's hindering you:</strong> {escape(str(at_a_glance.get("hindering", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>Quick wins to try:</strong> {escape(str(at_a_glance.get("quick_win", _t("N/A"))))}</div>
    <div class="merge-glance-item"><strong>On the horizon:</strong> {escape(str(at_a_glance.get("ambitious", _t("N/A"))))}</div>
  </div>

  <h3 class="merge-subhead">{_t("How You Use Hermes")}</h3>
  <div class="merge-card merge-narrative">
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or [""])[0]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", ""])[1]))}</div>
    <div class="merge-card-text">{escape(str((insights.get("narrative_parts") or ["", "", ""])[2]))}</div>
    <div class="merge-key">{escape(str(insights.get("key_insight") or ""))}</div>
  </div>
  <div class="merge-grid merge-grid-2">{_render_usage_cards(insights.get("usage_cards") or [], limit=4)}</div>

  <div class="merge-grid merge-grid-4">
    <div class="merge-card">
      <div class="merge-card-title">{_t("Model")}</div>
      <div class="merge-kpi">{escape(str((models[0] or {}).get("model", "N/A") if models else "N/A"))}</div>
      <div class="merge-card-text">{_t("Primary Model")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Tool Calls")}</div>
      <div class="merge-kpi">{___safe_int(hm.get("tool_call_count"))}</div>
      <div class="merge-card-text">{_t("Total tool calls this period")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Token Usage")}</div>
      <div class="merge-kpi">{_fmt(hm.get("total_tokens"))}</div>
      <div class="merge-card-text">{_t("Input+Output tokens")}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Active Days")}</div>
      <div class="merge-kpi">{___safe_int(hm.get("active_days"))}</div>
      <div class="merge-card-text">{_t("Based on session start time")}</div>
    </div>
  </div>

  <div class="merge-grid merge-grid-3">
    <div class="merge-card">
      <div class="merge-card-title">{_t("Model Distribution")}</div>
      <div class="merge-pill-row">{_render_pills(models, "model", "sessions", limit=6)}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Top Tools")}</div>
      <div class="merge-pill-row">{_render_pills(top_tools or [], "name", "count", limit=8)}</div>
    </div>
    <div class="merge-card">
      <div class="merge-card-title">{_t("Data Source")}</div>
      <div class="merge-pill-row"><span class="merge-pill">state.db</span></div>
    </div>
  </div>

  <h3 class="merge-subhead">{_t("Impressive Things You Did")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("wins") or [])}</div>

  <h3 class="merge-subhead">{_t("Where Things Go Wrong")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("friction") or [])}</div>

  <h3 class="merge-subhead">{_t("Features to Try")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("features") or [])}</div>

  <h3 class="merge-subhead">{_t("New Ways to Use Hermes")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("patterns") or [])}</div>

  <h3 class="merge-subhead">{_t("On the Horizon")}</h3>
  <div class="merge-grid merge-grid-2">{_render_text_cards(insights.get("horizon") or [])}</div>
</section>"""


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
.merge-stat-row { display: flex; gap: 24px; margin-bottom: 20px; flex-wrap: wrap; }
.merge-stat { text-align: center; }
.merge-stat-value { font-size: 24px; font-weight: 700; color: #0f172a; }
.merge-stat-label { font-size: 11px; color: #64748b; text-transform: uppercase; }
.merge-card-value { font-size: 18px; font-weight: 600; color: #334155; margin-bottom: 4px; }
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


def merge(claude_path, codex_path, opencode_path, cursor_path, trae_path, openclaw_path=None, hermes_path=None, trae_cn_path=None, out_path=None, week=None):
    # Handle backwards compat: openclaw_path and hermes_path might be passed as out_path/week
    # This handles the case where old code calls with 7 positional args
    if out_path is None:
        # Called as merge(claude, codex, opencode, cursor, trae, out, week) - old style
        out_path, week = trae_path, openclaw_path
        trae_path, openclaw_path = cursor_path, None
        cursor_path, hermes_path = opencode_path, None
        opencode_path = openclaw_path or ""
        openclaw_path = ""
        hermes_path = ""
    elif trae_cn_path is None:
        # Called with 9 args (before trae_cn was added)
        trae_cn_path = ""
        if hermes_path is None:
            hermes_path = ""
    elif hermes_path is None:
        hermes_path = ""
    if not out_path:
        out_path = ""
    if not week:
        week = "unknown"
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
    ol = parse_openclaw_report(openclaw_path)
    hm = parse_hermes_report(hermes_path)
    tc = parse_trae_cn_report(trae_cn_path)

    style_block = _build_merge_style()
    banner = _build_combined_banner(week, cc_raw, cx, oc, cu, tu, ol, hm, tc)
    codex_section = _build_codex_section(cx)
    cursor_section = _build_cursor_section(cu)
    trae_section = _build_trae_section(tu)
    opencode_section = _build_opencode_section(oc)
    openclaw_section = _build_openclaw_section(ol)
    trae_cn_section = _build_trae_cn_section(tc)
    hermes_section = _build_hermes_section(hm)
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
        "ol_sessions": ___safe_int((ol or {}).get("total_sessions")),
        "ol_days": ___safe_int((ol or {}).get("active_days")),
        "ol_data": ol,
        "hm_sessions": ___safe_int((hm or {}).get("total_sessions")),
        "hm_tokens": ___safe_int((hm or {}).get("total_tokens")),
        "hm_days": ___safe_int((hm or {}).get("active_days")),
        "hm_data": hm,
        "tc_sessions": ___safe_int((tc or {}).get("total_sessions")),
        "tc_messages": ___safe_int((tc or {}).get("total_messages")),
        "tc_days": ___safe_int((tc or {}).get("active_days")),
        "tc_agent_count": ___safe_int((tc or {}).get("agent_count")),
        "tc_data": tc,
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

    injection = "\n".join(part for part in [codex_section, opencode_section, cursor_section, trae_section, openclaw_section, hermes_section, trae_cn_section, raw_block] if part)
    body_end = html.rfind("</body>")
    if body_end == -1:
        raise RuntimeError({_t("Invalid Claude HTML: </body> tag not found")})
    container_close = html.rfind("</div>", 0, body_end)
    if container_close == -1:
        raise RuntimeError({_t("Invalid Claude HTML: container closing tag not found")})
    html = html[:container_close] + "\n" + injection + "\n" + html[container_close:]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    
    # Language post-processing
    lang = os.environ.get("AGENTS_REPORT_LANG", "zh")
    
    # Set lang attribute on <html> tag
    lang_attr = "zh-CN" if lang == "zh" else "en"
    html = html.replace("<html>", f'<html lang="{lang_attr}">', 1)
    
    # Apply language post-processing (ZH: EN→ZH for Claude headers, EN: full ZH→EN)
    translated = translate_html(html, lang)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(translated)

    print(f"Combined report: {out_path}", file=sys.stderr)


def translate_html(html, lang="zh"):
    """Post-process HTML to match target language."""
    if lang == "zh":
        # Apply EN→ZH replacements for known English headers from Claude Code insights
        _EN_TO_ZH = {k: v for k, v in _ZH_MAP.items()
                      if len(k) >= 4 and not any('\u4e00' <= c <= '\u9fff' for c in k)
                      and len(v) >= 2 and any('\u4e00' <= c <= '\u9fff' for c in v)}
        for en_phrase, zh_phrase in sorted(_EN_TO_ZH.items(), key=lambda x: -len(x[0])):
            html = html.replace(en_phrase, zh_phrase)
        return html
    # Build ZH→EN map from both directions in _ZH_MAP:
    #   Direction A: EN key → ZH value  (reverse: ZH→EN)
    #   Direction B: ZH key → EN value  (direct: ZH→EN)
    _EN = {}
    for k, v in _ZH_MAP.items():
        # Direction A: EN key → ZH value — reverse to ZH→EN
        if len(v) >= 2 and any('\u4e00' <= c <= '\u9fff' for c in v):
            _EN[v] = k
        # Direction B: ZH key → EN value — use directly (wins on collision)
        if len(k) >= 3 and any('\u4e00' <= c <= '\u9fff' for c in k):
            _EN[k] = v
    
    # Build templates from both ZH→EN directions
    _TEMPLATES = {}
    for k, v in _ZH_MAP.items():
        if "{" in k and "}" in k and any('\u4e00' <= c <= '\u9fff' for c in k):
            _TEMPLATES[k] = v  # ZH key with placeholders → EN value
        if "{" in v and "}" in v and any('\u4e00' <= c <= '\u9fff' for c in v):
            _TEMPLATES[v] = k  # ZH value with placeholders → EN key
    for zh_template, en_template in sorted(_TEMPLATES.items(), key=lambda x: -len(x[0])):
        try:
            parts = re.split(r"(\{[^}]+\})", zh_template)
            regex_parts = []
            var_names = []
            for part in parts:
                if part.startswith("{") and part.endswith("}"):
                    var_names.append(part[1:-1])
                    regex_parts.append(r"([\d.,a-zA-Z_\u4e00-\u9fff \-]+)")
                else:
                    regex_parts.append(re.escape(part))
            pattern = "".join(regex_parts)
            replacement = en_template
            for i, vname in enumerate(var_names):
                replacement = replacement.replace("{" + vname + "}", "\\" + str(i + 1))
            html = re.sub(pattern, replacement, html)
        except Exception:
            pass
    
    # Phase 2: Exact string replacement with Chinese-quote normalization
    # HTML text nodes may have "“看不到”" but _ZH_MAP has "看不到"
    # Strategy: for each ZH phrase not found in HTML, build a regex that allows
    # optional Chinese quotes between CJK characters and try matching
    _QUOTE_RE = re.compile(r'[“”‘’「」]')
    for zh_phrase, en_phrase in sorted(_EN.items(), key=lambda x: -len(x[0])):
        if zh_phrase in html:
            html = html.replace(zh_phrase, en_phrase)
        elif any('一' <= c <= '鿿' for c in zh_phrase):
            # Build regex: allow optional Chinese quotes between any CJK chars
            pattern_parts = []
            for c in zh_phrase:
                pattern_parts.append(re.escape(c))
                if '一' <= c <= '鿿':
                    pattern_parts.append(r'[“”‘’「」]*')
            pattern = ''.join(pattern_parts)
            try:
                html = re.sub(pattern, en_phrase, html)
            except Exception:
                pass
    
    return html

if __name__ == "__main__":
    argc = len(sys.argv)
    # merge(claude, codex, opencode, cursor, trae, openclaw, hermes, out, week) — 9 positional args
    if argc == 5:
        merge(sys.argv[1], sys.argv[2], "", "", "", "", "", sys.argv[3], sys.argv[4])
    elif argc == 6:
        merge(sys.argv[1], sys.argv[2], sys.argv[3], "", "", "", "", sys.argv[4], sys.argv[5])
    elif argc == 7:
        merge(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], "", "", "", sys.argv[5], sys.argv[6])
    elif argc == 8:
        merge(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], "", "", sys.argv[6], sys.argv[7])
    elif argc == 9:
        merge(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], "", sys.argv[7], sys.argv[8])
    elif argc == 10:
        merge(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7], "", sys.argv[8], sys.argv[9])
    elif argc == 11:
        merge(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7], sys.argv[8], sys.argv[9], sys.argv[10])
    else:
        print("Usage: merge_reports.py <claude.html> <codex.html> [opencode.html] [cursor.html] [trae.html] [openclaw.html] [hermes.html] [trae_cn.html] <out.html> <week>", file=sys.stderr)
        sys.exit(1)
