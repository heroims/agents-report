#!/usr/bin/env python3
"""移除定时上报（launchd / crontab / schtasks）任务。"""

import os
import platform
import subprocess
import sys
from pathlib import Path

CRON_MARKER = "# agents-report schedule"
PROJECT_DIR = Path(__file__).resolve().parent.parent
GETAGT_PY = str(PROJECT_DIR / "scripts" / "getagt.py")

TASK_NAMES = [
    "agents-report-weekly",
    "agents-report-monthly",
    "agents-report-quarterly",
    "agents-report-annual",
]


def _cron_remove():
    """从 crontab 中移除 agents-report 相关条目。"""
    try:
        existing = subprocess.check_output(
            ["crontab", "-l"], stderr=subprocess.DEVNULL, text=True
        )
    except subprocess.CalledProcessError:
        print("crontab 为空，无需移除。")
        return

    lines = existing.split("\n")
    removed = 0
    new_lines = []
    for line in lines:
        if CRON_MARKER in line or GETAGT_PY in line:
            removed += 1
            continue
        new_lines.append(line)

    if removed == 0:
        print("未找到相关定时任务。")
        return

    new_crontab = "\n".join(line for line in new_lines if line.strip()) + "\n"
    if not new_crontab.strip():
        subprocess.run(["crontab", "-r"], check=True)
        print("crontab 已清空。")
    else:
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    print(f"已移除 {removed} 个 crontab 定时任务。")


def _launchd_remove():
    """移除 launchd plist 任务。"""
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    removed = 0

    for period in ["weekly", "monthly", "quarterly", "annual"]:
        plist_path = agents_dir / f"com.agents-report.{period}.plist"
        if plist_path.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/501/com.agents-report.{period}"],
                capture_output=True,
            )
            plist_path.unlink()
            removed += 1

    if removed == 0:
        print("未找到相关 launchd 任务。")
    else:
        print(f"已移除 {removed} 个 launchd 定时任务。")


def _schtasks_remove():
    """移除 Windows schtasks 定时任务。"""
    removed = 0
    for task_name in TASK_NAMES:
        try:
            subprocess.run(
                ["schtasks", "/delete", "/tn", task_name, "/f"],
                check=True, capture_output=True, text=True,
            )
            removed += 1
        except subprocess.CalledProcessError:
            pass  # 任务不存在，跳过

    if removed == 0:
        print("未找到相关 schtasks 任务。")
    else:
        print(f"已移除 {removed} 个 schtasks 定时任务。")


def main():
    system = platform.system()
    if system == "Darwin":
        _launchd_remove()
    elif system == "Windows":
        _schtasks_remove()
    else:
        _cron_remove()


if __name__ == "__main__":
    main()
