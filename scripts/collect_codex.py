#!/usr/bin/env python3
"""采集 OpenAI Codex CLI 使用数据，生成带洞察的 HTML 报告。"""

import datetime
import html
import json
import os
import re
import shlex
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

CODEX_DB = Path.home() / ".codex" / "state_5.sqlite"

_PROJECT_ALIASES = {
    "agent-twitter": "agent",
}

_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "when", "your",
    "have", "has", "was", "were", "will", "shall", "about", "please", "there",
    "their", "what", "where", "which", "while", "after", "before", "should",
    "could", "would", "through", "using", "used", "todo", "done", "then", "than",
    "当前", "这个", "那个", "一个", "我们", "你们", "现在", "如何", "怎么", "什么", "一下",
    "以及", "进行", "需要", "可以", "是否", "没有", "还有", "直接", "已经", "因为", "所以",
}


def _path_label(path_value):
    normalized = str(path_value or "").rstrip("/\\")
    if not normalized:
        return ""
    name = Path(normalized).name or normalized
    return _PROJECT_ALIASES.get(name, name)


# period_start_end imported from period_utils


def _format_tokens(n):
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_text(value):
    return html.escape(str(value or ""))


def _normalize_session_label(title, first_user_message):
    text = (title or first_user_message or "").strip()
    if not text:
        return _I18N("Unnamed Session")
    first_line = text.splitlines()[0].strip()
    return first_line[:90]


def _extract_command_name(payload):
    parsed_cmd = payload.get("parsed_cmd") or []
    if parsed_cmd and isinstance(parsed_cmd, list):
        cmd = parsed_cmd[0].get("cmd")
        if cmd:
            raw = cmd.strip()
        else:
            raw = ""
    else:
        command = payload.get("command") or []
        raw = command[-1] if command else ""

    raw = str(raw or "").strip()
    if not raw:
        return ""

    try:
        parts = shlex.split(raw, posix=True)
    except ValueError:
        parts = raw.split()

    shell_keywords = {"for", "do", "done", "if", "then", "fi", "while", "case", "esac"}
    for part in parts:
        if not part or "=" in part and not part.startswith(("/", ".", "~")):
            continue
        if part in {"&&", "||", ";", "|"}:
            continue
        if part in shell_keywords:
            continue
        return Path(part).name
    return parts[0] if parts else raw[:24]


def _extract_keywords(text):
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,8}", text or "")
    result = []
    for word in words:
        lower = word.lower()
        if lower in _STOP_WORDS:
            continue
        if lower.isdigit():
            continue
        result.append(lower)
    return result


def _count_diff_lines(unified_diff):
    """统计 unified diff 中的新增/删除行数，忽略 diff 元信息。"""
    added = 0
    removed = 0
    for line in str(unified_diff or "").splitlines():
        if not line:
            continue
        if line.startswith("@@") or line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _workstream_label(index):
    return f"Workstream {index + 1}"


def _codex_work_focus(detail):
    title = str(detail.get("title") or "").lower()
    if any(word in title for word in ("报告", "采集", "getagt", "analyzeagt", "report")):
        return _I18N("Report Collection & Generation")
    if any(word in title for word in ("review", "code review", "审查")):
        return _I18N("Code Review")
    if any(word in title for word in ("bug", "修复", "排查", "错误", "异常", "fix")):
        return _I18N("Bug Investigation & Fix")
    if any(word in title for word in ("agents.md", "contributor guide", "文档", "规范", "readme")):
        return _I18N("Documentation & Standards")
    if _safe_int(detail.get("patch_files")) > 0 or _safe_int(detail.get("patch_success")) > 0:
        return _I18N("Implementation Delivery")
    return _I18N("Read & Analysis")


def _parse_rollout(path_value, period_start_iso=None, period_end_iso=None):
    """解析 rollout 文件。period_start_iso/period_end_iso 非空时只统计该时间段内的事件（用于跨周 thread）。"""
    path = Path(path_value or "")
    summary = {
        "tool_calls": Counter(),
        "commands": Counter(),
        "user_messages": [],
        "agent_messages": [],
        "web_queries": [],
        "patch_success": 0,
        "patch_files": set(),
        "lines_added": 0,
        "lines_removed": 0,
        "aborted_turns": 0,
        "compactions": 0,
        "web_searches": 0,
        "task_started": 0,
        "task_complete": 0,
        "turn_contexts": 0,
        "token_snapshots": [],
    }
    if not path.exists() or not path.is_file():
        return summary

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            item_type = obj.get("type")
            payload = obj.get("payload") or {}

            if item_type == "turn_context":
                summary["turn_contexts"] += 1
                continue

            if item_type == "response_item":
                response_type = payload.get("type")
                if response_type in {"function_call", "custom_tool_call"}:
                    name = payload.get("name")
                    if name:
                        summary["tool_calls"][name] += 1
                elif response_type == "web_search_call":
                    summary["tool_calls"]["web_search"] += 1
                continue

            if item_type != "event_msg":
                continue

            event_type = payload.get("type")
            if event_type == "user_message":
                message = (payload.get("message") or "").strip()
                if message:
                    summary["user_messages"].append(message[:240])
            elif event_type == "agent_message":
                message = (payload.get("message") or "").strip()
                if message:
                    summary["agent_messages"].append(message[:240])
            elif event_type == "task_started":
                summary["task_started"] += 1
            elif event_type == "task_complete":
                summary["task_complete"] += 1
            elif event_type == "turn_aborted":
                summary["aborted_turns"] += 1
            elif event_type == "context_compacted":
                summary["compactions"] += 1
            elif event_type == "web_search_end":
                summary["web_searches"] += 1
                query = payload.get("query")
                if query:
                    summary["web_queries"].append(query[:180])
            elif event_type == "exec_command_end":
                command_name = _extract_command_name(payload)
                if command_name:
                    summary["commands"][command_name] += 1
            elif event_type == "patch_apply_end":
                if payload.get("success"):
                    summary["patch_success"] += 1
                changes = payload.get("changes") or {}
                for changed_path, change in changes.items():
                    summary["patch_files"].add(changed_path)
                    add_count, remove_count = _count_diff_lines((change or {}).get("unified_diff"))
                    summary["lines_added"] += add_count
                    summary["lines_removed"] += remove_count
            elif event_type == "token_count":
                info = payload.get("info") or {}
                usage = info.get("total_token_usage") or {}
                total = usage.get("total_tokens")
                if total is not None:
                    summary["token_snapshots"].append(_safe_int(total))

    return summary


