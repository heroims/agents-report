#!/usr/bin/env python3
"""解析团队 Claude Code Insights 报告，生成汇总分析页面（支持周/月/季/年对比）。"""

import datetime
import json
import sys

import os
import re
from collections import Counter
from html import unescape
from html.parser import HTMLParser

import sys as _sys
_scripts_dir = str((__import__('pathlib').Path(__file__).resolve().parent))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
from period_utils import detect_period_type, parse_filename, period_label as plabel, period_sort_key, period_type_arg, current_period, period_start_end
from i18n import T as _I18nT
_LANG = _I18nT.detect()
_I18N = _I18nT(_LANG)

def set_lang(lang):
    global _LANG, _I18N
    _LANG = lang
    _I18N = _I18nT(lang)



class InsightsParser(HTMLParser):
    """从 insights HTML 中提取结构化数据。"""

    def __init__(self):
        super().__init__()
        self.data = {
            "stats": {},        # Messages, Lines, Files, Days, Msgs/Day
            "subtitle": "",     # 时间范围
            "areas": [],        # [{name, count}]
            "charts": {},       # {chart_title: [{label, value}]}
            "usage_narrative": "",
            "key_pattern": "",
        }
        # parser state
        self._tag_stack = []
        self._class_stack = []
        self._in_stat = False
        self._stat_values = []
        self._stat_labels = []
        self._current_stat_class = None
        self._in_area_name = False
        self._in_area_count = False
        self._in_area_desc = False
        self._in_subtitle = False
        self._in_chart_title = False
        self._current_chart_title = None
        self._in_bar_label = False
        self._in_bar_value = False
        self._current_bar_label = None
        self._in_narrative = False
        self._narrative_depth = 0
        self._in_key_insight = False
        self._in_key_insight_depth = 0
        self._in_raw_data = False
        self._raw_data_chars = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        self._tag_stack.append(tag)
        self._class_stack.append(cls)

        if "subtitle" in cls:
            self._in_subtitle = True
        elif "stat-value" in cls:
            self._current_stat_class = "value"
        elif "stat-label" in cls:
            self._current_stat_class = "label"
        elif "area-name" in cls:
            self._in_area_name = True
        elif "area-count" in cls:
            self._in_area_count = True
        elif "area-desc" in cls:
            self._in_area_desc = True
        elif "chart-title" in cls:
            self._in_chart_title = True
        elif "bar-label" in cls:
            self._in_bar_label = True
        elif "bar-value" in cls:
            self._in_bar_value = True
        elif "key-insight" in cls:
            self._in_key_insight = True
            self._key_insight_depth = len(self._tag_stack)
        elif "raw-data" in cls or "codex-raw-data" in cls or attrs_dict.get("id", "").endswith("raw-data"):
            self._in_raw_data = True

    def handle_endtag(self, tag):
        if self._tag_stack:
            self._tag_stack.pop()
        if self._class_stack:
            self._class_stack.pop()
        if self._in_narrative and len(self._tag_stack) < self._narrative_depth:
            self._in_narrative = False
        if self._in_key_insight and len(self._tag_stack) < self._in_key_insight_depth:
            self._in_key_insight = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return

        if self._in_subtitle:
            self.data["subtitle"] = text
            self._in_subtitle = False
        elif self._current_stat_class == "value":
            self._stat_values.append(text)
            self._current_stat_class = None
        elif self._current_stat_class == "label":
            self._stat_labels.append(text)
            self._current_stat_class = None
        elif self._in_area_name:
            self.data["areas"].append({"name": text, "count": "", "desc": ""})
            self._in_area_name = False
        elif self._in_area_count:
            if self.data["areas"]:
                self.data["areas"][-1]["count"] = text
            self._in_area_count = False
        elif self._in_area_desc:
            if self.data["areas"]:
                if self.data["areas"][-1]["desc"]:
                    self.data["areas"][-1]["desc"] += " "
                self.data["areas"][-1]["desc"] += text
            self._in_area_desc = False
        elif self._in_chart_title:
            self._current_chart_title = text
            self.data["charts"][text] = []
            self._in_chart_title = False
        elif self._in_bar_label:
            self._current_bar_label = text
            self._in_bar_label = False
        elif self._in_bar_value:
            if self._current_chart_title and self._current_bar_label:
                self.data["charts"][self._current_chart_title].append({
                    "label": self._current_bar_label,
                    "value": text,
                })
            self._current_bar_label = None
            self._in_bar_value = False
        elif self._in_key_insight:
            self.data["key_pattern"] += text + " "
        elif self._in_raw_data:
            self._raw_data_chars.append(text)
        elif self._in_narrative:
            self.data["usage_narrative"] += text + " "

    def finalize(self):
        for val, label in zip(self._stat_values, self._stat_labels):
            self.data["stats"][label] = val
        self.data["key_pattern"] = self.data["key_pattern"].strip()
        if self.data["key_pattern"].startswith("Key pattern:"):
            self.data["key_pattern"] = self.data["key_pattern"][len("Key pattern:"):].strip()
        self.data["usage_narrative"] = self.data["usage_narrative"].strip()
        return self.data


def _extract_raw_data(html):
    """Extract JSON from raw-data divs with regex (works regardless of parser depth issues)."""
    # Try most specific pattern first (combined-raw-data from merged reports)
    for id_name in ["combined-raw-data", "codex-raw-data", "opencode-raw-data"]:
        pattern = rf'id="{re.escape(id_name)}"[^>]*>([\s\S]*?)</div>'
        m = re.search(pattern, html)
        if m:
            raw_str = m.group(1).strip()
            if raw_str.startswith("{"):
                try:
                    return json.loads(raw_str)
                except Exception:
                    pass
    return None


def parse_report(filepath):
    """解析单个 insights HTML 文件。"""
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()
    parser = InsightsParser()
    parser.feed(html)
    data = parser.finalize()
    data["sections"] = _extract_report_sections(html)
    # Extract embedded JSON data (e.g., combined-raw-data from merged reports)
    raw_data = _extract_raw_data(html)
    if raw_data:
        data["raw"] = raw_data
    return data


