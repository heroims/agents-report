#!/usr/bin/env python3
"""從 session-meta 生成 Claude Code Insights HTML 報告，格式與 /insights 輸出一致。"""
import json
import pathlib
import sys
from collections import Counter


def bar_row(label, value, max_val, color="#0891b2"):
    pct = int(value / max_val * 100) if max_val > 0 else 0
    return (
        f'<div class="bar-row">'
        f'<div class="bar-label">{label}</div>'
        f'<div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>'
        f'<div class="bar-value">{value}</div>'
        f"</div>\n"
    )


def build_bars(counter, color, top_n=6):
    if not counter:
        return '<p class="empty">No data</p>'
    items = counter.most_common(top_n)
    max_val = items[0][1]
    return "".join(bar_row(label, cnt, max_val, color) for label, cnt in items)


def project_area_html(name, sessions, desc=""):
    desc_html = f'<div class="area-desc">{desc}</div>' if desc else ""
    return (
        f'<div class="project-area">'
        f'<div class="area-header">'
        f'<span class="area-name">{name}</span>'
        f'<span class="area-count">{sessions} sessions</span>'
        f"</div>{desc_html}</div>\n"
    )


def main(week, week_start, week_end):
    session_dir = pathlib.Path.home() / ".claude" / "usage-data" / "session-meta"
    facets_dir = pathlib.Path.home() / ".claude" / "usage-data" / "facets"

    # Load W sessions
    sessions = []
    session_ids = set()
    for f in session_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            t = d.get("start_time", "")[:10]
            if week_start <= t <= week_end:
                sessions.append(d)
                session_ids.add(d.get("session_id", ""))
        except Exception:
            pass

    if not sessions:
        print(f"No sessions found for {week_start} to {week_end}", file=sys.stderr)
        sys.exit(1)

    # Load facets for matching sessions
    facets = []
    for f in facets_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("session_id", "") in session_ids:
                facets.append(d)
        except Exception:
            pass

    # Aggregate stats
    total_sessions = len(sessions)
    total_user_msgs = sum(s.get("user_message_count", 0) for s in sessions)
    total_asst_msgs = sum(s.get("assistant_message_count", 0) for s in sessions)
    lines_added = sum(s.get("lines_added", 0) for s in sessions)
    lines_removed = sum(s.get("lines_removed", 0) for s in sessions)
    files_changed = sum(s.get("files_changed", 0) for s in sessions)
    active_days_set = set(s.get("start_time", "")[:10] for s in sessions)
    active_days = len(active_days_set)
    msgs_per_day = round(total_user_msgs / active_days, 1) if active_days > 0 else 0
    durations = [s.get("duration_minutes", 0) for s in sessions if s.get("duration_minutes", 0) > 0]
    avg_duration = round(sum(durations) / len(durations)) if durations else 0
    max_duration = max(durations) if durations else 0

    # Tools
    tools = Counter()
    for s in sessions:
        for k, v in s.get("tool_counts", {}).items():
            tools[k] += v

    # Projects
    projects = Counter()
    project_sessions = {}
    for s in sessions:
        raw_path = s.get("project_path", "")
        name = raw_path.replace("\\", "/").split("/")[-1] or raw_path
        projects[name] += 1
        if name not in project_sessions:
            project_sessions[name] = []
        project_sessions[name].append(s)

    # Facets aggregation
    friction_counts = Counter()
    goal_cats = Counter()
    outcomes = Counter()
    helpfulness = Counter()
    for fac in facets:
        for cat, cnt in fac.get("friction_counts", {}).items():
            friction_counts[cat] += cnt
        for cat, cnt in fac.get("goal_categories", {}).items():
            goal_cats[cat] += cnt
        outcome = fac.get("outcome", "")
        if outcome:
            outcomes[outcome.replace("_", " ").title()] += 1
        h = fac.get("claude_helpfulness", "")
        if h:
            helpfulness[h.replace("_", " ").title()] += 1

    # Build project area HTML
    proj_desc = {
        "agent-report": "Agent 使用數據採集與報告工具",
    }
    projects_html = ""
    for proj, cnt in projects.most_common(6):
        desc = proj_desc.get(proj, "")
        projects_html += project_area_html(proj, cnt, desc)

    # Charts
    tools_bars = build_bars(tools, "#0891b2")
    goal_bars = build_bars(goal_cats, "#2563eb") if goal_cats else '<p class="empty">Not enough data from facets</p>'
    friction_bars = build_bars(friction_counts, "#dc2626") if friction_counts else '<p class="empty">No friction events recorded</p>'
    outcome_bars = build_bars(outcomes, "#8b5cf6") if outcomes else '<p class="empty">Not enough data from facets</p>'

    # At a Glance narrative
    top_proj = projects.most_common(1)[0][0] if projects else "various projects"
    top_tool = tools.most_common(1)[0][0] if tools else "various tools"
    at_a_glance = f"""
        <div class="glance-section">
          <strong>工作重心：</strong>本週 {active_days} 個工作日完成 {total_sessions} 個 sessions，主要集中在 {top_proj} 上，平均每天 {msgs_per_day} 條訊息。
        </div>
        <div class="glance-section">
          <strong>工具使用：</strong>最常用工具為 {top_tool}（{tools[top_tool]} 次），程式碼變更 +{lines_added:,} / -{lines_removed:,} 行。
        </div>
        <div class="glance-section">
          <strong>工作節奏：</strong>平均 session 時長 {avg_duration} 分鐘，最長單次 {max_duration} 分鐘，整體呈現深度協作模式。
        </div>
"""

    # Raw data for team analysis
    raw_data = json.dumps({
        "week": week,
        "cc_sessions": total_sessions,
        "cc_messages": total_user_msgs,
        "cc_files": files_changed,
        "cc_days": active_days,
        "cc_lines_added": lines_added,
        "cc_lines_removed": lines_removed,
        "cc_tokens": 0,
    })

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
    .nav-toc a {{ font-size: 12px; color: #64748b; text-decoration: none; padding: 6px 12px; border-radius: 6px; background: #f1f5f9; transition: all 0.15s; }}
    .nav-toc a:hover {{ background: #e2e8f0; color: #334155; }}
    .stats-row {{ display: flex; gap: 24px; margin-bottom: 40px; padding: 20px 0; border-top: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; flex-wrap: wrap; }}
    .stat {{ text-align: center; }}
    .stat-value {{ font-size: 24px; font-weight: 700; color: #0f172a; }}
    .stat-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
    .at-a-glance {{ background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border: 1px solid #f59e0b; border-radius: 12px; padding: 20px 24px; margin-bottom: 32px; }}
    .glance-title {{ font-size: 16px; font-weight: 700; color: #92400e; margin-bottom: 16px; }}
    .glance-sections {{ display: flex; flex-direction: column; gap: 12px; }}
    .glance-section {{ font-size: 14px; color: #78350f; line-height: 1.6; }}
    .glance-section strong {{ color: #92400e; }}
    .project-areas {{ display: flex; flex-direction: column; gap: 12px; margin-bottom: 32px; }}
    .project-area {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }}
    .area-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
    .area-name {{ font-weight: 600; font-size: 15px; color: #0f172a; }}
    .area-count {{ font-size: 12px; color: #64748b; background: #f1f5f9; padding: 2px 8px; border-radius: 4px; }}
    .area-desc {{ font-size: 14px; color: #475569; line-height: 1.5; }}
    .charts-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin: 24px 0; }}
    .chart-card {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; }}
    .chart-title {{ font-size: 12px; font-weight: 600; color: #64748b; text-transform: uppercase; margin-bottom: 12px; }}
    .bar-row {{ display: flex; align-items: center; margin-bottom: 6px; }}
    .bar-label {{ width: 120px; font-size: 11px; color: #475569; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .bar-track {{ flex: 1; height: 6px; background: #f1f5f9; border-radius: 3px; margin: 0 8px; }}
    .bar-fill {{ height: 100%; border-radius: 3px; }}
    .bar-value {{ width: 32px; font-size: 11px; font-weight: 500; color: #64748b; text-align: right; }}
    .empty {{ color: #94a3b8; font-size: 13px; }}
    .raw-data {{ display: none; }}
    @media (max-width: 640px) {{ .charts-row {{ grid-template-columns: 1fr; }} .stats-row {{ justify-content: center; }} }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Claude Code Insights</h1>
    <p class="subtitle">{total_user_msgs} messages across {total_sessions} sessions | {week_start} to {week_end}</p>

    <div class="at-a-glance">
      <div class="glance-title">At a Glance</div>
      <div class="glance-sections">{at_a_glance}</div>
    </div>

    <nav class="nav-toc">
      <a href="#section-work">What You Work On</a>
      <a href="#section-usage">How You Use CC</a>
      <a href="#section-friction">Where Things Go Wrong</a>
      <a href="#section-outcomes">Outcomes</a>
    </nav>

    <div class="stats-row">
      <div class="stat"><div class="stat-value">{total_user_msgs}</div><div class="stat-label">Messages</div></div>
      <div class="stat"><div class="stat-value">+{lines_added:,}/-{lines_removed:,}</div><div class="stat-label">Lines</div></div>
      <div class="stat"><div class="stat-value">{total_sessions}</div><div class="stat-label">Sessions</div></div>
      <div class="stat"><div class="stat-value">{active_days}</div><div class="stat-label">Days</div></div>
      <div class="stat"><div class="stat-value">{msgs_per_day}</div><div class="stat-label">Msgs/Day</div></div>
    </div>

    <h2 id="section-work">What You Work On</h2>
    <div class="project-areas">{projects_html}</div>

    <h2 id="section-usage">How You Use Claude Code</h2>
    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-title">Top Tools Used</div>
        {tools_bars}
      </div>
      <div class="chart-card">
        <div class="chart-title">What You Wanted</div>
        {goal_bars}
      </div>
    </div>

    <h2 id="section-friction">Where Things Go Wrong</h2>
    <div class="chart-card">
      <div class="chart-title">Primary Friction Types</div>
      {friction_bars}
    </div>

    <h2 id="section-outcomes">Outcomes</h2>
    <div class="chart-card">
      <div class="chart-title">Session Outcomes</div>
      {outcome_bars}
    </div>

    <div class="raw-data" id="combined-raw-data">{raw_data}</div>
  </div>
</body>
</html>"""

    out = pathlib.Path.home() / ".claude" / "usage-data" / "report.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Generated: {out}", file=sys.stderr)
    print(
        f"Sessions={total_sessions}, Messages={total_user_msgs}, "
        f"Lines=+{lines_added}/-{lines_removed}, Days={active_days}",
        file=sys.stderr,
    )
    return total_sessions


if __name__ == "__main__":
    week = "2026-W18"
    week_start = "2026-04-27"
    week_end = "2026-05-03"
    main(week, week_start, week_end)
