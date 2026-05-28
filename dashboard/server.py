"""Flask dashboard server."""
import json
import os
import sys
import threading
from pathlib import Path

import requests as req
from flask import Flask, jsonify, request, send_file, Response

from gitlab_client import GitLabClient
from cache import DataCache

# Import analyze for server-side team report generation
_scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts'))
_sys_path = list(sys.path)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
from analyze import generate_team_report, current_period, period_type_arg
sys.path = _sys_path

app = Flask(__name__)

_cache = DataCache()
_client = None
_members_path = ''
_summary_cache = {}  # (group, week, lang) -> str
_summary_cache_lock = threading.Lock()


class LocalReportClient:
    """本地 reports 目录读取器，兼容 GitLabClient 接口。"""

    def __init__(self, reports_root: str):
        self.reports_root = os.path.abspath(reports_root)

    def list_report_files(self) -> list[str]:
        root = Path(self.reports_root)
        if not root.exists():
            return []
        files = []
        for path in root.rglob("*-report.html"):
            rel = path.relative_to(self.reports_root).as_posix()
            files.append(f"reports/{rel}")
        return sorted(files)

    def get_file_content(self, path: str) -> str | None:
        if not path.startswith("reports/"):
            return None
        rel = path[len("reports/"):].lstrip("/")
        target = os.path.abspath(os.path.join(self.reports_root, rel))
        if not target.startswith(self.reports_root + os.sep) and target != self.reports_root:
            return None
        if not os.path.exists(target) or not os.path.isfile(target):
            return None
        with open(target, "r", encoding="utf-8") as f:
            return f.read()


# ── routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_file(os.path.join(os.path.dirname(__file__), 'dashboard.html'))


@app.route('/api/data')
def api_data():
    group = request.args.get('group') or None
    period = request.args.get('period') or None
    period_type = request.args.get('period_type') or 'weekly'
    return jsonify(_cache.get_data(group, period, period_type))


@app.route('/api/report')
def api_report():
    """代理个人或团队报告 HTML，供 iframe 展示。"""
    path = request.args.get('path', '')
    # 支持两种格式: path=reports/... (GitLab 路径) 或 name=...&period=...&group=... (本地路径)
    if not path:
        name = request.args.get('name', '')
        period = request.args.get('period', '')
        group = request.args.get('group', '')
        kind = request.args.get('kind', 'member')  # member | team
        if kind == 'team':
            path = f'reports/{period}/team-report.html'
        elif name and period and group:
            path = f'reports/{period}/{group}/{name}-{period}-report.html'
    if not path or '..' in path:
        return 'invalid path', 400
    html = _client.get_file_content(path) if _client else None
    if html is None:
        return 'report not found', 404
    return Response(html, mimetype='text/html')


@app.route('/api/report/upload', methods=['PUT'])
def api_report_upload():
    """接收 getagt 脚本上传的个人报告，存入 reports/ 目录。"""
    name = request.args.get('name', '')
    period = request.args.get('period', '')
    group = request.args.get('group', 'group')
    if not name or not period:
        return 'missing name or period', 400
    # 安全检查
    for val in (name, period, group):
        if '..' in val or '/' in val or '\\' in val:
            return 'invalid parameter', 400

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if name == 'team':
        # 团队报告直接放 reports/{period}/team-report.html
        dest_dir = os.path.join(repo_root, 'reports', period)
        filename = 'team-report.html'
    else:
        # 个人报告放 reports/{period}/{group}/{name}-{period}-report.html
        dest_dir = os.path.join(repo_root, 'reports', period, group)
        filename = f'{name}-{period}-report.html'
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)

    data = request.get_data()
    with open(dest_path, 'wb') as f:
        f.write(data)

    # 刷新缓存
    _cache.load(_client, _members_path)
    return jsonify({'ok': True, 'path': os.path.relpath(dest_path, repo_root)})


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    period_type = request.args.get('period_type', 'weekly')
    period = current_period(period_type_arg(period_type))

    reports_dir = os.environ.get('REPORTS_ROOT') or os.path.join(repo_root, 'reports')
    members_path = os.environ.get('MEMBERS_PATH') or os.path.join(repo_root, 'scripts', 'members.json')

    try:
        success = generate_team_report(reports_dir, reports_dir, members_path, period)
        if success:
            _cache.load(_client, _members_path)
            return jsonify({'ok': True, 'period': period, 'path': f'reports/{period}/team-report.html'})
        else:
            return jsonify({'ok': False, 'error': 'Generation failed'}), 500
    except Exception as e:
        app.logger.error('Team report generation failed: %s', e)
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    _cache.load(_client, _members_path)
    data = _cache.get_data()
    return jsonify({'ok': True, 'last_updated': data['last_updated']})


