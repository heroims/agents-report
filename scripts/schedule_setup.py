#!/usr/bin/env python3
"""安装定时上报任务（周报/月报/季报/年报）。

适用平台：macOS (launchd)、Linux (crontab)、Windows (schtasks)。
Codex 用户优先使用 `/setup-schedule` skill（通过 codex_app__automation_update），
本脚本作为非 Codex 环境的回退方案。
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
GETAGT_PY = str(PROJECT_DIR / "scripts" / "getagt.py")

CRON_MARKER = "# agents-report schedule"

# (定时表达式, 命令行)
ENTRIES = {
    "weekly":   ("0 9 * * 1", f"cd {PROJECT_DIR} && {PYTHON} {GETAGT_PY} --period weekly"),
    "monthly":  ("0 9 1 * *", f"cd {PROJECT_DIR} && {PYTHON} {GETAGT_PY} --period monthly"),
    "quarterly": ("0 9 1 1,4,7,10 *", f"cd {PROJECT_DIR} && {PYTHON} {GETAGT_PY} --period quarterly"),
    "annual":   ("0 9 1 1 *", f"cd {PROJECT_DIR} && {PYTHON} {GETAGT_PY} --period annual"),
}

# Windows schtasks 参数
SCHTASKS_TASKS = {
    "weekly":   ("agents-report-weekly",   "WEEKLY", "/d MON"),
    "monthly":  ("agents-report-monthly",  "MONTHLY", "/d 1"),
    "quarterly": ("agents-report-quarterly", "MONTHLY", "/d 1 /m JAN,APR,JUL,OCT"),
    "annual":   ("agents-report-annual",   "YEARLY", "/d 1 /m JAN"),
}


def _cron_install():
    """在 crontab 中添加定时任务（仅添加尚不存在的条目）。"""
    try:
        existing = subprocess.check_output(
            ["crontab", "-l"], stderr=subprocess.DEVNULL, text=True
        )
    except subprocess.CalledProcessError:
        existing = ""

    lines = existing.strip().split("\n") if existing.strip() else []
    added = 0
    for label, (schedule, cmd) in ENTRIES.items():
        if cmd in existing:
            continue
        lines.append(f"{schedule} {cmd}  {CRON_MARKER}")
        added += 1

    if added == 0:
        print("所有定时任务已存在，无需添加。")
        return

    new_crontab = "\n".join(line for line in lines if line.strip()) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    print(f"已添加 {added} 个定时任务到 crontab。")


def _launchd_install():
    """通过 launchd plist 安装定时任务。"""
    import plistlib

    label_map = {
        "weekly":   ("StartCalendarInterval", {"Weekday": 1, "Hour": 9, "Minute": 0}),
        "monthly":  ("StartCalendarInterval", {"Day": 1, "Hour": 9, "Minute": 0}),
        "quarterly": ("StartCalendarInterval", {"Day": 1, "Month": [1, 4, 7, 10], "Hour": 9, "Minute": 0}),
        "annual":   ("StartCalendarInterval", {"Day": 1, "Month": 1, "Hour": 9, "Minute": 0}),
    }

    agents_dir = Path.home() / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    added = 0

    for label, (key, interval) in label_map.items():
        plist_name = f"com.agents-report.{label}.plist"
        plist_path = agents_dir / plist_name

        if plist_path.exists():
            plist_path.unlink()

        plist = {
            "Label": f"com.agents-report.{label}",
            "ProgramArguments": [PYTHON, GETAGT_PY, "--period", label],
            "WorkingDirectory": str(PROJECT_DIR),
            "RunAtLoad": False,
            key: interval,
            "StandardOutPath": f"/tmp/agents-report-{label}.log",
            "StandardErrorPath": f"/tmp/agents-report-{label}.err",
        }
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)

        subprocess.run(["launchctl", "bootstrap", "gui/501", str(plist_path)],
                       capture_output=True)
        added += 1

    print(f"已添加 {added} 个 launchd 定时任务。")


def _schtasks_install():
    """通过 Windows schtasks 安装定时任务。"""
    added = 0
    for label, (task_name, freq, extra) in SCHTASKS_TASKS.items():
        cmd = f'cmd /c "cd /d {PROJECT_DIR} && {PYTHON} {GETAGT_PY} --period {label}"'
        args = [
            "schtasks", "/create", "/f",
            "/tn", task_name,
            "/tr", cmd,
            "/sc", freq,
            "/st", "09:00",
        ]
        if "/d" in extra or "/m" in extra:
            args.extend(extra.split())
        try:
            subprocess.run(args, check=True, capture_output=True, text=True)
            added += 1
        except subprocess.CalledProcessError as e:
            print(f"  创建 {task_name} 失败: {e.stderr.strip() if e.stderr else e}")

    print(f"已添加 {added} 个 schtasks 定时任务。")


def main():
    system = platform.system()
    if system == "Darwin":
        print("检测到 macOS，使用 launchd 安装定时任务...")
        _launchd_install()
    elif system == "Windows":
        print("检测到 Windows，使用 schtasks 安装定时任务...")
        _schtasks_install()
    else:
        print("检测到 Linux，使用 crontab 安装定时任务...")
        _cron_install()

    print("\n各任务调度时间（每天 09:00 执行）：")
    print("  周报 — 每周一")
    print("  月报 — 每月 1 日")
    print("  季报 — 1/4/7/10 月 1 日")
    print("  年报 — 每年 1 月 1 日")


if __name__ == "__main__":
    main()
