"""
О.Л.Е.Г. — Менеджер напоминаний (Reminders Manager)
Поддерживает запланированные напоминания с сохранением на диск,
кешированием в памяти и нативными всплывающими Windows Toast-уведомлениями.
"""

import asyncio
import base64
import datetime
import json
import os
import re
import subprocess
import threading
import time
import uuid

try:
    import winsound
except ImportError:
    winsound = None

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
REMINDERS_FILE = os.path.join(DATA_DIR, "reminders.json")

_reminders_cache: list | None = None
_cache_lock = threading.Lock()


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(REMINDERS_FILE):
        try:
            with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _load_reminders() -> list:
    global _reminders_cache
    with _cache_lock:
        if _reminders_cache is not None:
            return _reminders_cache

        _ensure_data_dir()
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                _reminders_cache = json.load(f)
        except Exception:
            _reminders_cache = []
        return _reminders_cache


def _save_reminders(reminders: list):
    global _reminders_cache
    with _cache_lock:
        _reminders_cache = reminders
        _ensure_data_dir()
        tmp_file = REMINDERS_FILE + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(reminders, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, REMINDERS_FILE)
        except Exception as e:
            print(f"[Reminders] Ошибка сохранения: {e}")
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass


def show_toast(title: str, message: str, play_sound_type: str | None = "notification"):
    """
    Показывает нативное всплывающее Windows Toast-уведомление.
    Использует Base64-кодирование для 100% защиты от сбоев кавычек, спецсимволов и апострофов.
    """
    t_clean = (title or "О.Л.Е.Г.").strip()
    m_clean = (message or "").strip()

    t_b64 = base64.b64encode(t_clean.encode("utf-8")).decode("ascii")
    m_b64 = base64.b64encode(m_clean.encode("utf-8")).decode("ascii")

    ps_cmd = (
        f"$t = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{t_b64}')); "
        f"$m = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{m_b64}')); "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$textNodes = $template.GetElementsByTagName('text'); "
        "$textNodes.Item(0).AppendChild($template.CreateTextNode($t)) | Out-Null; "
        "$textNodes.Item(1).AppendChild($template.CreateTextNode($m)) | Out-Null; "
        "$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('O.L.E.G.'); "
        "$notification = [Windows.UI.Notifications.ToastNotification]::new($template); "
        "$notifier.Show($notification);"
    )
    try:
        subprocess.run(
            ["powershell", "-WindowStyle", "Hidden", "-NoProfile", "-Command", ps_cmd],
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"[Reminders] Ошибка вывода Toast: {e}")

    if play_sound_type:
        try:
            import sound_manager
            sound_manager.play_sound(play_sound_type)
        except Exception:
            if winsound:
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass


def parse_time_target(target_time_str: str) -> float | None:
    """Парсит строковое время (например '19:30', '14:00', 'в 9 утра') в unix timestamp."""
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

    match_h = re.search(r"в\s*(\d{1,2})\s*(?:ч|час|часов|утр|вечер)?", s)
    if match_h:
        hh = int(match_h.group(1))
        target_dt = now.replace(hour=hh, minute=0, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += datetime.timedelta(days=1)
        return target_dt.timestamp()

    return None


def add_reminder(text: str, delay_seconds: int = 0, target_time: str = "") -> dict:
    """Создает и регистрирует новое напоминание."""
    now_ts = time.time()
    due_ts = None

    if delay_seconds and delay_seconds > 0:
        due_ts = now_ts + delay_seconds
    elif target_time:
        due_ts = parse_time_target(target_time)

    if not due_ts:
        due_ts = now_ts + 900  # По умолчанию через 15 минут

    due_dt = datetime.datetime.fromtimestamp(due_ts)
    due_str = due_dt.strftime("%H:%M")
    if due_dt.date() > datetime.date.today():
        due_str = f"завтра в {due_str}"

    rem_id = "rem_" + uuid.uuid4().hex[:6]
    item = {
        "id": rem_id,
        "text": text.strip(),
        "created_at": now_ts,
        "due_at": due_ts,
        "due_str": due_str,
        "completed": False,
    }

    reminders = _load_reminders()
    reminders.append(item)
    _save_reminders(reminders)

    return {
        "success": True,
        "id": rem_id,
        "text": item["text"],
        "due_str": due_str,
        "message": f"Напоминание установлено на {due_str}: «{item['text']}».",
    }


def list_active_reminders() -> list:
    """Возвращает список активных напоминаний."""
    reminders = _load_reminders()
    active = [r for r in reminders if not r.get("completed")]
    active.sort(key=lambda x: x.get("due_at", 0))
    return active


def cancel_reminder(query: str = "") -> str:
    """Отменяет напоминание по ID или ключевым словам."""
    reminders = _load_reminders()
    if not reminders:
        return "У вас нет активных напоминаний."

    query_lower = (query or "").lower().strip()
    found = False
    cancelled_text = ""

    for r in reminders:
        if not r.get("completed"):
            if not query_lower or r["id"].lower() == query_lower or query_lower in r["text"].lower():
                r["completed"] = True
                found = True
                cancelled_text = r["text"]
                break

    if found:
        _save_reminders(reminders)
        return f"Напоминание «{cancelled_text}» отменено."
    return f"Напоминание по запросу '{query}' не найдено."


async def reminder_worker():
    """Фоновый воркер, проверяющий наступление времени напоминаний."""
    print("[Reminders] Фоновый мониторинг напоминаний запущен.")
    while True:
        try:
            now_ts = time.time()
            reminders = _load_reminders()
            changed = False

            for r in reminders:
                if not r.get("completed") and now_ts >= r.get("due_at", 0):
                    r["completed"] = True
                    changed = True

                    # Если напоминание устарело более чем на 2 часа (ПК был выключен), не спамим
                    if now_ts - r.get("due_at", 0) > 7200:
                        print(f"[Reminders] Пропущено устаревшее напоминание (офлайн): {r.get('text')}")
                        continue

                    msg = r.get("text", "Напоминание!")
                    print(f"[Reminders] Сработало напоминание: {msg}")
                    show_toast("О.Л.Е.Г. — Напоминание!", msg)

            if changed:
                _save_reminders(reminders)
        except Exception as e:
            print(f"[Reminders] Ошибка в воркере: {e}")

        await asyncio.sleep(3)
