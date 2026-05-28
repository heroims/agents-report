#!/usr/bin/env python3
"""跨平台采集 Claude Code + Codex 使用数据，支持周/月/季/年报归档。"""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

import sys as _sys
_scripts_dir = str((__import__('pathlib').Path(__file__).resolve().parent))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
from period_utils import current_period, period_label as plabel, period_type_arg



PROJECT_DIR = Path(__file__).resolve().parent.parent
MEMBERS_PATH = PROJECT_DIR / "scripts" / "members.json"
CLAUDE_REPORT = Path.home() / ".claude" / "usage-data" / "report.html"
CODEX_DB = Path.home() / ".codex" / "state_5.sqlite"


def _is_windows():
    return sys.platform == "win32"


def _get_env():
    """在 Windows 上注入 Git Bash 路径（claude.cmd 需要）。"""
    env = os.environ.copy()
    if _is_windows() and "CLAUDE_CODE_GIT_BASH_PATH" not in env:
        candidates = [
            r"D:\Pub\Git\Git\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ]
        for p in candidates:
            if Path(p).exists():
                env["CLAUDE_CODE_GIT_BASH_PATH"] = p
                break
    return env


def run(cmd):
    subprocess.run(cmd, cwd=PROJECT_DIR, check=True, shell=_is_windows(), env=_get_env())


def capture(cmd):
    result = subprocess.run(
        cmd,
        cwd=PROJECT_DIR,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=_is_windows(),
        env=_get_env(),
    )
    return result.stdout.strip()


def slugify_name(raw_name):
    slug = raw_name.strip().lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug


# ── 周期工具（来自 period_utils）─────────────────────────────
# current_period, period_label, period_type_arg 从顶层导入


def _run_optional(cmd, timeout=None):
    """执行环境探测命令，失败时返回空输出。"""
    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_DIR,
            check=False,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=_is_windows(),
            env=_get_env(),
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    return "\n".join(part.strip() for part in [result.stdout, result.stderr] if part and part.strip())


def _first_version_line(output):
    for line in str(output or "").splitlines():
        text = line.strip()
        if text:
            return text
    return ""


def _append_unique_jdk(items, seen, value, source):
    text = str(value or "").strip()
    if not text:
        return
    key = re.sub(r"\s+", " ", text.lower())
    if key in seen:
        return
    seen.add(key)
    items.append({"source": source, "version": text})


def _is_vpn_interface(name):
    text = str(name or "").strip().lower()
    return text.startswith(("utun", "tun", "tap", "ppp", "wg", "tailscale", "zt", "ham"))


def _is_virtual_interface(name):
    text = str(name or "").strip().lower()
    return text.startswith(("bridge", "vmenet", "vmnet", "awdl", "llw", "ap", "gif", "stf"))


def _append_unique_ip(items, seen, ip, interface, source):
    text = str(ip or "").strip()
    if not text or text.startswith("127.") or text == "::1":
        return
    if not re.fullmatch(r"\d+(?:\.\d+){3}", text):
        return
    key = (text, str(interface or ""))
    if key in seen:
        return
    seen.add(key)
    items.append({"ip": text, "interface": str(interface or ""), "source": source})


def _detect_public_ip(interface):
    if not interface:
        return ""
    endpoints = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
    ]
    for endpoint in endpoints:
        output = _run_optional(
            ["curl", "-fsS", "--max-time", "4", "--interface", interface, endpoint],
            timeout=6,
        )
        for line in output.splitlines():
            text = line.strip()
            if re.fullmatch(r"\d+(?:\.\d+){3}", text):
                return text
    return ""


def _jdk_display_label(raw):
    text = str(raw or "")
    match = re.search(r"1\.8\.0_(\d+)", text)
    if match:
        return f"JDK 8u{match.group(1)}"
    match = re.search(r"\b(\d{2,})(?:\.(\d+))?(?:\.(\d+))?", text)
    if not match:
        return ""
    version = ".".join(part for part in match.groups() if part is not None)
    return f"JDK {version}"


