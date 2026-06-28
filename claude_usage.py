from __future__ import annotations

GREEN = "#34c759"
ORANGE = "#ff9500"
RED = "#ff3b30"


def severity_color(pct: float) -> str:
    if pct > 90:
        return RED
    if pct >= 70:
        return ORANGE
    return GREEN


def format_duration(seconds: float, compact: bool) -> str:
    total_minutes = int(seconds // 60)
    if total_minutes <= 0:
        return "now"
    hours, minutes = divmod(total_minutes, 60)
    if hours == 0:
        return f"{minutes}m"
    sep = "" if compact else " "
    return f"{hours}h{sep}{minutes}m"
