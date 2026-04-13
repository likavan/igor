from datetime import datetime
from config import TZ
from db import (
    get_triage_tasks, get_triage_task, update_triage_score, score_triage_task,
    get_triage_column_names,
)

COLS = {name: i for i, name in enumerate(get_triage_column_names())}

# Thresholds
BACKLOG_MAX = 2
RADAR_MAX = 5
THIS_WEEK_MIN = 6
ALERT_MIN = 8


def _time_penalty(minutes):
    if minutes is None or minutes < 30:
        return 0
    if minutes <= 120:
        return 1
    if minutes <= 240:
        return 2
    if minutes <= 480:
        return 3
    return 4


def _deadline_bonus(due_date_str):
    if not due_date_str:
        return 0
    try:
        due = datetime.strptime(due_date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    except Exception:
        return 0
    days_left = (due - datetime.now(TZ)).days
    if days_left <= 1:
        return 4
    if days_left <= 3:
        return 3
    if days_left <= 7:
        return 2
    if days_left <= 14:
        return 1
    return 0


def _waiting_bonus(waiting):
    if waiting == "client":
        return 3
    if waiting == "internal":
        return 1
    return 0


def calculate_score(value, time_estimate_min, created_at_str, due_date_str=None, waiting="none"):
    if value is None:
        return None
    try:
        created = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except Exception:
        created = datetime.now(TZ)
    weeks = (datetime.now(TZ) - created).days / 7
    decay_bonus = weeks * 0.5
    penalty = _time_penalty(time_estimate_min)
    deadline = _deadline_bonus(due_date_str)
    blocker = _waiting_bonus(waiting)
    return round((value * 2 - penalty) + decay_bonus + deadline + blocker, 2)


def recalculate_and_save(task_id):
    task = get_triage_task(task_id)
    if not task:
        return None
    value = task[COLS["value"]]
    time_est = task[COLS["time_estimate"]]
    created = task[COLS["created_at"]]
    tier = task[COLS["tier"]]
    due_date = task[COLS["due_date"]]
    waiting = task[COLS["waiting"]] or "none"
    if tier == "forced":
        update_triage_score(task_id, 999)
        return 999
    score = calculate_score(value, time_est, created, due_date, waiting)
    if score is not None:
        update_triage_score(task_id, score)
    return score


def score_and_recalc(task_id, value):
    score_triage_task(task_id, value)
    return recalculate_and_save(task_id)


def get_score_label(score):
    if score is None:
        return "unscored"
    if score >= ALERT_MIN:
        return "URGENT"
    if score >= THIS_WEEK_MIN:
        return "tento tyden"
    if score > BACKLOG_MAX:
        return "radar"
    return "backlog"


def get_score_icon(score):
    if score is None:
        return "❓"
    if score >= ALERT_MIN:
        return "🔴"
    if score >= THIS_WEEK_MIN:
        return "🟠"
    if score > BACKLOG_MAX:
        return "🟡"
    return "⚪"


def get_displacement_report(new_task_time_est):
    """Show what gets pushed out when a forced task is added."""
    tasks = get_triage_tasks(only_open=True)
    this_week = []
    for t in tasks:
        score = t[COLS["priority_score"]]
        tier = t[COLS["tier"]]
        if tier == "forced" or score is None:
            continue
        if score >= THIS_WEEK_MIN:
            this_week.append(t)
    if not this_week:
        return None
    # Sort by score ascending — lowest score gets displaced first
    this_week.sort(key=lambda t: t[COLS["priority_score"]] or 0)
    displaced = []
    remaining_time = new_task_time_est or 120  # default 2h
    for t in this_week:
        t_time = t[COLS["time_estimate"]] or 60
        displaced.append(t)
        remaining_time -= t_time
        if remaining_time <= 0:
            break
    return displaced


def format_triage_task(task):
    tid = task[COLS["id"]]
    title = task[COLS["title"]]
    tier = task[COLS["tier"]]
    score = task[COLS["priority_score"]]
    value = task[COLS["value"]]
    time_est = task[COLS["time_estimate"]]
    source = task[COLS["source"]]
    icon = get_score_icon(score)
    tier_badge = ""
    if tier == "forced":
        tier_badge = " [FORCED]"
    elif tier == "negotiable":
        tier_badge = " [NEG]"
    time_str = ""
    if time_est:
        if time_est < 60:
            time_str = f"{time_est}m"
        else:
            time_str = f"{time_est // 60}h"
            if time_est % 60:
                time_str += f"{time_est % 60}m"
    score_str = f"{score:.1f}" if score is not None else "?"
    parts = [f"{icon} <b>{title}</b>{tier_badge}"]
    due_date = task[COLS["due_date"]]
    waiting = task[COLS["waiting"]] or "none"
    details = []
    if value is not None:
        details.append(f"val:{value}")
    if time_str:
        details.append(time_str)
    if due_date:
        details.append(f"dl:{due_date}")
    if waiting != "none":
        details.append(f"wait:{waiting}")
    details.append(f"score:{score_str}")
    details.append(f"src:{source}")
    parts.append(f"<i>({', '.join(details)}, id:{tid})</i>")
    return " ".join(parts)


def get_top_tasks(min_score=THIS_WEEK_MIN, limit=3):
    tasks = get_triage_tasks(only_open=True)
    result = []
    for t in tasks:
        score = t[COLS["priority_score"]]
        if score is not None and score >= min_score:
            result.append(t)
    result.sort(key=lambda t: t[COLS["priority_score"]] or 0, reverse=True)
    return result[:limit]


def get_alert_tasks():
    tasks = get_triage_tasks(only_open=True)
    return [t for t in tasks if (t[COLS["priority_score"]] or 0) >= ALERT_MIN]
