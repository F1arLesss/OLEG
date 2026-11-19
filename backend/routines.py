"""
О.Л.Е.Г. — Движок комплексных сценариев (Routines Engine)
Позволяет одной фразой запускать цепочки системных действий:
- «Игровой режим» (Дискорд + игра в Steam + буфер повторов OBS)
- «Режим отдыха / кино» (Тёмная тема + комфортная громкость + фоновая музыка)
- «Сводка дня / Начни день» (Время + погода + напоминания из базы + бодрый трек)
Поддерживает пользовательские сценарии из data/custom_routines.json.
"""

import json
import os
import tools
import reminders_manager
import memory_manager

ROUTINES_FILE = os.path.join(os.path.dirname(__file__), "data", "custom_routines.json")


def _load_custom_routines() -> dict:
    if os.path.exists(ROUTINES_FILE):
        try:
            with open(ROUTINES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def execute_routine(routine_name: str, target: str = "") -> str:
    """
    Выполняет комплексный сценарий (рутину).
    routine_name: "game" | "relax" | "morning" | пользовательский
    target: дополнительный параметр (например, название игры для game)
    """
    name = (routine_name or "").lower().strip()

    # 1. Игровой режим
    if name in ("game", "gaming", "игра", "игровой", "игровой режим"):
        results = []

        # Запуск Discord
        tools.open_program("Discord")
        results.append("Discord запущен")

        # Запуск игры или Steam
        game_to_launch = target.strip() if target else "RobRun"
        g_res = tools.launch_steam_game(game_to_launch)
        results.append(g_res)

        # Включение буфера повторов OBS
        obs_res = tools.obs_control("start_replay")
        results.append("буфер повторов OBS включен")

        return f"🎮 **Активирован игровой режим!**\n• {'; '.join(results)}.\nПриятной игры!"

    # 2. Режим отдыха / кино
    if name in ("relax", "movie", "отдых", "кино", "ночь", "режим отдыха"):
        results = []

        # Тёмная тема
        theme_res = tools.set_system_theme("dark")
        results.append("тёмная тема включена")

        # Комфортная громкость
        tools.set_volume(level=35)
        results.append("громкость системы 35%")

        # Спокойная музыка в Spotify
        spot_res = tools.play_spotify_music("Lofi Beats Chill")
        if "не настроен" not in spot_res.lower():
            results.append("включен Lofi плейлист в Spotify")

        return f"🌙 **Активирован режим отдыха:**\n• {'; '.join(results)}.\nОтличного отдыха!"

    # 3. Сводка дня / Доброе утро
    if name in ("morning", "daily", "утро", "доброе утро", "сводка", "начни день"):
        lines = ["☀️ **Добро пожаловать! Сводка на сегодня:**"]

        # Время
        time_res = tools.get_current_time()
        t_summary = time_res.get("summary") if isinstance(time_res, dict) else str(time_res)
        lines.append(f"• 🕒 {t_summary}")

        # Погода
        weather_res = tools.get_weather()
        if isinstance(weather_res, dict):
            w_summary = weather_res.get("summary") or weather_res.get("message") or f"{weather_res.get('city', 'Казань')}: {weather_res.get('temp', '')} ({weather_res.get('desc', '')})"
            lines.append(f"• 🌤️ {w_summary}")
        elif isinstance(weather_res, str) and "ошибка" not in weather_res.lower():
            lines.append(f"• 🌤️ {weather_res}")

        # Напоминания
        active_rems = reminders_manager.list_active_reminders()
        if active_rems:
            rem_texts = [f"{r['due_str']}: {r['text']}" for r in active_rems[:3]]
            lines.append("• 📌 **Ближайшие дела:** " + ", ".join(rem_texts))
        else:
            lines.append("• 📌 На сегодня активных напоминаний нет.")

        # Музыка
        spot_res = tools.control_spotify_playback("play")
        if "возобновлено" in spot_res.lower() or "играет" in spot_res.lower():
            lines.append("• 🎵 Музыка возобновлена.")

        return "\n".join(lines)

    # Проверка пользовательских сценариев из data/custom_routines.json
    custom = _load_custom_routines()
    if name in custom:
        c_item = custom[name]
        desc = c_item.get("description", "")
        actions = c_item.get("actions", [])
        executed_details = []

        for act in actions:
            tool = act.get("tool") or act.get("action") or ""
            args = act.get("args") or act.get("parameters") or {}
            try:
                if tool == "open_program":
                    p_name = args.get("app_name", "")
                    tools.open_program(p_name)
                    executed_details.append(f"запуск {p_name}")
                elif tool == "set_volume":
                    lvl = args.get("level", 50)
                    tools.set_volume(level=int(lvl))
                    executed_details.append(f"громкость {lvl}%")
                elif tool in ("play_spotify_music", "play_spotify"):
                    q = args.get("query", "")
                    tools.play_spotify_music(q)
                    executed_details.append(f"музыка «{q}»")
                elif tool == "set_system_theme":
                    th = args.get("theme", "dark")
                    tools.set_system_theme(th)
                    executed_details.append(f"тема {th}")
                elif tool == "launch_steam_game":
                    g = args.get("game_name", "")
                    tools.launch_steam_game(g)
                    executed_details.append(f"игра «{g}»")
                elif tool == "play_sound":
                    import sound_manager
                    st = args.get("sound_type", "notification")
                    sound_manager.play_sound(st)
                    executed_details.append(f"звук {st}")
            except Exception as e:
                print(f"[Routines] Ошибка шага {tool}: {e}")

        detail_str = f"\n• Выполнено: {'; '.join(executed_details)}." if executed_details else ""
        return f"⚡ **Выполнен пользовательский сценарий '{name}':** {desc}{detail_str}"

    return f"Сценарий '{routine_name}' не найден. Доступны базовые сценарии: 'game' (игровой), 'relax' (отдых), 'morning' (сводка дня)."