def _build_thread_details(threads, period_start_iso=None, period_end_iso=None):
    details = []
    total = {
        "tool_calls": Counter(),
        "commands": Counter(),
        "keywords": Counter(),
        "user_messages": 0,
        "agent_messages": 0,
        "patch_success": 0,
        "patch_files": set(),
        "lines_added": 0,
        "lines_removed": 0,
        "aborted_turns": 0,
        "compactions": 0,
        "web_searches": 0,
        "task_started": 0,
        "task_complete": 0,
        "turn_contexts": 0,
    }

    for thread in threads:
        rollout = _parse_rollout(thread["rollout_path"], period_start_iso=period_start_iso, period_end_iso=period_end_iso)
        label = _normalize_session_label(thread["title"], thread["first_user_message"])
        cwd = thread["cwd"]
        token_count = _safe_int(thread["tokens_used"])
        commands = [{"name": k, "count": v} for k, v in rollout["commands"].most_common(5)]
        tools = [{"name": k, "count": v} for k, v in rollout["tool_calls"].most_common(5)]
        sample_text = " ".join(
            [label] + rollout["user_messages"][:2] + [thread.get("first_user_message") or ""]
        )
        for word in _extract_keywords(sample_text):
            total["keywords"][word] += 1

        detail = {
            "id": thread["id"],
            "title": label,
            "cwd": cwd,
            "cwd_label": _path_label(cwd),
            "tokens": token_count,
            "commands": commands,
            "tools": tools,
            "patch_success": rollout["patch_success"],
            "patch_files": len(rollout["patch_files"]),
            "lines_added": rollout["lines_added"],
            "lines_removed": rollout["lines_removed"],
            "aborted_turns": rollout["aborted_turns"],
            "compactions": rollout["compactions"],
            "web_searches": rollout["web_searches"],
            "task_started": rollout["task_started"],
            "task_complete": rollout["task_complete"],
            "turn_contexts": rollout["turn_contexts"],
        }
        details.append(detail)

        total["tool_calls"].update(rollout["tool_calls"])
        total["commands"].update(rollout["commands"])
        total["user_messages"] += len(rollout["user_messages"])
        total["agent_messages"] += len(rollout["agent_messages"])
        total["patch_success"] += rollout["patch_success"]
        total["patch_files"].update(rollout["patch_files"])
        total["lines_added"] += rollout["lines_added"]
        total["lines_removed"] += rollout["lines_removed"]
        total["aborted_turns"] += rollout["aborted_turns"]
        total["compactions"] += rollout["compactions"]
        total["web_searches"] += rollout["web_searches"]
        total["task_started"] += rollout["task_started"]
        total["task_complete"] += rollout["task_complete"]
        total["turn_contexts"] += rollout["turn_contexts"]

    return details, total