def collect_jdk_versions():
    """采集当前机器可见的 JDK/JRE 版本，不让环境异常阻断报告生成。"""
    installed = []
    seen = set()

    java_version = _first_version_line(_run_optional(["java", "-version"]))
    javac_version = _first_version_line(_run_optional(["javac", "-version"]))
    java_home = os.environ.get("JAVA_HOME", "").strip()

    if java_version:
        _append_unique_jdk(installed, seen, java_version, "java -version")
    if javac_version:
        _append_unique_jdk(installed, seen, javac_version, "javac -version")

    if sys.platform == "darwin":
        output = _run_optional(["/usr/libexec/java_home", "-V"])
        for line in output.splitlines():
            text = line.strip()
            if not text or "Matching Java Virtual Machines" in text or text.startswith("/"):
                continue
            if re.search(r"\d", text):
                _append_unique_jdk(installed, seen, text, "java_home -V")
    elif _is_windows():
        output = _run_optional(["where", "java"])
        for line in output.splitlines():
            text = line.strip()
            if text:
                _append_unique_jdk(installed, seen, text, "where java")
    else:
        jvm_dir = Path("/usr/lib/jvm")
        if jvm_dir.exists():
            for child in sorted(jvm_dir.iterdir()):
                if child.is_dir():
                    _append_unique_jdk(installed, seen, child.name, "/usr/lib/jvm")

    return {
        "java_version": java_version,
        "javac_version": javac_version,
        "java_home": java_home,
        "installed": installed,
    }


def collect_network_ips():
    """采集内网、非 tunnel 出口和 tunnel 出口 IP。"""
    ips = []
    public_ips = []
    tunnel_ips = []
    seen = set()
    public_seen = set()
    tunnel_seen = set()
    default_interface = ""

    if sys.platform == "darwin":
        route_output = _run_optional(["route", "-n", "get", "default"])
        match = re.search(r"interface:\s*(\S+)", route_output)
        if match:
            default_interface = match.group(1)
        if default_interface and not _is_vpn_interface(default_interface):
            ip = _first_version_line(_run_optional(["ipconfig", "getifaddr", default_interface]))
            _append_unique_ip(ips, seen, ip, default_interface, "default route")
        if not ips:
            ifconfig_output = _run_optional(["ifconfig"])
            for match in re.finditer(r"(?m)^([a-zA-Z0-9]+):[\s\S]*?(?=^[a-zA-Z0-9]+:|\Z)", ifconfig_output):
                interface = match.group(1)
                block = match.group(0)
                if _is_vpn_interface(interface) or _is_virtual_interface(interface):
                    continue
                if "status: active" not in block:
                    continue
                ip_match = re.search(r"\binet\s+(\d+(?:\.\d+){3})\b", block)
                if ip_match:
                    default_interface = default_interface or interface
                    _append_unique_ip(ips, seen, ip_match.group(1), interface, "active interface")
                    break
    elif _is_windows():
        output = _run_optional([
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway -ne $null} | "
            "ForEach-Object { \"$($_.InterfaceAlias)|$($_.IPv4Address.IPAddress)\" }",
        ])
        for line in output.splitlines():
            if "|" not in line:
                continue
            interface, ip = line.split("|", 1)
            if not _is_vpn_interface(interface):
                if not default_interface:
                    default_interface = interface.strip()
                _append_unique_ip(ips, seen, ip, interface.strip(), "default route")
    else:
        route_output = _run_optional(["ip", "route", "show", "default"])
        match = re.search(r"\bdev\s+(\S+)", route_output)
        if match:
            default_interface = match.group(1)
        if default_interface and not _is_vpn_interface(default_interface):
            addr_output = _run_optional(["ip", "-4", "addr", "show", "dev", default_interface])
            match = re.search(r"\binet\s+(\d+(?:\.\d+){3})/", addr_output)
            if match:
                _append_unique_ip(ips, seen, match.group(1), default_interface, "default route")

    public_interface = ""
    if default_interface and not _is_vpn_interface(default_interface) and not _is_virtual_interface(default_interface):
        public_interface = default_interface
    elif ips:
        public_interface = ips[0].get("interface") or ""

    public_ip = _detect_public_ip(public_interface)
    if public_ip:
        _append_unique_ip(public_ips, public_seen, public_ip, public_interface, "public egress")

    tunnel_interface = default_interface if _is_vpn_interface(default_interface) else ""
    tunnel_ip = _detect_public_ip(tunnel_interface)
    if tunnel_ip:
        _append_unique_ip(tunnel_ips, tunnel_seen, tunnel_ip, tunnel_interface, "tunnel egress")

    return {
        "default_interface": default_interface,
        "public_interface": public_interface,
        "tunnel_interface": tunnel_interface,
        "ips": ips,
        "public_ips": public_ips,
        "tunnel_ips": tunnel_ips,
        "note": "public IP is detected via curl bound to the selected interface",
    }


