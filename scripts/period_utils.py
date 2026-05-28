#!/usr/bin/env python3
"""报告周期工具：统一周/月/季/年 的标识生成、解析和日期范围计算。

周期标识格式：
  周报: YYYY-WNN    (如 2026-W19)
  月报: YYYY-MM     (如 2026-05)
  季报: YYYY-QN     (如 2026-Q2)
  年报: YYYY        (如 2026)
"""

import datetime
import re
from argparse import ArgumentTypeError


# ── 周期正则 ──────────────────────────────────────────────
WEEKLY_RE  = re.compile(r"^(\d{4})-W(\d{1,2})$")
MONTHLY_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
QUARTERLY_RE = re.compile(r"^(\d{4})-Q([1-4])$")
ANNUAL_RE  = re.compile(r"^(\d{4})$")

# 文件名匹配（用于 reports 目录扫描）
FILENAME_RE = re.compile(
    r"^(.+)-("
    r"\d{4}-W\d{2}"          # weekly
    r"|\d{4}-Q[1-4]"         # quarterly
    r"|\d{4}-\d{2}"          # monthly (must come before annual to not eat MM)
    r"|\d{4}"                # annual
    r")-report\.html$"
)


def detect_period_type(period_str):
    """返回周期类型: 'weekly', 'monthly', 'quarterly', 'annual'。"""
    if WEEKLY_RE.match(period_str):
        return "weekly"
    if QUARTERLY_RE.match(period_str):
        return "quarterly"
    if MONTHLY_RE.match(period_str):
        return "monthly"
    if ANNUAL_RE.match(period_str):
        return "annual"
    return None


def period_start_end(period_str):
    """返回 (start_date, end_date) 包含该周期的第一天到最后一天。"""
    tp = detect_period_type(period_str)
    if tp is None:
        raise ValueError(f"无效周期标识: {period_str}")

    if tp == "weekly":
        m = WEEKLY_RE.match(period_str)
        year, week = int(m.group(1)), int(m.group(2))
        start = datetime.date.fromisocalendar(year, week, 1)
        end   = datetime.date.fromisocalendar(year, week, 7)
        return start, end

    if tp == "monthly":
        m = MONTHLY_RE.match(period_str)
        year, month = int(m.group(1)), int(m.group(2))
        start = datetime.date(year, month, 1)
        # last day of month
        if month == 12:
            end = datetime.date(year, 12, 31)
        else:
            end = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)
        return start, end

    if tp == "quarterly":
        m = QUARTERLY_RE.match(period_str)
        year, quarter = int(m.group(1)), int(m.group(2))
        start_month = (quarter - 1) * 3 + 1
        start = datetime.date(year, start_month, 1)
        end_month = start_month + 2
        if end_month == 12:
            end = datetime.date(year, 12, 31)
        else:
            end = datetime.date(year, end_month + 1, 1) - datetime.timedelta(days=1)
        return start, end

    if tp == "annual":
        year = int(period_str)
        return datetime.date(year, 1, 1), datetime.date(year, 12, 31)


def current_period(period_type="weekly"):
    """返回当前周期的标识字符串。"""
    today = datetime.date.today()

    if period_type == "weekly":
        iso = today.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    if period_type == "monthly":
        return f"{today.year}-{today.month:02d}"

    if period_type == "quarterly":
        quarter = (today.month - 1) // 3 + 1
        return f"{today.year}-Q{quarter}"

    if period_type == "annual":
        return str(today.year)

    raise ValueError(f"Unknown period type: {period_type}")


def period_type_arg(value):
    """argparse type 检查器。"""
    value = str(value or "weekly").strip().lower()
    if value not in ("weekly", "monthly", "quarterly", "annual"):
        raise ArgumentTypeError(f"无效周期类型: {value}，可选 weekly/monthly/quarterly/annual")
    return value


def parse_filename(filename):
    """从报告文件名提取 (name, period)。period 为 None 表示旧格式。"""
    m = FILENAME_RE.match(filename)
    if m:
        return m.group(1).lower(), m.group(2)
    # fallback: old format name-report.html
    m = re.match(r"^(.+)-report\.html$", filename)
    if m:
        return m.group(1).lower(), None
    return None, None


def period_label(period_str):
    """返回中英文友好的周期标签，如 '2026-W19 (本周)'。"""
    tp = detect_period_type(period_str)
    if tp is None:
        return period_str
    if tp == "weekly":
        return f"第{period_str[-2:]}周"
    if tp == "monthly":
        return f"{period_str[:4]}年{period_str[5:]}月"
    if tp == "quarterly":
        return f"{period_str[:4]}年{period_str[6]}季度"
    if tp == "annual":
        return f"{period_str}年"
    return period_str


def period_sort_key(period_str):
    """返回用于排序的元组 (type_rank, year, period_num)。"""
    tp = detect_period_type(period_str)
    if tp is None:
        return (9, 0, 0)
    type_rank = {"annual": 0, "quarterly": 1, "monthly": 2, "weekly": 3}
    m = re.match(r"^(\d{4})(?:-(?:W(\d+)|(\d+)|Q(\d+)))?$", period_str)
    if not m:
        return (type_rank.get(tp, 9), int(period_str[:4]) if period_str[:4].isdigit() else 0, 0)
    year = int(m.group(1) or 0)
    # group(2)=week, group(3)=month, group(4)=quarter
    num = int(m.group(2) or m.group(3) or m.group(4) or 0)
    return (type_rank.get(tp, 9), year, num)