def _summarize_work_patterns(data, thread_details, totals):
    total_sessions = _safe_int(data["summary"].get("total_threads"))
    active_days = _safe_int(data["summary"].get("active_days"))
    full_auto = _safe_int(data["summary"].get("full_auto"))
    interactive = _safe_int(data["summary"].get("interactive"))
    total_tokens = _safe_int(data["summary"].get("total_tokens"))
    patch_success = totals["patch_success"]
    patch_files = len(totals["patch_files"])
    lines_added = totals["lines_added"]
    lines_removed = totals["lines_removed"]
    user_messages = totals["user_messages"]
    agent_messages = totals["agent_messages"]
    message_events = user_messages + agent_messages
    total_messages = user_messages
    web_searches = totals["web_searches"]
    aborted_turns = totals["aborted_turns"]
    compactions = totals["compactions"]
    task_complete = totals["task_complete"]
    commands = totals["commands"]
    tools = totals["tool_calls"]
    keyword_counter = totals["keywords"]

    top_area = data["areas"][0] if data["areas"] else None
    top_model = data["models"][0] if data["models"] else None
    top_commands = [{"name": name, "count": count} for name, count in commands.most_common(8)]
    top_tools = [{"name": name, "count": count} for name, count in tools.most_common(8)]
    top_topics = [{"name": name, "count": count} for name, count in keyword_counter.most_common(10)]
    avg_messages = round(total_messages / total_sessions, 1) if total_sessions else 0
    completed_sessions = sum(1 for item in thread_details if _safe_int(item.get("task_complete")) > 0)
    patched_sessions = sum(1 for item in thread_details if _safe_int(item.get("patch_success")) > 0)
    completion_ratio = round(completed_sessions / total_sessions * 100) if total_sessions else 0
    patch_session_ratio = round(patched_sessions / total_sessions * 100) if total_sessions else 0
    friction_events = aborted_turns + compactions
    dominant_mode = _I18N("Fully Auto Priority") if full_auto >= interactive else _I18N("Interactive Priority")
    tool_list = ", ".join(item["name"] for item in top_tools[:3]) or _I18N("Tool Calls")
    command_list = ", ".join(item["name"] for item in top_commands[:4]) or _I18N("Command Execution")

    work_groups = defaultdict(lambda: {
        "sessions": 0,
        "tokens": 0,
        "patch_files": 0,
        "lines_added": 0,
        "lines_removed": 0,
        "tools": Counter(),
    })
    for item in thread_details:
        focus = _codex_work_focus(item)
        group = work_groups[focus]
        group["sessions"] += 1
        group["tokens"] += _safe_int(item.get("tokens"))
        group["patch_files"] += _safe_int(item.get("patch_files"))
        group["lines_added"] += _safe_int(item.get("lines_added"))
        group["lines_removed"] += _safe_int(item.get("lines_removed"))
        for tool in item.get("tools") or []:
            group["tools"][tool["name"]] += _safe_int(tool["count"])

    work_on = []
    ranked_work_groups = sorted(work_groups.items(), key=lambda item: (item[1]["sessions"], item[1]["tokens"]), reverse=True)
    for focus, group in ranked_work_groups[:5]:
        tool_mix = group["tools"]
        work_on.append({
            "name": focus,
            "sessions": group["sessions"],
            "tokens": group["tokens"],
            "desc": (
                f"约 {group['sessions']} 次会话、{_format_tokens(group['tokens'])} tokens。"
                f" 局部样本里涉及 {group['patch_files']} 个文件、+{group['lines_added']}/-{group['lines_removed']} 行，"
                f" 高频工具是 {', '.join(name for name, _ in tool_mix.most_common(3)) or '基础 CLI 工具'}。"
            ),
        })

    usage_cards = [
        {
            "title": _I18N("Execution Density"),
            "value": f"{avg_messages} msgs/session",
            "desc": f"每个会话平均约 {avg_messages} 条消息，说明你通常会把 Codex 拉进真实来回迭代，而不是一问一答就结束。",
        },
        {
            "title": _I18N("Tool-Driven"),
            "value": sum(item["count"] for item in top_tools),
            "desc": f"高频工具集中在 {tool_list}，说明这周的使用方式以真实读写和操作为主，而不是停留在解释层。",
        },
        {
            "title": _I18N("Implementation Intensity"),
            "value": patch_success,
            "desc": f"成功补丁 {patch_success} 次，影响 {patch_files} 个文件，总代码变更 +{lines_added}/-{lines_removed} 行，执行闭环是存在的。",
        },
        {
            "title": _I18N("Closure Status"),
            "value": f"{completion_ratio}%",
            "desc": f"约 {completed_sessions} 个会话出现过 task_complete，说明不少会话能从分析走到明确收口，但仍有一部分停在探索阶段。",
        },
    ]

    wins = []
    if patch_success >= max(3, total_sessions // 2):
        wins.append({
            "title": _I18N("Codex has entered real execution mode"),
            "detail": f"本周至少完成了 {patch_success} 次成功补丁写入，说明 Codex 不只是解释问题，而是在真实承担修改和落地工作。",
        })
    if full_auto > 0:
        wins.append({
            "title": _I18N("You have started using it as an agent"),
            "detail": f"{full_auto} 个会话运行在 `approval=never`，说明你已经在尝试把 Codex 放进可执行工作流，而不只是把它当问答界面。",
        })
    if len(commands) >= 4:
        wins.append({
            "title": _I18N("Command chains mirror real engineering workflows"),
            "detail": f"高频命令覆盖 {command_list}，说明这周的 Codex 会话确实在读代码、跑命令、收结果，而不是停留在抽象讨论。",
        })
    if task_complete >= max(2, total_sessions // 2):
        wins.append({
            "title": _I18N("Many tasks can truly reach closure"),
            "detail": f"记录到 {task_complete} 次 `task_complete` 事件，说明很多回合都能从分析进入明确收口，而不是一直停留在探索阶段。",
        })

    friction = []
    if aborted_turns > 0:
        friction.append({
            "title": _I18N("Error paths are manually interrupted"),
            "detail": f"本周出现 {aborted_turns} 次 `turn_aborted`。这通常意味着方向切换频繁，或者 Codex 在错误路径上走得太久才被你拦下来。",
        })
    if compactions > 0:
        friction.append({
            "title": _I18N("Long sessions start hitting context limits"),
            "detail": f"发生 {compactions} 次 `context_compacted`。一旦任务又长又碎，后续就容易出现上下文丢失、重复解释和返工。",
        })
    if web_searches > patch_success and web_searches >= 3:
        friction.append({
            "title": _I18N("Research sometimes outruns implementation"),
            "detail": f"外部检索 {web_searches} 次，但成功补丁只有 {patch_success} 次，说明一部分会话花在信息搜集或验证上，落地闭环还不够稳。",
        })
    if interactive > full_auto and interactive >= 3:
        friction.append({
            "title": _I18N("Manual approval remains the main rhythm"),
            "detail": f"交互式会话 {interactive} 个，高于全自动 {full_auto} 个。很多动作仍然要靠人工确认，自动化收益还没完全吃到。",
        })

    features = [
        {
            "title": _I18N("Move stable tasks into fixed workflows"),
            "detail": _I18N("For tasks done repeatedly, write the sequence as inspect→patch→verify→summarize to reduce Codex guessing the process."),
        },
        {
            "title": _I18N("Add persistent context to high-frequency workflows"),
            "detail": _I18N("Document common commands, test paths, output formats, and no-go zones in repo instructions to reduce wrong paths and repeated exploration."),
        },
        {
            "title": _I18N("Make verification the default closing action"),
            "detail": _I18N("Whenever a task involves changes, default to appending rerun/test/open-report to avoid sessions ending at: I changed it, take a look."),
        },
    ]
    patterns = [
        {
            "title": _I18N("Operational work, not just analysis"),
            "summary": f"从 {tool_list} 和 {command_list} 的占比看，这周的主旋律是实际操作、局部修复和结果验证，不是泛泛讨论。",
        },
        {
            "title": _I18N("Strong execution is there; what's missing is better upfront constraints"),
            "summary": _I18N("Friction isn't from capability gaps but from not pinning down output format, verification, and stop conditions upfront."),
        },
        {
            "title": _I18N("Automation potential exceeds current usage depth"),
            "summary": f"{dominant_mode} 说明你还在调试最佳协作边界；一旦把低风险任务迁进固定模板，吞吐会明显提升。",
        },
    ]
    horizon = [
        {
            "title": _I18N("True one-click fix loop"),
            "detail": _I18N("The next step is not longer prompts — let Codex own the inspect→patch→rerun→summarize loop, turning you from middle coordinator to final approver."),
        },
        {
            "title": _I18N("Build period memory by workflow, not by project"),
            "detail": _I18N("You can now track by behavior patterns — bug fixes, report generation, code reviews, batch changes — rather than specific project names."),
        },
        {
            "title": _I18N("Push low-risk operations to full automation"),
            "detail": _I18N("Low-risk tasks like read-only analysis, batch search, local text edits can gradually move to approval=never, leaving manual intervention only for key changes and final confirmation."),
        },
    ]

    at_a_glance = {
        "working": (
            f"你这周对 Codex 的使用已经很明确地偏向真实执行，而不是把它当聊天工具。{patch_session_ratio}% 左右的会话带来了成功补丁，"
            f"再加上 {command_list} 这样的命令链路，说明它已经进入你的工程主流程。"
            if patch_success > 0 or sum(commands.values()) > 0
            else "Codex usage is light this period — more scattered attempts than a stable workflow."
        ),
        "hindering": (
            f"真正拖慢节奏的不是能力上限，而是执行前半段的形状不够稳定。{friction_events} 个摩擦事件里，最典型的是中断和上下文压缩，说明会话经常在路径选择阶段消耗过多精力。"
            if friction_events
            else "No significant interruptions or context pressure this period — the gap is moving more stable tasks into fixed workflows rather than friction."
        ),
        "quick_win": (
            _I18N("The most direct improvement: write goals, output format, and verification like operation specs, not task descriptions. Codex can execute — now make it guess less.")
        ),
        "ambitious": (
            f"You’re at the threshold from assisted coding to managed agent. {dominant_mode} is just the current phase; next: batch low-risk tasks into automated loops, leaving only key checkpoints for manual approval."
        ),
    }

    narrative_parts = [
        f"你本周使用 Codex 的方式更像在带一个会操作终端和改代码的执行搭档，而不是在和模型聊天。{total_sessions} 个会话、{total_messages} 条消息、平均每会话 {avg_messages} 条消息，说明很多任务都不是一轮问答，而是要经过读代码、跑命令、修补丁、再确认的多步过程。",
        f"这套工作流的核心是工具和命令。高频工具集中在 {tool_list}，高频命令集中在 {command_list}，再叠加 +{lines_added}/-{lines_removed} 行代码变更，说明你让 Codex 参与的是具备真实后果的工程动作，而不是停留在建议层面。",
        f"真正的问题出现在流程前半段。{aborted_turns} 次中断、{compactions} 次上下文压缩、{web_searches} 次外部检索，都说明 Codex 在探索和收敛之间仍有摩擦。换句话说，它已经足够能干，但离稳定省心还差一层工作流约束。",
    ]
    key_insight = _I18N("For you, Codex is no longer about capability but about consistency with less manual correction.")

    return {
        "at_a_glance": at_a_glance,
        "top_area": {
            "name": _path_label(top_area["cwd"]),
            "cwd": top_area["cwd"],
            "sessions": _safe_int(top_area["cnt"]),
            "tokens": _safe_int(top_area["tokens"]),
        } if top_area else None,
        "top_model": {
            "name": top_model["model"] or "default",
            "sessions": _safe_int(top_model["cnt"]),
            "tokens": _safe_int(top_model["tokens"]),
        } if top_model else None,
        "top_commands": top_commands,
        "top_tools": top_tools,
        "top_topics": top_topics,
        "usage_cards": usage_cards,
        "work_on": work_on,
        "narrative_parts": narrative_parts,
        "key_insight": key_insight,
        "wins": wins[:4],
        "friction": friction[:4],
        "features": features,
        "patterns": patterns,
        "horizon": horizon,
        "patch_success": patch_success,
        "patch_files": patch_files,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "total_messages": total_messages,
        "user_messages": user_messages,
        "agent_messages": agent_messages,
        "message_events": message_events,
        "aborted_turns": aborted_turns,
        "compactions": compactions,
        "web_searches": web_searches,
        "task_complete": task_complete,
        "total_tokens": total_tokens,
        "total_sessions": total_sessions,
        "active_days": active_days,
        "full_auto": full_auto,
        "interactive": interactive,
    }


def collect(period_str):
    period_start, period_end = period_start_end(period_str)
    ts_start = int(datetime.datetime(period_start.year, period_start.month, period_start.day).timestamp())
    ts_end = int(datetime.datetime(period_end.year, period_end.month, period_end.day).timestamp()) + 86400

    if not CODEX_DB.exists():
        return None

    conn = sqlite3.connect(str(CODEX_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 只统计用户直接发起的主线程，排除子代理和子线程
    MAIN_THREAD_FILTER = "(agent_nickname IS NULL OR agent_nickname = '') AND (agent_role IS NULL OR agent_role = '')"

    cur.execute(
        f"""
        SELECT id, title, created_at, updated_at, model, model_provider,
               tokens_used, cwd, has_user_event, approval_mode, reasoning_effort,
               rollout_path, first_user_message
        FROM threads
        WHERE (created_at >= ? AND created_at < ?)
           OR (updated_at >= ? AND updated_at < ?)
          AND {MAIN_THREAD_FILTER}
        ORDER BY updated_at DESC
        """,
        (ts_start, ts_end, ts_start, ts_end),
    )
    threads = [dict(r) for r in cur.fetchall()]

    # 统一用 OR updated_at，确保跨周 thread 的活动也被计入
    WEEK_FILTER = "(created_at >= ? AND created_at < ?) OR (updated_at >= ? AND updated_at < ?)"
    week_params = (ts_start, ts_end, ts_start, ts_end)

    cur.execute(
        f"""
        SELECT COUNT(*) as total_threads,
               COUNT(DISTINCT date(datetime(updated_at, 'unixepoch', 'localtime'))) as active_days,
               SUM(CASE WHEN approval_mode = 'never' THEN 1 ELSE 0 END) as full_auto,
               SUM(CASE WHEN approval_mode != 'never' THEN 1 ELSE 0 END) as interactive
        FROM threads
        WHERE ({WEEK_FILTER})
          AND {MAIN_THREAD_FILTER}
        """,
        week_params,
    )
    summary = dict(cur.fetchone())

    # tokens 统计全量（含子代理），因为子代理消耗的仍是用户配额
    cur.execute(
        f"SELECT SUM(tokens_used) as total_tokens FROM threads WHERE {WEEK_FILTER}",
        week_params,
    )
    summary["total_tokens"] = (cur.fetchone())[0] or 0

    cur.execute(
        f"""
        SELECT model, COUNT(*) as cnt, SUM(tokens_used) as tokens
        FROM threads
        WHERE ({WEEK_FILTER})
          AND {MAIN_THREAD_FILTER}
        GROUP BY model
        ORDER BY cnt DESC
        """,
        week_params,
    )
    model_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        f"""
        SELECT reasoning_effort, COUNT(*) as cnt
        FROM threads
        WHERE ({WEEK_FILTER})
          AND {MAIN_THREAD_FILTER}
        GROUP BY reasoning_effort
        ORDER BY cnt DESC
        """,
        week_params,
    )
    effort_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        f"""
        SELECT cwd, COUNT(*) as cnt, SUM(tokens_used) as tokens
        FROM threads
        WHERE ({WEEK_FILTER})
          AND {MAIN_THREAD_FILTER}
        GROUP BY cwd
        ORDER BY cnt DESC
        LIMIT 10
        """,
        week_params,
    )
    area_rows = [dict(r) for r in cur.fetchall()]

    cur.execute(
        f"""
        SELECT date(datetime(updated_at, 'unixepoch', 'localtime')) as day,
               COUNT(*) as sessions,
               SUM(tokens_used) as tokens
        FROM threads
        WHERE ({WEEK_FILTER})
          AND {MAIN_THREAD_FILTER}
        GROUP BY day
        ORDER BY day DESC
        """,
        week_params,
    )
    daily_rows = [dict(r) for r in cur.fetchall()]

    # 统计被排除的线程数（用于 excluded_threads 字段）
    cur.execute(
        f"""
        SELECT COUNT(*) as raw_threads,
               SUM(CASE WHEN (agent_nickname IS NOT NULL AND agent_nickname != '') AND (agent_role IS NULL OR agent_role = '') THEN 1 ELSE 0 END) as child_threads,
               SUM(CASE WHEN agent_role IS NOT NULL AND agent_role != '' THEN 1 ELSE 0 END) as agent_threads
        FROM threads
        WHERE {WEEK_FILTER}
        """,
        week_params,
    )
    excl = dict(cur.fetchone())
    raw_t = excl["raw_threads"] or 0
    child_t = excl["child_threads"] or 0
    agent_t = excl["agent_threads"] or 0
    excluded_threads = {
        "raw_threads": raw_t,
        "child_threads": child_t,
        "agent_threads": agent_t,
        "main_threads": raw_t - child_t - agent_t,
    }

    conn.close()

    thread_details, rollout_totals = _build_thread_details(
        threads,
        period_start_iso=str(period_start),
        period_end_iso=str(period_end),
    )
    # 用 history.jsonl 重新计算 user_messages（按消息发送时间，而非 thread 创建时间）
    history_path = CODEX_DB.parent / "history.jsonl"
    history_user_messages = 0
    if history_path.exists():
        import json as _json
        for line in history_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                obj = _json.loads(line)
                msg_ts = int(obj.get("ts") or 0)
                if ts_start <= msg_ts < ts_end and (obj.get("text") or "").strip():
                    history_user_messages += 1
            except Exception:
                pass
    if history_user_messages > 0:
        rollout_totals["user_messages"] = history_user_messages

    insights = _summarize_work_patterns(
        {
            "summary": summary,
            "areas": area_rows,
            "models": model_rows,
        },
        thread_details,
        rollout_totals,
    )

    return {
        "week": period_str,
        "period_start": str(period_start),
        "period_end": str(period_end),
        "threads": threads,
        "thread_details": thread_details,
        "summary": summary,
        "models": model_rows,
        "efforts": effort_rows,
        "areas": area_rows,
        "daily": daily_rows,
        "insights": insights,
        "excluded_threads": excluded_threads,
    }


def _render_stat(value, label):
    return f"""      <div class="stat">
        <div class="stat-value">{_safe_text(value)}</div>
        <div class="stat-label">{_safe_text(label)}</div>
      </div>
"""


def _render_bar_rows(items, label_key, value_key, max_value, suffix=""):
    rows = []
    for item in items:
        value = _safe_int(item[value_key])
        pct = int(value / max_value * 100) if max_value > 0 else 0
        rows.append(
            f"""        <div class="bar-row">
          <div class="bar-label" title="{_safe_text(item[label_key])}">{_safe_text(item[label_key])}</div>
          <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
          <div class="bar-value">{_safe_text(str(value) + suffix)}</div>
        </div>"""
        )
    return "\n".join(rows)


def generate_html(data, out_path):
    s = data["summary"]
    insights = data["insights"]
    total_sessions = _safe_int(s.get("total_threads"))
    total_tokens = _safe_int(s.get("total_tokens"))
    active_days = _safe_int(s.get("active_days"))
    full_auto = _safe_int(s.get("full_auto"))
    interactive = _safe_int(s.get("interactive"))
    total_messages = _safe_int(insights.get("total_messages"))
    patch_success = _safe_int(insights.get("patch_success"))
    patch_files = _safe_int(insights.get("patch_files"))
    lines_added = _safe_int(insights.get("lines_added"))
    lines_removed = _safe_int(insights.get("lines_removed"))
    aborted_turns = _safe_int(insights.get("aborted_turns"))
    compactions = _safe_int(insights.get("compactions"))

    html_parts = [f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Codex Insights</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%); color: #1f2937; line-height: 1.65; padding: 40px 20px 72px; }}
    .container {{ max-width: 980px; margin: 0 auto; }}
    h1 {{ font-size: 36px; font-weight: 800; color: #0f172a; margin-bottom: 8px; }}
    h2 {{ font-size: 22px; font-weight: 700; color: #0f172a; margin-top: 44px; margin-bottom: 14px; }}
    .subtitle {{ color: #64748b; font-size: 15px; margin-bottom: 28px; }}
    .hero {{ background: linear-gradient(135deg, #fff7ed 0%, #ffedd5 45%, #e0e7ff 100%); border: 1px solid #fdba74; border-radius: 20px; padding: 24px 24px 18px; box-shadow: 0 12px 40px rgba(15, 23, 42, 0.06); }}
    .hero-grid {{ display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px; }}
    .glance-title {{ font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #9a3412; font-weight: 700; margin-bottom: 14px; }}
    .glance-item {{ font-size: 14px; color: #7c2d12; margin-bottom: 10px; }}
    .glance-item strong {{ color: #9a3412; }}
    .hero-side {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .hero-metric {{ background: rgba(255,255,255,0.72); border: 1px solid rgba(251, 146, 60, 0.25); border-radius: 14px; padding: 14px; }}
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
    .project-areas {{ display: flex; flex-direction: column; gap: 12px; }}
    .project-area {{ background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04); }}
    .area-header {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 8px; }}
    .area-name {{ font-weight: 700; font-size: 15px; color: #0f172a; word-break: break-all; }}
    .area-count {{ font-size: 12px; color: #475569; background: #f8fafc; padding: 3px 8px; border-radius: 999px; }}
    .area-desc {{ font-size: 13px; color: #64748b; margin-top: 6px; word-break: break-all; }}
    .examples {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{ display: inline-flex; align-items: center; padding: 4px 8px; border-radius: 999px; font-size: 12px; background: #eef2ff; color: #4338ca; }}
    .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-top: 18px; }}
    .chart-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 14px; padding: 16px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.04); }}
    .chart-title {{ font-size: 12px; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: 12px; }}
    .bar-row {{ display: flex; align-items: center; margin-bottom: 8px; }}
    .bar-label {{ width: 120px; font-size: 12px; color: #334155; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ flex: 1; height: 8px; background: #eef2ff; border-radius: 999px; margin: 0 10px; overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: 999px; background: linear-gradient(90deg, #f97316 0%, #6366f1 100%); }}
    .bar-value {{ width: 74px; font-size: 11px; font-weight: 600; color: #64748b; text-align: right; }}
    .model-row {{ display: flex; align-items: center; margin-bottom: 8px; }}
    .model-name {{ width: 140px; font-size: 12px; color: #334155; font-weight: 600; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .model-bar-track {{ flex: 1; height: 8px; background: #eef2ff; border-radius: 999px; margin: 0 10px; overflow: hidden; }}
    .model-bar-fill {{ height: 100%; border-radius: 999px; background: linear-gradient(90deg, #fb923c 0%, #8b5cf6 100%); }}
    .model-count {{ width: 48px; font-size: 11px; color: #64748b; text-align: right; }}
    .model-tokens {{ width: 64px; font-size: 11px; color: #94a3b8; text-align: right; }}
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
    <h1>Codex Insights</h1>
    <p class="subtitle">{_safe_text(data['period_start'])} ~ {_safe_text(data['period_end'])} &nbsp;|&nbsp; {_safe_text(data['week'])}</p>

    <div class="hero">
      <div class="hero-grid">
        <div>
          <div class="glance-title">At a Glance</div>
          <div class="glance-item"><strong>"What's working":</strong> {_safe_text(insights['at_a_glance']['working'])}</div>
          <div class="glance-item"><strong>"What's hindering you":</strong> {_safe_text(insights['at_a_glance']['hindering'])}</div>
          <div class="glance-item"><strong>Quick wins to try:</strong> {_safe_text(insights['at_a_glance']['quick_win'])}</div>
          <div class="glance-item"><strong>On the horizon:</strong> {_safe_text(insights['at_a_glance']['ambitious'])}</div>
        </div>
        <div class="hero-side">
          <div class="hero-metric"><div class="hero-metric-value">{total_sessions}</div><div class="hero-metric-label">Sessions</div></div>
          <div class="hero-metric"><div class="hero-metric-value">{_format_tokens(total_tokens)}</div><div class="hero-metric-label">Context Tokens</div></div>
          <div class="hero-metric"><div class="hero-metric-value">{patch_success}</div><div class="hero-metric-label">Patch Success</div></div>
          <div class="hero-metric"><div class="hero-metric-value">{aborted_turns + compactions}</div><div class="hero-metric-label">Friction Events</div></div>
        </div>
      </div>
    </div>

    <div class="stats-row">
"""]

    html_parts.append(_render_stat(total_sessions, _I18N("Sessions")))
    html_parts.append(_render_stat(total_messages, _I18N("Messages")))
    html_parts.append(_render_stat(_format_tokens(total_tokens), _I18N("Context Tokens")))
    html_parts.append(_render_stat(f"+{lines_added}/-{lines_removed}", _I18N("Code Lines")))
    html_parts.append(_render_stat(active_days, _I18N("Active Days")))
    html_parts.append(_render_stat(full_auto, _I18N("Fully Automated Sessions")))
    html_parts.append(_render_stat(interactive, _I18N("Interactive Sessions")))
    html_parts.append(_render_stat(patch_files, _I18N("Files Changed")))
    html_parts.append("""    </div>\n""")

    html_parts.append("""    <h2>What You Work On</h2>
    <p class="section-intro">Abstracting stable workflows rather than project names — showing what types of work you use Codex for.</p>
    <div class="project-areas">
""")
    if insights["work_on"]:
        for item in insights["work_on"]:
            html_parts.append(
                f"""      <div class="project-area">
        <div class="area-header">
          <span class="area-name">{_safe_text(item['name'])}</span>
          <span class="area-count">{item['sessions']} 会话 · {_format_tokens(item['tokens'])} tokens</span>
        </div>
        <div class="area-desc">{_safe_text(item['desc'])}</div>
      </div>
"""
            )
    else:
        html_parts.append('      <p class="empty">No project data available for this period.</p>\n')
    html_parts.append("    </div>\n")

    html_parts.append("""    <h2>How You Use Codex</h2>
    <div class="card" style="margin-bottom: 18px;">
      <div class="card-detail" style="font-size:14px;line-height:1.7;">""")
    for idx, paragraph in enumerate(insights["narrative_parts"]):
        if idx:
            html_parts.append("<br><br>")
        html_parts.append(_safe_text(paragraph))
    html_parts.append(
        f"""<div class="key-insight" style="margin-top:14px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:12px 14px;color:#166534;"><strong>Key pattern:</strong> {_safe_text(insights['key_insight'])}</div>
      </div>
    </div>
    <div class="cards">
""")
    for card in insights["usage_cards"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{_safe_text(card['title'])}: {_safe_text(card['value'])}</div>
        <div class="card-detail">{_safe_text(card['desc'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    html_parts.append('    <div class="charts-row">\n')
    if data["models"]:
        max_model = max(_safe_int(r["cnt"]) for r in data["models"]) or 1
        html_parts.append("""      <div class="chart-card">
        html_parts.append('      <div class="chart-title">Model Distribution</div>\n')
""")
        for r in data["models"]:
            model_name = r["model"] or "default"
            pct = int(_safe_int(r["cnt"]) / max_model * 100)
            html_parts.append(
                f"""        <div class="model-row">
          <div class="model-name" title="{_safe_text(model_name)}">{_safe_text(model_name)}</div>
          <div class="model-bar-track"><div class="model-bar-fill" style="width:{pct}%"></div></div>
          <div class="model-count">{_safe_int(r['cnt'])}x</div>
          <div class="model-tokens">{_format_tokens(r['tokens'])}</div>
        </div>
"""
            )
        html_parts.append("      </div>\n")
    else:
        html_parts.append('      <p class="empty">No model statistics for this period.</p>\n')

    if data["daily"]:
        max_daily = max(_safe_int(r["sessions"]) for r in data["daily"]) or 1
        html_parts.append("""      <div class="chart-card">
        html_parts.append('      <div class="chart-title">Daily Sessions</div>\n')
""")
        html_parts.append(_render_bar_rows(data["daily"], "day", "sessions", max_daily))
        html_parts.append("\n      </div>\n")
    else:
        html_parts.append('      <p class="empty">No daily data for this period.</p>\n')
    html_parts.append("    </div>\n")

    html_parts.append('    <div class="charts-row">\n')
    if insights["top_tools"]:
        max_tool = max(item["count"] for item in insights["top_tools"]) or 1
        html_parts.append("""      <div class="chart-card">
        html_parts.append('      <div class="chart-title">Top Tools</div>\n')
""")
        html_parts.append(_render_bar_rows(insights["top_tools"], "name", "count", max_tool, "x"))
        html_parts.append("\n      </div>\n")
    else:
        html_parts.append('      <p class="empty">No tool calls detected.</p>\n')

    if insights["top_commands"]:
        max_command = max(item["count"] for item in insights["top_commands"]) or 1
        html_parts.append("""      <div class="chart-card">
        html_parts.append('      <div class="chart-title">Top Commands</div>\n')
""")
        html_parts.append(_render_bar_rows(insights["top_commands"], "name", "count", max_command, "x"))
        html_parts.append("\n      </div>\n")
    else:
        html_parts.append('      <p class="empty">No command execution detected.</p>\n')
    html_parts.append("    </div>\n")

    html_parts.append("""    <h2>Impressive Things You Did</h2>
    <p class="section-intro">All conclusions are grounded in real rollout events and execution traces — no filler.</p>
    <div class="cards">
""")
    if insights["wins"]:
        for item in insights["wins"]:
            html_parts.append(
                f"""      <div class="card">
        <div class="card-title">{_safe_text(item['title'])}</div>
        <div class="card-detail">{_safe_text(item['detail'])}</div>
      </div>
"""
            )
    else:
        html_parts.append('      <div class="card"><div class="card-title">样本不足</div><div class="card-detail">这周 Codex 使用量偏少，暂时不足以提炼稳定优势。</div></div>\n')
    html_parts.append("    </div>\n")

    html_parts.append("""    <h2>Where Things Go Wrong</h2>
    <p class="section-intro">Focusing on friction that genuinely hurts efficiency: wrong paths, interruptions, context pressure, and the gap between research and implementation.</p>
    <div class="cards">
""")
    if insights["friction"]:
        for item in insights["friction"]:
            html_parts.append(
                f"""      <div class="card">
        <div class="card-title">{_safe_text(item['title'])}</div>
        <div class="card-detail">{_safe_text(item['detail'])}</div>
      </div>
"""
            )
    else:
        html_parts.append('      <div class="card"><div class="card-title">摩擦不明显</div><div class="card-detail">这周没有解析到显著的中断、压缩或探索失衡问题。</div></div>\n')
    html_parts.append("    </div>\n")

    html_parts.append("""    <h2>Features to Try</h2>
    <p class="section-intro">Not generic advice — the most relevant improvements for your current usage phase.</p>
    <div class="cards">
""")
    for item in insights["features"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{_safe_text(item['title'])}</div>
        <div class="card-detail">{_safe_text(item['detail'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    html_parts.append("""    <h2>New Ways to Use Codex</h2>
    <p class="section-intro">Behavior pattern insights derived from this period — not tied to any specific project.</p>
    <div class="cards">
""")
    for item in insights["patterns"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{_safe_text(item['title'])}</div>
        <div class="card-detail">{_safe_text(item['summary'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    html_parts.append("""    <h2>On the Horizon</h2>
    <p class="section-intro">The next stage your current usage already points toward — not distant fantasies.</p>
    <div class="cards">
""")
    for item in insights["horizon"]:
        html_parts.append(
            f"""      <div class="card">
        <div class="card-title">{_safe_text(item['title'])}</div>
        <div class="card-detail">{_safe_text(item['detail'])}</div>
      </div>
"""
        )
    html_parts.append("    </div>\n")

    if data.get("efforts"):
        effort_colors = {"low": "#22c55e", "medium": "#7c3aed", "high": "#ef4444"}
        max_effort = max(_safe_int(r["cnt"]) for r in data["efforts"]) or 1
        html_parts.append("""    <h2>Reasoning Effort</h2>
    <div class="chart-card">
      <div class="chart-title">推理深度分布</div>
""")
        for r in data["efforts"]:
            effort = r["reasoning_effort"] or "medium"
            pct = int(_safe_int(r["cnt"]) / max_effort * 100)
            color = effort_colors.get(effort, "#7c3aed")
            html_parts.append(
                f"""      <div class="model-row">
        <div class="model-name">{_safe_text(effort)}</div>
        <div class="model-bar-track"><div class="model-bar-fill" style="width:{pct}%;background:{color}"></div></div>
        <div class="model-count">{_safe_int(r['cnt'])}x</div>
      </div>
"""
            )
        html_parts.append("    </div>\n")

    raw_data = {
        "week": data["week"],
        "period_start": data["period_start"],
        "period_end": data["period_end"],
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "user_messages": _safe_int(insights.get("user_messages")),
        "agent_messages": _safe_int(insights.get("agent_messages")),
        "message_events": _safe_int(insights.get("message_events")),
        "total_tokens": total_tokens,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "active_days": active_days,
        "full_auto": full_auto,
        "interactive": interactive,
        "efforts": [{"effort": r["reasoning_effort"], "sessions": _safe_int(r["cnt"])} for r in data.get("efforts", [])],
        "models": [
            {"model": r["model"], "sessions": _safe_int(r["cnt"]), "tokens": _safe_int(r["tokens"])}
            for r in data["models"]
        ],
        "areas": [
            {"cwd": r["cwd"], "sessions": _safe_int(r["cnt"]), "tokens": _safe_int(r["tokens"])}
            for r in data["areas"]
        ],
        "daily": [
            {"day": r["day"], "sessions": _safe_int(r["sessions"]), "tokens": _safe_int(r["tokens"])}
            for r in data["daily"]
        ],
        "insights": insights,
        "thread_details": data["thread_details"][:20],
    }

    html_parts.append(
        f"""
    <div class="raw-data" id="codex-raw-data">{json.dumps(raw_data, ensure_ascii=False)}</div>
  </div>
</body>
</html>
"""
    )

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))
    print(f"Codex report: {out_path}", file=sys.stderr)


def main():
    import argparse

    today = datetime.date.today()
    iso_cal = today.isocalendar()
    default_week = f"{iso_cal[0]}-W{iso_cal[1]:02d}"

    parser = argparse.ArgumentParser(description="采集 Codex CLI 使用数据")
    parser.add_argument("week", nargs="?", default=default_week, help="ISO 周标识，如 2026-W13")
    parser.add_argument("--output", "-o", metavar="PATH", help="输出 HTML 路径")
    args = parser.parse_args()

    data = collect(args.week)
    if data is None:
        print("No Codex data found (~/.codex/state_5.sqlite not found)", file=sys.stderr)
        sys.exit(1)

    if args.output:
        generate_html(data, args.output)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
