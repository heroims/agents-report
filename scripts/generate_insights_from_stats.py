#!/usr/bin/env python3
"""从本地 Claude usage-data 生成 richer 的 Claude Code Insights HTML。"""

import datetime
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import sys as _sys
_scripts_dir = str((__import__('pathlib').Path(__file__).resolve().parent))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
from period_utils import detect_period_type, period_start_end, period_label as plabel



def load_exclude_paths():
    exclude_file = Path(__file__).resolve().parent / "exclude_paths.json"
    if not exclude_file.exists():
        return []
    try:
        with open(exclude_file, "r", encoding="utf-8") as f:
            paths = json.load(f)
        return [str(p).rstrip("/\\") for p in paths if p]
    except Exception:
        return []


def _is_excluded_path(project_path, exclude_paths):
    if not exclude_paths or not project_path:
        return False
    norm = str(project_path).rstrip("/\\")
    return any(
        norm == ex or norm.startswith(ex + "/") or norm.startswith(ex + "\\")
        for ex in exclude_paths
    )


def parse_period_arg(period_str=None):
    """解析周期标识（周/月/季/年），返回 (period_str, period_start, period_end)。"""
    if period_str:
        start, end = period_start_end(period_str)
        return period_str, start, end

    # 默认：当前 ISO 周
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    period_start = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    period_end = datetime.date.fromisocalendar(iso_year, iso_week, 7)
    return f"{iso_year}-W{iso_week:02d}", period_start, period_end