@app.route('/api/ai/summary', methods=['POST'])
def api_ai_summary():
    body = request.get_json() or {}
    group = body.get('group') or None
    week = body.get('week') or None
    lang = body.get('lang', 'zh')
    key = (group, week, lang)

    with _summary_cache_lock:
        if key in _summary_cache:
            return jsonify({'summary': _summary_cache[key]})

    data = _cache.get_data(group, week)
    prompt = _build_summary_prompt(data, lang)
    try:
        summary = _call_anthropic(prompt, model='claude-sonnet-4-6')
    except EnvironmentError as e:
        app.logger.error("AI summary failed: %s", e)
        return jsonify({'summary': None, 'error': f'AI 未配置：{e}'})
    except Exception as e:
        app.logger.error("AI summary failed: %s", e)
        msg = str(e)
        if 'timed out' in msg or 'Timeout' in msg:
            reason = 'AI 请求超时，请稍后重试'
        elif 'invalid_api_key' in msg or '401' in msg:
            reason = 'API Key 无效，请检查 AI_API_KEY 配置'
        elif '400' in msg:
            reason = f'请求参数错误：{msg[:120]}'
        else:
            reason = f'AI 服务异常：{msg[:120]}'
        return jsonify({'summary': None, 'error': reason})

    with _summary_cache_lock:
        _summary_cache[key] = summary
    return jsonify({'summary': summary, 'error': None})


