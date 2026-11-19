"""
О.Л.Е.Г. — Менеджер звуков и рингтонов (Sound Manager)
Четкое разделение категорий:
- notifications/ (звуки всплывающих напоминаний и уведомлений)
- alarms/ (звуки будильников и таймеров)
Предотвращает одновременное наложение звуков (каждый канал управляется изолированно).
"""

import ctypes
import json
import os
import threading
import time

SOUNDS_DIR = os.path.join(os.path.dirname(__file__), "sounds")
NOTIFICATIONS_DIR = os.path.join(SOUNDS_DIR, "notifications")
ALARMS_DIR = os.path.join(SOUNDS_DIR, "alarms")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CONFIG_FILE = os.path.join(DATA_DIR, "sounds_config.json")

DEFAULT_CONFIG = {
    "notification_sound": "уведомление.mp3",
    "alarm_sound": "будильник.mp3",
}

_channel_aliases = {
    "notification": set(),
    "alarm": set(),
}
_lock = threading.Lock()


def _ensure_dirs():
    os.makedirs(NOTIFICATIONS_DIR, exist_ok=True)
    os.makedirs(ALARMS_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def get_sounds_config() -> dict:
    _ensure_dirs()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return {**DEFAULT_CONFIG, **cfg}
    except Exception:
        return DEFAULT_CONFIG.copy()


def save_sounds_config(cfg: dict):
    _ensure_dirs()
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SoundManager] Ошибка сохранения конфига: {e}")


def list_available_sounds() -> dict:
    """Возвращает сгруппированный список звуков по категориям (notifications и alarms)."""
    _ensure_dirs()
    supported_exts = (".mp3", ".wav", ".ogg", ".m4a", ".flac")

    def _scan_folder(folder_path: str, category: str):
        items = []
        if os.path.exists(folder_path):
            for fname in os.listdir(folder_path):
                base, ext = os.path.splitext(fname)
                if ext.lower() in supported_exts:
                    fpath = os.path.join(folder_path, fname)
                    items.append({
                        "filename": fname,
                        "title": base.replace("_", " ").title(),
                        "category": category,
                        "size_bytes": os.path.getsize(fpath),
                        "path": fpath,
                    })
        return items

    notifications = _scan_folder(NOTIFICATIONS_DIR, "notification")
    alarms = _scan_folder(ALARMS_DIR, "alarm")

    return {
        "notifications": notifications,
        "alarms": alarms,
        "all": notifications + alarms,
    }


def stop_channel(channel: str = "all"):
    """Останавливает звуки конкретного канала ('notification', 'alarm' или 'all')."""
    try:
        winmm = ctypes.windll.winmm
        with _lock:
            targets = []
            if channel in ("all", "both"):
                targets.extend(list(_channel_aliases["notification"]))
                targets.extend(list(_channel_aliases["alarm"]))
                _channel_aliases["notification"].clear()
                _channel_aliases["alarm"].clear()
            elif channel in _channel_aliases:
                targets.extend(list(_channel_aliases[channel]))
                _channel_aliases[channel].clear()

            for alias in targets:
                try:
                    winmm.mciSendStringW(f'stop {alias}', None, 0, None)
                    winmm.mciSendStringW(f'close {alias}', None, 0, None)
                except Exception:
                    pass
    except Exception:
        pass