def _strip_html_to_text(raw_html):
    text = re.sub(r"<!--.*?-->", " ", raw_html, flags=re.S)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"</(p|div|li|h3|h4|tr|section)>", "\n", text, flags=re.I)
    text = re.sub(r"<br\\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    cleaned = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s == "Copy":
            continue
        if re.fullmatch(r"[\d,]+", s):
            continue
        cleaned.append(s)
    return "\n".join(cleaned)


def _extract_report_sections(html):
    """提取 report 中按 h2 划分的完整章节文本。"""
    matches = list(re.finditer(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', html, flags=re.S | re.I))
    sections = []
    for i, m in enumerate(matches):
        sec_id = m.group(1).strip()
        title = _strip_html_to_text(m.group(2))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        body_html = html[start:end]
        body_text = _strip_html_to_text(body_html)
        if body_text:
            sections.append({"id": sec_id, "title": title, "content": body_text})
    return sections


def parse_lines(lines_str):
    """解析 '+29,949/-1,459' 格式的代码行数。"""
    m = re.match(r"\+?([\d,]+)/-([\d,]+)", lines_str)
    if m:
        added = int(m.group(1).replace(",", ""))
        removed = int(m.group(2).replace(",", ""))
        return added, removed
    return 0, 0


def parse_number(s):
    """解析数字字符串，支持逗号分隔。"""
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _collect_raw_days(*daily_groups):
    days = set()
    for items in daily_groups:
        for item in items or []:
            day = str((item or {}).get("day") or "").strip()
            if day:
                days.add(day)
    return days


def _combined_metrics_from_raw(raw):
    """从合并报告 raw-data 中还原 dashboard 主指标。"""
    if not raw:
        return {}

    cx_data = raw.get("cx_data") or {}
    oc_data = raw.get("oc_data") or {}
    cu_data = raw.get("cu_data") or {}
    tu_data = raw.get("tu_data") or {}
    tc_data = raw.get("tc_data") or {}
    ol_data = raw.get("ol_data") or {}
    hm_data = raw.get("hm_data") or {}
    cc_messages = _safe_int(raw.get("cc_messages"))
    cx_messages = _safe_int(cx_data.get("user_messages") or cx_data.get("total_messages"))
    oc_messages = _safe_int(oc_data.get("total_messages"))
    cu_messages = _safe_int(cu_data.get("total_messages"))
    tu_messages = _safe_int(tu_data.get("total_messages"))
    tc_messages = _safe_int(tc_data.get("total_messages"))

    cc_added = _safe_int(raw.get("cc_lines_added"))
    cc_removed = _safe_int(raw.get("cc_lines_removed"))
    cx_added = _safe_int(cx_data.get("lines_added"))
    cx_removed = _safe_int(cx_data.get("lines_removed"))
    oc_added = _safe_int(oc_data.get("lines_added"))
    oc_removed = _safe_int(oc_data.get("lines_removed"))
    cu_added = _safe_int(cu_data.get("total_lines_added"))
    cu_removed = _safe_int(cu_data.get("total_lines_removed"))
    tu_added = _safe_int(tu_data.get("total_lines_added"))
    tu_removed = _safe_int(tu_data.get("total_lines_removed"))
    tc_added = _safe_int(tc_data.get("total_lines_added"))
    tc_removed = _safe_int(tc_data.get("total_lines_removed"))

    cc_files = _safe_int(raw.get("cc_files"))
    cx_files = sum(_safe_int(item.get("patch_files")) for item in cx_data.get("thread_details") or [])
    oc_files = _safe_int(oc_data.get("files_modified"))
    cu_files = _safe_int(cu_data.get("total_files"))
    tu_files = _safe_int(tu_data.get("total_files"))
    tc_files = _safe_int(tc_data.get("total_files"))

    cc_sessions = _safe_int(raw.get("cc_sessions"))
    cx_sessions = _safe_int(raw.get("cx_sessions") or cx_data.get("total_sessions"))
    oc_sessions = _safe_int(raw.get("oc_sessions") or oc_data.get("total_sessions"))
    cu_sessions = _safe_int(raw.get("cu_sessions") or cu_data.get("total_sessions"))
    tu_sessions = _safe_int(raw.get("tu_sessions") or tu_data.get("total_sessions"))
    tc_sessions = _safe_int(raw.get("tc_sessions") or tc_data.get("total_sessions"))
    ol_sessions = _safe_int(raw.get("ol_sessions") or ol_data.get("total_sessions"))
    hm_sessions = _safe_int(raw.get("hm_sessions") or hm_data.get("total_sessions"))
    hm_messages = _safe_int(raw.get("hm_messages") or hm_data.get("total_messages"))
    hm_tokens = _safe_int(raw.get("hm_tokens") or hm_data.get("total_tokens"))
    hm_tool_calls = _safe_int(hm_data.get("tool_call_count"))

    days = _collect_raw_days(raw.get("cc_daily") or [], cx_data.get("daily") or [], oc_data.get("daily") or [])
    cu_days_raw = cu_data.get("daily") or []
    for item in cu_days_raw:
        day = item.get("day")
        if day:
            days.add(str(day))
    tu_days_raw = tu_data.get("daily") or []
    for item in tu_days_raw:
        day = item.get("day")
        if day:
            days.add(str(day))
    tc_days_raw = tc_data.get("daily") or []
    for item in tc_days_raw:
        day = item.get("day")
        if day:
            days.add(str(day))
    ol_days_raw = ol_data.get("daily") or []
    for item in ol_days_raw:
        day = item.get("day")
        if day:
            days.add(str(day))
    hm_days_raw = hm_data.get("daily") or []
    for item in hm_days_raw:
        day = item.get("day")
        if day:
            days.add(str(day))
    total_messages = cc_messages + cx_messages + oc_messages + cu_messages + tu_messages + hm_messages + tc_messages
    total_days = len(days) or max(_safe_int(raw.get("cc_days")), _safe_int(raw.get("cx_days")), _safe_int(raw.get("oc_days")), _safe_int(raw.get("cu_days")), _safe_int(raw.get("tu_days")), _safe_int(raw.get("ol_days")))

    return {
        "messages": total_messages,
        "lines_added": cc_added + cx_added + oc_added + cu_added + tu_added + tc_added,
        "lines_removed": cc_removed + cx_removed + oc_removed + cu_removed + tu_removed + tc_removed,
        "files": cc_files + cx_files + oc_files + cu_files + tu_files + tc_files,
        "days": total_days,
        "sessions": cc_sessions + cx_sessions + oc_sessions + cu_sessions + tu_sessions + ol_sessions + hm_sessions + tc_sessions,
        "cu_messages": cu_messages,
        "cu_lines_added": cu_added,
        "cu_lines_removed": cu_removed,
        "cu_files": cu_files,
        "cu_sessions": cu_sessions,
        "tu_messages": tu_messages,
        "tu_lines_added": tu_added,
        "tu_lines_removed": tu_removed,
        "tu_files": tu_files,
        "tu_sessions": tu_sessions,
        "tc_messages": tc_messages,
        "tc_sessions": tc_sessions,
        "ol_sessions": ol_sessions,
        "hm_sessions": hm_sessions,
        "hm_messages": hm_messages,
        "hm_tokens": hm_tokens,
        "hm_tool_calls": hm_tool_calls,
        "msgs_day": round(total_messages / total_days, 1) if total_days else 0,
    }


def extract_member_data(filepath, name, display_name, data):
    """从解析后的报告数据提取成员结构化信息。"""
    stats = data["stats"]
    added, removed = parse_lines(stats.get("Lines", ""))
    # 支持 Claude Code 原生字段和合并报告中的字段
    messages = int(parse_number(stats.get("Messages", "0")))
    if messages == 0:
        messages = int(parse_number(stats.get("CC Messages", "0")))
    files = int(parse_number(stats.get("Files", "0")))
    if files == 0:
        files = int(parse_number(stats.get("CC Files", "0")))
    days = int(parse_number(stats.get("Days", "0")))
    if days == 0:
        days = int(parse_number(stats.get("Active Days", "0")))
    msgs_day = parse_number(stats.get("Msgs/Day", "0"))

    # Codex 字段（来自合并报告 combined-raw-data JSON）
    raw = data.get("raw") or {}
    codex_sessions = int(raw.get("cx_sessions", 0) or 0)
    codex_tokens = int(raw.get("cx_tokens", 0) or 0)
    opencode_sessions = int(raw.get("oc_sessions", 0) or 0)
    opencode_tokens = int(raw.get("oc_tokens", 0) or 0)
    codex_data = raw.get("cx_data") or {}
    codex_insights = codex_data.get("insights") or {}
    opencode_data = raw.get("oc_data") or {}
    # Cursor 字段（来自合并报告 combined-raw-data JSON）
    openclaw_sessions = int(raw.get("ol_sessions", 0) or 0)
    openclaw_data = raw.get("ol_data") or {}
    openclaw_days = int(raw.get("ol_days", 0) or openclaw_data.get("active_days", 0) or 0)
    hermes_sessions = int(raw.get("hm_sessions", 0) or 0)
    hermes_data = raw.get("hm_data") or {}
    hermes_tokens = int(raw.get("hm_tokens", 0) or hermes_data.get("total_tokens", 0) or 0)
    hermes_messages = int(raw.get("hm_messages", 0) or hermes_data.get("total_messages", 0) or 0)
    hermes_tool_calls = int(hermes_data.get("tool_call_count", 0) or 0)
    hermes_days = int(raw.get("hm_days", 0) or hermes_data.get("active_days", 0) or 0)
    cursor_sessions = int(raw.get("cu_sessions", 0) or 0)
    cursor_data = raw.get("cu_data") or {}
    cursor_messages = int(cursor_data.get("total_messages", 0) or 0)
    cursor_lines_added = int(cursor_data.get("total_lines_added", 0) or 0)
    cursor_lines_removed = int(cursor_data.get("total_lines_removed", 0) or 0)
    cursor_files = int(cursor_data.get("total_files", 0) or 0)
    cursor_agent_count = int(cursor_data.get("agent_count", 0) or 0)
    cursor_days = int(raw.get("cu_days", 0) or cursor_data.get("active_days", 0) or 0)
    # Trae fields
    trae_sessions = int(raw.get("tu_sessions", 0) or 0)
    trae_data = raw.get("tu_data") or {}
    trae_messages = int(raw.get("tu_messages", 0) or trae_data.get("total_messages", 0) or 0)
    trae_agent_count = int(trae_data.get("agent_count", 0) or 0)
    # Trae CN fields
    trae_cn_sessions = int(raw.get("tc_sessions", 0) or 0)
    trae_cn_data = raw.get("tc_data") or {}
    trae_cn_messages = int(raw.get("tc_messages", 0) or trae_cn_data.get("total_messages", 0) or 0)
    trae_cn_agent_count = int(trae_cn_data.get("agent_count", 0) or 0)
    claude_sessions = int(raw.get("cc_sessions", 0) or 0)
    claude_tokens = int(raw.get("cc_tokens", 0) or 0)
    combined_metrics = _combined_metrics_from_raw(raw)
    if combined_metrics:
        messages = combined_metrics["messages"]
        added = combined_metrics["lines_added"]
        removed = combined_metrics["lines_removed"]
        files = combined_metrics["files"]
        days = combined_metrics["days"]
        msgs_day = combined_metrics["msgs_day"]

    return {
        "name": name,
        "display_name": display_name,
        "messages": messages,
        "lines_added": added,
        "lines_removed": removed,
        "files": files,
        "days": days,
        "msgs_day": msgs_day,
        "codex_sessions": codex_sessions,
        "codex_tokens": codex_tokens,
        "opencode_sessions": opencode_sessions,
        "opencode_tokens": opencode_tokens,
        "claude_sessions": claude_sessions,
        "claude_tokens": claude_tokens,
        "combined_sessions": combined_metrics.get("sessions", 0),
        "cursor_sessions": cursor_sessions,
        "cursor_messages": cursor_messages,
        "cursor_lines_added": cursor_lines_added,
        "cursor_lines_removed": cursor_lines_removed,
        "cursor_files": cursor_files,
        "cursor_agent_count": cursor_agent_count,
        "cursor_days": cursor_days,
        "cursor_data": cursor_data,
        "trae_sessions": trae_sessions,
        "trae_messages": trae_messages,
        "trae_agent_count": trae_agent_count,
        "trae_cn_sessions": trae_cn_sessions,
        "trae_cn_messages": trae_cn_messages,
        "trae_cn_agent_count": trae_cn_agent_count,
        "trae_data": trae_data,
        "openclaw_sessions": openclaw_sessions,
        "openclaw_data": openclaw_data,
        "openclaw_days": openclaw_days,
        "hermes_sessions": hermes_sessions,
        "hermes_data": hermes_data,
        "hermes_tokens": hermes_tokens,
        "hermes_messages": hermes_messages,
        "hermes_tool_calls": hermes_tool_calls,
        "hermes_days": hermes_days,
        "codex_data": codex_data,
        "codex_insights": codex_insights,
        "opencode_data": opencode_data,
        "subtitle": data["subtitle"],
        "areas": data["areas"],
        "tools": data["charts"].get("Top Tools Used", []),
        "languages": data["charts"].get("Languages", []),
        "charts": data["charts"],
        "sections": data.get("sections", []),
        "key_pattern": data["key_pattern"],
        "usage_narrative": data["usage_narrative"],
    }


def _load_members(members_path):
    """加载 members.json（group->members 格式）。返回 {name: {display, group}}。

    JSON 格式: {"group": ["name1", {"slug": "DisplayName"}, ...]}
    - 字符串元素: slug，display 自动首字母大写
    - 字典元素: {slug: display}，用于特殊显示名（如 "jz" -> "JZ"）
    """
    if not os.path.exists(members_path):
        return {}
    with open(members_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    result = {}
    for group, member_list in raw.items():
        if not isinstance(member_list, list):
            continue
        for item in member_list:
            if isinstance(item, str):
                result[item] = {"display": item.capitalize(), "group": group}
            elif isinstance(item, dict):
                for slug, display in item.items():
                    result[slug] = {"display": display, "group": group}
    return result


def _detect_group_from_path(filepath, reports_dir):
    """从文件路径中检测分组。新格式 reports/{period}/{group}/file.html，兼容旧格式。"""
    rel = os.path.relpath(filepath, reports_dir).replace(os.sep, "/")
    parts = rel.split("/")
    # 新格式 {period}/{group}/file.html → parts[0] 是周期标识
    if len(parts) >= 3 and detect_period_type(parts[0]):
        return parts[1]
    # 旧格式 {group}/{period}/file.html 或展平 {group}/file.html
    if len(parts) >= 2:
        return parts[0]
    return None


def _aggregate_member_data(member_list):
    """将同一成员的多份报告数据汇总为一份。"""
    if not member_list:
        return None
    first = dict(member_list[0])
    for other in member_list[1:]:
        # 数值字段：累加
        for field in ["messages", "lines_added", "lines_removed", "files",
                       "codex_sessions", "codex_tokens", "opencode_sessions", "opencode_tokens",
                       "claude_sessions", "claude_tokens", "combined_sessions",
                       "cursor_sessions", "cursor_messages", "cursor_lines_added",
                       "cursor_lines_removed", "cursor_files", "cursor_agent_count", "cursor_days",
                       "trae_sessions", "trae_messages", "trae_agent_count",
                       "trae_cn_sessions", "trae_cn_messages", "trae_cn_agent_count",
                       "openclaw_sessions", "openclaw_days",
                       "hermes_sessions", "hermes_tokens", "hermes_messages",
                       "hermes_tool_calls", "hermes_days"]:
            first[field] = first.get(field, 0) + other.get(field, 0)
        # days: 取最大值
        first["days"] = max(first.get("days", 0), other.get("days", 0))
        # subtitle: 保留最新的
        if other.get("subtitle"):
            first["subtitle"] = other["subtitle"]
        # areas: 合并去重
        area_names = {a["name"]: a for a in first.get("areas", [])}
        for a in other.get("areas", []):
            if a["name"] not in area_names:
                area_names[a["name"]] = a
        first["areas"] = list(area_names.values())
        # tools/languages: 合并计数
        for field in ["tools", "languages"]:
            merged = {}
            for item in first.get(field, []):
                merged[item["label"]] = {"label": item["label"], "value": int(str(item.get("value", "0")).replace(",", ""))}
            for item in other.get(field, []):
                if item["label"] in merged:
                    merged[item["label"]]["value"] += int(str(item.get("value", "0")).replace(",", ""))
                else:
                    merged[item["label"]] = {"label": item["label"], "value": int(str(item.get("value", "0")).replace(",", ""))}
            first[field] = list(merged.values())
        # key_pattern/usage_narrative: 保留最新的非空
        for field in ["key_pattern", "usage_narrative"]:
            if other.get(field):
                first[field] = other[field]
    # 重算 msgs_day
    days = max(first.get("days", 0), 1)
    first["msgs_day"] = round(first["messages"] / days, 1) if days else 0
    return first


def _resolve_period_data(period_groups, target_period):
    """获取目标周期的成员数据，按优先级回退聚合。
    
    回退优先级：年报 → 季报 → 月报 → 周报。
    返回 (data_dict, actual_period, fallback_type) 或 (None, None, None)。
    """
    period_type = detect_period_type(target_period)
    if period_type is None:
        return None, None, None

    type_priority = {
        "annual": ["annual", "quarterly", "monthly", "weekly"],
        "quarterly": ["quarterly", "monthly", "weekly"],
        "monthly": ["monthly", "weekly"],
        "weekly": ["weekly"],
    }
    start, end = period_start_end(target_period)

    for try_type in type_priority.get(period_type, ["weekly"]):
        # 找出日期范围内该类型的所有周期
        matching = []
        for p in period_groups:
            if p == "_legacy":
                continue
            if detect_period_type(p) != try_type:
                continue
            p_start, p_end = period_start_end(p)
            if p_start <= end and p_end >= start:
                matching.append(p)

        if matching:
            matching.sort(key=period_sort_key)
            # 按成员聚合
            result = {}
            for p in matching:
                for name, data in period_groups[p].items():
                    if name not in result:
                        result[name] = [data]
                    else:
                        result[name].append(data)

            aggregated = {}
            for name, reports in result.items():
                aggregated[name] = _aggregate_member_data(reports)
                aggregated[name]["_fallback"] = try_type != period_type
                aggregated[name]["_source_periods"] = matching

            return aggregated, target_period, try_type

    return None, None, None


def _prev_period_of_type(current_period_str):
    """返回同类型的上一个周期标识。"""
    tp = detect_period_type(current_period_str)
    if tp is None:
        return None
    start, end = period_start_end(current_period_str)
    
    if tp == "weekly":
        prev = start - datetime.timedelta(days=7)
        iso = prev.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    elif tp == "monthly":
        if start.month == 1:
            prev = start.replace(year=start.year - 1, month=12)
        else:
            prev = start.replace(month=start.month - 1)
        return f"{prev.year}-{prev.month:02d}"
    elif tp == "quarterly":
        quarter = (start.month - 1) // 3
        if quarter == 0:
            prev = start.replace(year=start.year - 1, month=10)
        else:
            prev = start.replace(month=quarter * 3)
        new_quarter = (prev.month - 1) // 3 + 1
        return f"{prev.year}-Q{new_quarter}"
    elif tp == "annual":
        return str(start.year - 1)
    return None



def generate_team_report(reports_dir, output_dir, members_path, period=None):
    """生成团队汇总报告。period 为指定周期标识（如 2026-W22），None 时取最新。"""
    members = _load_members(members_path)

    # 按周期分组收集报告: { period: { name: member_data } }
    period_groups = {}
    for root, dirs, files in os.walk(reports_dir):
        for filename in sorted(files):
            name, file_period = parse_filename(filename)
            if name is None:
                continue
            if file_period is None:
                file_period = "_legacy"
            filepath = os.path.join(root, filename)
            member_info = members.get(name, {"display": name, "group": "group"})
            display_name = member_info["display"]
            group = _detect_group_from_path(filepath, reports_dir) or member_info["group"]
            data = parse_report(filepath)
            member = extract_member_data(filepath, name, display_name, data)
            member["group"] = group
            member["report_relpath"] = os.path.relpath(filepath, os.path.join(reports_dir, file_period)).replace(os.sep, "/")
            period_groups.setdefault(file_period, {})[name] = member

    if not period_groups:
        print("reports/ 目录下没有找到任何报告文件")
        print("请先让成员运行 /getagt 生成报告")
        return False

    # 周期筛选与回退聚合
    if period:
        # 先尝试直接匹配
        period_type = detect_period_type(period)
        matching = sorted(
            (p for p in period_groups if p != "_legacy" and detect_period_type(p) == period_type),
            key=period_sort_key
        )
        if matching:
            latest_period = matching[-1]
            prev_period = matching[-2] if len(matching) >= 2 else None
        else:
            # 无直接匹配，尝试聚合下级周期
            latest_data_new, latest_period, fallback_type = _resolve_period_data(period_groups, period)
            if latest_data_new:
                if fallback_type != period_type:
                    print(f"reports/ 中没有 {period} ({plabel(period)}) 数据，已用 {fallback_type} 数据聚合")
                period_groups[latest_period] = latest_data_new
                prev_period_str = _prev_period_of_type(period)
                if prev_period_str:
                    prev_data_new, prev_period_str2, _ = _resolve_period_data(period_groups, prev_period_str)
                    if prev_data_new:
                        period_groups[prev_period_str2] = prev_data_new
                        prev_period = prev_period_str2
                    else:
                        prev_period = None
                else:
                    prev_period = None
            else:
                # 完全无数据，回退到最新可用
                print(f"reports/ 中没有 {period} ({plabel(period)}) 的任何数据，回退到最新可用周期")
                periods_all = sorted((p for p in period_groups if p != "_legacy"), key=period_sort_key)
                if periods_all:
                    latest_period = periods_all[-1]
                    prev_period = periods_all[-2] if len(periods_all) >= 2 else None
                else:
                    print("reports/ 目录下没有找到任何报告文件")
                    print("请先让成员运行 /getagt 生成报告")
                    return False
    else:
        # 未指定：取所有周期中的最新
        periods_all = sorted((p for p in period_groups if p != "_legacy"), key=period_sort_key)
        if periods_all:
            latest_period = periods_all[-1]
            prev_period = periods_all[-2] if len(periods_all) >= 2 else None
        else:
            latest_period = "_legacy"
            prev_period = None

    latest_data = period_groups[latest_period]
    prev_data = period_groups[prev_period] if prev_period else {}

    # 成员基线口径：
    # 1. 优先以 members.json 为团队名单
    # 2. 再补充“本周实际提交但未配置”的成员，避免把历史偶发 slug 全算成{_cn("本周缺报")}
    configured_names = set(members.keys())
    all_names = set(configured_names) if configured_names else set()
    all_names.update(latest_data.keys())
    if not all_names:
        for pd_data in period_groups.values():
            all_names.update(pd_data.keys())

    # 收集所有分组名（从 members.json + 实际数据）
    all_groups = sorted(set(
        [members[n]["group"] for n in members]
        + [period_groups[p][n].get("group", "group") for p in period_groups for n in period_groups[p] if "group" in period_groups[p][n]]
    ))

    # 构建本周 team_data（附带 delta + 缺报状态）
    team_data = []
    for name in all_names:
        member_info = members.get(name, {"display": name, "group": "group"})
        display_name = member_info["display"]
        member_group = member_info["group"]
        if name in latest_data:
            d = dict(latest_data[name])
            d["status"] = "submitted"
            if "group" not in d:
                d["group"] = member_group
            prev = prev_data.get(name)
            if prev:
                d["delta_messages"] = d["messages"] - prev["messages"]
                d["delta_lines_added"] = d["lines_added"] - prev["lines_added"]
                d["delta_lines_removed"] = d["lines_removed"] - prev["lines_removed"]
                d["delta_files"] = d["files"] - prev["files"]
                d["delta_days"] = d["days"] - prev["days"]
                d["delta_msgs_day"] = round(d["msgs_day"] - prev["msgs_day"], 1)
            else:
                d["delta_messages"] = None
                d["delta_lines_added"] = None
                d["delta_lines_removed"] = None
                d["delta_files"] = None
                d["delta_days"] = None
                d["delta_msgs_day"] = None
        else:
            last_seen = None
            source = None
            for p in reversed(sorted((p for p in period_groups if p != "_legacy"), key=period_sort_key) if period_groups else ["_legacy"]):
                src = period_groups.get(p, {}).get(name)
                if src:
                    source = src
                    last_seen = p
                    break
            d = {
                "name": name,
                "display_name": display_name,
                "group": source.get("group", member_group) if source else member_group,
                "messages": 0,
                "lines_added": 0,
                "lines_removed": 0,
                "files": 0,
                "days": 0,
                "msgs_day": 0,
                "codex_sessions": 0,
                "codex_tokens": 0,
                "opencode_sessions": 0,
                "opencode_tokens": 0,
                "cursor_sessions": 0,
                "cursor_messages": 0,
                "cursor_lines_added": 0,
                "cursor_lines_removed": 0,
                "cursor_files": 0,
                "cursor_agent_count": 0,
                "cursor_days": 0,
                "trae_sessions": 0,
                "trae_messages": 0,
                "trae_agent_count": 0,
                "trae_cn_sessions": 0,
                "trae_cn_messages": 0,
                "trae_cn_agent_count": 0,
                "subtitle": source["subtitle"] if source else "",
                "areas": source["areas"] if source else [],
                "tools": source["tools"] if source else [],
                "languages": source["languages"] if source else [],
                "key_pattern": source["key_pattern"] if source else "",
                "usage_narrative": source["usage_narrative"] if source else "",
                "charts": source["charts"] if source else {},
                "sections": source["sections"] if source else [],
                "status": "missing",
                "last_seen_week": last_seen,
                "report_relpath": source.get("report_relpath") if source else None,
                "delta_messages": None,
                "delta_lines_added": None,
                "delta_lines_removed": None,
                "delta_files": None,
                "delta_days": None,
                "delta_msgs_day": None,
            }
        team_data.append(d)
    team_data.sort(key=lambda x: (x.get("status") != "submitted", -x["days"], x["display_name"]))

    active_team = [d for d in team_data if d.get("status") == "submitted"]

    # 本周汇总
    n = len(active_team)
    expected_total = len(members) if members else len(all_names)
    cur = {
        "messages": sum(d["messages"] for d in active_team),
        "added": sum(d["lines_added"] for d in active_team),
        "removed": sum(d["lines_removed"] for d in active_team),
        "files": sum(d["files"] for d in active_team),
        "days": sum(d["days"] for d in active_team),
        "codex_sessions": sum(d.get("codex_sessions", 0) for d in active_team),
        "codex_tokens": sum(d.get("codex_tokens", 0) for d in active_team),
        "opencode_sessions": sum(d.get("opencode_sessions", 0) for d in active_team),
        "opencode_tokens": sum(d.get("opencode_tokens", 0) for d in active_team),
        "cursor_sessions": sum(d.get("cursor_sessions", 0) for d in active_team),
        "cursor_messages": sum(d.get("cursor_messages", 0) for d in active_team),
        "cursor_lines_added": sum(d.get("cursor_lines_added", 0) for d in active_team),
        "cursor_lines_removed": sum(d.get("cursor_lines_removed", 0) for d in active_team),
        "trae_sessions": sum(d.get("trae_sessions", 0) for d in active_team),
        "trae_messages": sum(d.get("trae_messages", 0) for d in active_team),
        "trae_cn_sessions": sum(d.get("trae_cn_sessions", 0) for d in active_team),
        "trae_cn_messages": sum(d.get("trae_cn_messages", 0) for d in active_team),
        "openclaw_sessions": sum(d.get("openclaw_sessions", 0) for d in active_team),
        "hermes_sessions": sum(d.get("hermes_sessions", 0) for d in active_team),
        "hermes_tokens": sum(d.get("hermes_tokens", 0) for d in active_team),
        "hermes_tool_calls": sum(d.get("hermes_tool_calls", 0) for d in active_team),
        "avg_msgs_day": round(sum(d["msgs_day"] for d in active_team) / n, 1) if n else 0,
        "member_count": n,
        "expected_count": expected_total,
        "missing_count": len([d for d in team_data if d.get("status") == "missing"]),
    }
    cur["coverage_rate"] = round((cur["member_count"] / cur["expected_count"] * 100), 1) if cur["expected_count"] else 0

    # 上周汇总（用于 delta）
    if prev_data:
        prev_msgs = sum(d["messages"] for d in prev_data.values())
        prev_files = sum(d["files"] for d in prev_data.values())
        prev_n = len(prev_data)
        prev_avg = sum(d["msgs_day"] for d in prev_data.values()) / prev_n if prev_n else 0
    else:
        prev_msgs = prev_files = None
        prev_avg = None

    # 累计汇总（所有周中每个成员取最新一周的数据）
    cumulative = {}
    for name in all_names:
        for p in reversed(sorted((p for p in period_groups if p != "_legacy"), key=period_sort_key) if period_groups else ["_legacy"]):
            if name in period_groups.get(p, {}):
                cumulative[name] = period_groups[p][name]
                break
    cum = {
        "messages": sum(d["messages"] for d in cumulative.values()),
        "added": sum(d["lines_added"] for d in cumulative.values()),
        "removed": sum(d["lines_removed"] for d in cumulative.values()),
        "files": sum(d["files"] for d in cumulative.values()),
        "codex_sessions": sum(d.get("codex_sessions", 0) for d in cumulative.values()),
        "codex_tokens": sum(d.get("codex_tokens", 0) for d in cumulative.values()),
        "opencode_sessions": sum(d.get("opencode_sessions", 0) for d in cumulative.values()),
        "opencode_tokens": sum(d.get("opencode_tokens", 0) for d in cumulative.values()),
        "cursor_sessions": sum(d.get("cursor_sessions", 0) for d in cumulative.values()),
        "cursor_messages": sum(d.get("cursor_messages", 0) for d in cumulative.values()),
        "trae_sessions": sum(d.get("trae_sessions", 0) for d in cumulative.values()),
        "trae_messages": sum(d.get("trae_messages", 0) for d in cumulative.values()),
        "trae_cn_sessions": sum(d.get("trae_cn_sessions", 0) for d in cumulative.values()),
        "trae_cn_messages": sum(d.get("trae_cn_messages", 0) for d in cumulative.values()),
        "openclaw_sessions": sum(d.get("openclaw_sessions", 0) for d in cumulative.values()),
        "hermes_sessions": sum(d.get("hermes_sessions", 0) for d in cumulative.values()),
        "hermes_tokens": sum(d.get("hermes_tokens", 0) for d in cumulative.values()),
        "hermes_tool_calls": sum(d.get("hermes_tool_calls", 0) for d in cumulative.values()),
        "member_count": len(cumulative),
    }

    period_label_var = latest_period if latest_period != "_legacy" else "全部"

    totals = {
        "period_label_var": period_label_var,
        "prev_period_label": prev_period,
        "cur": cur,
        "cum": cum,
        "delta_messages": cur["messages"] - prev_msgs if prev_msgs is not None else None,
        "delta_files": cur["files"] - prev_files if prev_files is not None else None,
        "delta_avg_msgs_day": round(cur["avg_msgs_day"] - prev_avg, 1) if prev_avg is not None else None,
        "missing_members": [d["display_name"] for d in team_data if d.get("status") == "missing"],
    }

    totals["all_groups"] = all_groups

    html = generate_html(team_data, totals)
    output_path = os.path.join(reports_dir, latest_period, "team-report.html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Post-process language translation
    if _LANG == "en":
        from i18n import _ZH_MAP
        # Build ZH→EN map from both directions (same pattern as merge_reports.py)
        _en_map = {}
        for _zk, _zv in _ZH_MAP.items():
            if len(_zv) >= 3 and any('\u4e00' <= c <= '\u9fff' for c in _zv):
                _en_map[_zv] = _zk
            if len(_zk) >= 3 and any('\u4e00' <= c <= '\u9fff' for c in _zk):
                _en_map[_zk] = _zv
        # Also add reverse CN_MAP
        _en_map.update({v: k for k, v in _CN_MAP.items() if len(v) >= 3 and any('\u4e00' <= c <= '\u9fff' for c in v)})
        with open(output_path, "r", encoding="utf-8") as rf:
            translated = rf.read()
        for zh_phrase, en_phrase in sorted(_en_map.items(), key=lambda x: -len(x[0])):
            translated = translated.replace(zh_phrase, en_phrase)
        with open(output_path, "w", encoding="utf-8") as wf:
            wf.write(translated)

    # 若设置了 AGENTS_REPORT_URL，上传团队报告并刷新缓存
    report_url = os.environ.get("AGENTS_REPORT_URL", "").strip()
    if report_url:
        import urllib.request
        base = report_url.rstrip("/")
        # 上传报告文件
        target = f"{base}/api/report/upload?name=team&period={latest_period}&group=team"
        req = urllib.request.Request(target, data=open(output_path, "rb").read(), method="PUT")
        req.add_header("Content-Type", "text/html")
        try:
            urllib.request.urlopen(req, timeout=30)
            print(f"团队报告已上传到 {report_url}", file=sys.stderr)
        except Exception as e:
            print(f"团队报告上传失败: {e}", file=sys.stderr)
        # 刷新 Dashboard 缓存（确保 team-report.html 立即可见）
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/refresh", method="POST"), timeout=10)
        except Exception:
            pass


    # 清理旧的按周文件和软链

    if not os.path.isfile(output_path):
        print(f"生成失败: 未找到输出文件 {output_path}")
        return False

    # 终端输出
    print(f"报告周期: {period_label_var}")
    if prev_period:
        print(f"对比周期: {prev_period}")
    print(f"团队成员: {n}/{cur['expected_count']} 人 (覆盖率 {cur['coverage_rate']}%)")
    print(f"本周消息: {cur['messages']:,}{_delta_str(totals['delta_messages'])}")
    print(f"本周代码: +{cur['added']:,}/-{cur['removed']:,}")
    print(f"本周 CLI: Codex {cur['codex_sessions']:,} 会话 / {cur['codex_tokens']:,} tokens; OpenCode {cur['opencode_sessions']:,} 会话 / {cur['opencode_tokens']:,} tokens")
    print(f"本周 IDE: Cursor {cur['cursor_sessions']:,} 会话 / {cur['cursor_messages']:,} 消息 / +{cur['cursor_lines_added']:,}/-{cur['cursor_lines_removed']:,} 行")
    print(f"           Trae {cur['trae_sessions']:,} 会话 / {cur['trae_messages']:,} 消息")
    print(f"        Trae CN {cur['trae_cn_sessions']:,} 会话 / {cur['trae_cn_messages']:,} 消息")
    print(f"           编排: OpenClaw {cur['openclaw_sessions']:,} 会话")
    print(f"           Hermes {cur['hermes_sessions']:,} 会话 / {cur['hermes_tokens']:,} tokens / {cur['hermes_tool_calls']:,} 工具调用")
    print(f"累计消息: {cum['messages']:,} ({cum['member_count']} 人)")
    if totals["missing_members"]:
        print(f"{_cn("本周缺报")}: {len(totals['missing_members'])} 人 -> " + ", ".join(totals["missing_members"]))
    for group in all_groups:
        group_members = [d for d in team_data if d.get("group") == group]
        if not group_members:
            continue
        print(f"\n[{group.upper()}] 成员概览:")
        for d in group_members:
            delta = _delta_str(d["delta_messages"])
            print(f"  {d['display_name']}: {d['messages']:,} msgs{delta}, +{d['lines_added']:,}/-{d['lines_removed']:,} lines")
    print(f"\n报告已生成: {output_path}")
    return True


def _delta_str(val):
    """格式化 delta 值为终端显示字符串。"""
    if val is None:
        return ""
    if isinstance(val, float):
        return f" (+{val:.1f})" if val >= 0 else f" ({val:.1f})"
    return f" (+{val:,})" if val >= 0 else f" ({val:,})"


def _delta_badge(val):
    """格式化 delta 值为 HTML 徽章。"""
    if val is None:
        return ""
    if isinstance(val, float):
        if val > 0:
            return f'<span class="badge badge-up">+{val:.1f}</span>'
        elif val < 0:
            return f'<span class="badge badge-down">{val:.1f}</span>'
        return '<span class="badge badge-flat">0</span>'
    if val > 0:
        return f'<span class="badge badge-up">+{val:,}</span>'
    elif val < 0:
        return f'<span class="badge badge-down">{val:,}</span>'
    return '<span class="badge badge-flat">0</span>'


def _activity_level(msgs_day):
    """根据日均消息数返回 (css_class, label)。"""
    if msgs_day >= 30:
        return "lv-power", "高活跃"
    if msgs_day >= 15:
        return "lv-active", "活跃"
    if msgs_day >= 5:
        return "lv-moderate", "中活跃"
    return "lv-light", "轻活跃"


def _top_codex_members(team_data, limit=6):
    members = [d for d in team_data if d.get("status") == "submitted" and d.get("codex_sessions", 0) > 0]
    members.sort(key=lambda x: (x.get("codex_tokens", 0), x.get("codex_sessions", 0)), reverse=True)
    return members[:limit]


def _parse_metric_value(value):
    """解析图表/区域中的数值。"""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0
    matched = re.search(r"[-+]?[\d,]+(?:\.\d+)?", text)
    if not matched:
        return 0
    return parse_number(matched.group(0))


def _format_large_number(value):
    value = int(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value}"


def _format_member_list(members, limit=3):
    names = [d.get("display_name", d.get("name", "")) for d in members[:limit] if d.get("display_name") or d.get("name")]
    return "、".join(names) if names else "暂无"


def _top_ratio(items, value_key, limit=3):
    ranked = [item for item in items if item.get(value_key, 0) > 0]
    ranked.sort(key=lambda x: x.get(value_key, 0), reverse=True)
    total = sum(item.get(value_key, 0) for item in ranked)
    top = sum(item.get(value_key, 0) for item in ranked[:limit])
    ratio = (top / total * 100) if total else 0
    return ranked[:limit], round(ratio, 1)


def _count_non_empty(iterable):
    return len([item for item in iterable if item])


def _clean_pattern_text(text, limit=180):
    """裁剪过长的 pattern，避免整段报告被塞进摘要。"""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    for marker in [
        "Median:",
        "Impressive Things You Did",
        "Where Things Go Wrong",
        "Existing CC Features to Try",
        "New Ways to Use Claude Code",
        "On the Horizon",
    ]:
        idx = raw.find(marker)
        if idx > 0:
            raw = raw[:idx].strip()
            break
    if len(raw) > limit:
        raw = raw[: limit - 1].rstrip() + "…"
    return raw


def _aggregate_claude_insights(team_data, totals):
    submitted = [d for d in team_data if d.get("status") == "submitted"]
    users = [d for d in submitted if d.get("messages", 0) > 0 or d.get("days", 0) > 0 or d.get("files", 0) > 0]
    tool_counter = Counter()
    language_counter = Counter()
    area_counter = Counter()
    patterns = []

    for member in users:
        for item in member.get("tools", [])[:8]:
            name = item.get("label") or item.get("name")
            if name:
                tool_counter[_cn(name)] += int(_parse_metric_value(item.get("value")))
        for item in member.get("languages", [])[:8]:
            name = item.get("label") or item.get("name")
            if name:
                language_counter[_cn(name)] += int(_parse_metric_value(item.get("value")))
        for area in member.get("areas", [])[:5]:
            area_name = area.get("name")
            if area_name:
                area_counter[_cn(area_name)] += int(_parse_metric_value(area.get("count")))
        pattern = (member.get("key_pattern") or "").strip()
        if pattern:
            patterns.append({
                "member": member.get("display_name", member.get("name", "")),
                "pattern": _clean_pattern_text(_localize_text(pattern)),
            })

    top_members, concentration = _top_ratio(users, "messages")
    top_tools = tool_counter.most_common(5)
    top_languages = language_counter.most_common(5)
    top_areas = area_counter.most_common(4)
    heavy_users = [d for d in users if d.get("msgs_day", 0) >= 30]
    low_output = [d for d in users if d.get("messages", 0) > 0 and d.get("files", 0) <= 5]

    if not users:
        summary = "本周几乎没有可用的 Claude Code 交互数据，团队主工作流不在 Claude 侧。"
        risks = "继续把 Claude 指标放在报告核心位置会误导判断，因为当前周的主信号来自其他工具。"
        action = "把 Claude 视角降为辅助项，只保留成员覆盖和少量结构化字段。"
    else:
        summary = (
            f"Claude 本周覆盖 {len(users)}/{totals['cur']['expected_count']} 人，"
            f"总消息 {totals['cur']['messages']:,}，其中 TOP3（{_format_member_list(top_members)}）贡献 {concentration}% 消息量。"
        )
        risks = (
            f"{len(heavy_users)} 人日均消息 >= 30，说明深度协作集中在少数重度用户；"
            f"{len(low_output)} 人消息存在但文件改动很少，可能偏问答/排障而非直接落地。"
        )
        action = (
            f"优先复盘 {_format_member_list(top_members, 2)} 的高产出链路，"
            f"把高频主题沉淀成固定 prompt / 项目上下文，避免团队其他人重复试错。"
        )

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "top_tools": top_tools,
        "top_languages": top_languages,
        "top_areas": top_areas,
        "patterns": patterns[:3],
        "heavy_users": len(heavy_users),
    }


def _aggregate_codex_insights(team_data):
    top_tools = {}
    top_commands = {}
    top_projects = {}
    wins = []
    frictions = []
    suggestions = []

    for member in team_data:
        if member.get("status") != "submitted":
            continue
        insight = member.get("codex_insights") or {}
        for item in insight.get("top_tools", []):
            name = item.get("name")
            if name:
                top_tools[name] = top_tools.get(name, 0) + int(item.get("count", 0) or 0)
        for item in insight.get("top_commands", []):
            name = item.get("name")
            if name:
                top_commands[name] = top_commands.get(name, 0) + int(item.get("count", 0) or 0)
        top_area = insight.get("top_area") or {}
        area_name = top_area.get("name")
        if area_name:
            top_projects[area_name] = top_projects.get(area_name, 0) + int(top_area.get("sessions", 0) or 0)

        wins.extend((item.get("title") for item in insight.get("wins", []) if item.get("title")))
        frictions.extend((item.get("title") for item in insight.get("friction", []) if item.get("title")))
        suggestions.extend((item.get("title") for item in insight.get("suggestions", []) if item.get("title")))

    def _sort_counter(mapping, limit=5):
        return sorted(mapping.items(), key=lambda x: (-x[1], x[0]))[:limit]

    return {
        "tools": _sort_counter(top_tools),
        "commands": _sort_counter(top_commands),
        "projects": _sort_counter(top_projects),
        "wins": Counter(wins).most_common(4),
        "frictions": Counter(frictions).most_common(4),
        "suggestions": Counter(suggestions).most_common(4),
    }


def _summarize_codex_team(team_data):
    users = [d for d in team_data if d.get("status") == "submitted" and (d.get("codex_sessions", 0) > 0 or d.get("codex_tokens", 0) > 0)]
    total_sessions = sum(d.get("codex_sessions", 0) for d in users)
    total_tokens = sum(d.get("codex_tokens", 0) for d in users)
    patch_success = 0
    web_searches = 0
    aborted_turns = 0
    compactions = 0
    interactive = 0
    full_auto = 0

    for member in users:
        insight = member.get("codex_insights") or {}
        patch_success += int(insight.get("patch_success", 0) or 0)
        web_searches += int(insight.get("web_searches", 0) or 0)
        aborted_turns += int(insight.get("aborted_turns", 0) or 0)
        compactions += int(insight.get("compactions", 0) or 0)
        interactive += int(insight.get("interactive", 0) or 0)
        full_auto += int(insight.get("full_auto", 0) or 0)

    top_members, concentration = _top_ratio(users, "codex_tokens")
    execution_bias = "执行型"
    if web_searches > patch_success and web_searches >= 3:
        execution_bias = "调研型"
    elif patch_success == 0 and total_sessions > 0:
        execution_bias = "审阅型"

    if not users:
        summary = "本周没有汇总到 Codex 活动。"
        risks = "团队报告无法体现 CLI 代理式开发行为。"
        action = "确认成员周报是否都合并了 Codex 原始数据。"
    else:
        summary = (
            f"Codex 本周 {len(users)} 人使用，{total_sessions:,} 个会话，{_format_large_number(total_tokens)} tokens，"
            f"TOP3（{_format_member_list(top_members)}）占 {concentration}% tokens，明显是 {execution_bias} 工作流。"
        )
        risks = (
            f"累计 {aborted_turns} 次中断、{compactions} 次上下文压缩，"
            f"{'且仍以交互审批为主。' if interactive > full_auto else '自动化比例已开始提升。'}"
        )
        action = (
            f"优先把高频 Codex 项目上的常用命令、测试入口和收口标准写进仓库说明，"
            f"减少中断和长上下文回滚。"
        )

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "patch_success": patch_success,
        "web_searches": web_searches,
        "aborted_turns": aborted_turns,
        "compactions": compactions,
        "interactive": interactive,
        "full_auto": full_auto,
        "execution_bias": execution_bias,
    }


def _aggregate_opencode_insights(team_data):
    users = []
    area_counter = Counter()
    total_sessions = 0
    total_tokens = 0
    active_days = 0

    for member in team_data:
        if member.get("status") != "submitted":
            continue
        oc_sessions = int(member.get("opencode_sessions", 0) or 0)
        oc_tokens = int(member.get("opencode_tokens", 0) or 0)
        oc_data = member.get("opencode_data") or {}
        if oc_sessions <= 0 and oc_tokens <= 0 and not oc_data:
            continue
        users.append(member)
        total_sessions += oc_sessions
        total_tokens += oc_tokens
        active_days += int((oc_data or {}).get("active_days", 0) or 0)
        for area in (oc_data or {}).get("areas", [])[:8]:
            name = area.get("cwd")
            if name:
                area_counter[name] += int(area.get("sessions", 0) or 0)

    top_members, concentration = _top_ratio(users, "opencode_tokens")
    top_areas = area_counter.most_common(5)

    if not users:
        summary = "OpenCode 本周没有形成有效使用信号。"
        risks = "如果团队已经转向 CLI 代理，OpenCode 为空通常意味着还没接入，或者报告链路漏采。"
        action = "不要把 OpenCode 面板当装饰；要么补采集，要么在报告里明确它当前未使用。"
    else:
        summary = (
            f"OpenCode 本周 {len(users)} 人使用，{total_sessions:,} 个会话，{_format_large_number(total_tokens)} tokens，"
            f"TOP3（{_format_member_list(top_members)}）占 {concentration}% tokens。"
        )
        risks = (
            f"主要活动集中在 {top_areas[0][0] if top_areas else '少数目录'}，"
            f"说明使用面还窄，尚未形成团队级普及。"
        )
        action = "如果 OpenCode 是战略方向，就应该单独跟踪项目分布和活跃成员，而不是只显示 0/非 0。"

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "top_areas": top_areas,
        "active_days": active_days,
    }



def _aggregate_cursor_insights(team_data):
    """聚合团队 Cursor 使用数据，提取高频项目、模式分布。"""
    area_counter = Counter()
    model_counter = Counter()
    total_agent = 0
    total_chat = 0

    for member in team_data:
        if member.get("status") != "submitted":
            continue
        cu_data = member.get("cursor_data") or {}
        if not cu_data:
            continue
        for area in cu_data.get("areas", [])[:8]:
            name = area.get("cwd")
            if name:
                area_counter[name] += int(area.get("sessions", 0) or 0)
        for model in cu_data.get("models", [])[:8]:
            name = model.get("name")
            if name:
                model_counter[name] += int(model.get("sessions", 0) or 0)
        total_agent += int(cu_data.get("agent_count", 0) or 0)
        total_chat += int(cu_data.get("chat_count", 0) or 0)

    top_areas = area_counter.most_common(5)
    top_models = model_counter.most_common(5)

    return {
        "top_areas": top_areas,
        "top_models": top_models,
        "total_agent": total_agent,
        "total_chat": total_chat,
    }


def _summarize_cursor_team(team_data):
    """生成团队 Cursor 使用总结。"""
    users = [d for d in team_data if d.get("status") == "submitted" and d.get("cursor_sessions", 0) > 0]
    total_sessions = sum(d.get("cursor_sessions", 0) for d in users)
    total_messages = sum(d.get("cursor_messages", 0) for d in users)
    total_lines = sum(d.get("cursor_lines_added", 0) + d.get("cursor_lines_removed", 0) for d in users)
    total_agent = sum(d.get("cursor_agent_count", 0) for d in users)
    total_chat = 0
    for d in users:
        cu_d = d.get("cursor_data") or {}
        total_chat += int(cu_d.get("chat_count", 0) or 0)

    agent_ratio = round(total_agent / max(total_sessions, 1) * 100)

    if not users:
        summary = "Cursor 本周没有汇总到有效使用数据。"
        risks = "可能是成员没有提交 Cursor 采集，或者团队正在迁移到其他 IDE。"
        action = "确认 Cursor 数据源是否连接到周报采集流程。"
    else:
        summary = (
            f"Cursor 本周 {len(users)} 人使用，{total_sessions:,} 个会话，{total_messages:,} 条消息，"
            f"+{sum(d.get('cursor_lines_added', 0) for d in users):,}/-{sum(d.get('cursor_lines_removed', 0) for d in users):,} 行变更。"
        )
        risks = (
            f"Agent 模式占 {agent_ratio}%，{'Agent 使用率很高' if agent_ratio >= 50 else 'Chat 模式仍占主导，Agent 迁移空间大'}。"
        )
        action = "把团队高频 Cursor 项目的工作流沉淀为 .cursorrules，统一 Agent 行为。"

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_lines": total_lines,
        "agent_ratio": agent_ratio,
    }


def _top_cursor_members(team_data, limit=6):
    members = [d for d in team_data if d.get("status") == "submitted" and d.get("cursor_sessions", 0) > 0]
    members.sort(key=lambda x: (x.get("cursor_lines_added", 0) + x.get("cursor_lines_removed", 0), x.get("cursor_sessions", 0)), reverse=True)
    return members[:limit]



def _aggregate_trae_insights(team_data):
    """聚合团队 Trae 使用数据。"""
    area_counter = Counter()

    for member in team_data:
        if member.get("status") != "submitted":
            continue
        tu_data = member.get("trae_data") or {}
        if not tu_data:
            continue
        for area in tu_data.get("areas", [])[:8]:
            name = area.get("cwd")
            if name:
                area_counter[name] += int(area.get("sessions", 0) or 0)

    top_areas = area_counter.most_common(5)

    return {
        "top_areas": top_areas,
    }


def _aggregate_trae_cn_insights(team_data):
    """聚合团队 Trae CN 使用数据。"""
    area_counter = Counter()

    for member in team_data:
        if member.get("status") != "submitted":
            continue
        tc_data = member.get("trae_cn_data") or {}
        if not tc_data:
            continue
        for area in tc_data.get("areas", [])[:8]:
            name = area.get("cwd")
            if name:
                area_counter[name] += int(area.get("sessions", 0) or 0)

    top_areas = area_counter.most_common(5)

    return {
        "top_areas": top_areas,
    }


def _aggregate_openclaw_insights(team_data):
    agents_counter = Counter()
    sources_counter = Counter()
    submitted = [d for d in team_data if d.get("status") == "submitted" and d.get("openclaw_sessions", 0) > 0]
    for member in submitted:
        od = member.get("openclaw_data") or {}
        for agent in (od.get("agents") or []):
            agents_counter[(agent.get("name") or "")] += _safe_int(agent.get("sessions"))
        for src in (od.get("sources") or []):
            sources_counter[(src.get("name") or "")] += _safe_int(src.get("sessions"))
    return {
        "top_agents": agents_counter.most_common(10),
        "top_sources": sources_counter.most_common(5),
    }


def _aggregate_hermes_insights(team_data):
    tools_counter = Counter()
    models_counter = Counter()
    submitted = [d for d in team_data if d.get("status") == "submitted" and d.get("hermes_sessions", 0) > 0]
    for member in submitted:
        hd = member.get("hermes_data") or {}
        for tool in (hd.get("top_tools") or []):
            tools_counter[(tool.get("name") or "")] += _safe_int(tool.get("count"))
        for model in (hd.get("models") or []):
            models_counter[(model.get("model") or "")] += _safe_int(model.get("sessions"))
    return {
        "top_tools": tools_counter.most_common(10),
        "top_models": models_counter.most_common(10),
    }


def _summarize_openclaw_team(team_data):
    users = [d for d in team_data if d.get("status") == "submitted" and d.get("openclaw_sessions", 0) > 0]
    total_sessions = sum(d.get("openclaw_sessions", 0) for d in users)

    if not users:
        summary = "OpenClaw 本周没有汇总到有效使用数据。"
        risks = "可能是成员没有提交 OpenClaw 采集，或 commands.log 在本周无新事件。"
        action = "确认 ~/.openclaw/logs/commands.log 是否有本周会话创建事件。"
    else:
        summary = f"OpenClaw 本周 {len(users)} 人使用，{total_sessions:,} 个新会话。"
        risks = "OpenClaw 当前只能跟踪会话创建事件，无法评估执行质量和 agent 内部行为。"
        action = "如果 OpenClaw 是多 agent 编排入口，建议推进 agent 行为日志标准化。"

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "total_sessions": total_sessions,
    }