def collect_environment():
    return {
        "jdk": collect_jdk_versions(),
        "network": collect_network_ips(),
    }


def _build_environment_block(environment):
    jdk = environment.get("jdk") or environment
    network = environment.get("network") or {}
    installed = jdk.get("installed") or []
    ips = network.get("ips") or []
    public_ips = network.get("public_ips") or []
    tunnel_ips = network.get("tunnel_ips") or []

    jdk_display_items = []
    jdk_seen = set()
    for item in installed:
        label = _jdk_display_label(item.get("version"))
        if label and label not in jdk_seen:
            jdk_seen.add(label)
            jdk_display_items.append(label)

    jdk_items_html = "\n".join(
        f"""      <div class="env-row">
        <span class="env-version">{escape(label)}</span>
        <span class="env-source">java</span>
      </div>"""
        for label in jdk_display_items
    )

    jdk_section = ""
    if jdk_display_items:
        jdk_section = f"""  <div class="env-subtitle">JDK</div>
{jdk_items_html}"""

    network_items = []
    for item in ips:
        network_items.append({
            "ip": item.get("ip") or "",
            "source": item.get("interface") or item.get("source") or "",
        })
    for item in public_ips:
        network_items.append({
            "ip": item.get("ip") or "",
            "source": "egress",
        })
    for item in tunnel_ips:
        network_items.append({
            "ip": item.get("ip") or "",
            "source": "tunnel",
        })

    ip_items_html = "\n".join(
        f"""      <div class="env-row">
        <span class="env-version">{escape(str(item.get("ip") or ""))}</span>
        <span class="env-source">{escape(str(item.get("source") or ""))}</span>
      </div>"""
        for item in network_items
    )
    network_section = ""
    if network_items:
        network_section = f"""  <div class="env-subtitle">网络</div>
{ip_items_html}"""

    raw = json.dumps(environment, ensure_ascii=False)
    return f"""<style>
.env-section {{ margin: 28px 0 0; padding: 16px 18px; border: 1px solid #cbd5e1; border-radius: 12px; background: #f8fafc; }}
.env-title {{ font-size: 14px; font-weight: 700; color: #0f172a; margin-bottom: 10px; }}
.env-subtitle {{ font-size: 12px; font-weight: 700; color: #475569; margin-top: 12px; margin-bottom: 4px; text-transform: uppercase; }}
.env-subtitle:first-of-type {{ margin-top: 0; }}
.env-row {{ display: flex; justify-content: space-between; gap: 14px; padding: 8px 0; border-top: 1px solid #e2e8f0; font-size: 13px; }}
.env-row:first-of-type {{ border-top: 0; }}
.env-version {{ color: #334155; word-break: break-word; }}
.env-source {{ color: #64748b; white-space: nowrap; }}
.env-home {{ margin-top: 8px; font-size: 12px; color: #64748b; word-break: break-word; }}
@media (max-width: 760px) {{ .env-row {{ flex-direction: column; gap: 2px; }} .env-source {{ white-space: normal; }} }}
</style>
<section class="env-section">
  <div class="env-title">本机环境</div>
{jdk_section}
{network_section}
</section>
<div class="raw-data" id="environment-raw-data">{raw}</div>"""