@app.route('/api/ai/summary/stream', methods=['POST'])
def api_ai_summary_stream():
    body = request.get_json() or {}
    group = body.get('group') or None
    week = body.get('week') or None
    lang = body.get('lang', 'zh')
    key = (group, week, lang)

    # 命中缓存直接推全文
    with _summary_cache_lock:
        if key in _summary_cache:
            cached = _summary_cache[key]
            def _cached_gen():
                yield f'data: {json.dumps(cached, ensure_ascii=False)}\n\n'
                yield 'data: [DONE]\n\n'
            return Response(_cached_gen(), mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    data = _cache.get_data(group, week)
    prompt = _build_summary_prompt(data, lang)
    chunks = []

    def generate():
        try:
            for text in _call_anthropic_stream(prompt, {}, model='claude-sonnet-4-6', system_override=''):
                chunks.append(text)
                yield f'data: {json.dumps(text, ensure_ascii=False)}\n\n'
        except Exception as e:
            app.logger.error("AI summary stream failed: %s", e)
            yield f'data: {json.dumps("[错误]", ensure_ascii=False)}\n\n'
        else:
            full = ''.join(chunks)
            with _summary_cache_lock:
                _summary_cache[key] = full
        yield 'data: [DONE]\n\n'

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    body = request.get_json() or {}
    question = body.get('question', '')
    context = body.get('context', {})

    def generate():
        try:
            for chunk in _call_anthropic_stream(question, context, model='qwen-plus'):
                # chunk is JSON-encoded; frontend must JSON.parse(e.data) to decode
                yield f'data: {json.dumps(chunk, ensure_ascii=False)}\n\n'
        except Exception as e:
            yield f'data: {json.dumps("[错误]", ensure_ascii=False)}\n\n'

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── AI helpers ────────────────────────────────────────────────────────────────

def _build_summary_prompt(data: dict, lang: str) -> str:
    totals = data['totals']
    members = data['members']

    # 按 group 聚合
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for m in members:
        groups[m.get('group') or 'other'].append(m)

    def _fmt(m):
        if m['reported']:
            return f"{m['display']}({m['messages']}条/{m['lines_added']}行)"
        return f"{m['display']}(未提交)"

    group_lines = []
    for g, ms in sorted(groups.items()):
        reported = [m for m in ms if m['reported']]
        not_reported = [m for m in ms if not m['reported']]
        block = f"[{g.upper()} {len(reported)}/{len(ms)}人提交] " + "; ".join(_fmt(m) for m in ms)
        group_lines.append(block)

    members_section = "\n".join(group_lines)

    if lang == 'zh':
        return (
            f"请用中文分析以下团队 Claude Code 使用数据，生成简洁周报摘要（4-6句话）：\n\n"
            f"周: {data.get('current_week', '')}\n"
            f"总对话: {totals['messages']}, 总行数: {totals['lines_added']}, "
            f"覆盖率: {totals['coverage_pct']}% ({totals['reported']}/{totals['expected']}人)\n\n"
            f"各组成员数据:\n{members_section}\n\n"
            f"重点: 各组活跃度对比、高产出成员、未提交成员情况。"
        )
    return (
        f"Analyze this team's Claude Code usage and write a brief weekly summary (4-6 sentences):\n\n"
        f"Week: {data.get('current_week', '')}\n"
        f"Messages: {totals['messages']}, Lines: {totals['lines_added']}, "
        f"Coverage: {totals['coverage_pct']}% ({totals['reported']}/{totals['expected']})\n\n"
        f"Members by group:\n{members_section}\n\n"
        f"Focus: group activity comparison, top contributors, members who didn't submit."
    )


_AI_BASE = os.environ.get('AI_BASE_URL', 'https://xxx/v1')


def _ai_api_key() -> str | None:
    return os.environ.get('AI_API_KEY', '') or None


def _call_anthropic(prompt: str, model: str) -> str:
    key = _ai_api_key()
    if not key:
        raise EnvironmentError("AI_API_KEY 未配置")
    resp = req.post(
        f'{_AI_BASE}/chat/completions',
        json={'model': model, 'max_tokens': 600, 'messages': [{'role': 'user', 'content': prompt}]},
        headers={'Authorization': f'Bearer {key}'},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


def _call_anthropic_stream(question: str, context: dict, model: str, system_override: str | None = None):
    key = _ai_api_key()
    if not key:
        raise EnvironmentError("AI_API_KEY 未配置")
    if system_override is not None:
        system = system_override
    else:
        totals = context.get('totals', {})
        members = context.get('members', [])[:20]
        members_text = '; '.join(
            f"{m.get('display', '?')}: {m.get('messages', 0)}条"
            if m.get('reported')
            else f"{m.get('display', '?')}: 未提交"
            for m in members
        )
        system = (
            f"你是团队效能分析助手。数据 — 周: {context.get('week', '')}, "
            f"对话: {totals.get('messages', 0)}, 覆盖率: {totals.get('coverage_pct', 0)}%, "
            f"成员: {members_text}"
        )
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': question})
    with req.post(
        f'{_AI_BASE}/chat/completions',
        json={
            'model': model, 'max_tokens': 500, 'stream': True,
            'messages': messages,
        },
        headers={'Authorization': f'Bearer {key}'},
        stream=True,
        timeout=60,
    ) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode('utf-8') if isinstance(raw, bytes) else raw
            if line.startswith('data:'):
                payload = line[5:].strip()
                if payload == '[DONE]':
                    break
                try:
                    obj = json.loads(payload)
                    text = obj['choices'][0]['delta'].get('content', '')
                    if text:
                        yield text
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass


# ── startup ───────────────────────────────────────────────────────────────────

def _init():
    global _client, _members_path
    base_url = os.environ.get('GITLAB_URL', '')
    token = os.environ.get('GITLAB_TOKEN', '')
    project = os.environ.get('GITLAB_PROJECT', '')
    _here = os.path.dirname(os.path.abspath(__file__))
    _repo_root = os.path.abspath(os.path.join(_here, '..'))
    _members_path = os.environ.get('MEMBERS_PATH') or next(
        (p for p in [
            os.path.join(_here, 'scripts', 'members.json'),   # Docker: /app/scripts/members.json
            os.path.join(_here, '..', 'scripts', 'members.json'),  # 本地: dashboard/../scripts/members.json
        ] if os.path.exists(p)),
        os.path.join(_here, 'scripts', 'members.json'),
    )
    if token:
        _client = GitLabClient(base_url, token, project)
        print('Loading reports from GitLab...')
    else:
        reports_root = os.environ.get('REPORTS_ROOT') or os.path.join(_repo_root, 'reports')
        _client = LocalReportClient(reports_root)
        print(f'Loading reports from local path: {reports_root}')
    # 后台加载，不阻塞 Flask 启动
    threading.Thread(target=_background_load, daemon=True).start()


def _background_load():
    _cache.load(_client, _members_path)
    print('Ready.')


if __name__ == '__main__':
    _init()
    port = int(os.environ.get('DASHBOARD_PORT', 8080))
    print(f'Dashboard at http://localhost:{port}')
    app.run(host='0.0.0.0', port=port, debug=False)