def _summarize_hermes_team(team_data):
    users = [d for d in team_data if d.get("status") == "submitted" and d.get("hermes_sessions", 0) > 0]
    total_sessions = sum(d.get("hermes_sessions", 0) for d in users)
    total_tokens = sum(d.get("hermes_tokens", 0) for d in users)
    total_tool_calls = sum(d.get("hermes_tool_calls", 0) for d in users)
    avg_tool_calls = round(total_tool_calls / max(total_sessions, 1), 1)

    if not users:
        summary = "Hermes 本周没有汇总到有效使用数据。"
        risks = "可能是成员没有提交 Hermes 采集，或 ~/.hermes/state.db 在本周无新会话。"
        action = "确认 ~/.hermes/state.db 是否包含本周会话。"
    else:
        summary = f"Hermes 本周 {len(users)} 人使用，{total_sessions:,} 个会话，{total_tokens:,} tokens，{total_tool_calls:,} 次工具调用（平均 {avg_tool_calls}/会话）。"
        risks = "Hermes 当前可见 token 和工具调用，但缺乏文件改动统计，执行闭环还需补全。"
        action = "如果 Hermes 承担了工具密集型任务，可以推进 SOUL.md 和 skills 的标准化配置。"

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "total_sessions": total_sessions,
        "total_tokens": total_tokens,
        "total_tool_calls": total_tool_calls,
    }