def inject_environment_report(report_path, environment):
    jdk = environment.get("jdk") or environment
    network = environment.get("network") or {}
    if not (
        jdk.get("installed")
        or jdk.get("java_home")
        or network.get("ips")
        or network.get("public_ips")
        or network.get("tunnel_ips")
    ):
        return

    html = report_path.read_text(encoding="utf-8")
    block = _build_environment_block(environment)
    body_end = html.rfind("</body>")
    if body_end == -1:
        raise RuntimeError("无效的报告 HTML：未找到 </body>")
    container_close = html.rfind("</div>", 0, body_end)
    if container_close == -1:
        html = html[:body_end] + block + "\n" + html[body_end:]
    else:
        html = html[:container_close] + "\n" + block + "\n" + html[container_close:]
    report_path.write_text(html, encoding="utf-8")


def generate_claude_report(period):
    """基于本地 stats-cache.json 生成 Claude 报告（周/月/季/年）。"""
    CLAUDE_REPORT.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            str(PROJECT_DIR / "scripts" / "generate_insights_from_stats.py"),
            period,
            f"--output={CLAUDE_REPORT}",
        ]
    )
    if not CLAUDE_REPORT.exists():
        raise RuntimeError("Claude 报告生成失败，请检查 ~/.claude/stats-cache.json 后重试")


def generate_codex_report(period, name):
    if not CODEX_DB.exists():
        return None

    out_path = Path(tempfile.gettempdir()) / f"{name}-codex-{period}.html"
    try:
        run([sys.executable, str(PROJECT_DIR / "scripts" / "collect_codex.py"), period, f"--output={out_path}"])
    except subprocess.CalledProcessError:
        return None
    return out_path if out_path.exists() else None


