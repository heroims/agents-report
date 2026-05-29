#!/usr/bin/env python3
"""采集 OpenCode 使用数据，生成与现有周报兼容的 HTML。"""

import argparse
import datetime
import html
import json
import os
import re
import sqlite3
import subprocess
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
from i18n import T as _I18nT
_LANG = _I18nT.detect()
_I18N = _I18nT(_LANG)


# parse_week replaced by period_start_end from period_utils


def epoch_ms(dt_obj):
    """datetime -> unix epoch milliseconds（本地时区语义）。"""
    return int(dt_obj.timestamp() * 1000)


def format_tokens(n):
    """Token 数字格式化。"""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def safe_text(value):
    return html.escape(str(value or ""))


def workstream_label(index):
    return f"Workstream {index + 1}"


def resolve_db_path():
    """通过 opencode db path 动态获取 sqlite 路径。"""
    try:
        result = subprocess.run(
            ["opencode", "db", "path"],
            check=True,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"无法执行 'opencode db path': {exc}") from exc

    db_path = (result.stdout or "").strip()
    if not db_path:
        raise RuntimeError("opencode db path 未返回有效路径")
    path = Path(db_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"OpenCode DB 不存在: {path}")
    if not os.access(path, os.R_OK):
        raise RuntimeError(f"OpenCode DB 不可读: {path}")
    return path


def list_columns(conn, table_name):
    """读取表字段名（用于 schema 探测）。"""
    cur = conn.execute(f'PRAGMA table_info("{table_name}")')
    return [row[1] for row in cur.fetchall()]


def parse_message_tokens(raw_data):
    """从 message.data JSON 中提取 token 总量（容错多种结构）。"""
    if not raw_data:
        return 0
    try:
        payload = json.loads(raw_data)
    except Exception:
        return 0

    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        if isinstance(tokens.get("total"), (int, float)):
            return int(tokens.get("total") or 0)
        base = int(tokens.get("input") or 0) + int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        return base + int(cache.get("read") or 0) + int(cache.get("write") or 0)

    if isinstance(payload.get("tokenUsage"), dict):
        token_usage = payload.get("tokenUsage")
        if isinstance(token_usage.get("total"), (int, float)):
            return int(token_usage.get("total") or 0)

    return 0


def to_basename_label(path_value):
    value = str(path_value or "").strip()
    if not value or value == "(unknown)":
        return "(unknown)"
    trimmed = value.rstrip("/\\")
    if not trimmed:
        return "(root)"
    name = Path(trimmed).name
    return name or "(root)"


