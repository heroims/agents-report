"""GitLab API client — 从 GitLab 仓库获取报告文件列表和原始文件内容。"""
import time
from urllib.parse import quote

import requests


class GitLabClient:
    """从 GitLab 仓库读取 CC 报告 HTML 文件。"""

    def __init__(self, base_url: str, token: str, project: str):
        if not token:
            raise ValueError("GitLab token must not be empty")
        self.base_url = base_url.rstrip('/')
        # 支持数字 ID（直接使用）或路径（URL 编码）
        self.project_encoded = project if project.isdigit() else quote(project, safe='')
        self.session = requests.Session()
        self.session.headers['PRIVATE-TOKEN'] = token

    def list_report_files(self) -> list[str]:
        """返回所有匹配 reports/**/*-report.html 的文件路径。"""
        files = []
        url = f"{self.base_url}/api/v4/projects/{self.project_encoded}/repository/tree"
        params = {
            'path': 'reports',
            'recursive': 'true',
            'per_page': 100,
            'pagination': 'keyset',
        }
        MAX_PAGES = 200
        page_count = 0
        while url:
            if page_count >= MAX_PAGES:
                raise RuntimeError(f"list_report_files exceeded {MAX_PAGES} pages")
            page_count += 1
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            for item in resp.json():
                if item['type'] == 'blob' and item['path'].endswith('-report.html'):
                    files.append(item['path'])
            url = resp.links.get('next', {}).get('url')
            params = {}
        return files

    def get_file_content(self, path: str) -> str | None:
        """返回文件的原始 HTML 内容，文件不存在时返回 None。"""
        encoded_path = quote(path, safe='')
        url = (
            f"{self.base_url}/api/v4/projects/{self.project_encoded}"
            f"/repository/files/{encoded_path}/raw"
        )
        for attempt in range(3):
            resp = self.session.get(url, params={'ref': 'master'}, timeout=30)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                retry_after_raw = resp.headers.get('retry-after', '10')
                try:
                    wait = int(retry_after_raw)
                except ValueError:
                    wait = 10
                time.sleep(wait)
                if attempt == 2:
                    raise requests.HTTPError("Rate limit exceeded after 3 retries", response=resp)
                continue
            if resp.status_code >= 500:
                if attempt == 2:
                    resp.raise_for_status()
                time.sleep(2)
                continue
            resp.raise_for_status()
            return resp.text
        return None