def load_stats():
    """加载 stats-cache.json。"""
    stats_path = Path.home() / ".claude" / "stats-cache.json"
    if not stats_path.exists():
        return None
    with open(stats_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_session_meta(period_start, period_end, exclude_paths=None):
    """加载 usage-data/session-meta 作为 Claude 周报主数据源。"""
    session_meta_dir = Path.home() / ".claude" / "usage-data" / "session-meta"
    if not session_meta_dir.exists():
        return []

    records = []
    for path in session_meta_dir.glob("*.json"):
        try:
            obj = json.loads(path.read_text())
        except Exception:
            continue
        start_time = obj.get("start_time") or ""
        if not start_time:
            continue
        try:
            entry_date = datetime.date.fromisoformat(start_time[:10])
        except ValueError:
            continue
        if period_start <= entry_date <= period_end:
            if _is_excluded_path(obj.get("project_path"), exclude_paths):
                continue
            records.append(obj)
    return records


def load_facets(session_ids):
    """按 session_id 加载 facets 数据。"""
    facets_dir = Path.home() / ".claude" / "usage-data" / "facets"
    if not facets_dir.exists() or not session_ids:
        return {}

    wanted = set(session_ids)
    facets_by_session = {}
    for path in facets_dir.glob("*.json"):
        try:
            obj = json.loads(path.read_text())
        except Exception:
            continue
        session_id = obj.get("session_id")
        if session_id and session_id in wanted:
            facets_by_session[session_id] = obj
    return facets_by_session


def load_ccusage_blocks(period_start, period_end):
    """获取 ccusage token 数据：优先实时调用 ccusage --json，回退到 ccusage-cache.json。"""
    import subprocess as _sp

    since_str = period_start.strftime("%Y%m%d")
    until_str = period_end.strftime("%Y%m%d")
    try:
        result = _sp.run(
            ["ccusage", "blocks", "--since", since_str, "--until", until_str, "--json", "--no-color"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            blocks = data.get("blocks", []) if isinstance(data, dict) else data
            return [b for b in blocks if not b.get("isGap") and b.get("totalTokens")]
    except Exception:
        pass

    # 回退：从 ccusage-cache.json 读取（可能过期）
    cache_path = Path.home() / ".claude" / "ccusage-cache.json"
    if not cache_path.exists():
        return []
    try:
        blocks = json.loads(cache_path.read_text(encoding="utf-8")).get("blocks", [])
    except Exception:
        return []
    result_blocks = []
    for block in blocks:
        if block.get("isGap"):
            continue
        start_time = block.get("startTime", "")
        if not start_time:
            continue
        try:
            entry_date = datetime.date.fromisoformat(start_time[:10])
        except ValueError:
            continue
        if period_start <= entry_date <= period_end:
            result_blocks.append(block)
    return result_blocks


def _iter_transcript_paths():
    """遍历 Claude 本地 JSONL 记录，兼容不同版本的数据目录。"""
    claude_dir = Path.home() / ".claude"
    transcripts_dir = claude_dir / "transcripts"
    if transcripts_dir.exists():
        yield from transcripts_dir.glob("*.jsonl")

    projects_dir = claude_dir / "projects"
    if projects_dir.exists():
        for path in projects_dir.rglob("*.jsonl"):
            if "subagents" in path.parts:
                continue
            yield path


def _entry_timestamp(obj):
    timestamp = obj.get("timestamp")
    if timestamp:
        return timestamp
    snapshot = obj.get("snapshot") or {}
    return snapshot.get("timestamp") or ""


def _entry_date(obj):
    timestamp = _entry_timestamp(obj)
    if not timestamp:
        return None
    try:
        return datetime.date.fromisoformat(timestamp[:10])
    except ValueError:
        return None


_LANGUAGE_BY_EXT = {
    ".bash": "Shell",
    ".css": "CSS",
    ".go": "Go",
    ".gradle": "Gradle",
    ".graphql": "GraphQL",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".json": "JSON",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".md": "Markdown",
    ".php": "PHP",
    ".proto": "Protocol Buffers",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scss": "CSS",
    ".sh": "Shell",
    ".sql": "SQL",
    ".svelte": "Svelte",
    ".swift": "Swift",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
}

_SPECIAL_LANGUAGE_FILES = {
    "dockerfile": "Docker",
    "go.mod": "Go",
    "go.sum": "Go",
    "makefile": "Make",
    "package.json": "JSON",
    "pom.xml": "Java",
    "requirements.txt": "Python",
    "tsconfig.json": "TypeScript",
}

_PATH_KEYS = {
    "file",
    "file_path",
    "filename",
    "notebook_path",
    "old_path",
    "new_path",
    "path",
    "source",
    "target",
}

_WRITE_TOOLS = {"Edit", "MultiEdit", "NotebookEdit", "Write"}
_PATH_IN_TEXT_RE = re.compile(
    r"(?:[A-Za-z0-9_@.+~-]+/)*[A-Za-z0-9_@.+~-]+\."
    r"(?:bash|css|go|gradle|graphql|html|java|js|json|jsx|kt|kts|md|php|proto|py|rb|rs|scss|sh|sql|svelte|swift|toml|ts|tsx|vue|xml|ya?ml)"
)


def _content_blocks(obj):
    message = obj.get("message") or {}
    content = obj.get("content")
    if content is None:
        content = message.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _normalize_prompt_text(text):
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    return " ".join(text.split())


def _is_noise_prompt_text(text):
    return str(text or "").strip().lower() in {"/exit", "exit", "/exit exit", "catch you later!", "see ya!", "see ya"}


def _is_user_prompt(obj):
    if obj.get("type") != "user" or obj.get("isMeta"):
        return False
    blocks = _content_blocks(obj)
    if not blocks or all(item.get("type") == "tool_result" for item in blocks):
        return False
    texts = [
        _normalize_prompt_text(item.get("text") or item.get("content") or "")
        for item in blocks
        if item.get("type") != "tool_result"
    ]
    cleaned = " ".join(text for text in texts if text).strip()
    return bool(cleaned)


def _prompt_text(obj):
    if not _is_user_prompt(obj):
        return ""
    parts = []
    for item in _content_blocks(obj):
        if item.get("type") == "tool_result":
            continue
        value = item.get("text") or item.get("content") or ""
        if isinstance(value, str):
            parts.append(value)
    return _truncate(_normalize_prompt_text(" ".join(parts)), 500)


def _clean_path_token(value):
    text = str(value or "").strip().strip("`'\".,;:()[]{}")
    if ":" in text:
        prefix, suffix = text.rsplit(":", 1)
        if suffix.isdigit():
            text = prefix
    return text


def _looks_like_path(value):
    text = _clean_path_token(value)
    if not text or len(text) > 400 or "\n" in text:
        return False
    name = Path(text).name.lower()
    return name in _SPECIAL_LANGUAGE_FILES or Path(text).suffix.lower() in _LANGUAGE_BY_EXT


def _extract_paths_from_text(text):
    return {_clean_path_token(match.group(0)) for match in _PATH_IN_TEXT_RE.finditer(str(text or ""))}


def _is_path_key(key):
    key = str(key or "")
    lowered = key.lower()
    return key in _PATH_KEYS or lowered.endswith("_path") or lowered.endswith("path")


def _collect_paths(value, parent_key=""):
    paths = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                if _is_path_key(key) and _looks_like_path(item):
                    paths.add(_clean_path_token(item))
                elif key in {"command", "glob", "pattern"}:
                    paths.update(_extract_paths_from_text(item))
            elif isinstance(item, (dict, list)):
                paths.update(_collect_paths(item, key))
    elif isinstance(value, list):
        for item in value:
            paths.update(_collect_paths(item, parent_key))
    elif isinstance(value, str) and _is_path_key(parent_key) and _looks_like_path(value):
        paths.add(_clean_path_token(value))
    return paths


def _language_for_path(path_value):
    text = _clean_path_token(path_value)
    if not text:
        return ""
    name = Path(text).name.lower()
    if name in _SPECIAL_LANGUAGE_FILES:
        return _SPECIAL_LANGUAGE_FILES[name]
    suffix = Path(text).suffix.lower()
    return _LANGUAGE_BY_EXT.get(suffix, "")


def _tool_name(item):
    return str(item.get("name") or item.get("toolName") or item.get("tool_name") or "tool_use")


def _tool_input(item):
    return item.get("input") or item.get("tool_input") or item.get("parameters") or {}


def _tool_key(obj, item):
    tool_id = item.get("id") or item.get("tool_use_id") or item.get("toolUseID")
    if tool_id:
        return ("id", str(tool_id))
    try:
        input_key = json.dumps(_tool_input(item), sort_keys=True, ensure_ascii=False)[:500]
    except Exception:
        input_key = str(_tool_input(item))[:500]
    return ("entry", str(obj.get("uuid") or (obj.get("message") or {}).get("id") or ""), _tool_name(item), input_key)


def _iter_tool_uses(obj):
    if obj.get("type") == "tool_use":
        yield obj
    for item in _content_blocks(obj):
        if item.get("type") == "tool_use":
            yield item


def _bash_writes(command):
    lowered = str(command or "").lower()
    return any(marker in lowered for marker in ("apply_patch", "cat >", "tee ", "sed -i", "python -c", "python3 -c"))


def _collect_tool_usage(obj, seen_tool_uses):
    tool_counts = Counter()
    paths = set()
    write_paths = set()
    for item in _iter_tool_uses(obj):
        key = _tool_key(obj, item)
        if key in seen_tool_uses:
            continue
        seen_tool_uses.add(key)
        name = _tool_name(item)
        tool_counts[name] += 1
        tool_input = _tool_input(item)
        item_paths = _collect_paths(tool_input)
        if name == "Bash" and isinstance(tool_input, dict):
            item_paths.update(_extract_paths_from_text(tool_input.get("command", "")))
        paths.update(item_paths)
        if name in _WRITE_TOOLS or (name == "Bash" and isinstance(tool_input, dict) and _bash_writes(tool_input.get("command", ""))):
            write_paths.update(item_paths)
    return tool_counts, paths, write_paths


def _usage_key(obj):
    message = obj.get("message") or {}
    if not message.get("usage"):
        return None
    return message.get("id") or obj.get("requestId") or obj.get("uuid")


def _usage_token_parts(obj):
    usage = (obj.get("message") or {}).get("usage") or {}
    input_keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {
        "input_tokens": sum(_safe_int(usage.get(key)) for key in input_keys),
        "output_tokens": _safe_int(usage.get("output_tokens")),
    }


def _parse_datetime(timestamp):
    if not timestamp:
        return None
    try:
        return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _error_category(text):
    lowered = str(text or "").lower()
    if "permission" in lowered or "denied" in lowered:
        return "permission_denied"
    if "no such file" in lowered or "not found" in lowered:
        return "missing_file"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "syntax" in lowered or "parse" in lowered:
        return "syntax_error"
    if "reject" in lowered or "rejected" in lowered:
        return "user_rejected"
    return "tool_error"


def _tool_error_categories(obj):
    errors = Counter()
    if obj.get("isApiErrorMessage"):
        errors["api_error"] += 1
    for item in _content_blocks(obj):
        if item.get("type") != "tool_result":
            continue
        is_error = item.get("is_error") or item.get("isError")
        if is_error:
            errors[_error_category(item.get("content") or item.get("text") or "")] += 1
    result = obj.get("toolUseResult") or {}
    if isinstance(result, dict):
        if result.get("interrupted"):
            errors["interrupted"] += 1
        if result.get("is_error") or result.get("error"):
            errors[_error_category(result.get("stderr") or result.get("error") or result.get("stdout") or "")] += 1
    return errors


def _contains_any(text, words):
    return any(word in text for word in words)


def _infer_goal_categories(prompt_texts, tool_counts, write_paths):
    goals = Counter()
    classifiers = [
        ("reporting", ("报告", "周报", "采集", "report", "getagt", "analyzeagt")),
        ("debugging", ("bug", "debug", "error", "fix", "报错", "错误", "异常", "修复", "排查", "问题")),
        ("code_review", ("review", "code review", "审查", "review 下", "帮我看看")),
        ("testing", ("test", "pytest", "测试", "用例", "验证")),
        ("documentation", ("doc", "readme", "文档", "说明")),
        ("analysis", ("analysis", "analyze", "分析", "定位", "为什么", "为啥", "怎么", "如何", "检查")),
        ("implementation", ("implement", "add", "create", "update", "change", "实现", "添加", "创建", "更新", "修改", "改")),
    ]
    for prompt in prompt_texts:
        lowered = prompt.lower()
        matched = False
        for label, words in classifiers:
            if _contains_any(lowered, words):
                goals[label] += 1
                matched = True
        if not matched and "?" in prompt:
            goals["question_answering"] += 1
    if write_paths or any(tool_counts.get(name) for name in _WRITE_TOOLS):
        goals["implementation"] += 1
    if not goals:
        goals["exploration" if tool_counts else "conversation"] += 1
    return goals


def _infer_session_type(goal_categories, tool_counts, write_paths):
    if write_paths or any(tool_counts.get(name) for name in _WRITE_TOOLS):
        return "implementation"
    if goal_categories.get("debugging"):
        return "debugging"
    if goal_categories.get("code_review"):
        return "code_review"
    if goal_categories.get("reporting") or goal_categories.get("analysis"):
        return "analysis_report"
    if goal_categories.get("testing"):
        return "testing"
    return "exploration" if tool_counts else "conversation"


def _iter_cli_transcript_paths():
    """只遍历 ~/.claude/projects/ 下的 CLI 主会话 JSONL（排除 web transcripts 和子代理）。"""
    projects_dir = Path.home() / ".claude" / "projects"
    if projects_dir.exists():
        for path in projects_dir.rglob("*.jsonl"):
            if "subagents" in path.parts:
                continue
            yield path


def load_from_transcripts(period_start, period_end, cli_only=False, exclude_paths=None):
    """从 Claude JSONL 提取基础统计，作为 session-meta 缺失时的 fallback。
    cli_only=True 时只读 projects/ 下的 CLI 会话，排除 web transcripts。
    """
    records = []
    seen = set()
    paths_iter = _iter_cli_transcript_paths() if cli_only else _iter_transcript_paths()
    for path in paths_iter:
        if path in seen:
            continue
        seen.add(path)
        messages = 0
        tool_counts = Counter()
        language_counter = Counter()
        tool_error_counter = Counter()
        prompt_texts = []
        prompt_times = []
        message_hours = []
        work_messages = 0
        all_paths = set()
        modified_paths = set()
        seen_tool_uses = set()
        seen_usage = set()
        first_ts = None
        last_ts = None
        input_tokens = 0
        output_tokens = 0
        project_path = "(unknown)"
        session_id = path.stem
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                entry_date = _entry_date(obj)
                if not entry_date or not (period_start <= entry_date <= period_end):
                    continue
                if obj.get("sessionId"):
                    session_id = obj["sessionId"]
                if obj.get("cwd"):
                    project_path = obj["cwd"]

                timestamp = _entry_timestamp(obj)
                parsed_ts = _parse_datetime(timestamp)
                if timestamp and not first_ts:
                    first_ts = timestamp
                if timestamp:
                    last_ts = timestamp

                if _is_user_prompt(obj):
                    messages += 1
                    text = _prompt_text(obj)
                    if text and not _is_noise_prompt_text(text):
                        prompt_texts.append(text)
                        work_messages += 1
                    if parsed_ts:
                        prompt_times.append(parsed_ts)
                        message_hours.append(parsed_ts.astimezone().hour)

                entry_tool_counts, entry_paths, entry_write_paths = _collect_tool_usage(obj, seen_tool_uses)
                tool_counts.update(entry_tool_counts)
                all_paths.update(entry_paths)
                modified_paths.update(entry_write_paths)

                snapshot_paths = set()
                snapshot = obj.get("snapshot") or {}
                backups = snapshot.get("trackedFileBackups") or {}
                if isinstance(backups, dict):
                    snapshot_paths = set(backups.keys())
                    all_paths.update(snapshot_paths)
                    modified_paths.update(snapshot_paths)

                tool_error_counter.update(_tool_error_categories(obj))

                usage_key = _usage_key(obj)
                if usage_key and usage_key not in seen_usage:
                    seen_usage.add(usage_key)
                    token_parts = _usage_token_parts(obj)
                    input_tokens += token_parts["input_tokens"]
                    output_tokens += token_parts["output_tokens"]
        except Exception:
            continue
        if messages > 0 and not _is_excluded_path(project_path, exclude_paths):
            for file_path in all_paths:
                language = _language_for_path(file_path)
                if language:
                    language_counter[language] += 1
            goal_categories = _infer_goal_categories(prompt_texts, tool_counts, modified_paths)
            session_type = _infer_session_type(goal_categories, tool_counts, modified_paths)
            prompt_times.sort()
            response_times = []
            for previous, current in zip(prompt_times, prompt_times[1:]):
                delta = (current - previous).total_seconds()
                if 1 <= delta <= 3600:
                    response_times.append(round(delta, 1))
            duration_minutes = 1
            start_dt = _parse_datetime(first_ts)
            end_dt = _parse_datetime(last_ts)
            if start_dt and end_dt and end_dt >= start_dt:
                duration_minutes = max(1, round((end_dt - start_dt).total_seconds() / 60))
            primary_success = "file_editing" if modified_paths else ("tool_grounded_execution" if tool_counts else "conversation")
            outcome = "mostly_achieved" if tool_counts and not tool_error_counter else "unknown"
            records.append({
                "session_id": session_id,
                "start_time": first_ts or f"{period_start.isoformat()}T00:00:00Z",
                "user_message_count": messages,
                "tool_counts": dict(tool_counts),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "project_path": project_path,
                "work_message_count": work_messages,
                "languages": dict(language_counter),
                "files_modified": len(modified_paths),
                "first_prompt": prompt_texts[0] if prompt_texts else "",
                "goal_categories": dict(goal_categories),
                "session_type": session_type,
                "tool_error_categories": dict(tool_error_counter),
                "user_response_times": response_times,
                "message_hours": message_hours,
                "duration_minutes": duration_minutes,
                "uses_task_agent": bool(tool_counts.get("Task")),
                "uses_web_search": bool(tool_counts.get("WebSearch")),
                "uses_web_fetch": bool(tool_counts.get("WebFetch")),
                "primary_success": primary_success,
                "outcome": outcome,
                "claude_helpfulness": "likely_helpful" if tool_counts else "mixed",
                "user_satisfaction_counts": {"neutral": 1},
                "friction_counts": {"tool_errors": sum(tool_error_counter.values())} if tool_error_counter else {},
            })
    return records


def inspect_local_sources(period_start, period_end, stats):
    """检查本地 Claude 数据源新鲜度。"""
    transcript_files = []
    newest_transcript = None
    for path in _iter_transcript_paths():
        modified_day = datetime.date.fromtimestamp(path.stat().st_mtime)
        if period_start <= modified_day <= period_end:
            transcript_files.append(path)
        if newest_transcript is None or path.stat().st_mtime > newest_transcript.stat().st_mtime:
            newest_transcript = path

    last_computed = None
    if stats and stats.get("lastComputedDate"):
        try:
            last_computed = datetime.date.fromisoformat(stats["lastComputedDate"])
        except ValueError:
            last_computed = None

    newest_transcript_day = None
    if newest_transcript:
        newest_transcript_day = datetime.date.fromtimestamp(newest_transcript.stat().st_mtime)

    issues = []
    if last_computed and last_computed < period_start:
        issues.append(f"stats-cache 停在 {last_computed.isoformat()}")
    if newest_transcript_day and newest_transcript_day < period_start:
        issues.append(f"Claude JSONL 最新只到 {newest_transcript_day.isoformat()}")
    if not transcript_files:
        issues.append("本周 Claude JSONL 目录没有会话文件")

    return {
        "stats_last_computed": last_computed.isoformat() if last_computed else "",
        "week_transcript_count": len(transcript_files),
        "newest_transcript_day": newest_transcript_day.isoformat() if newest_transcript_day else "",
        "has_current_week_source": bool(transcript_files),
        "issues": issues,
    }


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fmt_tokens(value):
    value = _safe_int(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _labelize(name):
    return str(name or "").replace("_", " ").replace("-", " ").title()


def _truncate(text, limit=180):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _workstream_label(index):
    return f"Workstream {index + 1}"


def _project_display_name(project_path):
    path = Path(str(project_path or "").rstrip("/"))
    name = path.name or str(project_path or "(unknown)")
    if name == ".docs" and path.parent.name:
        return f"{path.parent.name} docs"
    return name


def _focus_from_goals(goal_counts, fallback_text=""):
    if goal_counts:
        top_goal = goal_counts.most_common(1)[0][0]
        labels = {
            "analysis": "分析与定位",
            "code_review": "代码审查",
            "debugging": "Bug 排查",
            "documentation": "文档整理",
            "implementation": "功能实现与改动",
            "reporting": "报告产出",
            "testing": "测试与验证",
        }
        return labels.get(top_goal, _labelize(top_goal))
    text = fallback_text.lower()
    if "report" in text or "报告" in fallback_text:
        return "报告产出"
    if "test" in text or "case" in text or "测试" in fallback_text:
        return "测试与验证"
    if "review" in text or "审查" in fallback_text:
        return "代码审查"
    return "排查与落地"


def _bucket_response_times(values):
    buckets = [
        ("2-10s", 2, 10),
        ("10-30s", 10, 30),
        ("30s-1m", 30, 60),
        ("1-2m", 60, 120),
        ("2-5m", 120, 300),
        ("5-15m", 300, 900),
        (">15m", 900, None),
    ]
    counts = []
    for label, low, high in buckets:
        if high is None:
            count = sum(1 for v in values if v >= low)
        else:
            count = sum(1 for v in values if low <= v < high)
        counts.append({"name": label, "count": count})
    return counts


def compute_week_stats(stats, period_start, period_end, session_meta_records=None, ccusage_blocks=None):
    """计算本周基础统计。"""
    session_meta_records = session_meta_records or []
    if session_meta_records:
        daily_tokens_map = defaultdict(int)
        daily_messages_map = defaultdict(int)
        tool_counter = Counter()
        project_counter = {}
        week_messages = 0
        week_sessions = 0
        week_tool_calls = 0
        week_tokens = 0

        for record in session_meta_records:
            day = (record.get("start_time") or "")[:10]
            if not day:
                continue
            messages = _safe_int(record.get("user_message_count"))
            tokens = _safe_int(record.get("input_tokens")) + _safe_int(record.get("output_tokens"))
            tool_counts = record.get("tool_counts") or {}
            project_path = record.get("project_path") or "(unknown)"
            project_name = os.path.basename(project_path.rstrip("/")) or project_path
            project = project_counter.setdefault(
                project_name,
                {"name": project_name, "path": project_path, "sessions": 0, "tokens": 0, "messages": 0},
            )

            week_messages += messages
            week_sessions += 1
            week_tokens += tokens
            daily_tokens_map[day] += tokens
            daily_messages_map[day] += messages
            project["sessions"] += 1
            project["tokens"] += tokens
            project["messages"] += messages

            for tool_name, count in tool_counts.items():
                count_value = _safe_int(count)
                tool_counter[tool_name] += count_value
                week_tool_calls += count_value

        models_used = {}
        for entry in (stats or {}).get("dailyModelTokens", []):
            entry_date = datetime.date.fromisoformat(entry["date"])
            if period_start <= entry_date <= period_end:
                for model, tokens in entry.get("tokensByModel", {}).items():
                    models_used[model] = models_used.get(model, 0) + _safe_int(tokens)

        # session-meta 无 token 数据时（如 transcript fallback），用 ccusage 补充
        if week_tokens == 0 and ccusage_blocks:
            for block in ccusage_blocks:
                day = block.get("startTime", "")[:10]
                tc = block.get("tokenCounts") or {}
                block_tokens = sum(_safe_int(tc.get(k)) for k in ("inputTokens", "outputTokens", "cacheCreationInputTokens", "cacheReadInputTokens"))
                week_tokens += block_tokens
                daily_tokens_map[day] += block_tokens
                n_models = len(block.get("models") or []) or 1
                for model in block.get("models") or []:
                    models_used[model] = models_used.get(model, 0) + (block_tokens // n_models)

        daily_tokens = [
            {"day": day, "tokens": daily_tokens_map[day], "messages": daily_messages_map[day]}
            for day in sorted(daily_tokens_map)
        ]
        areas = sorted(project_counter.values(), key=lambda item: (item["sessions"], item["tokens"]), reverse=True)
        top_tools = [{"name": name, "count": count} for name, count in tool_counter.most_common(10)]
        estimated_lines = sum(_safe_int(record.get("lines_added")) for record in session_meta_records) or (week_messages * 50)
        estimated_removed = sum(_safe_int(record.get("lines_removed")) for record in session_meta_records) or (estimated_lines // 10)
        files_modified = sum(_safe_int(record.get("files_modified")) for record in session_meta_records) or (week_sessions * 3)
        return {
            "messages": week_messages,
            "sessions": week_sessions,
            "tool_calls": week_tool_calls,
            "tokens": week_tokens,
            "estimated_lines": estimated_lines,
            "estimated_removed": estimated_removed,
            "estimated_files": files_modified,
            "models_used": models_used,
            "daily_tokens": daily_tokens,
            "areas": areas,
            "top_tools": top_tools,
            "source": "session-meta",
        }

    daily_activity = (stats or {}).get("dailyActivity", [])
    daily_model_tokens = (stats or {}).get("dailyModelTokens", [])
    week_messages = 0
    week_sessions = 0
    week_tool_calls = 0
    week_tokens = 0
    models_used = {}
    daily_tokens = []

    for entry in daily_activity:
        entry_date = datetime.date.fromisoformat(entry["date"])
        if period_start <= entry_date <= period_end:
            week_messages += _safe_int(entry.get("messageCount"))
            week_sessions += _safe_int(entry.get("sessionCount"))
            week_tool_calls += _safe_int(entry.get("toolCallCount"))

    for entry in daily_model_tokens:
        entry_date = datetime.date.fromisoformat(entry["date"])
        if period_start <= entry_date <= period_end:
            day_tokens = sum(_safe_int(v) for v in entry.get("tokensByModel", {}).values())
            week_tokens += day_tokens
            daily_tokens.append({"day": entry["date"], "tokens": day_tokens, "messages": 0})
            for model, tokens in entry.get("tokensByModel", {}).items():
                models_used[model] = models_used.get(model, 0) + _safe_int(tokens)

    # stats-cache 过期时，用 ccusage-cache.json 补充 token 数据
    if week_tokens == 0 and ccusage_blocks:
        daily_tokens_map: dict = defaultdict(int)
        for block in ccusage_blocks:
            day = block.get("startTime", "")[:10]
            tc = block.get("tokenCounts") or {}
            day_tokens = sum(_safe_int(tc.get(k)) for k in ("inputTokens", "outputTokens", "cacheCreationInputTokens", "cacheReadInputTokens"))
            week_tokens += day_tokens
            daily_tokens_map[day] += day_tokens
            for model in block.get("models") or []:
                models_used[model] = models_used.get(model, 0) + (day_tokens // max(len(block.get("models") or [1]), 1))
        daily_tokens = [{"day": d, "tokens": t, "messages": 0} for d, t in sorted(daily_tokens_map.items())]

    estimated_lines = week_messages * 50
    return {
        "messages": week_messages,
        "sessions": week_sessions,
        "tool_calls": week_tool_calls,
        "tokens": week_tokens,
        "estimated_lines": estimated_lines,
        "estimated_removed": estimated_lines // 10,
        "estimated_files": week_sessions * 3,
        "models_used": models_used,
        "daily_tokens": daily_tokens,
        "areas": [],
        "top_tools": [],
        "source": "stats-cache",
    }


def build_rich_insights(session_meta_records, facets_by_session, ws):
    """构建 richer 的 Claude Insights 数据。"""
    goal_counter = Counter()
    language_counter = Counter()
    session_type_counter = Counter()
    success_counter = Counter()
    outcome_counter = Counter()
    friction_counter = Counter()
    satisfaction_counter = Counter()
    tool_error_counter = Counter()
    work_metrics = defaultdict(lambda: {
        "name": "",
        "sessions": 0,
        "tokens": 0,
        "messages": 0,
        "goals": Counter(),
        "languages": Counter(),
        "tools": Counter(),
    })
    response_times = []
    hour_counter = Counter()
    helpfulness_counter = Counter()

    overlap_events = 0
    overlap_sessions = set()
    intervals = []

    for record in session_meta_records:
        session_id = record.get("session_id")
        facet = facets_by_session.get(session_id, {})
        tokens = _safe_int(record.get("input_tokens")) + _safe_int(record.get("output_tokens"))
        messages = _safe_int(record.get("user_message_count"))
        work_messages = _safe_int(record.get("work_message_count"))
        record_goals = facet.get("goal_categories") or record.get("goal_categories") or {}
        summary = facet.get("brief_summary") or record.get("first_prompt") or ""
        focus = _focus_from_goals(Counter({key: _safe_int(value) for key, value in record_goals.items()}), summary)

        metric = None
        if work_messages or record.get("tool_counts"):
            metric = work_metrics[focus]
            metric["name"] = focus
            metric["sessions"] += 1
            metric["tokens"] += tokens
            metric["messages"] += work_messages or messages

        for goal, count in record_goals.items():
            goal_counter[goal] += _safe_int(count)
            if metric is not None:
                metric["goals"][goal] += _safe_int(count)
        for language, count in (record.get("languages") or {}).items():
            language_counter[language] += _safe_int(count)
            if metric is not None:
                metric["languages"][language] += _safe_int(count)
        for tool_name, count in (record.get("tool_counts") or {}).items():
            if metric is not None:
                metric["tools"][tool_name] += _safe_int(count)
        session_type = facet.get("session_type") or record.get("session_type")
        if session_type:
            session_type_counter[session_type] += 1
        primary_success = facet.get("primary_success") or record.get("primary_success")
        if primary_success:
            success_counter[primary_success] += 1
        outcome = facet.get("outcome") or record.get("outcome")
        if outcome:
            outcome_counter[outcome] += 1
        helpfulness = facet.get("claude_helpfulness") or record.get("claude_helpfulness")
        if helpfulness:
            helpfulness_counter[helpfulness] += 1
        for friction, count in (facet.get("friction_counts") or record.get("friction_counts") or {}).items():
            friction_counter[friction] += _safe_int(count)
        for label, count in (facet.get("user_satisfaction_counts") or record.get("user_satisfaction_counts") or {}).items():
            satisfaction_counter[label] += _safe_int(count)
        for err, count in (record.get("tool_error_categories") or {}).items():
            tool_error_counter[err] += _safe_int(count)

        response_times.extend(record.get("user_response_times") or [])
        for hour in record.get("message_hours") or []:
            hour_counter[_safe_int(hour)] += 1

        start_time = record.get("start_time")
        if start_time:
            try:
                start_dt = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                end_dt = start_dt + datetime.timedelta(minutes=max(_safe_int(record.get("duration_minutes")), 1))
                intervals.append((session_id, start_dt, end_dt))
            except ValueError:
                pass

    intervals.sort(key=lambda item: item[1])
    for idx, left in enumerate(intervals):
        for right in intervals[idx + 1 :]:
            if right[1] >= left[2]:
                break
            overlap_events += 1
            overlap_sessions.add(left[0])
            overlap_sessions.add(right[0])

    ranked_work = sorted(work_metrics.items(), key=lambda item: (item[1]["sessions"], item[1]["tokens"]), reverse=True)
    work_on = []
    for _, metric in ranked_work[:5]:
        work_style = _t("High-density execution flow") if metric["sessions"] >= 3 else _t("Sporadic task flow")
        delivery_shape = _t("Change and verify in parallel") if metric["messages"] >= 10 else _t("Read and confirm primarily")
        top_languages = "、".join(name for name, _ in metric["languages"].most_common(3)) or _t("Mixed stack")
        top_tools = "、".join(name for name, _ in metric["tools"].most_common(3)) or _t("Dialogue")
        work_on.append({
            "name": metric["name"],
            "sessions": metric["sessions"],
            "desc": (
                f"约 {metric['sessions']} 次会话、{metric['messages']} 条消息，属于本周较稳定的 {work_style}。"
                f" 涉及 {top_languages}，高频工具是 {top_tools}，会话形态上以 {delivery_shape} 为主。"
            ),
        })

    top_goal_labels = [_labelize(name) for name, _ in goal_counter.most_common(3)]
    top_success = _labelize(success_counter.most_common(1)[0][0]) if success_counter else "Delivery"
    top_friction = _labelize(friction_counter.most_common(1)[0][0]) if friction_counter else "Low Friction"
    top_helpfulness = _labelize(helpfulness_counter.most_common(1)[0][0]) if helpfulness_counter else "Mixed"
    top_outcome = _labelize(outcome_counter.most_common(1)[0][0]) if outcome_counter else "Mixed"
    active_days = len({item["day"] for item in ws.get("daily_tokens") or []})
    achieved_count = sum(count for name, count in outcome_counter.items() if name in {"fully_achieved", "mostly_achieved"})
    dissatisfied_count = sum(count for name, count in satisfaction_counter.items() if name in {"dissatisfied", "frustrated"})
    top_tool_names = [item.get("name", "") for item in (ws.get("top_tools") or [])[:3] if item.get("name")]
    top_language_names = [_labelize(name) for name, _ in language_counter.most_common(3)]
    task_agent_sessions = sum(1 for record in session_meta_records if record.get("uses_task_agent"))
    web_enabled_sessions = sum(1 for record in session_meta_records if record.get("uses_web_search") or record.get("uses_web_fetch"))
    response_distribution = _bucket_response_times(response_times)
    response_median = round(statistics.median(response_times), 1) if response_times else 0
    response_avg = round(statistics.mean(response_times), 1) if response_times else 0
    frequent_response_bucket = next(
        (item["name"] for item in response_distribution if item["count"] == max((x["count"] for x in response_distribution), default=0)),
        "mixed",
    )

    glance = [
        {
            "title": "What's working",
            "detail": (
                f"You are using Claude Code as an execution system, not a chat sidebar. "
                f"This week centered on {', '.join(top_goal_labels[:2]) or 'code work'}, and the strongest recurring win was still {top_success.lower()}. "
                f"{achieved_count}/{max(len(facets_by_session), 1)} faceted sessions landed in mostly or fully achieved outcomes, which is a strong hit rate for work that clearly involves real debugging, editing, and verification."
            )
        },
        {
            "title": "What's hindering you",
            "detail": (
                f"The main drag was still {top_friction.lower()}, and that matters because it wastes your time before the real work even begins. "
                f"Claude helpfulness still skewed toward {top_helpfulness.lower()}, so the issue is not lack of capability; it is that the model still starts too many tasks with weak assumptions or the wrong output shape. "
                f"You logged {dissatisfied_count} dissatisfied or frustrated signals, which is high enough to treat as a workflow problem rather than random variance."
            )
        },
        {
            "title": "Quick wins to try",
            "detail": (
                f"The fastest win is to front-load output format, verification target, and tool boundaries before Claude touches {', '.join(top_tool_names) or 'its main tools'}. "
                f"Your best sessions already behave this way: the task is framed as inspect -> change -> verify, not just 'look into this'. "
                f"If a task ends in a report, a passing test, or a generated artifact, make that success condition explicit in the first turn."
            )
        },
        {
            "title": "Ambitious workflows",
            "detail": (
                f"You are already operating close to autonomous engineering loops. "
                f"With {task_agent_sessions} task-agent sessions, {web_enabled_sessions} web-enabled sessions, and heavy work across {', '.join(top_language_names) or 'mixed stacks'}, the next upgrade is not more prompts but fuller workflows: inspect -> patch -> rerun -> open result. "
                f"Your response pattern clusters in the {frequent_response_bucket} band, which suggests you are already comfortable supervising tight, iterative execution."
            )
        },
    ]

    narrative_parts = [
        f"You use Claude Code like a working engineering partner, not a note-taking assistant. Across {ws['sessions']} sessions and {ws['messages']} messages over {active_days} active days, the center of gravity stayed on {', '.join(top_goal_labels[:3]) or 'general engineering work'}, which means the week was dominated by execution, investigation, and delivery pressure rather than open-ended brainstorming.",
        f"The shape of the week is highly tool-driven. {', '.join(top_tool_names) or 'Claude tools'} carried a large share of the work, and language activity concentrated in {', '.join(_labelize(name) for name, _ in language_counter.most_common(4)) or 'mixed stacks'}. That combination usually points to a user who wants the model to read the real system, act on the real system, and verify results against the real system instead of staying in explanation mode.",
        f"The important nuance is that your friction profile is not a sign that Claude is failing outright. Outcomes skewed toward {top_outcome.lower()} and success skewed toward {top_success.lower()}, but the repeated cost was {top_friction.lower()}: wasted motion before convergence. In practice, that means your leverage now comes less from better answers and more from better entry constraints.",
    ]

    key_insight = "Claude is most valuable for you when the task already has a concrete finish line; your main waste comes from letting the session spend too long discovering the shape of the task instead of executing it."

    periods = {
        "Morning (6-12)": 0,
        "Afternoon (12-18)": 0,
        "Evening (18-24)": 0,
        "Night (0-6)": 0,
    }
    for hour, count in hour_counter.items():
        if 6 <= hour < 12:
            periods["Morning (6-12)"] += count
        elif 12 <= hour < 18:
            periods["Afternoon (12-18)"] += count
        elif 18 <= hour < 24:
            periods["Evening (18-24)"] += count
        else:
            periods["Night (0-6)"] += count

    wins = []
    for facet in sorted(facets_by_session.values(), key=lambda item: (_safe_int((item.get("user_satisfaction_counts") or {}).get("likely_satisfied")), item.get("outcome") == "fully_achieved"), reverse=True):
        summary = facet.get("brief_summary")
        if not summary:
            continue
        wins.append({
            "title": _labelize(facet.get("primary_success") or "Meaningful Delivery"),
            "desc": summary,
        })
        if len(wins) >= 3:
            break
    if not wins and session_meta_records:
        wins.append({
            "title": "Real engineering progress",
            "desc": "This week still contains real local Claude sessions with code reading, command execution, and iterative debugging.",
        })

    frictions = []
    for facet in facets_by_session.values():
        detail = facet.get("friction_detail") or ""
        if not detail:
            continue
        title = _labelize(next(iter((facet.get("friction_counts") or {}).keys()), "Friction"))
        frictions.append({
            "title": title,
            "desc": detail,
            "examples": [_truncate(facet.get("brief_summary") or facet.get("underlying_goal") or "", 160)],
        })
    if not frictions and friction_counter:
        for name, _ in friction_counter.most_common(3):
            frictions.append({
                "title": _labelize(name),
                "desc": "这一类摩擦在本周重复出现，说明它不是偶发问题，而是提示方式或任务边界定义的问题。",
                "examples": [],
            })

    features = [
        {
            "title": "Custom Skills",
            "why": "Your recurring workflows are already recognizable. A reusable skill can hard-code the order you clearly prefer: inspect -> run -> verify -> open result, so Claude spends less time rediscovering your operating style.",
        },
        {
            "title": "Hooks",
            "why": "Post-edit or post-test hooks would turn some of this week's buggy-code and wrong-path friction into immediate feedback instead of another round-trip with you in the middle.",
        },
        {
            "title": "Project Instructions",
            "why": "This week's misses suggest Claude still needs clearer standing constraints at the start, especially around output format, preferred tools, and what counts as done.",
        },
    ]

    patterns = [
        {
            "title": "Exploration becomes execution fastest when the target is concrete",
            "summary": "Your best sessions had an explicit verify condition: run the check, generate the artifact, or open the result instead of ending on analysis complete.",
        },
        {
            "title": "Wrong-approach friction is still the primary waste source",
            "summary": "Most of the pain this week was not inability, but wasted motion before Claude converged on the right path and right output shape.",
        },
        {
            "title": "Report-generating workflows are a good fit for automation",
            "summary": "You already use Claude inside test and report loops. Those are strong candidates for commandized, repeatable flows because the finish line is objective.",
        },
    ]

    horizon = [
        {
            "title": "Autonomous test-run + report loops",
            "desc": "The most obvious next step is a single flow that reproduces a case, fixes it, reruns it, and opens the resulting report without manual coordination between stages.",
        },
        {
            "title": "Project inventory and compliance snapshots",
            "desc": "Your exploration and documentation sessions can evolve into scheduled project snapshots that inventory implemented tests, module coverage, and environment drift.",
        },
        {
            "title": "Claude as a repeatable QA operator",
            "desc": "With tighter instructions, the current pattern of inspect -> execute -> verify can become a reliable weekly QA assistant rather than a session-by-session tool.",
        },
    ]

    fun_ending = None
    if frictions:
        fun_ending = {
            "headline": frictions[0]["title"],
            "detail": frictions[0]["desc"],
        }

    return {
        "glance": glance,
        "work_on": work_on,
        "goal_counter": goal_counter,
        "language_counter": language_counter,
        "session_type_counter": session_type_counter,
        "success_counter": success_counter,
        "outcome_counter": outcome_counter,
        "friction_counter": friction_counter,
        "satisfaction_counter": satisfaction_counter,
        "tool_error_counter": tool_error_counter,
        "response_distribution": response_distribution,
        "response_median": response_median,
        "response_avg": response_avg,
        "time_periods": periods,
        "overlap_events": overlap_events,
        "overlap_sessions": len(overlap_sessions),
        "narrative_parts": narrative_parts,
        "key_insight": key_insight,
        "wins": wins,
        "frictions": frictions[:3],
        "features": features,
        "patterns": patterns,
        "horizon": horizon,
        "fun_ending": fun_ending,
    }


def _render_bar_rows(items, color):
    if not items:
        return '<p class="empty">No data available.</p>'
    max_count = max(item["count"] for item in items) or 1
    rows = []
    for item in items:
        width = (item["count"] / max_count) * 100 if max_count else 0
        rows.append(
            f"""<div class="bar-row">
        <div class="bar-label">{item['name']}</div>
        <div class="bar-track"><div class="bar-fill" style="width:{width}%;background:{color}"></div></div>
        <div class="bar-value">{item['count']}</div>
      </div>"""
        )
    return "\n".join(rows)


def generate_html(period_str, period_start, period_end, ws, insights, source_status=None):
    """生成 HTML 报告。"""
    total_sessions = ws["sessions"]
    total_messages = ws["messages"]
    total_tokens = ws["tokens"]
    estimated_files = ws.get("estimated_files", total_sessions * 3)
    active_days = len(set(item["day"] for item in ws["daily_tokens"]))
    msgs_per_day = round(total_messages / active_days, 1) if active_days else 0

    source_status = source_status or {}
    data_warning_html = ""
    if total_messages == 0 and total_sessions == 0 and source_status.get("issues"):
        issue_text = "；".join(source_status["issues"])
        data_warning_html = f"""    <div class="data-warning">
      <strong>数据源缺失：</strong>{issue_text}。当前页面的 0 值不代表你这周没用 Claude，只代表本机本地源拿不到本周数据。
    </div>
"""

    work_on_html = ""
    for item in insights["work_on"]:
        work_on_html += f"""      <div class="project-area">
        <div class="area-header">
          <span class="area-name">{item['name']}</span>
          <span class="area-count">~{item['sessions']} sessions</span>
        </div>
        <div class="area-desc">{item['desc']}</div>
      </div>
"""
    if not work_on_html:
        work_on_html = '      <p class="empty">本周暂无足够的项目分布样本。</p>\n'

    goals_html = _render_bar_rows(
        [{"name": _labelize(name), "count": count} for name, count in insights["goal_counter"].most_common(6)],
        "#2563eb",
    )
    tools_html = _render_bar_rows(ws.get("top_tools") or [], "#0891b2")
    languages_html = _render_bar_rows(
        [{"name": name, "count": count} for name, count in insights["language_counter"].most_common(6)],
        "#10b981",
    )
    session_types_html = _render_bar_rows(
        [{"name": _labelize(name), "count": count} for name, count in insights["session_type_counter"].most_common(6)],
        "#8b5cf6",
    )
    success_html = _render_bar_rows(
        [{"name": _labelize(name), "count": count} for name, count in insights["success_counter"].most_common(6)],
        "#16a34a",
    )
    outcome_html = _render_bar_rows(
        [{"name": _labelize(name), "count": count} for name, count in insights["outcome_counter"].most_common(6)],
        "#8b5cf6",
    )
    friction_bar_html = _render_bar_rows(
        [{"name": _labelize(name), "count": count} for name, count in insights["friction_counter"].most_common(6)],
        "#dc2626",
    )
    satisfaction_html = _render_bar_rows(
        [{"name": _labelize(name), "count": count} for name, count in insights["satisfaction_counter"].most_common(6)],
        "#eab308",
    )
    response_html = _render_bar_rows(insights["response_distribution"], "#6366f1")
    time_html = _render_bar_rows(
        [{"name": name, "count": count} for name, count in insights["time_periods"].items()],
        "#8b5cf6",
    )
    tool_errors_html = _render_bar_rows(
        [{"name": _labelize(name), "count": count} for name, count in insights["tool_error_counter"].most_common(6)],
        "#dc2626",
    )

    wins_html = ""
    for item in insights["wins"]:
        wins_html += f"""      <div class="big-win">
        <div class="big-win-title">{item['title']}</div>
        <div class="big-win-desc">{item['desc']}</div>
      </div>
"""

    friction_html = ""
    for item in insights["frictions"]:
        examples_html = ""
        if item["examples"]:
            examples_html = '<ul class="friction-examples"><li>' + "</li><li>".join(item["examples"]) + "</li></ul>"
        friction_html += f"""      <div class="friction-category">
        <div class="friction-title">{item['title']}</div>
        <div class="friction-desc">{item['desc']}</div>
        {examples_html}
      </div>
"""

    features_html = ""
    for item in insights["features"]:
        features_html += f"""      <div class="feature-card">
        <div class="feature-title">{item['title']}</div>
        <div class="feature-why">{item['why']}</div>
      </div>
"""

    patterns_html = ""
    for item in insights["patterns"]:
        patterns_html += f"""      <div class="pattern-card">
        <div class="pattern-title">{item['title']}</div>
        <div class="pattern-summary">{item['summary']}</div>
      </div>
"""

    horizon_html = ""
    for item in insights["horizon"]:
        horizon_html += f"""      <div class="horizon-card">
        <div class="horizon-title">{item['title']}</div>
        <div class="horizon-possible">{item['desc']}</div>
      </div>
"""

    glance_html = ""
    anchors = {
        "What's working": "#section-wins",
        "What's hindering you": "#section-friction",
        "Quick wins to try": "#section-features",
        "Ambitious workflows": "#section-horizon",
    }
    anchor_labels = {
        "What's working": "Impressive Things You Did",
        "What's hindering you": "Where Things Go Wrong",
        "Quick wins to try": "Features to Try",
        "Ambitious workflows": "On the Horizon",
    }
    for item in insights["glance"]:
        href = anchors.get(item["title"], "#section-work")
        link_label = anchor_labels.get(item["title"], "See More")
        glance_html += f"""        <div class="glance-section"><strong>{item['title']}:</strong> {item['detail']} <a href="{href}" class="see-more">{link_label} →</a></div>
"""

    raw_data = {
        "week": period_str,
        "cc_sessions": total_sessions,
        "cc_messages": total_messages,
        "cc_days": active_days,
        "cc_tokens": total_tokens,
        "cc_files": estimated_files,
        "cc_daily": ws.get("daily_tokens") or [],
        "source_status": source_status,
    }

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Claude Code Insights</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f8fafc; color: #334155; line-height: 1.65; padding: 48px 24px; }}
    .container {{ max-width: 800px; margin: 0 auto; }}
    h1 {{ font-size: 32px; font-weight: 700; color: #0f172a; margin-bottom: 8px; }}
    h2 {{ font-size: 20px; font-weight: 600; color: #0f172a; margin-top: 48px; margin-bottom: 16px; }}
    .subtitle {{ color: #64748b; font-size: 15px; margin-bottom: 32px; }}
    .nav-toc {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 24px 0 32px 0; padding: 16px; background: white; border-radius: 8px; border: 1px solid #e2e8f0; }}
    .nav-toc a {{ font-size: 12px; color: #64748b; text-decoration: none; padding: 6px 12px; border-radius: 6px; background: #f1f5f9; }}
    .stats-row {{ display: flex; gap: 24px; margin-bottom: 40px; padding: 20px 0; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; flex-wrap: wrap; }}
    .stat {{ text-align: center; }}
    .stat-value {{ font-size: 24px; font-weight: 700; color: #0f172a; }}
    .stat-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
    .at-a-glance {{ background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border: 1px solid #f59e0b; border-radius: 12px; padding: 20px 24px; margin-bottom: 32px; }}
    .glance-title {{ font-size: 16px; font-weight: 700; color: #92400e; margin-bottom: 16px; }}
    .glance-sections {{ display: flex; flex-direction: column; gap: 12px; }}
    .glance-section {{ font-size: 14px; color: #78350f; line-height: 1.6; }}
    .glance-section strong {{ color: #92400e; }}
    .see-more {{ color: #b45309; text-decoration: none; font-size: 13px; white-space: nowrap; }}
    .project-areas {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 32px; }}
    .project-area {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }}
    .area-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; gap: 12px; }}
    .area-name {{ font-weight: 600; font-size: 15px; color: #0f172a; }}
    .area-count {{ font-size: 12px; color: #64748b; background: #f1f5f9; padding: 2px 8px; border-radius: 4px; }}
    .area-desc {{ font-size: 14px; color: #475569; line-height: 1.5; }}
    .narrative {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 24px; }}
    .narrative p {{ margin-bottom: 12px; font-size: 14px; color: #475569; line-height: 1.7; }}
    .key-insight {{ background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 12px 16px; margin-top: 12px; font-size: 14px; color: #166534; }}
    .section-intro {{ font-size: 14px; color: #64748b; margin-bottom: 16px; }}
    .big-wins {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 24px; }}
    .big-win {{ background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 16px; }}
    .big-win-title {{ font-weight: 600; font-size: 15px; color: #166534; margin-bottom: 8px; }}
    .big-win-desc {{ font-size: 14px; color: #15803d; line-height: 1.5; }}
    .friction-categories {{ display: flex; flex-direction: column; gap: 16px; margin-bottom: 24px; }}
    .friction-category {{ background: #fef2f2; border: 1px solid #fca5a5; border-radius: 8px; padding: 16px; }}
    .friction-title {{ font-weight: 600; font-size: 15px; color: #991b1b; margin-bottom: 6px; }}
    .friction-desc {{ font-size: 13px; color: #7f1d1d; margin-bottom: 10px; }}
    .friction-examples {{ margin: 0 0 0 20px; font-size: 13px; color: #334155; }}
    .features-section, .patterns-section {{ display: flex; flex-direction: column; gap: 12px; margin: 16px 0; }}
    .feature-card {{ background: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 16px; }}
    .pattern-card {{ background: #f0f9ff; border: 1px solid #7dd3fc; border-radius: 8px; padding: 16px; }}
    .feature-title, .pattern-title {{ font-weight: 600; font-size: 15px; color: #0f172a; margin-bottom: 6px; }}
    .feature-why, .pattern-summary {{ font-size: 13px; color: #334155; line-height: 1.6; }}
    .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin: 24px 0; }}
    .chart-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }}
    .chart-title {{ font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase; margin-bottom: 12px; }}
    .bar-row {{ display: flex; align-items: center; margin-bottom: 6px; }}
    .bar-label {{ width: 120px; font-size: 11px; color: #475569; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ flex: 1; height: 6px; background: #f1f5f9; border-radius: 3px; margin: 0 8px; }}
    .bar-fill {{ height: 100%; border-radius: 3px; }}
    .bar-value {{ width: 34px; font-size: 11px; font-weight: 500; color: #64748b; text-align: right; }}
    .empty {{ color: #94a3b8; font-size: 13px; }}
    .horizon-section {{ display: flex; flex-direction: column; gap: 16px; }}
    .horizon-card {{ background: linear-gradient(135deg, #faf5ff 0%, #f5f3ff 100%); border: 1px solid #c4b5fd; border-radius: 8px; padding: 16px; }}
    .horizon-title {{ font-weight: 600; font-size: 15px; color: #5b21b6; margin-bottom: 8px; }}
    .horizon-possible {{ font-size: 14px; color: #334155; line-height: 1.5; }}
    .fun-ending {{ background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border: 1px solid #fbbf24; border-radius: 12px; padding: 24px; margin-top: 40px; text-align: center; }}
    .fun-headline {{ font-size: 18px; font-weight: 600; color: #78350f; margin-bottom: 8px; }}
    .fun-detail {{ font-size: 14px; color: #92400e; }}
    .data-warning {{ background: #fff7ed; color: #9a3412; border: 1px solid #fdba74; border-radius: 12px; padding: 16px 18px; margin-bottom: 24px; font-size: 14px; }}
    .raw-data {{ display: none; }}
    @media (max-width: 640px) {{ .charts-row {{ grid-template-columns: 1fr; }} .stats-row {{ justify-content: center; }} }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Claude Code Insights</h1>
    <p class="subtitle">{total_messages} messages across {total_sessions} sessions | {period_start} to {period_end}</p>
{data_warning_html}
    <div class="at-a-glance">
      <div class="glance-title">At a Glance</div>
      <div class="glance-sections">
{glance_html}      </div>
    </div>

    <nav class="nav-toc">
      <a href="#section-work">What You Work On</a>
      <a href="#section-usage">How You Use CC</a>
      <a href="#section-wins">Impressive Things</a>
      <a href="#section-friction">Where Things Go Wrong</a>
      <a href="#section-features">Features to Try</a>
      <a href="#section-patterns">New Usage Patterns</a>
      <a href="#section-horizon">On the Horizon</a>
    </nav>

    <div class="stats-row">
      <div class="stat"><div class="stat-value">{total_messages}</div><div class="stat-label">Messages</div></div>
      <div class="stat"><div class="stat-value">+{ws["estimated_lines"]:,}/-{ws.get("estimated_removed", ws["estimated_lines"] // 10):,}</div><div class="stat-label">Lines</div></div>
      <div class="stat"><div class="stat-value">{estimated_files}</div><div class="stat-label">Files</div></div>
      <div class="stat"><div class="stat-value">{active_days}</div><div class="stat-label">Days</div></div>
      <div class="stat"><div class="stat-value">{msgs_per_day}</div><div class="stat-label">Msgs/Day</div></div>
    </div>

    <h2 id="section-work">What You Work On</h2>
    <div class="project-areas">
{work_on_html}    </div>

    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">What You Wanted</div>
{goals_html}
      </div>
      <div class="chart-card">
        <div class="chart-title">Top Tools Used</div>
{tools_html}
      </div>
    </div>

    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">Languages</div>
{languages_html}
      </div>
      <div class="chart-card">
        <div class="chart-title">Session Types</div>
{session_types_html}
      </div>
    </div>

    <h2 id="section-usage">How You Use Claude Code</h2>
    <div class="narrative">
      <p>{insights["narrative_parts"][0]}</p>
      <p>{insights["narrative_parts"][1]}</p>
      <p>{insights["narrative_parts"][2]}</p>
      <div class="key-insight"><strong>Key pattern:</strong> {insights["key_insight"]}</div>
    </div>

    <div class="chart-card" style="margin: 24px 0;">
      <div class="chart-title">User Response Time Distribution</div>
{response_html}
      <div style="font-size: 12px; color: #64748b; margin-top: 8px;">Median: {insights["response_median"]}s • Average: {insights["response_avg"]}s</div>
    </div>

    <div class="chart-card" style="margin: 24px 0;">
      <div class="chart-title">Multi-Clauding (Parallel Sessions)</div>
      <div style="display: flex; gap: 24px; margin: 12px 0;">
        <div style="text-align: center;"><div style="font-size: 24px; font-weight: 700; color: #7c3aed;">{insights["overlap_events"]}</div><div style="font-size: 11px; color: #64748b; text-transform: uppercase;">Overlap Events</div></div>
        <div style="text-align: center;"><div style="font-size: 24px; font-weight: 700; color: #7c3aed;">{insights["overlap_sessions"]}</div><div style="font-size: 11px; color: #64748b; text-transform: uppercase;">Sessions Involved</div></div>
        <div style="text-align: center;"><div style="font-size: 24px; font-weight: 700; color: #7c3aed;">{round((insights["overlap_sessions"] / total_sessions) * 100) if total_sessions else 0}%</div><div style="font-size: 11px; color: #64748b; text-transform: uppercase;">Of Sessions</div></div>
      </div>
    </div>

    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">User Messages by Time of Day</div>
{time_html}
      </div>
      <div class="chart-card">
        <div class="chart-title">Tool Errors Encountered</div>
{tool_errors_html}
      </div>
    </div>

    <h2 id="section-wins">Impressive Things You Did</h2>
    <p class="section-intro">This section highlights the moments where Claude moved beyond chat and materially helped the work forward.</p>
    <div class="big-wins">
{wins_html}    </div>

    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">What Helped Most</div>
{success_html}
      </div>
      <div class="chart-card">
        <div class="chart-title">Outcomes</div>
{outcome_html}
      </div>
    </div>

    <h2 id="section-friction">Where Things Go Wrong</h2>
    <p class="section-intro">Your sessions still show repeated friction patterns. The goal here is not blame, but identifying where better constraints would save cycles.</p>
    <div class="friction-categories">
{friction_html}    </div>

    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">Primary Friction Types</div>
{friction_bar_html}
      </div>
      <div class="chart-card">
        <div class="chart-title">Inferred Satisfaction</div>
{satisfaction_html}
      </div>
    </div>

    <h2 id="section-features">Existing CC Features to Try</h2>
    <div class="features-section">
{features_html}    </div>

    <h2 id="section-patterns">New Ways to Use Claude Code</h2>
    <div class="patterns-section">
{patterns_html}    </div>

    <h2 id="section-horizon">On the Horizon</h2>
    <p class="section-intro">These are the next-step workflows your current usage pattern is already pointing toward.</p>
    <div class="horizon-section">
{horizon_html}    </div>

    {f'<div class="fun-ending"><div class="fun-headline">{insights["fun_ending"]["headline"]}</div><div class="fun-detail">{insights["fun_ending"]["detail"]}</div></div>' if insights.get("fun_ending") else ''}

    <div class="raw-data" id="claude-raw-data">{json.dumps(raw_data, ensure_ascii=False)}</div>
  </div>
</body>
</html>
"""
    return html


def main():
    import argparse

    parser = argparse.ArgumentParser(description="从本地 Claude usage-data 生成 richer 周报")
    parser.add_argument("period", nargs="?", default=None, help="周期标识: YYYY-WNN (周) / YYYY-MM (月) / YYYY-QN (季) / YYYY (年)")
    parser.add_argument("--output", "-o", metavar="PATH", help="输出 HTML 路径")
    args = parser.parse_args()

    period_str, period_start, period_end = parse_period_arg(args.period)
    exclude_paths = load_exclude_paths()
    stats = load_stats() or {"dailyActivity": [], "dailyModelTokens": []}
    session_meta_records = load_session_meta(period_start, period_end, exclude_paths)
    if not session_meta_records:
        session_meta_records = load_from_transcripts(period_start, period_end, exclude_paths=exclude_paths)
    else:
        # 补充 session-meta 中没有对应文件的 JSONL session（如仍活跃未写 meta 的 session）
        meta_ids = {r.get("session_id") for r in session_meta_records if r.get("session_id")}
        supplement = [r for r in load_from_transcripts(period_start, period_end, exclude_paths=exclude_paths) if r.get("session_id") not in meta_ids]
        if supplement:
            session_meta_records = session_meta_records + supplement
    ccusage_blocks = load_ccusage_blocks(period_start, period_end)
    ws = compute_week_stats(stats, period_start, period_end, session_meta_records, ccusage_blocks)
    facets_by_session = load_facets([item.get("session_id") for item in session_meta_records if item.get("session_id")])
    insights = build_rich_insights(session_meta_records, facets_by_session, ws)
    source_status = inspect_local_sources(period_start, period_end, stats)
    html = generate_html(period_str, period_start, period_end, ws, insights, source_status)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = Path.home() / ".claude" / "usage-data"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "report.html"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Generated: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
