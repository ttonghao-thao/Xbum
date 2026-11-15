from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from lunardate import LunarDate


def local_now(timezone):
    return datetime.now(ZoneInfo(timezone))


def lunar_date(year, month, day):
    try:
        value = LunarDate(year, month, day)
        return value.to_solar_date() if hasattr(value, "to_solar_date") else value.toSolarDate()
    except ValueError:
        return None


def festivals(day):
    result = []
    fixed = {(1, 1): "元旦", (5, 1): "劳动节", (6, 1): "儿童节", (10, 1): "国庆"}
    if (day.month, day.day) in fixed:
        result.append(fixed[(day.month, day.day)])
    for month, number, label in [(1, 1, "春节"), (1, 15, "元宵"), (5, 5, "端午"), (8, 15, "中秋")]:
        if lunar_date(day.year, month, number) == day:
            result.append(label)
    return result


def anniversary_projection(left, right):
    projected_dates = []
    for year in (right.year - 1, right.year, right.year + 1):
        if not 1 <= year <= 9999:
            continue
        try:
            projected = left.replace(year=year)
        except ValueError:  # February 29
            projected = date(year, 2, 28)
        projected_dates.append(projected)
    return min(projected_dates, key=lambda value: abs((value - right).days))


def day_distance(left, right):
    return abs((anniversary_projection(left, right) - right).days)


def parse_time(query, timezone, today=None):
    import re

    query = normalize_search_text(query)

    today = today or local_now(timezone).date()
    year = today.year - (1 if "去年" in query else 2 if "前年" in query else 0)
    match = re.search(r"(20\d{2})年", query)
    if match:
        year = int(match.group(1))
    for label, month, day in [("春节", 1, 1), ("元宵", 1, 15), ("端午", 5, 5), ("中秋", 8, 15)]:
        if label in query:
            center = lunar_date(year, month, day)
            if center:
                return center - timedelta(days=3), center + timedelta(days=7), label
    if "上个月" in query:
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, "上个月"
    if "这个月" in query or "本月" in query:
        start = today.replace(day=1)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1), "本月"
    if match or any(word in query for word in ["去年", "前年", "今年"]):
        return date(year, 1, 1), date(year, 12, 31), f"{year}年"
    return None, None, ""


def normalize_search_text(value):
    # whisper.cpp Chinese can emit Traditional Chinese. Normalize known query
    # vocabulary only; retain the original transcript and all user-facing text.
    for source, target in {
        "春節": "春节",
        "上個月": "上个月",
        "這個月": "这个月",
        "國慶": "国庆",
        "海邊": "海边",
    }.items():
        value = value.replace(source, target)
    return value


def is_quiet(config, timezone):
    value = local_now(timezone).strftime("%H:%M")
    start, end = config["quiet_start"], config["quiet_end"]
    if start == end:
        return False
    return (start <= value < end) if start < end else (value >= start or value < end)
