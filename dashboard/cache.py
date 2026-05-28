"""In-memory data cache. Loads from GitLab via GitLabClient, serves filtered JSON."""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from analyze import InsightsParser, _extract_raw_data, extract_member_data, parse_filename, parse_number, detect_period_type


TYPE_LABELS = {'weekly': '周报', 'monthly': '月报', 'quarterly': '季报', 'annual': '年报'}


class DataCache:
    """Thread-safe in-memory cache for parsed report data."""

    def __init__(self):
        self._lock = Lock()
        self._state = _empty_state()

    def load(self, gitlab_client, members_path: str) -> None:
        """Fetch all reports from GitLab, parse, and store in cache."""
        with self._lock:
            self._state['is_loading'] = True

        try:
            members = _load_members(members_path)
            all_data: dict = {}  # {period: {name: member_data}}
            team_reports: set = set()  # periods that have team-report.html

            for path in gitlab_client.list_report_files():
                filename = os.path.basename(path)

                # 检测团队报告
                if filename == 'team-report.html':
                    parts = path.replace('\\', '/').split('/')
                    if len(parts) >= 3:
                        period = parts[-2] if detect_period_type(parts[-2]) else None
                        if period:
                            team_reports.add(period)
                    continue

                # 个人报告
                name, period = parse_filename(filename)
                if not name or not period:
                    continue

                # 从路径检测分组
                parts = path.replace('\\', '/').split('/')
                group = None
                if len(parts) >= 4:
                    if detect_period_type(parts[1]):
                        group = parts[2]
                    else:
                        group = parts[1]

                html = gitlab_client.get_file_content(path)
                if not html:
                    continue

                parser = InsightsParser()
                parser.feed(html)
                parser.finalize()
                raw_data = _extract_raw_data(html)
                if raw_data:
                    parser.data["raw"] = raw_data

                meta = members.get(name, {'display': name.capitalize(), 'group': group or 'unknown'})
                member_data = extract_member_data(path, name, meta['display'], parser.data)

                stats = parser.data.get('stats', {})
                member_data['sessions'] = member_data.get('combined_sessions') or int(parse_number(stats.get('Sessions', '0')))
                member_data['cost'] = round(float(parse_number(stats.get('Cost ($)', '0'))), 2)
                member_data['group'] = meta.get('group') or group or 'unknown'

                if period not in all_data:
                    all_data[period] = {}
                all_data[period][name] = member_data

            # 按类型分组周期（个人报告）
            periods_by_type: dict = defaultdict(list)
            for p in all_data:
                pt = detect_period_type(p) or 'weekly'
                periods_by_type[pt].append(p)

            # 将团队报告的周期也加入 periods_by_type（即使没有个人报告）
            for tr_period in team_reports:
                pt = detect_period_type(tr_period) or 'weekly'
                if tr_period not in periods_by_type[pt]:
                    periods_by_type[pt].append(tr_period)

            for pt in periods_by_type:
                periods_by_type[pt].sort()

            all_groups = sorted({
                m['group']
                for wd in all_data.values()
                for m in wd.values()
                if m.get('group')
            })
            for m in members.values():
                if m['group'] and m['group'] not in all_groups:
                    all_groups.append(m['group'])
            all_groups = sorted(set(all_groups))

            with self._lock:
                self._state = {
                    'all_data': all_data,
                    'periods_by_type': dict(periods_by_type),
                    'team_reports': team_reports,
                    'members': members,
                    'all_groups': all_groups,
                    'last_updated': datetime.now(timezone.utc).isoformat(),
                    'is_loading': False,
                }
        except Exception:
            with self._lock:
                self._state['is_loading'] = False
            raise

    def get_data(self, group: str | None = None, period: str | None = None,
                 period_type: str = 'weekly') -> dict:
        """返回按 group 和 period 过滤、排序后的数据。"""
        with self._lock:
            state = dict(self._state)

        all_data = state.get('all_data', {})
        periods_by_type = state.get('periods_by_type', {})
        type_periods = sorted(periods_by_type.get(period_type, []))
        if not type_periods:
            # 无个人报告时，检查是否有团队报告
            team_periods = [p for p in state.get('team_reports', set()) if detect_period_type(p) == period_type]
            if team_periods:
                target_period = period if period and period in team_periods else team_periods[-1]
                prev_period = None
            else:
                return _empty_response(state, period_type)
        else:
            target_period = period if period and period in type_periods else type_periods[-1]
            prev_idx = type_periods.index(target_period) - 1
            prev_period = type_periods[prev_idx] if prev_idx >= 0 else None

        period_data = all_data.get(target_period, {})
        prev_data = all_data.get(prev_period, {}) if prev_period else {}
        members_meta = state['members']
        all_groups = state['all_groups']

        if group and group in all_groups:
            meta_names = [n for n, m in members_meta.items() if m['group'] == group]
            meta_expected = len(meta_names)
        else:
            meta_names = list(members_meta.keys())
            meta_expected = len(meta_names)

        # 同时纳入 period_data 中已提交但不在 members.json 的成员
        # 按分组过滤额外成员：只在对应分组或无分组筛选时才纳入
        extra_names = [
            n for n in period_data
            if n not in meta_names
            and (not group or period_data[n].get('group') == group)
        ]
        names = meta_names + extra_names
        expected = meta_expected + len(extra_names)

        reported_rows = []
        unreported_rows = []

        for name in names:
            meta = members_meta.get(name, {'display': name.capitalize(), 'group': 'unknown'})
            if name in period_data:
                md = period_data[name]
                prev_msgs = prev_data.get(name, {}).get('messages', 0)
                delta = md['messages'] - prev_msgs if prev_period else None
                reported_rows.append({
                    'name': name,
                    'display': md['display_name'],
                    'group': md['group'],
                    'reported': True,
                    'messages': md['messages'],
                    'lines_added': md['lines_added'],
                    'files': md['files'],
                    'days': md.get('days', 0),
                    'msgs_day': md.get('msgs_day', 0),
                    'sessions': md.get('sessions', 0),
                    'cost': md.get('cost', 0.0),
                    'delta_messages': delta,
                })
            else:
                unreported_rows.append({
                    'name': name,
                    'display': meta.get('display', name.capitalize()),
                    'group': meta.get('group', 'unknown'),
                    'reported': False,
                    'messages': 0, 'lines_added': 0, 'files': 0,
                    'days': 0, 'msgs_day': 0, 'sessions': 0, 'cost': 0.0,
                    'delta_messages': None,
                })

        reported_rows.sort(key=lambda x: -x['messages'])
        unreported_rows.sort(key=lambda x: x['display'].lower())
        sorted_members = reported_rows + unreported_rows

        total_msgs = sum(m['messages'] for m in reported_rows)
        total_lines = sum(m['lines_added'] for m in reported_rows)
        total_files = sum(m['files'] for m in reported_rows)
        total_sessions = sum(m['sessions'] for m in reported_rows)
        total_cost = round(sum(m['cost'] for m in reported_rows), 2)

        prev_names_msgs = sum(prev_data.get(n, {}).get('messages', 0) for n in names)
        prev_names_lines = sum(prev_data.get(n, {}).get('lines_added', 0) for n in names)
        prev_names_files = sum(prev_data.get(n, {}).get('files', 0) for n in names)
        delta_msgs = total_msgs - prev_names_msgs if prev_period else None
        delta_lines = total_lines - prev_names_lines if prev_period else None
        delta_files = total_files - prev_names_files if prev_period else None

        # 同类型最近 4 个周期趋势
        trend_periods = type_periods[-4:]
        period_trend = []
        for p in trend_periods:
            pd = all_data.get(p, {})
            msgs = sum(
                v['messages'] for k, v in pd.items()
                if not group or members_meta.get(k, {}).get('group') == group
            )
            period_trend.append({'period': p, 'messages': msgs})

        return {
            'periods': type_periods,
            'current_period': target_period,
            'period_type': period_type,
            'period_types': list(periods_by_type.keys()),
            'groups': all_groups,
            'has_team_report': target_period in state.get('team_reports', set()),
            'last_updated': state['last_updated'],
            'is_loading': state['is_loading'],
            'totals': {
                'messages': total_msgs,
                'lines_added': total_lines,
                'files': total_files,
                'sessions': total_sessions,
                'cost': total_cost,
                'reported': len(reported_rows),
                'expected': expected,
                'coverage_pct': round(len(reported_rows) / expected * 100, 1) if expected else 0.0,
                'delta_messages': delta_msgs,
                'delta_lines_added': delta_lines,
                'delta_files': delta_files,
            },
            'period_trend': period_trend,
            'members': sorted_members,
        }


