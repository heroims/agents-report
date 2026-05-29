#!/usr/bin/env python3
"""采集 Trae CN 使用数据，生成 HTML 报告（与 Trae/Codex/OpenCode 同风格）。

数据源:
  ~/Library/Application Support/Trae CN/User/workspaceStorage/*/state.vscdb
    - ItemTable: memento/icube-ai-agent-storage (会话列表)
    - ItemTable: icube-ai-agent-storage-input-history (用户输入历史)
    - ItemTable: icube_session_agent_map (会话→Agent 类型映射)

跨平台路径说明:
  - macOS:  ~/Library/Application Support/Trae CN/User/workspaceStorage/
  - Linux:  ~/.config/Trae CN/User/workspaceStorage/
  - Windows: %APPDATA%/Trae CN/User/workspaceStorage/
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
from i18n import T as _I18nT
_LANG = _I18nT.detect()
_I18N = _I18nT(_LANG)
_t = _I18N


def _trae_cn_workspace_storage():
    """返回 Trae CN workspaceStorage 根目录（跨平台）。"""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Trae CN"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", "")) / "Trae CN"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Trae CN"
    return base / "User" / "workspaceStorage"


def _trae_cn_workspace_name(ws_dir):
    """从 workspaceStorage 目录下的 workspace.json 读取工作区名称。"""
    ws_json = ws_dir / "workspace.json"
    if not ws_json.exists():
        return "(unknown)"
    try:
        with open(ws_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        folder = (data.get("folder") or "").replace("file://", "")
        if folder:
            return Path(folder).name or folder
    except Exception:
        pass
    return "(unknown)"


def safe_text(value):
    return html.escape(str(value or ""))


def safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def format_tokens(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _workstream_label(index):
    return f"Workstream {index + 1}"


def collect_sessions(period_start, period_end):
    """从所有 workspaceStorage 收集 Trae CN 会话数据。"""
    ws_root = _trae_cn_workspace_storage()
    if not ws_root.exists():
        return []

    sessions = []
    for ws_dir in ws_root.iterdir():
        if not ws_dir.is_dir():
            continue

        db_path = ws_dir / "state.vscdb"
        if not db_path.exists():
            continue

        ws_name = _trae_cn_workspace_name(ws_dir)

        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()

            # 读取 session → agent 映射
            agent_map = {}
            cur.execute("SELECT value FROM ItemTable WHERE key = 'icube_session_agent_map'")
            row = cur.fetchone()
            if row and row[0]:
                try:
                    agent_map = json.loads(row[0])
                except json.JSONDecodeError:
                    pass

            # 读取 memento 中的会话列表
            cur.execute("SELECT value FROM ItemTable WHERE key = 'memento/icube-ai-agent-storage'")
            row = cur.fetchone()
            if not row or not row[0]:
                conn.close()
                continue

            try:
                memento = json.loads(row[0])
            except json.JSONDecodeError:
                conn.close()
                continue

            # 读取输入历史
            input_history = []
            cur.execute("SELECT value FROM ItemTable WHERE key = 'icube-ai-agent-storage-input-history'")
            row = cur.fetchone()
            if row and row[0]:
                try:
                    input_history = json.loads(row[0])
                except json.JSONDecodeError:
                    pass

            conn.close()

            session_list = memento.get("list", [])
            for s in session_list:
                sid = s.get("sessionId", "")
                if not sid:
                    continue
                agent_type = agent_map.get(sid, "chat")

                msg_count = len(s.get("messages", []))
                is_current = bool(s.get("isCurrent"))

                sessions.append({
                    "session_id": sid,
                    "agent_type": agent_type,
                    "workspace": ws_name,
                    "messages": msg_count,
                    "is_current": is_current,
                })

        except sqlite3.Error:
            continue

    return sessions


def build_insights(sessions):
    """从会话数据生成结构化洞察（与 Codex/OpenCode 同层级）。"""
    total_sessions = len(sessions)
    total_messages = sum(s["messages"] for s in sessions)
    agent_count = sum(1 for s in sessions if s.get("agent_type") == "builder")
    chat_count = total_sessions - agent_count

    # 按工作区聚合
    area_counter = Counter()
    for s in sessions:
        area_counter[s["workspace"]] += 1
    areas = [{"cwd": k, "sessions": v} for k, v in area_counter.most_common()]

    # Agent 类型分布
    builder_ratio = round(agent_count / max(total_sessions, 1) * 100)
    dominant_mode = "Builder 为主" if agent_count >= chat_count else "Chat 为主"

    # 按项目聚合
    top_area = areas[0] if areas else None
    concentration = round((top_area["sessions"] / total_sessions * 100) if top_area else 0)

    avg_messages = round(total_messages / max(total_sessions, 1), 1)
    project_count = len(areas)

    # at_a_glance
    at_a_glance = {
        "working": (
            f"Trae CN 本周共 {total_sessions} 个会话，分布在 {project_count} 个项目上，"
            f"以 {dominant_mode} 使用模式为主（{agent_count} 次 Builder / {chat_count} 次 Chat）。"
            if total_sessions
            else _t("No Trae CN usage data this period.")
        ),
        "hindering": (
            "Trae CN 当前不记录会话时间戳，无法精确按周过滤，所有已存在会话都会被纳入统计。"
            if total_sessions
            else ""
        ),
        "quick_win": (
            f"在{' Builder ' if agent_count >= chat_count else ' Chat '}模式下明确描述完整任务目标和验收标准，减少反复确认。"
            if total_sessions
            else ""
        ),
        "ambitious": (
            "如果后续补上时间戳和 Token 采集，Trae CN 这块也能像其他工具一样从活跃度报告升级成执行质量分析。"
            if total_sessions
            else ""
        ),
    }

    # narrative
    narrative_parts = []
    if total_sessions:
        narrative_parts.append(
            f"你这周使用 Trae CN 共 {total_sessions} 个会话、{total_messages} 条消息，"
            f"覆盖 {project_count} 个不同项目。"
            f"其中 Builder 模式占 {builder_ratio}%（{agent_count} 次），"
            f"说明{'你已经把 Trae CN 当作自主代理使用，不只是一问一答。' if agent_count >= chat_count else '你更倾向于手动控制每一步。'}"
        )
        if top_area:
            narrative_parts.append(
                f"项目主要集中在 {top_area['cwd']}（{top_area['sessions']} 次会话，占 {concentration}%），"
                f"其余分散在 {project_count - 1} 个项目中。"
            )
        narrative_parts.append(
            f"平均每会话 {avg_messages} 条消息，"
            f"{'交互密度较高，说明你会在 Trae CN 中做多轮迭代。' if avg_messages >= 3 else '会话偏轻量，更像快速查询而非深度协作。'}"
        )
    else:
        narrative_parts.append(_t("No Trae CN usage data this period."))

    key_insight = (
        f"Trae CN 目前更接近{'一个自主代理执行入口' if agent_count >= chat_count else '一个辅助问答工具'}，"
        f"Builder 占比 {builder_ratio}%。下一步可以从固化常用任务流程入手，提升会话效率。"
        if total_sessions
        else "暂无 Trae CN 使用痕迹，可以尝试用 Builder 模式完成一个小任务来体验自主代理能力。"
    )

    # usage_cards
    usage_cards = []
    if total_sessions:
        usage_cards = [
            {
                "title": _t("Session Density"),
                "value": f"{avg_messages} msgs/session",
                "desc": f"平均每会话约 {avg_messages} 条消息，"
                       f"{'多轮交互说明你在深度使用 Trae CN。' if avg_messages >= 3 else '会话偏单向查询，可以考虑增加迭代深度。'}",
            },
            {
                "title": _t("Builder Adoption Rate"),
                "value": f"{builder_ratio}%",
                "desc": f"{total_sessions} 次会话中 {agent_count} 次使用 Builder 模式。"
                       f"{'Builder 已经成为默认选择。' if builder_ratio >= 50 else 'Chat 仍占主导，尝试更多 Builder 会话可获得更强自主执行能力。'}",
            },
            {
                "title": _t("Project Coverage"),
                "value": f"{project_count} 个项目",
                "desc": f"覆盖 {project_count} 个不同工作区，"
                       f"{'使用场景较广。' if project_count >= 3 else '集中在少数项目中，可以尝试在新项目中也使用 Trae CN。'}",
            },
            {
                "title": _t("Workflow Concentration"),
                "value": f"{concentration}%",
                "desc": f"最活跃项目（{top_area['cwd'] if top_area else '无'}）占 {concentration}% 的会话，"
                       f"{'说明你已经找到适合 Trae CN 的场景。' if concentration >= 50 else '使用比较分散。'}",
            },
        ]

    # wins
    wins = []
    if total_sessions:
        if agent_count > 0:
            wins.append({
                "title": _t("Builder mode has entered regular use"),
                "detail": f"{agent_count} 次 Builder 会话说明你已经开始将 Trae CN 作为自主代理使用，不仅仅是问答工具。",
            })
        if project_count >= 3:
            wins.append({
                "title": _t("Covering multiple project scenarios"),
                "detail": f"从 {areas[0]['cwd']} 到 {areas[-1]['cwd']}，Trae CN 已经在 {project_count} 个项目中发挥作用。",
            })
        if total_sessions >= 5:
            wins.append({
                "title": _t("Usage frequency established"),
                "detail": f"{total_sessions} 个会话说明 Trae CN 已经成为你工具箱中的固定成员，不是偶尔尝试。",
            })

    # friction
    friction = []
    if total_sessions:
        friction.append({
            "title": _t("Limited data observability"),
            "detail": "Trae CN 不记录时间戳、Token 用量和文件改动轨迹，无法像 Claude/Codex 那样做执行质量分析。",
        })
        if avg_messages < 2:
            friction.append({
                "title": _t("Session depth is shallow"),
                "detail": f"平均仅 {avg_messages} 条消息/会话，可能说明大部分会话停留在单轮问答，没有形成深度协作。",
            })
        if chat_count > agent_count and total_sessions >= 3:
            friction.append({
                "title": _t("Chat mode still dominates"),
                "detail": f"{chat_count} 次 Chat vs {agent_count} 次 Builder，Builder 的自主执行能力还没有完全发挥。",
            })

    # features
    features = []
    if total_sessions:
        features.append({
            "title": _t("Solidify stable tasks into Builder"),
            "detail": "对于已经重复验证过的任务流程，直接写成 Builder 指令模板，减少每次重新描述的成本。",
        })
        features.append({
            "title": _t("Clarify task acceptance criteria"),
            "detail": "在 Builder 会话的第一个 Prompt 里就把预期输出形式说死（文件、报告、修复清单），减少中途返工。",
        })
        features.append({
            "title": _t("Expand usage scenarios"),
            "detail": "把 Trae CN 从当前高频项目延伸到其他项目，看看在不同技术栈和项目结构下 Builder 的表现差异。",
        })

    # patterns
    patterns = []
    if total_sessions:
        patterns.append({
            "title": f"{_t("Trae CN is stable in its")} {'autonomous execution' if agent_count >= chat_count else 'assistant Q&A'} {_t("role")}",
            "summary": f"Builder 占比 {builder_ratio}%，会话分布在 {project_count} 个项目上，使用模式趋于固定。",
        })
        patterns.append({
            "title": "项目集中度高说明你找到了适合场景",
            "summary": f"最活跃项目占 {concentration}%，说明你在特定场景下已经信任 Trae CN 的能力。",
        })

    # horizon
    horizon = []
    if total_sessions:
        horizon.append({
            "title": _t("From activation frequency to execution quality"),
            "detail": "一旦补上时间戳、Token 和改动追踪，Trae CN 的周报就能从「用了多少」升级成「做成了什么」。",
        })
        horizon.append({
            "title": _t("Form multi-tool division of labor"),
            "detail": "当 Claude、Codex、OpenCode、Cursor、Trae、Trae CN 的数据都充足后，可以开始分析谁负责探索、谁负责执行、谁负责批处理。",
        })

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
        "top_areas": areas[:5],
    }


def generate_report(week, period_start, period_end, sessions):
    """生成 Trae CN HTML 报告（与 Codex/OpenCode 同风格）。"""
    total_sessions = len(sessions)
    total_messages = sum(s["messages"] for s in sessions)
    agent_count = sum(1 for s in sessions if s.get("agent_type") == "builder")
    chat_count = total_sessions - agent_count

    # 按工作区聚合
    area_counter = Counter()
    for s in sessions:
        area_counter[s["workspace"]] += 1
    areas = [{"cwd": k, "sessions": v} for k, v in area_counter.most_common()]

    insights = build_insights(sessions)

    week_label = f"{period_start} ~ {period_end}"

    # 构建原始数据
    raw_data = {
        "week": week,
        "period_start": str(period_start),
        "period_end": str(period_end),
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_lines_added": 0,
        "total_lines_removed": 0,
        "total_files": 0,
        "active_days": 1,
        "agent_count": agent_count,
        "chat_count": chat_count,
        "daily": [],
        "areas": areas,
        "models": [],
        "insights": insights,
    }

    raw_json = json.dumps(raw_data, ensure_ascii=False)

    # Build usage cards HTML
    usage_cards_html = ""
    for item in insights["usage_cards"]:
        usage_cards_html += f"""      <div class="card">
        <div class="card-title">{safe_text(item['title'])}: {safe_text(item['value'])}</div>
        <div class="card-detail">{safe_text(item['desc'])}</div>
      </div>