def generate_opencode_report(period, name):
    """采集 OpenCode 报告；失败时返回 None（不阻断主流程）。"""
    try:
        db_path = capture(["opencode", "db", "path"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    if not db_path:
        return None

    db_file = Path(db_path).expanduser()
    if not db_file.exists() or not db_file.is_file() or not db_file.stat().st_size:
        return None

    out_path = Path(tempfile.gettempdir()) / f"{name}-opencode-{period}.html"
    try:
        run([sys.executable, str(PROJECT_DIR / "scripts" / "collect_opencode.py"), period, f"--output={out_path}"])
    except subprocess.CalledProcessError:
        return None
    return out_path if out_path.exists() else None


def generate_cursor_report(period, name):
    """采集 Cursor 报告；失败时返回 None（不阻断主流程）。"""
    if sys.platform == "darwin":
        cursor_db = Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    elif sys.platform == "win32":
        cursor_db = Path(os.environ.get("APPDATA", "")) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        cursor_db = Path(xdg) / "Cursor" / "User" / "globalStorage" / "state.vscdb"

    if not cursor_db.exists():
        return None

    out_path = Path(tempfile.gettempdir()) / f"{name}-cursor-{period}.html"
    try:
        run([sys.executable, str(PROJECT_DIR / "scripts" / "collect_cursor.py"), period, f"--output={out_path}"])
    except subprocess.CalledProcessError:
        return None
    return out_path if out_path.exists() else None


def generate_trae_report(period, name):
    """采集 Trae 报告；失败时返回 None（不阻断主流程）。"""
    if sys.platform == "darwin":
        trae_ws = Path.home() / "Library" / "Application Support" / "Trae" / "User" / "workspaceStorage"
    elif sys.platform == "win32":
        trae_ws = Path(os.environ.get("APPDATA", "")) / "Trae" / "User" / "workspaceStorage"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        trae_ws = Path(xdg) / "Trae" / "User" / "workspaceStorage"

    if not trae_ws.exists() or not any(trae_ws.iterdir()):
        return None

    out_path = Path(tempfile.gettempdir()) / f"{name}-trae-{period}.html"
    try:
        run([sys.executable, str(PROJECT_DIR / "scripts" / "collect_trae.py"), period, f"--output={out_path}"])
    except subprocess.CalledProcessError:
        return None
    return out_path if out_path.exists() else None


def load_group(name):
    """从 members.json 读取成员所属分组。JSON 格式: {"group": ["name1", {"slug": "Display"}, ...]}"""
    if not MEMBERS_PATH.exists():
        return None
    with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    for group, member_list in raw.items():
        if not isinstance(member_list, list):
            continue
        for item in member_list:
            if isinstance(item, str) and item == name:
                return group
            elif isinstance(item, dict) and name in item:
                return group
    return "group"


def archive_report(period, name, codex_report, opencode_report, cursor_report=None, trae_report=None):
    group = load_group(name)
    output_dir = PROJECT_DIR / "reports" / period / group
    output_dir.mkdir(parents=True, exist_ok=True)
    final_report = output_dir / f"{name}-{period}-report.html"

    # 始终使用 merge_reports.py 生成合并格式报告（即使外部数据为空）
    run(
        [
            sys.executable,
            str(PROJECT_DIR / "scripts" / "merge_reports.py"),
            str(CLAUDE_REPORT),
            str(codex_report or ""),
            str(opencode_report or ""),
            str(cursor_report or ""),
            str(trae_report or ""),
            str(final_report),
            period,
        ]
    )

    return final_report


def open_report_in_browser(report_path):
    """生成完成后自动打开本地报告。"""
    target = str(report_path.resolve())
    if _is_windows():
        os.startfile(target)
        return
    if sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
        return
    subprocess.run(["xdg-open", target], check=False)


def commit_and_push(report_path, name, period):
    label = plabel(period)
    run(["git", "add", str(report_path)])
    run(["git", "commit", "-m", f"data: {name} {period} ({label}) 使用数据采集"])
    run(["git", "push", "origin", "HEAD"])


def upload_report(report_path, name, period, group):
    """通过 HTTP PUT 上传报告到 AGENTS_REPORT_URL 指定的 dashboard 服务。"""
    import urllib.request
    url = os.environ.get("AGENTS_REPORT_URL", "").rstrip("/")
    if not url:
        return False
    target = f"{url}/api/report/upload?name={name}&period={period}&group={group}"
    with open(report_path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(target, data=data, method="PUT")
    req.add_header("Content-Type", "text/html")
    try:
        urllib.request.urlopen(req, timeout=30)
        return True
    except Exception as e:
        print(f"上传报告失败: {e}", file=sys.stderr)
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="采集 Claude Code + Codex 等工具使用数据")
    parser.add_argument(
        "--period", "-p",
        type=period_type_arg,
        default="weekly",
        help="报告周期: weekly (周报, 默认), monthly (月报), quarterly (季报), annual (年报)",
    )
    parser.add_argument("--skip-git", action="store_true", help="跳过 git add/commit/push")

    # 从 sys.argv 兼容旧的 --skip-git 位置参数
    args, _ = parser.parse_known_args(sys.argv[1:])

    try:
        git_name = capture(["git", "config", "user.name"])
    except subprocess.CalledProcessError:
        print("未能读取 git 用户名", file=sys.stderr)
        print('下一步：先执行 git config user.name "your-name" 后重试 /getagt', file=sys.stderr)
        return 1

    name = slugify_name(git_name)
    if not name:
        print("未能从 git 用户名生成有效标识", file=sys.stderr)
        print('下一步：先执行 git config user.name "your-name" 后重试 /getagt', file=sys.stderr)
        return 1

    period = current_period(args.period)
    period_display = plabel(period)
    print(f"报告周期: {period} ({period_display})", file=sys.stderr)

    try:
        generate_claude_report(period)
        codex_report = generate_codex_report(period, name)
        opencode_report = generate_opencode_report(period, name)
        cursor_report = generate_cursor_report(period, name)
        trae_report = generate_trae_report(period, name)
        final_report = archive_report(period, name, codex_report, opencode_report, cursor_report, trae_report)
        inject_environment_report(final_report, collect_environment())
        report_url = os.environ.get("AGENTS_REPORT_URL", "").strip()
        if not args.skip_git:
            if report_url:
                group = load_group(name)
                if upload_report(final_report, name, period, group or "group"):
                    print(f"已上传到 {report_url}", file=sys.stderr)
                else:
                    print("上传失败，回退到 git push", file=sys.stderr)
                    commit_and_push(final_report, name, period)
            else:
                commit_and_push(final_report, name, period)
    except subprocess.CalledProcessError as exc:
        failed_cmd = " ".join(str(part) for part in exc.cmd)
        print(f"命令执行失败: {failed_cmd}", file=sys.stderr)
        return exc.returncode or 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"已完成采集：{final_report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