# ── 私有辅助函数 ──────────────────────────────────────────────────────────────

def _load_members(members_path: str) -> dict:
    if not os.path.exists(members_path):
        return {}
    with open(members_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    result = {}
    for group, member_list in raw.items():
        if not isinstance(member_list, list):
            continue
        for item in member_list:
            if isinstance(item, str):
                result[item] = {'display': item.capitalize(), 'group': group}
            elif isinstance(item, dict):
                for slug, display in item.items():
                    result[slug] = {'display': display, 'group': group}
    return result


def _empty_state() -> dict:
    return {
        'all_data': {},
        'periods_by_type': {},
        'team_reports': set(),
        'members': {},
        'all_groups': [],
        'last_updated': None,
        'is_loading': False,
    }


def _empty_response(state: dict, period_type: str = 'weekly') -> dict:
    return {
        'periods': [],
        'current_period': None,
        'period_type': period_type,
        'period_types': [],
        'groups': state.get('all_groups', []),
        'has_team_report': False,
        'last_updated': state.get('last_updated'),
        'is_loading': state.get('is_loading', False),
        'totals': {
            'messages': 0, 'lines_added': 0, 'files': 0, 'sessions': 0,
            'cost': 0.0, 'reported': 0, 'expected': 0, 'coverage_pct': 0.0,
            'delta_messages': None, 'delta_lines_added': None, 'delta_files': None,
        },
        'period_trend': [],
        'members': [],
    }