"""

    def _render_cards(items):
        if not items:
            return '      <div class="card"><div class="card-title">' + _t("Sample insufficient") + '</div><div class="card-detail">本周数据还不足以稳定提炼这一部分内容。</div></div>\n'
        parts = []
        for item in items:
            detail = item.get("detail") or item.get("summary") or item.get("desc") or ""
            parts.append(f"""      <div class="card">
        <div class="card-title">{safe_text(item.get('title', ''))}</div>
        <div class="card-detail">{safe_text(detail)}</div>
      </div>
""")
        return "".join(parts)

    # Build work on HTML
    work_on_html = ""
    top_areas = insights.get("top_areas", []) or areas[:5]
    for item in top_areas:
        sessions_count = item.get("sessions", 0)
        intensity = "高频活跃" if sessions_count >= 3 else "点状使用"
        work_on_html += f"""      <div class="project-area">
        <div class="area-header">
          <span class="area-name">{safe_text(item['cwd'])}</span>
          <span class="area-count">{sessions_count} 会话</span>
        </div>
        <div class="area-desc">约 {sessions_count} 次会话，占本周 {round(sessions_count / max(total_sessions, 1) * 100)}% 的 Trae CN 使用量，属于{safe_text(intensity)}项目。</div>
      </div>
"""
    if not work_on_html:
        work_on_html = '      <p class="empty">本周暂无可用项目数据。</p>\n'

    # Build area pills
    area_pills = "".join(
        f'<span class="pill">{safe_text(a["cwd"])} <b>{a["sessions"]}</b></span>'
        for a in areas[:8]
    ) or '<span class="empty">暂无</span>'

    builder_ratio = round(agent_count / max(total_sessions, 1) * 100)

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Trae CN Insights · {safe_text(week)}</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%); color: #334155; line-height: 1.65; padding: 48px 24px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ font-size: 32px; font-weight: 700; color: #0f172a; margin-bottom: 8px; }}
    h2 {{ font-size: 20px; font-weight: 600; color: #0f172a; margin-top: 42px; margin-bottom: 14px; }}
    .subtitle {{ color: #64748b; font-size: 15px; margin-bottom: 28px; }}
    .hero {{ background: linear-gradient(135deg, #fefce8 0%, #fef9c3 45%, #ecfccb 100%); border: 1px solid #fde047; border-radius: 16px; padding: 22px 24px; margin-bottom: 24px; }}
    .glance-title {{ font-size: 16px; font-weight: 700; color: #854d0e; margin-bottom: 14px; }}
    .glance-sections {{ display: flex; flex-direction: column; gap: 10px; }}
    .glance-section {{ font-size: 14px; color: #713f12; }}
    .glance-section strong {{ color: #a16207; }}
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
    .pill-row {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }}
    .pill {{ background: #e2e8f0; color: #334155; padding: 4px 12px; border-radius: 16px; font-size: 13px; }}
    .pill b {{ color: #0f172a; }}
    .empty {{ color: #94a3b8; font-size: 13px; }}
    .raw-data {{ display: none; }}
    @media (max-width: 760px) {{ .cards {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Trae CN Insights</h1>
    <p class="subtitle">{safe_text(week_label)} | {safe_text(week)}</p>

    <div class="hero">
      <div class="glance-title">At a Glance</div>
      <div class="glance-sections">
        <div class="glance-section"><strong>What's working:</strong> {safe_text(insights['at_a_glance']['working'])}</div>
        <div class="glance-section"><strong>What's hindering you:</strong> {safe_text(insights['at_a_glance']['hindering'])}</div>
        <div class="glance-section"><strong>Quick wins to try:</strong> {safe_text(insights['at_a_glance']['quick_win'])}</div>
        <div class="glance-section"><strong>Ambitious workflows:</strong> {safe_text(insights['at_a_glance']['ambitious'])}</div>
      </div>
    </div>

    <div class="stats-row">
      <div class="stat"><div class="stat-value">{total_sessions}</div><div class="stat-label">' + _t("Sessions") + '</div></div>
      <div class="stat"><div class="stat-value">{total_messages}</div><div class="stat-label">' + _t("Messages") + '</div></div>
      <div class="stat"><div class="stat-value">{builder_ratio}%</div><div class="stat-label">' + _t("Builder Ratio") + '</div></div>
      <div class="stat"><div class="stat-value">{len(areas)}</div><div class="stat-label">' + _t("Active Projects") + '</div></div>
      <div class="stat"><div class="stat-value">{agent_count}/{chat_count}</div><div class="stat-label">Builder/Chat</div></div>
    </div>

    <h2>What You Work On</h2>
    <p class="section-intro">这里不展示具体项目名，只保留工作流层面的行为画像。</p>
    <div class="project-areas">
{work_on_html}    </div>

    <h2>How You Use Trae CN</h2>
    <div class="narrative">
      {''.join(f'<p>{safe_text(p)}</p>' for p in insights['narrative_parts'])}
      <div class="key-insight"><strong>Key pattern:</strong> {safe_text(insights['key_insight'])}</div>
    </div>

    <div class="cards" style="margin-top:18px;">
{usage_cards_html}    </div>

    <h2>Active Projects</h2>
    <div class="pill-row">{area_pills}</div>

    <h2>Impressive Things You Did</h2>
    <p class="section-intro">先看已经稳定形成的使用优势。</p>
    <div class="cards">
{_render_cards(insights['wins'])}    </div>

    <h2>Where Things Go Wrong</h2>
    <p class="section-intro">当前主要摩擦更多来自数据可观测性和使用深度。</p>
    <div class="cards">
{_render_cards(insights['friction'])}    </div>

    <h2>Features to Try</h2>
    <div class="cards">
{_render_cards(insights['features'])}    </div>

    <h2>New Ways to Use Trae CN</h2>
    <div class="cards">
{_render_cards(insights['patterns'])}    </div>

    <h2>On the Horizon</h2>
    <div class="cards">
{_render_cards(insights['horizon'])}    </div>

    <div class="raw-data" id="trae-cn-raw-data">{raw_json}</div>
  </div>
</body>
</html>"""

    return html


def main():
    parser = argparse.ArgumentParser(description="采集 Trae CN 使用数据，生成 HTML 报告")
    parser.add_argument("week", help="周标识，如 2026-W22")
    parser.add_argument("--output", "-o", required=True, help="输出 HTML 路径")
    args = parser.parse_args()

    period_start, period_end = period_start_end(args.week)
    week = args.week

    sessions = collect_sessions(period_start, period_end)
    html = generate_report(week, period_start, period_end, sessions)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Trae CN report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