def _summarize_trae_team(team_data):

    """生成团队 Trae 使用总结。"""
    users = [d for d in team_data if d.get("status") == "submitted" and d.get("trae_sessions", 0) > 0]
    total_sessions = sum(d.get("trae_sessions", 0) for d in users)
    total_messages = sum(d.get("trae_messages", 0) for d in users)
    total_agent = sum(d.get("trae_agent_count", 0) for d in users)

    agent_ratio = round(total_agent / max(total_sessions, 1) * 100)

    if not users:
        summary = "Trae 本周没有汇总到有效使用数据。"
        risks = "可能是成员没有提交 Trae 采集，或者 Trae 不在团队主工作流中。"
        action = "确认 Trae 数据源是否连接到周报采集流程。"
    else:
        summary = (
            f"Trae 本周 {len(users)} 人使用，{total_sessions:,} 个会话，{total_messages:,} 条消息。"
        )
        risks = (
            f"Builder 模式占 {agent_ratio}%，{'Builder 使用率较高' if agent_ratio >= 50 else 'Chat 模式仍占主导'}。"
        )
        action = "如果 Trae 是团队战略 IDE，可以推进 .traerules 和项目级配置标准化。"

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "agent_ratio": agent_ratio,
    }


def _summarize_trae_cn_team(team_data):

    """生成团队 Trae CN 使用总结。"""
    users = [d for d in team_data if d.get("status") == "submitted" and d.get("trae_cn_sessions", 0) > 0]
    total_sessions = sum(d.get("trae_cn_sessions", 0) for d in users)
    total_messages = sum(d.get("trae_cn_messages", 0) for d in users)
    total_agent = sum(d.get("trae_cn_agent_count", 0) for d in users)

    agent_ratio = round(total_agent / max(total_sessions, 1) * 100)

    if not users:
        summary = "Trae CN 本周没有汇总到有效使用数据。"
        risks = "可能是成员没有提交 Trae CN 采集，或者 Trae CN 不在团队主工作流中。"
        action = "确认 Trae CN 数据源是否连接到周报采集流程。"
    else:
        summary = (
            f"Trae CN 本周 {len(users)} 人使用，{total_sessions:,} 个会话，{total_messages:,} 条消息。"
        )
        risks = (
            f"Builder 模式占 {agent_ratio}%，{'Builder 使用率较高' if agent_ratio >= 50 else 'Chat 模式仍占主导'}。"
        )
        action = "如果 Trae CN 是团队战略 IDE，可以推进 .traerules 和项目级配置标准化。"

    return {
        "users": len(users),
        "summary": summary,
        "risks": risks,
        "action": action,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "agent_ratio": agent_ratio,
    }