def collect(period_str):
    """采集指定周 OpenCode 使用数据。"""
    period_start, period_end = period_start_end(period_str)
    week_label = period_str  # keep backward compat label
    start_ms = epoch_ms(datetime.datetime(period_start.year, period_start.month, period_start.day, 0, 0, 0))
    end_ms = epoch_ms(datetime.datetime(period_end.year, period_end.month, period_end.day, 23, 59, 59, 999000))

    db_path = resolve_db_path()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "session" not in tables:
            raise RuntimeError("OpenCode schema 缺少 session 表")

        session_cols = set(list_columns(conn, "session"))
        required_session_cols = {"id", "time_updated"}
        if not required_session_cols.issubset(session_cols):
            raise RuntimeError("OpenCode session 表缺少关键字段: id/time_updated")

        has_directory = "directory" in session_cols
        has_project_id = "project_id" in session_cols
        has_parent_id = "parent_id" in session_cols
        has_time_created = "time_created" in session_cols

        select_cols = ["id", "time_updated"]
        if has_time_created:
            select_cols.append("time_created")
        if has_directory:
            select_cols.append("directory")
        if has_project_id:
            select_cols.append("project_id")

        # 只统计主会话（parent_id IS NULL），排除子会话和子代理线程
        parent_filter = "AND parent_id IS NULL" if has_parent_id else ""
        session_sql = (
            f"SELECT {', '.join(select_cols)} FROM session "
            f"WHERE time_updated >= ? AND time_updated <= ? {parent_filter} ORDER BY time_updated DESC"
        )
        session_rows = conn.execute(session_sql, (start_ms, end_ms)).fetchall()
        if not session_rows:
            raise RuntimeError(f"OpenCode 在 {week_label} 无会话数据")

        project_name_by_id = {}
        if has_project_id and "project" in tables:
            project_cols = set(list_columns(conn, "project"))
            if "id" in project_cols:
                name_field = "name" if "name" in project_cols else "worktree" if "worktree" in project_cols else None
                if name_field:
                    for row in conn.execute(f"SELECT id, {name_field} FROM project").fetchall():
                        project_name_by_id[row[0]] = row[1] or ""

        session_map = {}
        sessions_by_day = defaultdict(set)
        areas = defaultdict(lambda: {"sessions": 0, "tokens": 0})

        for row in session_rows:
            sid = row["id"]
            updated_ms = int(row["time_updated"] or 0)
            # 用 time_created 归因活跃日，避免同一天 update 导致 active_days 虚低
            created_ms = int(row["time_created"] or 0) if has_time_created else updated_ms
            # time_created 在本周范围内才用于归因，否则退回 time_updated
            # 避免上周创建但本周 update 的 session 被归到周外日期
            day_ms = created_ms if (created_ms and start_ms <= created_ms <= end_ms) else updated_ms
            day_key = datetime.datetime.fromtimestamp(day_ms / 1000).date().isoformat()

            directory = ""
            if has_directory:
                directory = str(row["directory"] or "").strip()
            if not directory and has_project_id:
                directory = str(project_name_by_id.get(row["project_id"], "") or "").strip()
            directory = to_basename_label(directory)

            session_map[sid] = {
                "session_id": sid,
                "day": day_key,
                "directory": directory,
                "time_updated": updated_ms,
                "time_created": created_ms,
            }
            sessions_by_day[day_key].add(sid)
            areas[directory]["sessions"] += 1

        total_tokens = 0
        total_messages = 0
        daily_tokens = defaultdict(int)

        source_notes = [
            "sessions from session.time_updated filter; active_days from session.time_created" if has_time_created else "sessions/active_days from session.time_updated",
            "tokens parsed from message.data.tokens",
        ]

        if "message" in tables:
            message_cols = set(list_columns(conn, "message"))
            if {"session_id", "time_created", "data"}.issubset(message_cols):
                message_sql = (
                    "SELECT session_id, time_created, data FROM message "
                    "WHERE time_created >= ? AND time_created <= ?"
                )
                message_rows = conn.execute(message_sql, (start_ms, end_ms)).fetchall()
                for m in message_rows:
                    sid = m["session_id"]
                    if sid not in session_map:
                        continue
                    # 只统计用户发送的消息，与 CC/CX 对齐（不计 assistant 回复）
                    try:
                        msg_data = json.loads(m["data"] or "{}")
                    except Exception:
                        msg_data = {}
                    if msg_data.get("role") == "assistant":
                        # tokens 仍然累加（assistant 消耗配额）
                        token_total = parse_message_tokens(m["data"])
                        total_tokens += token_total
                        msg_day = datetime.datetime.fromtimestamp(int(m["time_created"]) / 1000).date().isoformat()
                        daily_tokens[msg_day] += token_total
                        areas[session_map[sid]["directory"]]["tokens"] += token_total
                        continue
                    total_messages += 1
                    token_total = parse_message_tokens(m["data"])
                    total_tokens += token_total
                    msg_day = datetime.datetime.fromtimestamp(int(m["time_created"]) / 1000).date().isoformat()
                    daily_tokens[msg_day] += token_total
                    areas[session_map[sid]["directory"]]["tokens"] += token_total
            else:
                source_notes.append("message table exists but lacks required columns; tokens fallback=0")
        else:
            source_notes.append("message table not found; tokens fallback=0")

        # 从 part 表解析 apply_patch 行数及 patch 文件数
        total_lines_added = 0
        total_lines_removed = 0
        modified_files: set = set()
        if "part" in tables:
            part_cols = set(list_columns(conn, "part"))
            if {"session_id", "time_created", "data"}.issubset(part_cols):
                part_sql = (
                    "SELECT session_id, data FROM part "
                    "WHERE time_created >= ? AND time_created <= ?"
                )
                for p in conn.execute(part_sql, (start_ms, end_ms)).fetchall():
                    if p["session_id"] not in session_map:
                        continue
                    try:
                        pd = json.loads(p["data"] or "{}")
                    except Exception:
                        continue
                    # apply_patch tool → 统计行数
                    if pd.get("type") == "tool" and pd.get("tool") == "apply_patch":
                        patch_text = (pd.get("state") or {}).get("input", {}).get("patchText") or ""
                        for pline in patch_text.splitlines():
                            if pline.startswith("+") and not pline.startswith("+++"):
                                total_lines_added += 1
                            elif pline.startswith("-") and not pline.startswith("---"):
                                total_lines_removed += 1
                    # patch type → 统计唯一文件数
                    elif pd.get("type") == "patch":
                        for f in pd.get("files") or []:
                            if f:
                                modified_files.add(f)
                source_notes.append("lines_added/removed from part.apply_patch.patchText; files_modified from part.patch.files")
            else:
                source_notes.append("part table lacks required columns; lines/files fallback=0")
        else:
            source_notes.append("part table not found; lines/files fallback=0")

        total_sessions = len(session_map)
        active_days = sum(1 for _, sids in sessions_by_day.items() if sids)

        daily = []
        all_days = sorted(set(sessions_by_day.keys()) | set(daily_tokens.keys()))
        for day in all_days:
            daily.append(
                {
                    "day": day,
                    "sessions": len(sessions_by_day.get(day, set())),
                    "tokens": int(daily_tokens.get(day, 0)),
                }
            )

        area_list = []
        for name, metric in areas.items():
            area_list.append(
                {
                    "cwd": name,
                    "sessions": int(metric["sessions"]),
                    "tokens": int(metric["tokens"]),
                }
            )
        area_list.sort(key=lambda x: (x["sessions"], x["tokens"]), reverse=True)

        return {
            "week": week_label,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "total_sessions": total_sessions,
            "total_messages": int(total_messages),
            "total_tokens": int(total_tokens),
            "active_days": active_days,
            "lines_added": total_lines_added,
            "lines_removed": total_lines_removed,
            "files_modified": len(modified_files),
            "daily": daily,
            "areas": area_list[:10],
            "source": {
                "db_path": db_path.name,
                "schema": {
                    "tables": sorted(tables),
                    "session_columns": sorted(session_cols),
                },
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
    daily = data.get("daily") or []
    areas = data.get("areas") or []

    avg_messages = round(total_messages / total_sessions, 1) if total_sessions else 0
    avg_tokens = round(total_tokens / total_sessions) if total_sessions else 0
    msgs_per_day = round(total_messages / active_days, 1) if active_days else 0
    tokens_per_day = round(total_tokens / active_days) if active_days else 0
    peak_day = max(daily, key=lambda item: (int(item.get("tokens") or 0), int(item.get("sessions") or 0)), default=None)
    total_area_sessions = sum(int(item.get("sessions") or 0) for item in areas) or 1
    concentration = round(int(areas[0].get("sessions") or 0) / total_area_sessions * 100) if areas else 0
    day_session_counter = Counter(int(item.get("sessions") or 0) for item in daily)
    dominant_daily_shape = day_session_counter.most_common(1)[0][0] if day_session_counter else 0

    work_on = []
    for idx, area in enumerate(areas[:5]):
        sessions = int(area.get("sessions") or 0)
        tokens = int(area.get("tokens") or 0)
        intensity = "高频执行" if sessions >= 3 else "点状处理"
        depth = "上下文较深" if tokens >= max(avg_tokens, 1) else "上下文较轻"
        work_on.append(
            {
                "name": workstream_label(idx),
                "sessions": sessions,
                "tokens": tokens,
                "desc": f"约 {sessions} 次会话、{format_tokens(tokens)} tokens，属于本周的 {intensity} 工作流，整体呈现 {depth} 的使用特征。",
            }
        )

    top_workstream = work_on[0]["name"] if work_on else "主工作流"
    at_a_glance = {
        "working": (
            f"OpenCode 这周已经不是偶尔点开用一下的状态了。{total_sessions} 个会话分布在 {active_days} 个活跃日上，平均每天 {msgs_per_day} 条消息，说明它已经进入你的日常执行链路。"
            if total_sessions
            else _t("OpenCode has almost no stable usage this period.")
        ),
        "hindering": (
            f"当前最大的短板不是缺会话，而是可观测性偏弱。现有数据能看到会话、消息和 token，但还看不到像补丁结果、工具链路那样更细的执行痕迹，所以一些行为只能做保守判断。"
        ),
        "quick_win": (
            f"最直接的提升，是把 OpenCode 里高频出现的 {top_workstream} 固化成 inspect -> execute -> summarize 的固定流程，让它别在每次会话里重新找节奏。"
        ),
        "ambitious": (
            f"如果后续补上工具调用、文件改动和命令轨迹采集，OpenCode 这块也能像 Claude/Codex 一样，从“看活跃度”升级到“看执行质量和闭环能力”。"
        ),
    }

    narrative_parts = [
        f"你这周使用 OpenCode 的方式已经具备稳定节奏。{total_sessions} 个会话、{total_messages} 条消息、{format_tokens(total_tokens)} tokens，平均每会话 {avg_messages} 条消息，说明它并不是只承担一次性查询，而是在一些任务里承接了连续交互。",
        f"从时间分布看，OpenCode 的使用更像补位型执行工具。{active_days} 个活跃日里，日常形态多是 {dominant_daily_shape} 个会话上下，峰值出现在 {peak_day.get('day')}，当天吃掉了 {format_tokens(peak_day.get('tokens') or 0)} tokens。说明你会在特定时段把它拉进相对集中的任务处理。"
        if peak_day
        else "从时间分布看，OpenCode 的样本还比较薄，但已经能看出它不是单次触发后就闲置的工具。",
        f"当前最明显的特征是工作流集中度较高。最活跃的工作流占了约 {concentration}% 的会话份额，这通常意味着你已经找到一两类适合交给 OpenCode 的任务，但还没有把更多任务迁过去。",
    ]
    key_insight = "OpenCode 现在更像一个已经进入日常链路、但行为采样还不够细的执行工具；下一步不是多用一点，而是把高频场景固化下来。"

    usage_cards = [
        {
            "title": _t("Usage Density"),
            "value": f"{avg_messages} msgs/session",
            "desc": f"平均每会话约 {avg_messages} 条消息，说明不少会话都包含来回追问和逐步收敛。",
        },
        {
            "title": _t("Context Depth"),
            "value": format_tokens(avg_tokens),
            "desc": f"平均每会话约 {format_tokens(avg_tokens)} tokens，OpenCode 在部分任务里已经承担了有上下文负载的工作。",
        },
        {
            "title": _t("Daily Rhythm"),
            "value": f"{msgs_per_day} msgs/day",
            "desc": f"活跃日平均约 {msgs_per_day} 条消息、{format_tokens(tokens_per_day)} tokens，整体更像集中使用而不是长尾触发。",
        },
        {
            "title": _t("Workflow Concentration"),
            "value": f"{concentration}%",
            "desc": "会话更多集中在少数几条工作流上，说明你已经有初步适配场景，但还没完全铺开。",
        },
    ]

    wins = [
        {
            "title": _t("Stable weekly rhythm formed"),
            "detail": f"{active_days} 个活跃日和 {total_sessions} 个会话说明 OpenCode 已经不是偶发尝试，而是进入了你本周的固定工具箱。",
        },
        {
            "title": _t("Context capacity being genuinely utilized"),
            "detail": f"总 token 达到 {format_tokens(total_tokens)}，平均每会话 {format_tokens(avg_tokens)}，说明你不是只拿它处理轻量问题。",
        },
        {
            "title": _t("High-frequency workflows taking shape"),
            "detail": f"最集中的工作流占了约 {concentration}% 的会话，说明你已经在筛选哪些任务更适合交给 OpenCode。",
        },
    ]

    friction = [
        {
            "title": _t("Behavioral details not yet visible"),
            "detail": "和 Claude、Codex 相比，OpenCode 当前采集侧看不到足够细的工具和改动轨迹，所以能分析节奏，但还不能充分分析执行质量。",
        },
        {
            "title": _t("Task distribution still concentrated"),
            "detail": "高频工作流占比高，说明 OpenCode 还主要服务于少数场景，暂时没有扩展成更通用的执行入口。",
        },
    ]

    features = [
        {
            "title": _t("Turn high-frequency scenarios into fixed templates"),
            "detail": "既然工作流已经开始集中，就值得把目标、边界和输出格式固化成模板，减少每次重新起手。",
        },
        {
            "title": _t("Make every session carry a result definition"),
            "detail": "如果目标是报告、摘要、修复建议或清单，第一轮就把最终输出形式说死，能显著减少中途改方向。",
        },
        {
            "title": _t("Collect finer execution events"),
            "detail": "后续把工具调用、命令轨迹、文件影响补上，才能让 OpenCode 洞察从活跃度报告升级成执行质量报告。",
        },
    ]

    patterns = [
        {
            "title": _t("OpenCode is a batch processor not a workhorse"),
            "summary": "它本周的会话分布更像在特定时间段承接一批任务，而不是均匀分散在所有工作时间里。",
        },
        {
            "title": _t("Found task contours but not yet expanded"),
            "summary": "高集中度说明适配场景开始清晰，下一步是把这些场景标准化，而不是继续零散试探。",
        },
        {
            "title": _t("Bottleneck is observability, not willingness"),
            "summary": "你已经在用，但现有数据还不足以像 Claude/Codex 那样看清楚它是怎么做事的。",
        },
    ]

    horizon = [
        {
            "title": _t("From weekly activity to weekly execution quality"),
            "detail": "一旦补上命令、工具和改动采集，OpenCode 可以从“本周用了多少”升级成“本周做成了什么、卡在了哪里”。",
        },
        {
            "title": _t("Push concentrated scenarios into standard workflows"),
            "detail": "最适合 OpenCode 的那几类任务，下一步应该沉淀成固定模板或 SOP，让使用门槛继续下降。",
        },
        {
            "title": _t("Form three-tool role division"),
            "detail": "当 Claude、Codex、OpenCode 都有足够洞察后，可以开始明确谁负责探索、谁负责落地、谁负责批处理，而不是混着用。",
        },
    ]

    return {
        "at_a_glance": at_a_glance,
        "work_on": work_on,
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
    """生成 OpenCode 周报 HTML。"""
    total_sessions = int(data.get("total_sessions") or 0)
    total_messages = int(data.get("total_messages") or 0)
    total_tokens = int(data.get("total_tokens") or 0)
    active_days = int(data.get("active_days") or 0)
    daily = data.get("daily") or []
    areas = data.get("areas") or []
    insights = data.get("insights") or build_insights(data)

    max_daily_tokens = max([int(item.get("tokens", 0) or 0) for item in daily], default=1) or 1
    max_daily_sessions = max([int(item.get("sessions", 0) or 0) for item in daily], default=1) or 1

    def render_daily(rows, key, title_suffix):
        result = []
        max_value = max([int(item.get(key, 0) or 0) for item in rows], default=1) or 1
        for item in rows:
            value = int(item.get(key) or 0)
            pct = int(value / max_value * 100) if max_value else 0
            result.append(
                f"""      <div class="bar-row">
        <div class="bar-label">{safe_text(item.get('day', ''))}</div>
        <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
        <div class="bar-value">{safe_text(str(value) + title_suffix)}</div>
      </div>"""
            )
        return "\n".join(result) if result else '<p class="empty">本周' + _t('N/A') + '每日数据。</p>'

    work_on_html = ""
    for item in insights["work_on"]:
        work_on_html += f"""      <div class="project-area">
        <div class="area-header">
          <span class="area-name">{safe_text(item['name'])}</span>
          <span class="area-count">{item['sessions']} 会话 · {format_tokens(item['tokens'])} tokens</span>
        </div>
        <div class="area-desc">{safe_text(item['desc'])}</div>
      </div>
"""
    if not work_on_html:
        work_on_html = '      <p class="empty">本周' + _I18N('N/A') + '可用工作流样本。</p>\n'

    usage_cards_html = ""
    for item in insights["usage_cards"]:
        usage_cards_html += f"""      <div class="card">
        <div class="card-title">{safe_text(item['title'])}: {safe_text(item['value'])}</div>
        <div class="card-detail">{safe_text(item['desc'])}</div>
      </div>
"""

    def render_cards(items):
        if not items:
            return '      <div class="card"><div class="card-title">' + _t('Sample insufficient') + '</div><div class="card-detail">本周数据还不足以稳定提炼这一部分内容。</div></div>\n'
        html_parts = []
        for item in items:
            detail = item.get("detail") or item.get("summary") or item.get("desc") or ""
            html_parts.append(
                f"""      <div class="card">
        <div class="card-title">{safe_text(item.get('title'))}</div>
        <div class="card-detail">{safe_text(detail)}</div>
      </div>
"""
            )
        return "".join(html_parts)

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"utf-8\"> 
  <title>OpenCode Insights</title>
  <link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap\" rel=\"stylesheet\">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(180deg, #f8fafc 0%, #eff6ff 100%); color: #334155; line-height: 1.65; padding: 48px 24px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ font-size: 32px; font-weight: 700; color: #0f172a; margin-bottom: 8px; }}
    h2 {{ font-size: 20px; font-weight: 600; color: #0f172a; margin-top: 42px; margin-bottom: 14px; }}
    .subtitle {{ color: #64748b; font-size: 15px; margin-bottom: 28px; }}
    .hero {{ background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 45%, #ecfeff 100%); border: 1px solid #93c5fd; border-radius: 16px; padding: 22px 24px; margin-bottom: 24px; }}
    .glance-title {{ font-size: 16px; font-weight: 700; color: #1d4ed8; margin-bottom: 14px; }}
    .glance-sections {{ display: flex; flex-direction: column; gap: 10px; }}
    .glance-section {{ font-size: 14px; color: #1e3a8a; }}
    .glance-section strong {{ color: #1d4ed8; }}
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
    .bar-label {{ width: 120px; font-size: 12px; color: #334155; flex-shrink: 0; }}
    .bar-track {{ flex: 1; height: 8px; border-radius: 999px; background: #dbeafe; margin: 0 10px; overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: 999px; background: linear-gradient(90deg, #3b82f6 0%, #06b6d4 100%); }}
    .bar-value {{ width: 70px; font-size: 11px; color: #64748b; text-align: right; }}
    .raw-data {{ display: none; }}
    .empty {{ color: #94a3b8; font-size: 13px; }}
    @media (max-width: 760px) {{ .cards, .charts-row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class=\"container\">
    <h1>OpenCode Insights</h1>
    <p class=\"subtitle\">{data['period_start']} ~ {data['period_end']} | {data['week']}</p>

    <div class=\"hero\">
      <div class=\"glance-title\">At a Glance</div>
      <div class=\"glance-sections\">
        <div class=\"glance-section\"><strong>What's working:</strong> {safe_text(insights['at_a_glance']['working'])}</div>
        <div class=\"glance-section\"><strong>What's hindering you:</strong> {safe_text(insights['at_a_glance']['hindering'])}</div>
        <div class=\"glance-section\"><strong>Quick wins to try:</strong> {safe_text(insights['at_a_glance']['quick_win'])}</div>
        <div class=\"glance-section\"><strong>Ambitious workflows:</strong> {safe_text(insights['at_a_glance']['ambitious'])}</div>
      </div>
    </div>

    <div class=\"stats-row\">
      <div class=\"stat\"><div class=\"stat-value\">{total_sessions}</div><div class=\"stat-label\">会话数</div></div>
      <div class=\"stat\"><div class=\"stat-value\">{total_messages}</div><div class=\"stat-label\">消息数</div></div>
      <div class=\"stat\"><div class=\"stat-value\">{format_tokens(total_tokens)}</div><div class=\"stat-label\">Token 用量</div></div>
      <div class=\"stat\"><div class=\"stat-value\">{active_days}</div><div class=\"stat-label\">活跃天数</div></div>
    </div>

    <h2>What You Work On</h2>
    <p class=\"section-intro\">这里不展示具体项目名，只保留工作流层面的行为画像。</p>
    <div class=\"project-areas\">
{work_on_html}    </div>

    <h2>How You Use OpenCode</h2>
    <div class=\"narrative\">
      <p>{safe_text(insights['narrative_parts'][0])}</p>
      <p>{safe_text(insights['narrative_parts'][1])}</p>
      <p>{safe_text(insights['narrative_parts'][2])}</p>
      <div class=\"key-insight\"><strong>Key pattern:</strong> {safe_text(insights['key_insight'])}</div>
    </div>

    <div class=\"cards\" style=\"margin-top:18px;\">
{usage_cards_html}    </div>

    <div class=\"charts-row\">
      <div class=\"chart-card\">
        <div class=\"chart-title\">每日 Sessions</div>
{render_daily(daily, 'sessions', 'x')}
      </div>
      <div class=\"chart-card\">
        <div class=\"chart-title\">每日 Tokens</div>
{render_daily(daily, 'tokens', '')}
      </div>
    </div>

    <h2>Impressive Things You Did</h2>
    <p class=\"section-intro\">先看已经稳定形成的使用优势。</p>
    <div class=\"cards\">
{render_cards(insights['wins'])}    </div>

    <h2>Where Things Go Wrong</h2>
    <p class=\"section-intro\">当前主要摩擦更多来自可观测性和工作流展开程度，而不是单纯用得不够多。</p>
    <div class=\"cards\">
{render_cards(insights['friction'])}    </div>

    <h2>Features to Try</h2>
    <div class=\"cards\">
{render_cards(insights['features'])}    </div>

    <h2>New Ways to Use OpenCode</h2>
    <div class=\"cards\">
{render_cards(insights['patterns'])}    </div>

    <h2>On the Horizon</h2>
    <div class=\"cards\">
{render_cards(insights['horizon'])}    </div>
"""

    raw_data = {
        "week": data.get("week"),
        "period_start": data.get("period_start"),
        "period_end": data.get("period_end"),
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_tokens": total_tokens,
        "active_days": active_days,
        "lines_added": data.get("lines_added", 0),
        "lines_removed": data.get("lines_removed", 0),
        "files_modified": data.get("files_modified", 0),
        "daily": daily,
        "areas": areas,
        "insights": insights,
        "source": data.get("source") or {},
    }

    html += f"""
    <div class=\"raw-data\" id=\"opencode-raw-data\">{json.dumps(raw_data, ensure_ascii=False)}</div>
  </div>
</body>
</html>
"""

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    today = datetime.date.today()
    iso = today.isocalendar()
    default_week = f"{iso[0]}-W{iso[1]:02d}"

    parser = argparse.ArgumentParser(description="采集 OpenCode 使用数据")
    parser.add_argument("week", nargs="?", default=default_week, help="ISO 周标识，如 2026-W15")
    parser.add_argument("--output", "-o", metavar="PATH", help="输出 HTML 路径")
    args = parser.parse_args()

    try:
        data = collect(args.week)
    except Exception as exc:
        print(f"OpenCode 采集失败: {exc}", file=sys.stderr)
        return 1

    if not data or int(data.get("total_sessions") or 0) <= 0:
        print(f"OpenCode 在 {args.week} 无可用数据", file=sys.stderr)
        return 1

    if args.output:
        generate_html(data, args.output)
        print(f"OpenCode report: {args.output}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