def _play_thread(sound_path: str, channel: str, duration_sec: float | None = None, delete_on_finish: bool = False):
    """Фоновое воспроизведение через Windows Multimedia API с привязкой к каналу и контролем завершения."""
    winmm = ctypes.windll.winmm
    alias = f"oleg_{channel}_{int(time.time() * 1000)}"

    with _lock:
        _channel_aliases[channel].add(alias)

    try:
        abs_path = os.path.abspath(sound_path)
        cmd_open = f'open "{abs_path}" type mpegvideo alias {alias}'
        if winmm.mciSendStringW(cmd_open, None, 0, None) != 0:
            cmd_open = f'open "{abs_path}" alias {alias}'
            winmm.mciSendStringW(cmd_open, None, 0, None)

        # Считываем точную аппаратную длительность аудиофайла от Windows MCI в миллисекундах
        winmm.mciSendStringW(f'set {alias} time format milliseconds', None, 0, None)
        buf_len = ctypes.create_unicode_buffer(64)
        exact_dur_sec = None
        if winmm.mciSendStringW(f'status {alias} length', buf_len, 64, None) == 0:
            val = buf_len.value.strip()
            if val.isdigit() and int(val) > 0:
                exact_dur_sec = int(val) / 1000.0

        winmm.mciSendStringW(f'play {alias} from 0', None, 0, None)

        # Вычисляем максимальное время ожидания:
        # Если передан duration_sec (например, 7 сек для будильника) — используем его.
        # Если нет — проигрываем аудиофайл полностью по точной длительности (+0.5 сек запаса, чтобы не обрезать эхо/затухание).
        if duration_sec is not None and duration_sec > 0:
            max_play_sec = duration_sec
        elif exact_dur_sec is not None:
            max_play_sec = exact_dur_sec + 0.5
        else:
            max_play_sec = 60.0

        start_t = time.time()
        buf_mode = ctypes.create_unicode_buffer(64)
        while time.time() - start_t < max_play_sec:
            with _lock:
                if alias not in _channel_aliases[channel]:
                    # Принудительно остановлено извне (например, stop_channel)
                    break
            winmm.mciSendStringW(f'status {alias} mode', buf_mode, 64, None)
            if buf_mode.value != "playing":
                # Трек доиграл до самого конца
                break
            time.sleep(0.05)

        time.sleep(0.08)
        winmm.mciSendStringW(f'stop {alias}', None, 0, None)
        winmm.mciSendStringW(f'close {alias}', None, 0, None)
    except Exception as e:
        print(f"[SoundManager] Ошибка воспроизведения ({channel}): {e}")
    finally:
        with _lock:
            _channel_aliases[channel].discard(alias)
        if delete_on_finish and os.path.exists(sound_path):
            try:
                os.remove(sound_path)
            except Exception:
                pass


def play_sound(
    sound_type: str = "notification",
    custom_filename: str = "",
    duration_sec: float | None = None,
    delete_on_finish: bool = False,
) -> bool:
    """
    Воспроизводит звук из соответствующей категории (notifications/ или alarms/).
    sound_type: 'notification' | 'alarm' | 'timer'
    """
    _ensure_dirs()
    cfg = get_sounds_config()
    st = sound_type.lower().strip()

    is_alarm = any(k in st for k in ["alarm", "timer", "будильник", "таймер"])
    channel = "alarm" if is_alarm else "notification"

    # Защита от наложения:
    # 1. Если играет будильник — глушим предыдущий звук будильника и любые мелкие уведомления
    if is_alarm:
        stop_channel("all")
        default_dur = 7.0
        folder = ALARMS_DIR
        fname = custom_filename or cfg.get("alarm_sound", "будильник.mp3")
    else:
        # 2. Если уже звучит активный будильник — не перебиваем его уведомлением
        with _lock:
            if len(_channel_aliases["alarm"]) > 0:
                print("[SoundManager] Будильник уже звенит — уведомление пропущено во избежание наложения.")
                return False
        stop_channel("notification")
        default_dur = None  # Для уведомлений и речи воспроизводим полностью по длине файла
        folder = NOTIFICATIONS_DIR
        fname = custom_filename or cfg.get("notification_sound", "уведомление.mp3")

    dur = duration_sec if duration_sec is not None else default_dur

    sound_path = os.path.join(folder, fname)
    if not os.path.exists(sound_path):
        # Fallback на другую папку или корень sounds/
        alt_path = os.path.join(SOUNDS_DIR, fname)
        if os.path.exists(alt_path):
            sound_path = alt_path
        elif os.path.exists(custom_filename):
            sound_path = custom_filename
        else:
            print(f"[SoundManager] Аудиофайл не найден: {sound_path}")
            return False

    t = threading.Thread(
        target=_play_thread,
        args=(sound_path, channel, dur, delete_on_finish),
        daemon=True,
    )
    t.start()
    return True