def _build_team_judgement(team_data, totals, claude_summary, codex_team, opencode_summary):
    submitted = [d for d in team_data if d.get("status") == "submitted"]
    hybrid = 0
    claude_only = 0
    cli_only = 0
    dormant = totals["cur"]["missing_count"]

    for member in submitted:
        has_claude = member.get("messages", 0) > 0 or member.get("days", 0) > 0
        has_cli = (member.get("codex_sessions", 0) + member.get("opencode_sessions", 0)) > 0
        if has_claude and has_cli:
            hybrid += 1
        elif has_claude:
            claude_only += 1
        elif has_cli:
            cli_only += 1

    if codex_team["users"] > 0 and claude_summary["users"] > 0 and opencode_summary["users"] == 0:
        core_judgement = "团队已经进入 Claude + Codex 混合期，但本周新增执行更偏 Codex，OpenCode 仍未形成真实存在感。"
    elif codex_team["users"] > 0 and claude_summary["users"] == 0:
        core_judgement = "团队主工作流已经明显转向 Codex，Claude 在本周基本退出主流程。"
    elif claude_summary["users"] > 0 and codex_team["users"] == 0:
        core_judgement = "团队目前仍以 Claude 为主，Codex 只是局部补充。"
    else:
        core_judgement = "团队正在从 Claude 向 CLI 代理工作流迁移，但工具分工还没完全稳定。"

    adoption = (
        f"本周提交 {totals['cur']['member_count']}/{totals['cur']['expected_count']} 人，"
        f"混合型 {hybrid} 人，Claude-only {claude_only} 人，CLI-only {cli_only} 人，缺报 {dormant} 人。"
    )

    risk_parts = []
    if totals["cur"]["coverage_rate"] < 80:
        risk_parts.append("覆盖率偏低，团队视图存在盲区")
    if codex_team["aborted_turns"] + codex_team["compactions"] >= 10:
        risk_parts.append("Codex 长上下文与中断成本高")
    if opencode_summary["users"] == 0:
        risk_parts.append("OpenCode 面板当前没有有效数据")
    if claude_summary["users"] <= max(1, totals["cur"]["member_count"] // 4):
        risk_parts.append("Claude 数据已经不再代表团队主流程")
    risk_judgement = "；".join(risk_parts) if risk_parts else "当前没有明显结构性风险，主要是继续提高覆盖率和可比较性。"

    next_action = "下一步不要再加卡片，优先把团队判断绑定到工具结构、成员分层和执行摩擦上。"

    return {
        "core": core_judgement,
        "adoption": adoption,
        "risk": risk_judgement,
        "next_action": next_action,
    }


_CN_MAP = {
    "What You Wanted": "你的主要诉求",
    "Top Tools Used": "工具使用排行",
    "Languages": "语言分布",
    "Session Types": "会话类型",
    "User Response Time Distribution": "用户响应时长分布",
    "Multi-Clauding (Parallel Sessions)": "并行会话",
    "User Messages by Time of Day": "按时段消息分布",
    "Tool Errors Encountered": "工具报错类型",
    "What Helped Most (Claude's Capabilities)": "最有帮助的能力",
    "Outcomes": "结果分布",
    "Primary Friction Types": "主要阻塞类型",
    "Inferred Satisfaction (model-estimated)": "满意度推断（模型估计）",
    "Bug Fix": "修复问题",
    "Feature Implementation": "功能实现",
    "Information Seeking": "信息查询",
    "Configuration Setup": "配置搭建",
    "Add Feature": "新增功能",
    "Fix Report Format": "修复报告格式",
    "Read": "读取",
    "Write": "写入",
    "Edit": "编辑",
    "ToolSearch": "工具检索",
    "Iterative Refinement": "迭代优化",
    "Multi Task": "多任务",
    "Single Task": "单任务",
    "Exploration": "探索",
    "Quick Question": "快速问答",
    "Morning (6-12)": "上午 (6-12)",
    "Afternoon (12-18)": "下午 (12-18)",
    "Evening (18-24)": "晚上 (18-24)",
    "Night (0-6)": "凌晨 (0-6)",
    "Command Failed": "命令失败",
    "Other": "其他",
    "File Not Found": "文件不存在",
    "User Rejected": "用户拒绝",
    "File Too Large": "文件过大",
    "Multi-file Changes": "多文件修改",
    "Good Explanations": "解释清晰",
    "Fast/Accurate Search": "检索准确",
    "Good Debugging": "调试能力",
    "Correct Code Edits": "代码修改正确",
    "Proactive Help": "主动辅助",
    "Not Achieved": "未达成",
    "Partially Achieved": "部分达成",
    "Mostly Achieved": "基本达成",
    "Fully Achieved": "完全达成",
    "Wrong Approach": "方向错误",
    "Buggy Code": "代码缺陷",
    "Misunderstood Request": "需求误解",
    "User Rejected Action": "操作被拒绝",
    "Excessive Changes": "改动过大",
    "Tool Infrastructure Issues": "工具基础设施问题",
    "Frustrated": "挫败",
    "Dissatisfied": "不满意",
    "Likely Satisfied": "可能满意",
    "Satisfied": "满意",
    "Bash": "Bash",
    "Markdown": "Markdown",
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "JSON": "JSON",
    "CSS": "CSS",
    "Python": "Python",
    "AI Tool Configuration & Governance": "AI 工具配置与治理",
    "Web Performance & Quality Analysis Tools": "Web 性能与质量分析工具",
    "Figma Design Intelligence & Audit": "Figma 设计智能与审计",
    "Data Visualization & Poster Generator": "数据可视化与海报生成",
    "Claude Code Ecosystem & Plugin Setup": "Claude Code 生态与插件配置",
    "What You Work On": "你的工作内容",
    "How You Use Claude Code": "如何使用 Claude Code",
    "Impressive Things You Did": "你做的那些令人印象深刻的事",
    "Where Things Go Wrong": "哪里出了问题",
    "Existing CC Features to Try": "现有 CC 功能可供尝试",
    "New Ways to Use Claude Code": "使用 Claude Code 的新方法",
    "On the Horizon": "地平线上",
    "Team Feedback": "团队反馈",
    # ── generate_html i18n ──
        "Messages": "消息",
    "Code Lines": "代码行",
    "Files": "文件",
    "Active Days": "活跃天",
            "Avg Msgs/Day": "日均消息",
    "CLI Sessions(CX+OC)": "CLI 会话(CX+OC)",
    "Report": "报告",
    "Missing this period": "本周缺报",
    "Core Judgment": "核心判断",
    "Team Stratification": "团队分层",
    "Current Risks": "当前风险",
    "Next Steps": "下一步",
    "Tool Portrait": "工具画像",
    " people": "人",
        "Top Tools": "高频工具",
    "Top Languages": "高频语言",
    "Work Areas": "工作领域",
    "Top Commands": "高频命令",
    "Top Projects": "高频项目",
    "Active Projects": "活跃项目",
    "Common Highlights": "常见亮点",
    "Common Friction Points": "常见摩擦点",
    "Top Suggestions": "高频建议",
    "Members": "成员",
    "Member Details": "成员详情",
    "vs": "对比",
        "N/A": "暂无",
    "Active Dirs": "活跃目录",
    "Model Distribution": "模型分布",
    "Active Agents": "活跃 Agent",
    "Trigger Sources": "触发来源",
    "Codex Members": "Codex 成员",
    "Main Projects": "主要项目",
    "Generated on": "生成于",
    "Team Report": "团队报告",
    "Missing (last:": "缺报（上次:",
    "No history": "无历史",
    "View full report": "查看完整报告",
}


def _cn(s):
    """Bilingual translation. zh: EN->CN via CN_MAP+i18n. en: any->EN via i18n+reverse CN_MAP."""
    if _LANG == "en":
        # Reverse: try i18n first, then reverse CN_MAP
        result = _I18N(s)
        if result != s:
            return result
        # Build reverse lookup once
        _cn._reverse = getattr(_cn, '_reverse', None) or {v: k for k, v in _CN_MAP.items()}
        _cn._reverse = _cn._reverse or {v: k for k, v in _CN_MAP.items()}
        return _cn._reverse.get(s, s)
    # zh mode: i18n first, then CN_MAP
    result = _I18N(s)
    if result != s:
        return result
    return _CN_MAP.get(s, s)


def _subtitle_to_cn(subtitle):
    if not subtitle:
        return "统计周期：本周"
    m = re.search(r"(\d[\d,]*) messages .*?\| (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", subtitle)
    if m:
        return f"统计周期：{m.group(2)} 至 {m.group(3)}，消息 {m.group(1)} 条"
    return "统计周期：本周"


def _build_cn_narrative(d):
    if _LANG == "zh":
        top_tools = "、".join(_cn(t["label"]) for t in d.get("tools", [])[:3]) if d.get("tools") else "暂无"
        top_langs = "、".join(_cn(l["label"]) for l in d.get("languages", [])[:3]) if d.get("languages") else "暂无"
        return (
            f"本周期消息 {d.get('messages', 0)} 条，活跃 {d.get('days', 0)} 天，"
            f"处理文件 {d.get('files', 0)} 个，日均 {d.get('msgs_day', 0)}。"
            f"主要工具：{top_tools}；主要语言：{top_langs}。"
        )
    top_tools = ", ".join(t["label"] for t in d.get("tools", [])[:3]) if d.get("tools") else "N/A"
    top_langs = ", ".join(l["label"] for l in d.get("languages", [])[:3]) if d.get("languages") else "N/A"
    return (
        f"Messages this period: {d.get('messages', 0)}, "
        f"active {d.get('days', 0)} days, "
        f"{d.get('files', 0)} files handled, "
        f"avg {d.get('msgs_day', 0)}/day. "
        f"Top tools: {top_tools}; "
        f"Top languages: {top_langs}."
    )


_AREA_DESC_CN = {
    "AI Tool Configuration & Governance": "在跨项目统一和审核 AI 工具配置（Claude Code、Codex、Cursor、Copilot）方面做了大量工作，包括执行配置文件单一数据源原则、创建 AI 配置审计命令、同步命名规范、建立自动操作约束及设置提交/PR 工作流命令。",
    "Web Performance & Quality Analysis Tools": "构建前端质量分析和网站性能基准测试工具，包括基于 Playwright 的分析器（支持 PC/H5），将自定义评分迁移到 Lighthouse 指标，并配置带评分系统的路由。重点在脚手架搭建、报告格式迭代和兼容性问题处理。",
    "Figma Design Intelligence & Audit": "开发 Figma 设计审核与智能平台，包含审计工具搭建、多维评审能力设计、可视化输出、MCP 连通性验证，以及从设计稿生成高还原页面。重点在规划、实现与集成联调。",
    "Data Visualization & Poster Generator": "构建交互式数据可视化组件，包括树状热图、数据编辑模态和布局修复，并对算法、列宽、文本缩放和定位问题进行多轮迭代优化。",
    "Claude Code Ecosystem & Plugin Setup": "探索并配置 Claude Code 开发环境，包括工具安装、插件接入、协作模式配置、使用报告生成与插件问题排查，重点在自动化安装、配置治理和流程打通。",
}


def _area_desc_cn(area_name, raw_desc):
    if area_name in _AREA_DESC_CN:
        return _AREA_DESC_CN[area_name]
    return "该领域为本周期的重要工作方向，建议结合下方完整统计图表进行复盘。"


def _localize_text(text):
    """对可识别的英文短语做中文替换（仅 zh 模式），保留无法准确翻译的原文。"""
    if _LANG == "en":
        return text
    pairs = [
        ("Key pattern:", "关键模式："),
        ("What's working:", "做得好的："),
        ("What's hindering you:", "阻碍点："),
        ("Quick wins to try:", "可快速尝试："),
        ("Ambitious workflows:", "进阶工作流："),
    ]
    out = text
    for src, dst in pairs:
        out = out.replace(src, dst)
    return out


_LOW_VALUE_SECTION_KEYS = {
    "核心结论",
    "风险与动作",
    "风险动作",
    "数据一致性校验",
    "数据校验",
    "一致性校验",
}


def _should_skip_section(sec):
    """过滤低价值章节，突出成员深度报告。"""
    sec_id = str(sec.get("id", "")).strip().lower()
    title = str(sec.get("title", "")).strip()
    compact = re.sub(r"\s+", "", title)
    if compact in _LOW_VALUE_SECTION_KEYS:
        return True
    # 兼容不同模板的英文/缩写 id
    return sec_id in {"summary", "quality", "risk", "risks", "action", "actions"}


_SEC_THEME = {
    "section-work":     ("sec-work",     "工作领域"),
    "section-usage":    ("sec-usage",    "如何使用 Claude Code"),
    "section-wins":     ("sec-wins",     "令人印象深刻的事"),
    "section-friction": ("sec-friction", "哪里出了问题"),
    "section-features": ("sec-features", "现有功能可供尝试"),
    "section-patterns": ("sec-patterns", "使用新方法"),
    "section-horizon":  ("sec-horizon",  "地平线上"),
}


def generate_html(team_data, totals):
    """生成团队汇总 HTML — 单一报告：本周 + 累计。"""
    from datetime import datetime
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    period_label_var = totals["period_label_var"]
    prev_label = totals.get("prev_period_label")
    cur = totals["cur"]
    cum = totals["cum"]

    # 按组构建成员详情表格
    all_groups = totals.get("all_groups", ["group"])
    claude_summary = _aggregate_claude_insights(team_data, totals)
    codex_summary = _aggregate_codex_insights(team_data)
    codex_team = _summarize_codex_team(team_data)
    opencode_summary = _aggregate_opencode_insights(team_data)
    cursor_summary = _aggregate_cursor_insights(team_data)
    cursor_team = _summarize_cursor_team(team_data)
    trae_summary = _aggregate_trae_insights(team_data)
    trae_team = _summarize_trae_team(team_data)
    trae_cn_summary = _aggregate_trae_cn_insights(team_data)
    trae_cn_team = _summarize_trae_cn_team(team_data)
    openclaw_summary = _aggregate_openclaw_insights(team_data)
    openclaw_team = _summarize_openclaw_team(team_data)
    hermes_summary = _aggregate_hermes_insights(team_data)
    hermes_team = _summarize_hermes_team(team_data)
    team_judgement = _build_team_judgement(team_data, totals, claude_summary, codex_team, opencode_summary)
    codex_members = _top_codex_members(team_data)
    cursor_members = _top_cursor_members(team_data)

    def _build_member_table(group_members, table_id=""):
        rows = ""
        for i, d in enumerate(group_members):
            is_missing = d.get("status") == "missing"

            status_badge = ""
            if is_missing:
                last_seen = d.get("last_seen_week") or _cn("No history")
                status_badge = f'<span class="status-miss">{_cn("Missing (last:")}{last_seen})</span>'

            messages_val = f"{d['messages']:,}" if not is_missing else "—"
            code_val = f"+{d['lines_added']:,}/-{d['lines_removed']:,}" if not is_missing else "—"
            files_val = f"{d['files']:,}" if not is_missing else "—"
            days_val = f"{d['days']}" if not is_missing else "—"
            avg_val = f"{d['msgs_day']}" if not is_missing else "—"
            messages_sort = d["messages"] if not is_missing else -1
            code_sort = (d["lines_added"] + d["lines_removed"]) if not is_missing else -1
            files_sort = d["files"] if not is_missing else -1
            days_sort = d["days"] if not is_missing else -1
            avg_sort = d["msgs_day"] if not is_missing else -1

            report_link = "—"
            rp = d.get("report_relpath")
            if rp and not is_missing:
                report_link = (
                    f'<a class="report-icon" href="{_esc(rp)}" target="_blank" '
                    f'title="{_cn("View full report")}" aria-label="{_cn("View full report")} ({_esc(d["display_name"])})">'
                    f'&#128279;</a>'
                )

            rows += f'''
            <tr>
              <td class="col-member"><span class="rank seq">{i+1}</span> <span class="member-name-cell">{_esc(d["display_name"])}</span> {status_badge}</td>
              <td class="num" data-sort-value="{messages_sort}">{messages_val}</td>
              <td class="num" data-sort-value="{code_sort}">{code_val}</td>
              <td class="num" data-sort-value="{files_sort}">{files_val}</td>
              <td class="num" data-sort-value="{days_sort}">{days_val}</td>
              <td class="num" data-sort-value="{avg_sort}">{avg_val}</td>
              <td class="report">{report_link}</td>
            </tr>'''

        tid = f' id="{table_id}"' if table_id else ""
        return f'''
        <div class="member-table-wrap">
          <table class="member-table"{tid}>
            <thead>
              <tr>
                <th style="text-align:left">{_cn("Members")}</th>
                <th class="sortable" data-col="1">{_cn("Messages")}<span class="sort-arrow"></span></th>
                <th class="sortable" data-col="2">{_cn("Code Lines")}<span class="sort-arrow"></span></th>
                <th class="sortable" data-col="3">{_cn("Files")}<span class="sort-arrow"></span></th>
                <th class="sortable" data-col="4">{_cn("Active Days")}<span class="sort-arrow"></span></th>
                <th class="sortable" data-col="5">{_cn("Avg/Day")}<span class="sort-arrow"></span></th>
                <th>{_cn("报告")}</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
        </div>'''

    # 按组生成表格
    group_tables_html = ""
    for group in all_groups:
        group_members = [d for d in team_data if d.get("group") == group]
        if not group_members:
            continue
        group_submitted = [d for d in group_members if d.get("status") == "submitted"]
        group_label = group.upper()
        group_count = f"{len(group_submitted)}/{len(group_members)}"
        group_tables_html += f'''
    <h2><span class="group-tag">{_esc(group_label)}</span> {_cn("Member Details")} <span style="font-size:14px;color:#64748b;font-weight:400">({group_count})</span></h2>
    {_build_member_table(group_members, table_id=f"table-{group}")}'''

    member_table_html = group_tables_html

    compare_html = f'<span class="week-tag" style="background:#f1f5f9;color:#64748b">{_cn("vs")} {prev_label}</span>' if prev_label else ""
    coverage_html = f' · {_cn("Coverage")} {cur["member_count"]}/{cur["expected_count"]} ({cur["coverage_rate"]}%)' if cur["expected_count"] else ""
    missing_tip_html = ""
    if totals.get("missing_members"):
        missing_tip_html = f'<div class="missing-tip">{_cn("本周缺报")} {len(totals["missing_members"])} {_cn(" people")}：{_esc(", ".join(totals["missing_members"]))}</div>'

    def _pill_list(items, value_fmt=None, empty_text=_cn("N/A")):
        if not items:
            return f'<span class="pill-empty">{_esc(empty_text)}</span>'
        html = ""
        for name, count in items:
            value = value_fmt(count) if value_fmt else count
            html += f'<span class="insight-pill">{_esc(str(name))} <b>{_esc(str(value))}</b></span>'
        return html

    patterns_html = ""
    if claude_summary["patterns"]:
        pattern_rows = []
        for item in claude_summary["patterns"]:
            pattern_rows.append(
                f'<div class="quote-item"><strong>{_esc(item["member"])}</strong>：{_esc(item["pattern"])}</div>'
            )
        patterns_html = "".join(pattern_rows)

    executive_html = f'''
    <div class="executive-panel">
      <div class="executive-card">
        <div class="executive-title">{_cn("核心判断")}</div>
        <div class="executive-text">{_esc(team_judgement["core"])}</div>
      </div>
      <div class="executive-card">
        <div class="executive-title">{_cn("团队分层")}</div>
        <div class="executive-text">{_esc(team_judgement["adoption"])}</div>
      </div>
      <div class="executive-card">
        <div class="executive-title">{_cn("当前风险")}</div>
        <div class="executive-text">{_esc(team_judgement["risk"])}</div>
      </div>
      <div class="executive-card">
        <div class="executive-title">{_cn("下一步")}</div>
        <div class="executive-text">{_esc(team_judgement["next_action"])}</div>
      </div>
    </div>'''

    tool_insights_html = f'''
    <h2>{_cn("工具画像")}</h2>
    <div class="tool-grid">
      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">Claude Code</span>
          <span class="tool-stat">{claude_summary["users"]} {_cn("人")}</span>
        </div>
        <p class="tool-summary">{_esc(claude_summary["summary"])}</p>
        <p class="tool-risk">{_esc(claude_summary["risks"])}</p>
        <p class="tool-action">{_esc(claude_summary["action"])}</p>
        <div class="tool-subtitle">{_cn("高频工具")}</div>
        <div class="pill-row">{_pill_list(claude_summary["top_tools"])}</div>
        <div class="tool-subtitle">{_cn("高频语言")}</div>
        <div class="pill-row">{_pill_list(claude_summary["top_languages"])}</div>
        <div class="tool-subtitle">{_cn("工作领域")}</div>
        <div class="pill-row">{_pill_list(claude_summary["top_areas"])}</div>
        {f'<div class="quote-list">{patterns_html}</div>' if patterns_html else ''}
      </div>

      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">Codex</span>
          <span class="tool-stat">{codex_team["users"]} {_cn("人")} / {cur["codex_sessions"]:,} {_cn("会话")}</span>
        </div>
        <p class="tool-summary">{_esc(codex_team["summary"])}</p>
        <p class="tool-risk">{_esc(codex_team["risks"])}</p>
        <p class="tool-action">{_esc(codex_team["action"])}</p>
        <div class="tool-subtitle">{_cn("高频工具")}</div>
        <div class="pill-row">{_pill_list(codex_summary["tools"])}</div>
        <div class="tool-subtitle">{_cn("高频命令")}</div>
        <div class="pill-row">{_pill_list(codex_summary["commands"])}</div>
        <div class="tool-subtitle">{_cn("高频项目")}</div>
        <div class="pill-row">{_pill_list(codex_summary["projects"])}</div>
      </div>

      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">OpenCode</span>
          <span class="tool-stat">{opencode_summary["users"]} {_cn("人")} / {cur["opencode_sessions"]:,} {_cn("会话")}</span>
        </div>
        <p class="tool-summary">{_esc(opencode_summary["summary"])}</p>
        <p class="tool-risk">{_esc(opencode_summary["risks"])}</p>
        <p class="tool-action">{_esc(opencode_summary["action"])}</p>
        <div class="tool-subtitle">{_cn("Active Dirs")}</div>
        <div class="pill-row">{_pill_list(opencode_summary["top_areas"])}</div>
      </div>

      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">Cursor</span>
          <span class="tool-stat">{cursor_team["users"]} {_cn("人")} / {cur["cursor_sessions"]:,} {_cn("会话")}</span>
        </div>
        <p class="tool-summary">{_esc(cursor_team["summary"])}</p>
        <p class="tool-risk">{_esc(cursor_team["risks"])}</p>
        <p class="tool-action">{_esc(cursor_team["action"])}</p>
        <div class="tool-subtitle">{_cn("活跃项目")}</div>
        <div class="pill-row">{_pill_list(cursor_summary["top_areas"])}</div>
        <div class="tool-subtitle">{_cn("Model Distribution")}</div>
        <div class="pill-row">{_pill_list(cursor_summary["top_models"])}</div>
      </div>

      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">Trae</span>
          <span class="tool-stat">{trae_team["users"]} {_cn("人")} / {cur["trae_sessions"]:,} {_cn("会话")}</span>
        </div>
        <p class="tool-summary">{_esc(trae_team["summary"])}</p>
        <p class="tool-risk">{_esc(trae_team["risks"])}</p>
        <p class="tool-action">{_esc(trae_team["action"])}</p>
        <div class="tool-subtitle">{_cn("活跃项目")}</div>
        <div class="pill-row">{_pill_list(trae_summary["top_areas"])}</div>
      </div>

      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">Trae CN</span>
          <span class="tool-stat">{trae_cn_team["users"]} {_cn("人")} / {cur["trae_cn_sessions"]:,} {_cn("会话")}</span>
        </div>
        <p class="tool-summary">{_esc(trae_cn_team["summary"])}</p>
        <p class="tool-risk">{_esc(trae_cn_team["risks"])}</p>
        <p class="tool-action">{_esc(trae_cn_team["action"])}</p>
        <div class="tool-subtitle">{_cn("活跃项目")}</div>
        <div class="pill-row">{_pill_list(trae_cn_summary["top_areas"])}</div>
      </div>

      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">OpenClaw</span>
          <span class="tool-stat">{openclaw_team["users"]} {_cn("人")} / {cur["openclaw_sessions"]:,} {_cn("会话")}</span>
        </div>
        <p class="tool-summary">{_esc(openclaw_team["summary"])}</p>
        <p class="tool-risk">{_esc(openclaw_team["risks"])}</p>
        <p class="tool-action">{_esc(openclaw_team["action"])}</p>
        <div class="tool-subtitle">{_cn("Active Agents")}</div>
        <div class="pill-row">{_pill_list(openclaw_summary["top_agents"])}</div>
        <div class="tool-subtitle">{_cn("Trigger Sources")}</div>
        <div class="pill-row">{_pill_list(openclaw_summary["top_sources"])}</div>
      </div>

      <div class="tool-card">
        <div class="tool-card-head">
          <span class="tool-name">Hermes</span>
          <span class="tool-stat">{hermes_team["users"]} {_cn("人")} / {cur["hermes_sessions"]:,} {_cn("会话")}</span>
        </div>
        <p class="tool-summary">{_esc(hermes_team["summary"])}</p>
        <p class="tool-risk">{_esc(hermes_team["risks"])}</p>
        <p class="tool-action">{_esc(hermes_team["action"])}</p>
        <div class="tool-subtitle">{_cn("高频工具")}</div>
        <div class="pill-row">{_pill_list(hermes_summary["top_tools"])}</div>
        <div class="tool-subtitle">{_cn("Model Distribution")}</div>
        <div class="pill-row">{_pill_list(hermes_summary["top_models"])}</div>
      </div>
    </div>'''

    codex_summary_html = ""
    if cur["codex_sessions"] > 0:
        codex_member_rows = ""
        for d in codex_members:
            insight = d.get("codex_insights") or {}
            area = (insight.get("top_area") or {}).get("name") or "—"
            codex_member_rows += f'''
            <tr>
              <td>{_esc(d["display_name"])}</td>
              <td class="num">{d.get("codex_sessions", 0):,}</td>
              <td class="num">{d.get("codex_tokens", 0):,}</td>
              <td>{_esc(area)}</td>
            </tr>'''

        codex_summary_html = f'''
    <h2>Codex Insights</h2>
    <div class="codex-panel">
      <div class="codex-grid">
        <div class="codex-card">
          <div class="codex-card-title">{_cn("Top Tools")}</div>
          <div class="pill-row">{_pill_list(codex_summary["tools"])}</div>
        </div>
        <div class="codex-card">
          <div class="codex-card-title">{_cn("Top Commands")}</div>
          <div class="pill-row">{_pill_list(codex_summary["commands"])}</div>
        </div>
        <div class="codex-card">
          <div class="codex-card-title">{_cn("Top Projects")}</div>
          <div class="pill-row">{_pill_list(codex_summary["projects"])}</div>
        </div>
        <div class="codex-card">
          <div class="codex-card-title">{_cn("Common Highlights")}</div>
          <div class="pill-row">{_pill_list(codex_summary["wins"])}</div>
        </div>
        <div class="codex-card">
          <div class="codex-card-title">{_cn("Common Friction Points")}</div>
          <div class="pill-row">{_pill_list(codex_summary["frictions"])}</div>
        </div>
        <div class="codex-card">
          <div class="codex-card-title">{_cn("Top Suggestions")}</div>
          <div class="pill-row">{_pill_list(codex_summary["suggestions"])}</div>
        </div>
      </div>

      <div class="member-table-wrap" style="margin-top:14px">
        <table class="member-table">
          <thead>
            <tr>
              <th style="text-align:left">{_cn("Codex Members")}</th>
              <th>{_cn("会话")}</th>
              <th>Tokens</th>
              <th style="text-align:left">{_cn("Main Projects")}</th>
            </tr>
          </thead>
          <tbody>{codex_member_rows}</tbody>
        </table>
      </div>
    </div>'''

    return f'''<!DOCTYPE html>
<html lang="{"zh-CN" if _LANG == "zh" else "en"}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Claude Code Team Report — {period_label_var}</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif; background: #f8fafc; color: #334155; line-height: 1.65; padding: 40px 24px; -webkit-font-smoothing: antialiased; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ font-size: 32px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }}
    h2 {{ font-size: 20px; font-weight: 600; color: #0f172a; margin-top: 24px; margin-bottom: 10px; }}
    .subtitle {{ color: #64748b; font-size: 15px; margin-bottom: 6px; }}

    /* ── Team stats ── */
    .team-stats {{ display: flex; gap: 0; margin: 20px 0; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; background: white; }}
    .ts {{ flex: 1; padding: 20px 16px; text-align: center; border-right: 1px solid #e2e8f0; }}
    .ts:last-child {{ border-right: none; }}
    .ts-val {{ font-size: 26px; font-weight: 700; color: #0f172a; line-height: 1.2; }}
    .ts-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}
    .ts-delta {{ margin-top: 4px; min-height: 20px; }}

    /* ── Badges ── */
    .badge-up {{ display: inline-block; font-size: 11px; font-weight: 500; padding: 1px 6px; border-radius: 4px; background: #ecfdf5; color: #059669; border: 1px solid #a7f3d0; }}
    .badge-down {{ display: inline-block; font-size: 11px; font-weight: 500; padding: 1px 6px; border-radius: 4px; background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }}
    .week-tag {{ font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 20px; background: #eef2ff; color: #4f46e5; }}

    /* ── Member table ── */
    .member-table-wrap {{ margin-top: 10px; overflow-x: auto; border: 1px solid #e2e8f0; border-radius: 10px; background: white; }}
    .member-table {{ width: 100%; border-collapse: collapse; min-width: 760px; }}
    .member-table thead {{ background: #f8fafc; }}
    .member-table th {{
      padding: 12px 14px; font-size: 12px; font-weight: 600; color: #64748b;
      text-transform: uppercase; letter-spacing: 0.4px; border-bottom: 1px solid #e2e8f0; text-align: right;
    }}
    .member-table th.sortable {{ cursor: pointer; user-select: none; }}
    .member-table th .sort-arrow {{ margin-left: 6px; color: #cbd5e1; font-size: 11px; }}
    .member-table th.sortable.active .sort-arrow {{ color: #475569; }}
    .member-table th.sortable.asc .sort-arrow::before {{ content: "▲"; }}
    .member-table th.sortable.desc .sort-arrow::before {{ content: "▼"; }}
    .member-table td {{ padding: 11px 14px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
    .member-table tbody tr:last-child td {{ border-bottom: none; }}
    .member-table td.num {{ text-align: right; color: #0f172a; font-weight: 600; white-space: nowrap; }}
    .member-table td.report {{ text-align: center; color: #94a3b8; }}
    .member-table td.col-member {{ text-align: left; white-space: nowrap; }}
    .member-name-cell {{ font-weight: 700; color: #0f172a; margin-right: 8px; }}
    .report-icon {{
      display: inline-flex; align-items: center; justify-content: center;
      width: 28px; height: 28px; border-radius: 6px;
      border: 1px solid #c7d2fe; background: #eef2ff; color: #4f46e5;
      text-decoration: none; font-size: 14px;
    }}
    .report-icon:hover {{ background: #e0e7ff; }}

    .rank {{ font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; background: #f1f5f9; color: #475569; }}
    .status-miss {{ font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px; background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }}

    /* ── Misc ── */
    .missing-tip {{ margin-top: 12px; font-size: 13px; color: #92400e; background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px; padding: 10px 14px; }}
    .group-tag {{ font-size: 13px; font-weight: 700; padding: 3px 10px; border-radius: 6px; background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; text-transform: uppercase; letter-spacing: 0.5px; }}
    .executive-panel {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 18px 0 22px; }}
    .executive-card {{ background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%); border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; }}
    .executive-title {{ font-size: 12px; color: #64748b; text-transform: uppercase; font-weight: 700; margin-bottom: 8px; }}
    .executive-text {{ font-size: 14px; color: #0f172a; line-height: 1.7; }}
    .tool-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 10px; }}
    .tool-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; }}
    .tool-card-head {{ display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 10px; }}
    .tool-name {{ font-size: 16px; font-weight: 700; color: #0f172a; }}
    .tool-stat {{ font-size: 12px; color: #475569; background: #f8fafc; border-radius: 999px; padding: 4px 10px; }}
    .tool-summary, .tool-risk, .tool-action {{ font-size: 13px; color: #334155; margin-bottom: 8px; line-height: 1.7; }}
    .tool-risk {{ color: #9a3412; }}
    .tool-action {{ color: #1d4ed8; }}
    .tool-subtitle {{ font-size: 11px; color: #64748b; text-transform: uppercase; font-weight: 700; margin: 12px 0 6px; }}
    .quote-list {{ margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }}
    .quote-item {{ font-size: 12px; color: #475569; background: #f8fafc; border-radius: 8px; padding: 8px 10px; }}
    .codex-panel {{ margin-top: 10px; }}
    .codex-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .codex-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px; }}
    .codex-card-title {{ font-size: 12px; color: #64748b; text-transform: uppercase; margin-bottom: 8px; font-weight: 700; }}
    .pill-row {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .insight-pill {{ display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; border-radius: 999px; background: #eef2ff; color: #4338ca; font-size: 12px; }}
    .insight-pill b {{ color: #312e81; }}
    .pill-empty {{ font-size: 12px; color: #94a3b8; }}

    @media (max-width: 640px) {{
      .team-stats {{ flex-wrap: wrap; }}
      .ts {{ min-width: 80px; }}
      .member-table {{ min-width: 640px; }}
      .executive-panel {{ grid-template-columns: 1fr; }}
      .tool-grid {{ grid-template-columns: 1fr; }}
      .codex-grid {{ grid-template-columns: 1fr; }}
    }}
    @media print {{
      body {{ background: white; padding: 24px; }}
      .member-table-wrap {{ break-inside: avoid; box-shadow: none; }}
      @page {{ margin: 1.5cm; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>{_cn("Team Report")}</h1>
    <p class="subtitle">{period_label_var} · {cur['member_count']} {_cn("Members")}{coverage_html} {compare_html}</p>
    <p style="font-size:13px;color:#94a3b8">{_cn("Generated on")} {generated_at}</p>
    {missing_tip_html}
    {executive_html}

    <div class="team-stats">
      <div class="ts">
        <div class="ts-val">{cur['member_count']}</div>
        <div class="ts-label">{_cn("成员")}</div>
        <div class="ts-delta"></div>
      </div>
      <div class="ts">
        <div class="ts-val">{cur['messages']:,}</div>
        <div class="ts-label">{_cn("消息")}</div>
        <div class="ts-delta">{_delta_badge(totals['delta_messages'])}</div>
      </div>
      <div class="ts">
        <div class="ts-val">+{cur['added']:,}</div>
        <div class="ts-label">{_cn("新增行")}</div>
        <div class="ts-delta"></div>
      </div>
      <div class="ts">
        <div class="ts-val">{cur['files']:,}</div>
        <div class="ts-label">{_cn("文件")}</div>
        <div class="ts-delta">{_delta_badge(totals['delta_files'])}</div>
      </div>
      <div class="ts">
        <div class="ts-val">{cur['avg_msgs_day']}</div>
        <div class="ts-label">{_cn("日均消息")}</div>
        <div class="ts-delta">{_delta_badge(totals['delta_avg_msgs_day'])}</div>
      </div>
      <div class="ts">
        <div class="ts-val">{cur['codex_sessions'] + cur['opencode_sessions']}</div>
        <div class="ts-label">{_cn("CLI 会话(CX+OC)")}</div>
        <div class="ts-delta"></div>
      </div>
      <div class="ts">
        <div class="ts-val">{(cur['codex_tokens'] + cur['opencode_tokens']):,}</div>
        <div class="ts-label">CLI Tokens(CX+OC)</div>
        <div class="ts-delta"></div>
      </div>
    </div>

    {tool_insights_html}

    {codex_summary_html}

    {member_table_html}

    <p style="margin-top:36px;font-size:12px;color:#94a3b8;text-align:center">_cn("Team Report") · {generated_at} · analyze.py</p>
  </div>
  <script>
    document.querySelectorAll(".member-table").forEach((table) => {{
      const tbody = table.querySelector("tbody");
      const headers = Array.from(table.querySelectorAll("th.sortable"));

      const sortBy = (colIndex, direction) => {{
        const rows = Array.from(tbody.querySelectorAll("tr"));
        rows.sort((a, b) => {{
          const av = Number(a.children[colIndex].dataset.sortValue ?? -1);
          const bv = Number(b.children[colIndex].dataset.sortValue ?? -1);
          return direction === "asc" ? av - bv : bv - av;
        }});
        rows.forEach((row) => tbody.appendChild(row));
        rows.forEach((row, idx) => {{
          const seq = row.querySelector(".seq");
          if (seq) seq.textContent = String(idx + 1);
        }});
      }};

      headers.forEach((th) => {{
        th.classList.remove("active");
        th.classList.remove("asc");
        th.classList.remove("desc");
        th.addEventListener("click", () => {{
          const colIndex = Number(th.dataset.col);
          const next = th.classList.contains("desc") ? "asc" : "desc";
          headers.forEach((h) => h.classList.remove("active", "asc", "desc"));
          th.classList.add("active", next);
          sortBy(colIndex, next);
        }});
      }});

      const defaultHeader = table.querySelector('th.sortable[data-col="4"]');
      if (defaultHeader) {{
        defaultHeader.classList.add("active", "desc");
        sortBy(4, "desc");
      }}
    }});
  </script>
</body>
</html>'''


def _esc(s):
    """HTML 转义。"""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


if __name__ == "__main__":
    import argparse
    import urllib.request

    parser = argparse.ArgumentParser(description="生成团队 Claude Code 使用报告")
    parser.add_argument(
        "--period", "-p",
        type=period_type_arg,
        default="weekly",
        help="报告周期: weekly (周报, 默认), monthly (月报), quarterly (季报), annual (年报)",
    )
    parser.add_argument(
        "--lang", "-l",
        choices=["zh", "en"],
        default=None,
        help="报告语言: zh (中文, 默认), en (英文). 默认从 AGENTS_REPORT_LANG 环境变量读取或根据提问语言自动判断",
    )
    args, _ = parser.parse_known_args(sys.argv[1:])

    # Set language for report generation
    if args.lang:
        os.environ["AGENTS_REPORT_LANG"] = args.lang
    # Re-init i18n after lang is set
    set_lang(os.environ.get("AGENTS_REPORT_LANG", "zh"))

    report_url = os.environ.get("AGENTS_REPORT_URL", "").strip()
    if report_url:
        # 不在本地生成，通知 Dashboard 执行服务端分析
        target = f"{report_url.rstrip('/')}/api/analyze?period_type={args.period}&lang={os.environ.get('AGENTS_REPORT_LANG', 'zh')}"
        req = urllib.request.Request(target, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                print(f"团队报告已通过 Dashboard 生成: {result.get('path')}")
                sys.exit(0)
            else:
                print(f"Dashboard 分析失败: {result.get('error')}", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            print(f"Dashboard 分析请求失败: {e}", file=sys.stderr)
            sys.exit(1)

    # 本地模式：直接生成团队报告
    period = current_period(args.period)
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    reports_dir = os.path.join(project_dir, "reports")
    output_dir = os.path.join(project_dir, "output")
    members_path = os.path.join(project_dir, "scripts", "members.json")
    success = generate_team_report(reports_dir, output_dir, members_path, period)
    sys.exit(0 if success else 1)
