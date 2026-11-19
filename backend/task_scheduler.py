"""
О.Л.Е.Г. — Планировщик отложенных действий (Task Scheduler)
Позволяет запланировать выполнение ЛЮБОГО действия через N времени или в точное время:
- «через 1 минуту включи музыку»
- «через 10 минут выключи компьютер»
- «в 18:42 запусти доту»
- «через полчаса останови запись»
"""

import asyncio
import datetime
import json
import os
import re
import threading
import time
import uuid

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TASKS_FILE = os.path.join(DATA_DIR, "scheduled_tasks.json")

_tasks_cache: list | None = None
_cache_lock = threading.Lock()


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _load_tasks() -> list:
    global _tasks_cache
    with _cache_lock:
        if _tasks_cache is not None:
            return _tasks_cache

        _ensure_data_dir()
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                _tasks_cache = json.load(f)
        except Exception:
            _tasks_cache = []
        return _tasks_cache


def _save_tasks(tasks: list):
    global _tasks_cache
    with _cache_lock:
        _tasks_cache = tasks
        _ensure_data_dir()
        tmp_file = TASKS_FILE + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, TASKS_FILE)
        except Exception as e:
            print(f"[Scheduler] Ошибка сохранения задач: {e}")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass


def parse_schedule_time(target_time_str: str) -> float | None:
    """Парсит время в формате 'ЧЧ:ММ' или 'в 18:42' в unix timestamp."""
    s = target_time_str.strip().lower()
    now = datetime.datetime.now()

    match = re.search(r"(\d{1,2}):(\d{2})", s)
    if match:
        hh = int(match.group(1))
        mm = int(match.group(2))
        target_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += datetime.timedelta(days=1)
        return target_dt.timestamp()

    match_h = re.search(r"в\s*(\d{1,2})\s*(?:ч|час|часов)?", s)
    if match_h:
        hh = int(match_h.group(1))
        target_dt = now.replace(hour=hh, minute=0, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += datetime.timedelta(days=1)
        return target_dt.timestamp()

    return None


def schedule_task(
    description: str,
    tool_name: str,
    tool_args: dict | None = None,
    delay_seconds: int = 0,
    target_time: str = "",
) -> dict:
    """Регистрирует отложенную задачу."""
    now_ts = time.time()
    due_ts = None

    if delay_seconds and delay_seconds > 0:
        due_ts = now_ts + delay_seconds
    elif target_time:
        due_ts = parse_schedule_time(target_time)

    if not due_ts:
        due_ts = now_ts + 60  # По умолчанию через 1 минуту

    due_dt = datetime.datetime.fromtimestamp(due_ts)
    due_str = due_dt.strftime("%H:%M")
    if due_dt.date() > datetime.date.today():
        due_str = f"завтра в {due_str}"

    task_id = "task_" + uuid.uuid4().hex[:6]
    item = {
        "id": task_id,
        "description": description.strip() or f"Действие {tool_name}",
        "tool_name": tool_name.strip(),
        "tool_args": tool_args or {},
        "created_at": now_ts,
        "due_at": due_ts,
        "due_str": due_str,
        "status": "pending",
    }

    tasks = _load_tasks()
    tasks.append(item)
    _save_tasks(tasks)

    return {
        "success": True,
        "id": task_id,
        "description": item["description"],
        "due_str": due_str,
        "message": f"Запланировано на {due_str}: {item['description']}.",
    }


def list_scheduled_tasks() -> list:
    """Возвращает список активных запланированных действий."""
    tasks = _load_tasks()
    active = [t for t in tasks if t.get("status") == "pending"]
    active.sort(key=lambda x: x.get("due_at", 0))
    return active


def cancel_scheduled_task(query: str = "") -> str:
    """Отменяет запланированное действие по ID или ключевым словам."""
    tasks = _load_tasks()
    if not tasks:
        return "У вас нет активных запланированных действий."

    q_lower = (query or "").lower().strip()
    found = False
    cancelled_desc = ""

    aliases = {
        "дота": "dota", "dota": "дота",
        "кс": "cs", "cs": "кс",
        "комп": "выключ", "компьютер": "shutdown",
    }
    alt_q = aliases.get(q_lower, q_lower)

    for t in tasks:
        if t.get("status") == "pending":
            desc_lower = t["description"].lower()
            tool_lower = t["tool_name"].lower()
            args_str = json.dumps(t.get("tool_args", {}), ensure_ascii=False).lower()

            match = (
                not q_lower
                or t["id"].lower() == q_lower
                or q_lower in desc_lower
                or alt_q in desc_lower
                or q_lower in tool_lower
                or q_lower in args_str
                or alt_q in args_str
            )

            if match:
                t["status"] = "cancelled"
                found = True
                cancelled_desc = t["description"]
                break

    if found:
        _save_tasks(tasks)
        return f"Запланированное действие «{cancelled_desc}» успешно отменено."
    return f"Запланированное действие по запросу '{query}' не найдено."


async def _execute_task_async(task: dict):
    """Исполнение одного запланированного действия с выводом уведомления."""
    desc = task.get("description", "Действие")
    tool = task.get("tool_name", "")
    args = task.get("tool_args", {})

    print(f"[Scheduler] ⏰ Выполнение запланированного действия: {desc} ({tool})")
    try:
        import brain
        await brain._execute_single_tool(tool, args)
    except Exception as exec_err:
        print(f"[Scheduler] Ошибка выполнения {tool}: {exec_err}")

    try:
        import reminders_manager
        reminders_manager.show_toast(
            "О.Л.Е.Г. ⚡ Запланированное действие",
            f"Выполнено: {desc}",
            play_sound_type="notification"
        )
    except Exception:
        pass


async def scheduler_worker():
    """Фоновый воркер, выполняющий запланированные действия при наступлении времени."""
    print("[Scheduler] Фоновый планировщик отложенных задач запущен.")
    while True:
        try:
            now_ts = time.time()
            tasks = _load_tasks()
            changed = False

            for t in tasks:
                if t.get("status") == "pending" and now_ts >= t.get("due_at", 0):
                    changed = True
                    # Защита от запуска старых действий при старте ПК (если задача просрочена более чем на 15 мин)
                    if now_ts - t.get("due_at", 0) > 900:
                        t["status"] = "expired"
                        print(f"[Scheduler] Задача просрочена и отменена (ПК был офлайн): {t.get('description')}")
                        continue

                    t["status"] = "executed"
                    # Запускаем выполнение в фоне, чтобы не блокировать цикл планировщика
                    asyncio.create_task(_execute_task_async(t))

            if changed:
                _save_tasks(tasks)
        except Exception as e:
            print(f"[Scheduler] Ошибка в воркере планировщика: {e}")

        await asyncio.sleep(2)
