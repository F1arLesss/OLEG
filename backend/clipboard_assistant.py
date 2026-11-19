"""
О.Л.Е.Г. — Интеллектуальный помощник по буферу обмена (Smart Clipboard)
Обработка скопированного текста: перевод, краткая выжимка (TL;DR), исправление ошибок,
объяснение кода и терминов, а также история буфера обмена (Smart Clipboard History).
"""

import asyncio
import datetime
import json
import os
import threading
import time
import pyperclip
import brain

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(DATA_DIR, "clipboard_history.json")

_clipboard_history: list = []
_history_lock = threading.Lock()
_last_seen_clip = ""


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _load_history():
    global _clipboard_history
    with _history_lock:
        if _clipboard_history:
            return _clipboard_history
        _ensure_dir()
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                _clipboard_history = json.load(f)
        except Exception:
            _clipboard_history = []
        return _clipboard_history


def _save_history():
    _ensure_dir()
    tmp = HISTORY_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_clipboard_history[:50], f, ensure_ascii=False, indent=2)
        os.replace(tmp, HISTORY_FILE)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def record_clip(text: str):
    """Сохраняет уникальный текстовый фрагмент в историю буфера обмена."""
    global _last_seen_clip
    t_clean = text.strip()
    if not t_clean or len(t_clean) < 2 or t_clean == _last_seen_clip:
        return

    _last_seen_clip = t_clean
    _load_history()

    now_ts = time.time()
    dt_str = datetime.datetime.fromtimestamp(now_ts).strftime("%H:%M:%S")

    with _history_lock:
        # Убираем предыдущую копию этого же текста
        _clipboard_history[:] = [c for c in _clipboard_history if c.get("text", "").strip() != t_clean]
        preview = t_clean[:120].replace("\n", " ") + ("..." if len(t_clean) > 120 else "")
        _clipboard_history.insert(0, {
            "timestamp": now_ts,
            "time_str": dt_str,
            "preview": preview,
            "length": len(t_clean),
            "text": t_clean,
        })
        if len(_clipboard_history) > 50:
            _clipboard_history.pop()

    _save_history()


def get_clipboard_raw_items(limit: int = 20, query: str = "") -> list[dict]:
    """Возвращает список словарей последних записей буфера."""
    history = _load_history()
    if not history:
        return []
    filtered = history
    if query:
        q_low = query.lower().strip()
        filtered = [c for c in history if q_low in c.get("text", "").lower()]
    return filtered[:limit]


def get_clipboard_history(limit: int = 5, query: str = "") -> str:
    """Возвращает форматированный список последних скопированных элементов."""
    history = _load_history()
    if not history:
        return "История буфера обмена пуста."

    filtered = history
    if query:
        q_low = query.lower().strip()
        filtered = [c for c in history if q_low in c.get("text", "").lower()]

    if not filtered:
        return f"В истории буфера обмена не найдено совпадений по запросу «{query}»."

    lines = [f"📋 **История буфера обмена ({len(filtered[:limit])}):**"]
    for idx, c in enumerate(filtered[:limit], 1):
        lines.append(f"{idx}. `[{c.get('time_str', '')}]` {c.get('preview', '')}")

    return "\n".join(lines)


async def clipboard_monitor_worker():
    """Фоновый воркер, отслеживающий новые фрагменты в буфере обмена."""
    global _last_seen_clip
    try:
        _last_seen_clip = (pyperclip.paste() or "").strip()
        if _last_seen_clip:
            record_clip(_last_seen_clip)
    except Exception:
        pass

    while True:
        try:
            txt = pyperclip.paste()
            if txt and txt.strip() and txt.strip() != _last_seen_clip:
                record_clip(txt)
        except Exception:
            pass
        await asyncio.sleep(2)


async def process_clipboard(
    action: str = "translate",
    target_lang: str = "русский",
    custom_instruction: str = "",
    copy_to_clipboard: bool = False,
) -> str:
    """
    Интеллектуальная обработка текста из буфера обмена.
    action: "translate", "summarize", "fix_grammar", "explain", "format_code", "history", "custom"
    """
    act_low = action.lower().strip()
    if act_low in ("history", "история", "список", "записи"):
        return get_clipboard_history(limit=7, query=custom_instruction)

    try:
        text = pyperclip.paste()
    except Exception as e:
        return f"Не удалось прочитать буфер обмена: {e}"

    if not text or not text.strip():
        return "В буфере обмена сейчас пусто. Выделите нужный текст в любой программе, нажмите Ctrl+C и попросите снова."

    text_sample = text.strip()
    record_clip(text_sample)

    if len(text_sample) > 8000:
        text_sample = text_sample[:8000] + "\n...[текст обрезан]"

    prompts = {
        "translate": f"Переведи следующий текст на {target_lang}. Выведи только качественный перевод без вводных фраз:\n\n{text_sample}",
        "summarize": f"Сделай краткую, структурированную выжимку (TL;DR) главных мыслей следующего текста на русском языке:\n\n{text_sample}",
        "fix_grammar": f"Исправь любые орфографические, пунктуационные и стилистические ошибки в следующем тексте. Сохраняй исходный смысл. Выведи только исправленный текст:\n\n{text_sample}",
        "explain": f"Объясни простыми и понятными словами суть следующего фрагмента (ошибки, кода или термина) на русском языке:\n\n{text_sample}",
        "format_code": f"Красиво отформатируй следующий фрагмент кода, исправь очевидные синтаксические огрехи и оформи в блок кода:\n\n{text_sample}",
    }

    if custom_instruction:
        task_prompt = f"Задача: {custom_instruction}\n\nТекст из буфера обмена пользователя:\n{text_sample}"
    else:
        task_prompt = prompts.get(act_low, prompts["translate"])

    client = brain._get_client()
    try:
        resp = await brain._safe_chat_completion(
            client,
            model=brain.MODEL,
            messages=[
                {"role": "system", "content": "Ты высокоточный ИИ-редактор текста и переводчик. Выполняй задачу быстро, качественно и лаконично."},
                {"role": "user", "content": task_prompt}
            ],
            temperature=0.2,
            max_tokens=brain.MAX_TOKENS,
        )
        result = resp.choices[0].message.content or "Готово."
    except Exception as e:
        return f"Ошибка при обработке текста нейросетью: {e}"

    if copy_to_clipboard:
        try:
            pyperclip.copy(result)
            return f"{result}\n\n📋 *Результат скопирован обратно в буфер обмена.*"
        except Exception:
            pass

    return result
