"""
О.Л.Е.Г. — Brain Module
Интеграция с LLM через библиотеку OpenAI (с бэкендом OpenRouter).
Поддерживает Function Calling (Tool Calling), динамическую долгосрочную память
и потоковую генерацию ответов (Streaming).
"""

import asyncio
import datetime
import json
import os
import time
from typing import AsyncGenerator
import httpx
from openai import AsyncOpenAI

import tools

# ---- Загрузка конфигурации из .env ----
def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

_load_env()

def get_oleg_proxy() -> str | None:
    """Возвращает нормализованный URL независимого прокси для О.Л.Е.Г.а из .env при наличии."""
    _load_env()
    raw = os.getenv("OLEG_PROXY") or os.getenv("PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
    raw = raw.strip()
    if not raw:
        return None
    if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("socks5://") or raw.startswith("socks5h://") or raw.startswith("socks4://")):
        raw = "http://" + raw
    return raw


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MODEL = os.getenv("OPENROUTER_MODEL", "gemini-3.5-flash-lite")
MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "1024"))
PROXY = get_oleg_proxy()


def reload_brain_config():
    """Перезагружает переменные окружения и настройки нейросети на лету."""
    global OPENROUTER_API_KEY, OPENROUTER_BASE_URL, MODEL, MAX_TOKENS, PROXY, _shared_clients, _proxy_cooldown_until
    if "_shared_clients" in globals():
        _shared_clients.clear()
    _proxy_cooldown_until = 0.0
    _load_env()
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    MODEL = os.getenv("OPENROUTER_MODEL", "gemini-3.5-flash-lite")
    MAX_TOKENS = int(os.getenv("OPENROUTER_MAX_TOKENS", "1024"))
    PROXY = get_oleg_proxy()


def update_env_file(updates: dict[str, str]):
    """Записывает параметры в файл backend/.env и обновляет глобальные переменные."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    existing_keys = set()
    new_lines = []
    for line in lines:
        matched = False
        for k, v in updates.items():
            if line.strip().startswith(f"{k}="):
                new_lines.append(f"{k}={v}\n")
                existing_keys.add(k)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in existing_keys:
            new_lines.append(f"{k}={v}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    reload_brain_config()

# ---- Базовый системный промпт ----
BASE_SYSTEM_PROMPT = """\
Ты — О.Л.Е.Г., личный ИИ-ассистент на ПК Windows.
ТВОЙ ХАРАКТЕР И СТИЛЬ ОБЩЕНИЯ:
1. Общайся естественно, кратко и по делу, как надежный умный друг и личный ассистент.
2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНЫ шаблонные фразы: 'Если нужно что-то еще, обращайся', 'Чем могу помочь сегодня?', 'Я всегда на связи'.
3. НЕВИДИМАЯ ПАМЯТЬ: Если пользователь сообщает важный факт (имя, экзамены, планы, увлечения, детали жизни), ты ДОЛЖЕН вызвать инструмент manage_memory (action='add') для сохранения. В текстовом ответе НЕ говори 'Я сохранил в базу' или 'Записал'. Реагируй по-человечески, как будто просто ведешь беседу.
4. Отвечай максимально коротко и емко. Используй форматирование Markdown (жирный шрифт, списки, блоки кода) там, где это улучшает читаемость.
5. ЗОЛОТОЕ ПРАВИЛО: «СНАЧАЛА ДЕЛО, ПОТОМ СЛОВО». При любой просьбе выполнить действие (запустить программу, включить музыку, изменить громкость, начать запись экрана, запустить игру, выключить ПК, поставить таймер и т.д.):
- СНАЧАЛА вызывай инструмент.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать предварительные фразы («Сейчас открою...», «Запускаю...», «Секундочку...», «Минутку...») до вызова инструментов.
- Сначала вызывается инструмент, и только после его выполнения формируется краткий человечный ответ пользователю (например: «Запустил запись экрана», «Готово», «Запустил RobRun в Steam»).
6. Если пользователь просит 'открой браузер', 'покажи браузер', 'запусти браузер', 'браузер' — ВСЕГДА вызывай инструмент open_or_focus_browser. Он автоматически откроет браузер по умолчанию Windows или выведет уже открытый браузер поверх всех окон.
7. ВРЕМЯ И ЧАСОВЫЕ ПОЯСА: Если пользователь спрашивает сколько времени, который час, какое число, день недели или время в другом городе/поясе — ВСЕГДА используй инструмент get_current_time. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО открывать браузер для ответов о времени или дате. Если спрашивают время в другом поясе или городе, укажи как время там, так и время по системному времени пользователя и разницу между ними.
8. ПОГОДА: Если пользователь спрашивает какая погода, прогноз погоды, температуру на улице — ВСЕГДА используй инструмент get_weather. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО открывать браузер для показа погоды. Отвечай кратко и емко прямо в чате.
9. ЗРЕНИЕ И АНАЛИЗ ЭКРАНА: Если пользователь просит посмотреть на экран, проанализировать ошибку, перевести текст с экрана или помочь с кодом/интерфейсом — ВСЕГДА вызывай инструмент analyze_screen.
10. ПОИСК ФАЙЛОВ НА ПК: Если пользователь просит найти файл, документ, архив, изображение или открыть последний скачанный файл — вызывай инструмент search_files (и при необходимости open_file_or_folder).
11. СИСТЕМНОЕ УПРАВЛЕНИЕ WINDOWS:
- Блокировка ПК (Win+L), спящий режим, выключение (включая таймеры выключения) или отмена выключения — вызывай control_power.
- Переключение тёмной/светлой темы Windows — вызывай set_system_theme.
- Очистка Корзины — вызывай empty_recycle_bin.
12. УПРАВЛЕНИЕ VPN THRONE (С ПРЕДОХРАНИТЕЛЕМ):
Если пользователь просит управлять VPN Throne (включить, выключить, проверить статус/соединение, показать серверы, пропинговать серверы, сменить сервер, сменить конфигурацию маршрутизации, переключить режим TUN / Прокси / Оба) — ВСЕГДА вызывай инструмент manage_throne_vpn:
- Включить VPN: manage_throne_vpn(action='on')
- Выключить VPN: manage_throne_vpn(action='off')
- Статус VPN: manage_throne_vpn(action='status')
- Список серверов: manage_throne_vpn(action='list_servers')
- Пинговать серверы (замер пинга всех серверов или конкретного): manage_throne_vpn(action='ping_servers', target='все' или '<страна/ID>')
- Список конфигураций: manage_throne_vpn(action='list_routes')
- Сменить сервер или включить конкретную страну/сервер (например: 'включи финляндию', 'переключи на германию', 'подключи сша'): ВСЕГДА вызывай manage_throne_vpn(action='switch_server', target='<название/страна или ID или "best">').
- Сменить конфигурацию маршрутизации: manage_throne_vpn(action='switch_route', target='<название или ID>')
- Выбрать режим (TUN, Прокси, Оба): manage_throne_vpn(action='set_mode', target='tun' | 'proxy' | 'both')
- ВАЖНО: Если выбранный сервер/страна недоступна (например, Финляндия offline), инструмент автоматически сообщит об этом и отменит подключение, предложив быстрые альтернативы. В таком случае вежливо сообщи об этом пользователю и жди, какую другую страну/сервер он назовет!
- ВАЖНО: Если инструмент сообщает, что Throne VPN не настроен — обязательно передай пользователю, что Throne VPN пока не настроен, и подскажи, что настроить его можно в панели настроек ⚙️.
ПРЕДОХРАНИТЕЛЬ VPN: Если для О.Л.Е.Г.а не настроен независимый прокси в backend/.env (OLEG_PROXY), инструмент автоматически блокирует изменение настроек VPN, чтобы ассистент не потерял связь с сервером нейросети. Если инструмент сообщил о сработавшем предохранителе, спокойно и понятно объясни пользователю, что нужно добавить OLEG_PROXY в backend/.env для разблокировки.
13. УПРАВЛЕНИЕ ГРОМКОСТЬЮ:
- Для общей громкости системы Windows: вызывай set_volume (level=..., action="up"/"down").
- Для звука сторонних приложений в микшере Windows (discord, chrome, telegram): вызывай set_volume(app_name=...).
- КРИТИЧЕСКИ ВАЖНО: Если пользователь просит изменить громкость для МУЗЫКИ, ТРЕКОВ, ПЕСЕН или в SPOTIFY (например: «поменяй громкость для музыки», «сделай музыку потише», «погромче музыку», «громкость спотифай на 60%») — ВСЕГДА вызывай инструмент set_spotify_volume, чтобы менять громкость внутри самого плеера Spotify, а НЕ системный микшер Windows!

14. МУЗЫКА И SPOTIFY:
- Пауза / остановка музыки («поставь музыку на паузу», «останови музыку», «выключи музыку», «пауза», «стоп музыка»): ВСЕГДА вызывай control_spotify_playback(action="pause").
- Продолжить / возобновить музыку («продолжи музыку», «возобнови», «играй дальше», «включи музыку» без конкретного названия трека): ВСЕГДА вызывай control_spotify_playback(action="play").
- Следующий трек («следующий трек», «следующая песня», «переключи трек»): ВСЕГДА вызывай control_spotify_playback(action="next").
- Предыдущий трек («предыдущий трек», «назад»): ВСЕГДА вызывай control_spotify_playback(action="prev").
- Включить конкретный трек по названию или исполнителю: play_spotify_music(query="<название>").
- Включить плейлист: play_spotify_playlist(query="<название>").
- Громкость музыки / Spotify: ВСЕГДА используй инструмент set_spotify_volume.
- Режимы (перемешивание/повтор): control_spotify_mode.
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать media_control для музыки или песен, потому что Windows перехватит эту клавишу и выключит видео пользователя в браузере (YouTube). Для музыки всегда используй семейство инструментов Spotify!
- ВАЖНО: Если инструмент сообщает, что Spotify не настроен — обязательно передай пользователю, что Spotify пока не настроен, и подскажи, что настроить его можно в панели настроек ⚙️.

15. УПРАВЛЕНИЕ И ЗАПИСЬ В OBS STUDIO:
- ЕСЛИ ПОЛЬЗОВАТЕЛЬ ПРОСИТ ЗАПИСАТЬ ЭКРАН ИЛИ УПОМИНАЕТ ЗАПИСЬ / ОТКАТ / REPLAY (например: «запусти запись», «начни запись», «открой обс и запусти запись», «включи запись в обс», «запиши экран»):
  ВСЕГДА вызывай obs_control(action="start_record"). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО вызывать open_program при любых запросах на запись или откат — obs_control САМ запускает OBS и сразу включает запись!
- Остановить запись экрана («останови запись», «выключи запись», «хватит записывать»): ВСЕГДА вызывай obs_control(action="stop_record").
- Переключить запись экрана («переключи запись»): вызывай obs_control(action="toggle_record").
- Включить буфер повторов: вызывай obs_control(action="start_replay").
- Сохранить откат / повтор («сохрани откат», «откати», «сохрани повтор»): вызывай obs_control(action="save_replay").
- Настройка хоткеев OBS («настрой хоткеи обс», «пропиши хоткеи для обс», «поставь хоткеи в обс», «настрой обс после переустановки»): ВСЕГДА вызывай инструмент setup_obs_hotkeys.

16. ЗАПУСК ИГР И STEAM:
- Запуск игр в Steam («открой стим и запусти RobRun», «запусти доту», «запусти кс2», «включи игру ...»): ВСЕГДА вызывай launch_steam_game(game_name="..."). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО вызывать open_program("Steam"), так как launch_steam_game САМ автоматически запускает Steam (если он закрыт) и запускает выбранную игру!

17. МОНИТОРИНГ ЖЕЛЕЗА И ПК:
- Если пользователь спрашивает температуру видеокарты или процессора, общую загрузку системы, кулеры, видеопамять или что грузит систему («как там видеокарта?», «какая температура у видюхи?», «какая загрузка проца?», «что грузит ПК?», «мониторинг железа»): ВСЕГДА вызывай get_hardware_status(target="all" | "gpu" | "cpu" | "ram" | "top_processes").

18. УМНЫЕ НАПОМИНАНИЯ (WINDOWS NOTIFICATIONS):
- Создать напоминание («напомни через 15 минут размяться», «напомни в 19:30 сделать реферат», «напомни завтра в 14:00 погулять»): ВСЕГДА вызывай manage_reminders(action="add", text="...", delay_seconds=..., target_time="...").
- Список напоминаний («какие у меня напоминания?», «покажи напоминания»): вызывай manage_reminders(action="list").
- Отменить напоминание («отмени напоминание про реферат»): вызывай manage_reminders(action="cancel", query="...").

19. УМНЫЙ БУФЕР ОБМЕНА:
- Если пользователь просит перевести скопированный текст, сделать краткую выжимку (TL;DR) текста из буфера, исправить ошибки в буфере или объяснить термин/ошибку из буфера («переведи то что я скопировал», «сделай выжимку из буфера», «исправь ошибки в тексте из буфера», «объясни ошибку из буфера»): ВСЕГДА вызывай process_clipboard_text(action="translate" | "summarize" | "fix_grammar" | "explain" | "format_code").

20. КОМПЛЕКСНЫЕ СЦЕНАРИИ (РЕЖИМЫ):
- Игровой режим («Олег, я играть», «игровой режим», «запусти игровой режим в RobRun», «режим игры»): ВСЕГДА вызывай execute_routine(routine_name="game", target="<название игры при наличии>").
- Режим отдыха / кино («режим отдыха», «режим кино», «включи кино», «я отдыхать»): ВСЕГДА вызывай execute_routine(routine_name="relax").
- Сводка дня / Начни день («доброе утро», «сводка дня», «начни день», «какие планы на сегодня»): ВСЕГДА вызывай execute_routine(routine_name="morning").

21. ЗВУКОВЫЕ ЭФФЕКТЫ:
- Если пользователь просит воспроизвести/проверить звук уведомления или будильника («проверь звук уведомления», «сыграй будильник», «проверь будильник», «воспроизведи уведомление»): ВСЕГДА вызывай play_sound_effect(sound_type="notification" | "alarm").

22. ОТЛОЖЕННЫЕ И ЗАПЛАНИРОВАННЫЕ ДЕЙСТВИЯ (ВЫПОЛНЕНИЕ ЧЕРЕЗ N ВРЕМЕНИ / В ТОЧНОЕ ВРЕМЯ):
- Если пользователь просит выполнить ЛЮБОЕ действие через N минут/секунд или в конкретное время («через 1 минуту включи музыку», «через 10 минут выключи компьютер», «в 18:42 запусти доту», «через полчаса останови запись», «в 23:00 выключи звук»):
  ВСЕГДА вызывай инструмент schedule_action:
  - action_description: понятное краткое описание (например: "Включить музыку", "Выключить компьютер", "Запустить Dota 2", "Остановить запись OBS")
  - tool_name: имя инструмента, который нужно выполнить (например: "control_spotify_playback", "launch_steam_game", "control_power", "obs_control", "set_volume", "open_program")
  - tool_args: параметры инструмента в виде объекта (например: {"action": "play"} для музыки, {"action": "shutdown"} для выключения ПК, {"game_name": "Dota 2"} для игры, {"action": "stop_record"} для OBS)
  - delay_seconds: задержка в секундах, если время относительное («через 1 минуту» = 60, «через 10 минут» = 600, «через полчаса» = 1800)
  - target_time: строка точного времени, если указано точное время («18:42», «23:00», «в 8 утра»)
- Список запланированных действий («какие действия запланированы?», «что запланировано?»): вызывай manage_scheduled_actions(action="list").
- Отменить запланированное действие («отмени выключение ПК», «отмени запуск доты»): вызывай manage_scheduled_actions(action="cancel", query="...").

23. НАЖАТИЕ НА КНОПКИ И ЭЛЕМЕНТЫ ИНТЕРФЕЙСА НА ЭКРАНЕ (VISION GUI CLICK):
- Если пользователь просит нажать, кликнуть, щёлкнуть или выбрать кнопку, меню, вкладку, иконку или ссылку на экране («нажми на кнопку библиотека», «кликни на библиотека в стиме», «нажми обновить», «нажми кнопку играть», «кликни по плейлисту», «нажми закрыть», «кликни правой кнопкой на ...»):
  КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО открывать браузер или искать в интернете!
  ВСЕГДА вызывай инструмент click_screen_element:
  - target: название или описание элемента/кнопки (например: "кнопка Библиотека", "Сообщество", "Магазин", "Настройки", "Play", "кнопка Обновить")
  - click_type: "click" (по умолчанию), "double_click" (двойной клик) или "right_click" (клик правой кнопкой)
  - app_name: название программы при необходимости (например: "Steam", "Chrome", "Discord")
  О.Л.Е.Г. через компьютерное зрение мгновенно находит кнопку на экране и физически нажимает на неё курсором мыши!

24. ПРОКРУТКА И ЛИСТАНИЕ СТРАНИЦЫ / ОКНА (MOUSE SCROLL):
- Если пользователь просит пролистать, прокрутить, проскроллить страницу, ленту, окно с товарами, каталог, список («пролистай вниз», «прокрути страницу», «пролистай вверх», «наведи на окно с товарами и пролистай», «пролистай до конца», «проскролль чуть-чуть», «листай дальше»):
  ВСЕГДА вызывай инструмент scroll_screen:
  - direction: "down" (вниз, по умолчанию) или "up" (вверх)
  - amount: "medium" (обычно), "small" (чуть-чуть) или "large" (много / далеко)
  - target: описание области экрана при наличии (например: "окно с товарами", "каталог", "лента", "список игр")
  - app_name: название приложения при необходимости (например: "Steam", "Chrome")
"""

import memory_manager

MAX_HISTORY_MESSAGES = 14
_current_user_query = ""
_llm_lock = asyncio.Lock()


def _sanitize_messages(msgs: list) -> list:
    """
    Гарантирует 100% валидность структуры сообщений для Gemini и OpenAI API:
    - Устраняет висячие сообщения role='tool' без предшествующего assistant с matching tool_call_id
    - Устраняет assistant с tool_calls, если за ними нет валидных role='tool' ответов
    - Гарантирует корректное чередование ролей
    """
    if not msgs:
        return []
    sanitized = []
    i = 0
    while i < len(msgs):
        m = msgs[i]
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)

        if role == "tool":
            if not sanitized:
                i += 1
                continue
            prev = sanitized[-1]
            prev_tool_calls = prev.get("tool_calls") if isinstance(prev, dict) else getattr(prev, "tool_calls", None)
            if not prev_tool_calls:
                i += 1
                continue
            prev_ids = {tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "") for tc in prev_tool_calls}
            tc_id = m.get("tool_call_id") if isinstance(m, dict) else getattr(m, "tool_call_id", "")
            if tc_id not in prev_ids:
                i += 1
                continue
            sanitized.append(m)
            i += 1
        elif role == "assistant" and (m.get("tool_calls") if isinstance(m, dict) else getattr(m, "tool_calls", None)):
            tc_list = m.get("tool_calls") if isinstance(m, dict) else getattr(m, "tool_calls", None)
            tc_ids = [tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "") for tc in tc_list]
            tool_replies = []
            j = i + 1
            while j < len(msgs) and (msgs[j].get("role") if isinstance(msgs[j], dict) else getattr(msgs[j], "role", None)) == "tool":
                tool_replies.append(msgs[j])
                j += 1
            reply_ids = {tr.get("tool_call_id") if isinstance(tr, dict) else getattr(tr, "tool_call_id", "") for tr in tool_replies}
            if all(t_id in reply_ids for t_id in tc_ids):
                sanitized.append(m)
                sanitized.extend(tool_replies)
                i = j
            else:
                content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                if content:
                    sanitized.append({"role": "assistant", "content": content})
                i = j
        else:
            sanitized.append(m)
            i += 1
    return sanitized


def get_current_system_prompt(query: str = "") -> str:
    """Формирует актуальный системный промпт с инъекцией релевантной памяти Mem0 и системного времени."""
    prompt = BASE_SYSTEM_PROMPT

    # Динамический контекст времени пользователя
    now = datetime.datetime.now().astimezone()
    days_ru = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
    months_ru = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    day_name = days_ru[now.weekday()]
    date_str = f"{now.day} {months_ru[now.month - 1]} {now.year}"
    time_str = now.strftime("%H:%M:%S")
    tz_str = now.strftime("UTC%z")
    prompt += f"\n\nСИСТЕМНОЕ ВРЕМЯ ПОЛЬЗОВАТЕЛЯ:\nСейчас на ПК пользователя: {day_name}, {date_str}, {time_str} ({tz_str})."

    memories = []
    if query:
        memories = memory_manager.search_memory(query, limit=4)
    if not memories:
        memories = memory_manager.get_all_memories()[:4]

    if memories:
        facts = "\n".join(f"• {m}" for m in memories)
        prompt += f"\n\nАКТУАЛЬНЫЕ ФАКТЫ О ПОЛЬЗОВАТЕЛЕ (ИЗ ИНТЕЛЛЕКТУАЛЬНОЙ ПАМЯТИ MEM0):\n{facts}"
    return prompt


# ---- Глобальная контекстная память диалога ----
conversation_history: list = [
    {"role": "system", "content": get_current_system_prompt()}
]


def trim_history(user_query: str = "", max_messages: int = MAX_HISTORY_MESSAGES) -> None:
    """Обрезает историю сообщений, сохраняя системный промпт и целостность tool-call пар."""
    global conversation_history
    if not conversation_history:
        conversation_history = [{"role": "system", "content": get_current_system_prompt(user_query)}]
        return

    # Обновляем системный промпт актуальными семантическими воспоминаниями Mem0 под запрос
    conversation_history[0] = {"role": "system", "content": get_current_system_prompt(user_query)}
    non_system = conversation_history[1:]

    if len(non_system) > max_messages:
        slice_candidates = non_system[-max_messages:]
        start_idx = 0
        for i, msg in enumerate(slice_candidates):
            role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
            if role == "user":
                start_idx = i
                break
        clean_slice = slice_candidates[start_idx:]
        conversation_history = [conversation_history[0]] + _sanitize_messages(clean_slice)
    else:
        conversation_history = [conversation_history[0]] + _sanitize_messages(non_system)


def clear_history() -> None:
    """Сбрасывает кратковременную историю диалога."""
    global conversation_history
    conversation_history = [{"role": "system", "content": get_current_system_prompt()}]


# ---- Схема инструментов (Function Calling) ----
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "Получить текущую загрузку процессора (CPU), оперативной памяти (RAM), свободное место на диске и заряд батареи.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hardware_status",
            "description": "Мониторинг аппаратного состояния ПК: точная температура Nvidia RTX GPU, скорость кулеров, загрузка процессора (CPU), оперативная память (RAM), видеопамять (VRAM) и топ процессов, нагружающих систему.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["all", "gpu", "cpu", "ram", "top_processes"],
                        "description": "Какую часть железа проверить: 'all' (полная сводка), 'gpu' (видеокарта и температура), 'cpu' (процессор), 'ram' (память), 'top_processes' (что грузит ПК).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_reminders",
            "description": "Управление всплывающими напоминаниями Windows (Toast Notifications): создание новых напоминаний с таймером или на точное время, просмотр списка дел и отмена.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "cancel"],
                        "description": "Действие: 'add' (создать), 'list' (список напоминаний), 'cancel' (отменить).",
                    },
                    "text": {
                        "type": "string",
                        "description": "Текст напоминания (о чем напомнить).",
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Задержка в секундах, если указано относительное время (например 'через 15 минут' = 900, 'через час' = 3600).",
                    },
                    "target_time": {
                        "type": "string",
                        "description": "Точное время срабатывания (например '19:30', '14:00', 'в 9 утра').",
                    },
                    "query": {
                        "type": "string",
                        "description": "ID или ключевые слова напоминания для отмены.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_clipboard_text",
            "description": "Интеллектуальная обработка текста из буфера обмена: перевод, краткая выжимка (TL;DR), исправление орфографии и стиля, объяснение ошибок и кода.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["translate", "summarize", "fix_grammar", "explain", "format_code"],
                        "description": "Действие: 'translate' (перевод), 'summarize' (краткая выжимка), 'fix_grammar' (исправление ошибок), 'explain' (объяснение кода/термина), 'format_code' (форматирование кода).",
                    },
                    "target_lang": {
                        "type": "string",
                        "description": "Язык перевода (по умолчанию 'русский').",
                    },
                    "copy_to_clipboard": {
                        "type": "boolean",
                        "description": "Скопировать ли готовый результат обратно в буфер обмена Windows.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_routine",
            "description": "Выполнение комплексных сценариев (рутин) в одну фразу: 'game' (игровой режим: запуск игры + Discord + буфер повторов OBS), 'relax' (режим отдыха: темная тема + тихая музыка + громкость 35%), 'morning' (сводка дня: время + погода + дела + музыка).",
            "parameters": {
                "type": "object",
                "properties": {
                    "routine_name": {
                        "type": "string",
                        "enum": ["game", "relax", "morning"],
                        "description": "Название сценария: 'game' (игровой), 'relax' (отдых/кино), 'morning' (сводка дня).",
                    },
                    "target": {
                        "type": "string",
                        "description": "Дополнительный параметр (например, название игры для 'game': 'RobRun', 'CS2').",
                    },
                },
                "required": ["routine_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_sound_effect",
            "description": "Воспроизведение звуковых эффектов Олега: проверка звука уведомления или будильника.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sound_type": {
                        "type": "string",
                        "enum": ["notification", "alarm", "timer"],
                        "description": "Тип звука: 'notification' (уведомление), 'alarm' (будильник/таймер).",
                    },
                },
                "required": ["sound_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_action",
            "description": "Запланировать выполнение любого системного действия или команды через N минут/секунд или в точное время (например: 'через 1 минуту включи музыку', 'через 10 минут выключи компьютер', 'в 18:42 запусти доту', 'через 30 минут останови запись OBS').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_description": {
                        "type": "string",
                        "description": "Краткое понятное описание действия (например: 'Включить музыку', 'Выключить компьютер', 'Запустить Dota 2').",
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Имя инструмента для выполнения (например: 'control_spotify_playback', 'launch_steam_game', 'control_power', 'obs_control', 'set_volume', 'open_program', 'manage_throne_vpn').",
                    },
                    "tool_args": {
                        "type": "object",
                        "description": "Аргументы для вызываемого инструмента в формате JSON-объекта (например: {'action': 'play'} или {'game_name': 'Dota 2'} или {'action': 'shutdown'}).",
                    },
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Задержка в секундах, если указано относительное время (например 'через 1 минуту' = 60, 'через 10 минут' = 600).",
                    },
                    "target_time": {
                        "type": "string",
                        "description": "Точное время срабатывания в формате 'ЧЧ:ММ' (например '18:42', '22:30').",
                    },
                },
                "required": ["action_description", "tool_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_scheduled_actions",
            "description": "Управление списком запланированных отложенных действий: просмотр активных задач ('list') или отмена по ключевым словам/ID ('cancel').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "cancel"],
                        "description": "Действие: 'list' (список запланированных действий) или 'cancel' (отменить).",
                    },
                    "query": {
                        "type": "string",
                        "description": "ID или ключевые слова действия для отмены (например 'музыка', 'дота', 'выключение').",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_spotify_playback",
            "description": "Управление воспроизведением в плеере Spotify: поставить музыку на паузу, продолжить воспроизведение, следующий трек, предыдущий трек. ВСЕГДА используй этот инструмент при любых командах: 'пауза', 'поставь музыку на паузу', 'останови музыку', 'продолжи музыку', 'играй дальше', 'включи музыку' (без указания названия трека), 'следующий трек', 'переключи песню', 'предыдущий трек'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pause", "play", "play_pause", "next", "prev"],
                        "description": "Действие: 'pause' (пауза/остановить), 'play' (продолжить/возобновить/включить), 'play_pause' (переключить), 'next' (следующий трек), 'prev' (предыдущий трек).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify_music",
            "description": "Ищет и включает музыку в Spotify. Извлекай название трека или артиста только из текущего сообщения.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Точное название трека или артиста",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_spotify_volume",
            "description": "Изменяет внутреннюю громкость воспроизведения в самом Spotify (плеере). ВСЕГДА вызывай этот инструмент, когда пользователь просит изменить громкость музыки, трека, песен или самого Spotify (например: 'поменяй громкость для музыки', 'музыку потише', 'сделай музыку погромче', 'громкость спотифай 60%').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["up", "down", "set"],
                        "description": "Направление изменения: 'down' (убавить громкость Spotify на 10%), 'up' (прибавить на 10%), 'set' (установить конкретный уровень).",
                    },
                    "delta": {
                        "type": "integer",
                        "description": "Шаг изменения громкости в процентах (по умолчанию 10).",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Точный уровень громкости Spotify в процентах (0-100), если указан конкретный процент.",
                        "minimum": 0,
                        "maximum": 100,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_spotify_mode",
            "description": "Управляет режимами воспроизведения (shuffle, repeat).",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["shuffle", "repeat"],
                        "description": "Режим воспроизведения.",
                    },
                    "state": {
                        "type": "boolean",
                        "description": "true для включения, false для выключения.",
                    },
                },
                "required": ["mode", "state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_spotify_playlist",
            "description": "Включает плейлист или любимые треки в Spotify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Название плейлиста или 'любимые треки'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_track",
            "description": "Проверяет, какой трек сейчас играет в Spotify.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_memory",
            "description": "Управляет долгосрочной памятью. 'add' сохраняет факт, 'read' читает, 'clear' очищает.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "read", "clear"],
                        "description": "Действие с памятью.",
                    },
                    "fact": {
                        "type": "string",
                        "description": "Сам факт для сохранения.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_timer",
            "description": "Запускает таймер на заданное количество секунд с уведомлением Windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Время в секундах.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Текст уведомления по истечении времени.",
                    },
                },
                "required": ["seconds", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_steam_game",
            "description": "Поиск и запуск установленных игр из библиотеки Steam.",
            "parameters": {
                "type": "object",
                "properties": {
                    "game_name": {
                        "type": "string",
                        "description": "Название игры на английском (например, counter-strike 2, dota 2, pubg).",
                    },
                },
                "required": ["game_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_or_focus_browser",
            "description": (
                "Открывает браузер по умолчанию в Windows или выводит уже открытый браузер поверх всех окон. "
                "Используй ВСЕГДА, когда пользователь просит 'открой браузер', 'покажи браузер', 'запусти браузер', 'браузер'. "
                "Если браузер уже запущен, этот инструмент НЕ открывает новое окно, а мгновенно разворачивает и показывает открытый браузер поверх всех окон."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Необязательный URL или поисковый запрос для открытия.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_in_browser",
            "description": "Открывает указанный URL или выполняет поисковый запрос в браузере.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос или URL.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_clipboard",
            "description": "Чтение текстового содержимого системного буфера обмена Windows.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_clipboard",
            "description": "Копирование текста в системный буфер обмена Windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Текст для копирования.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clipboard_history",
            "description": "Просмотр истории буфера обмена (недавно скопированные фрагменты текста) с опциональным поиском.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Количество последних записей (по умолчанию 5).",
                    },
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос для фильтрации скопированных фрагментов.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obs_control",
            "description": "Управление OBS Studio: запуск и остановка записи экрана, включение буфера повторов и сохранение отката (replay). Автоматически открывает OBS, если он закрыт.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "start_record",
                            "stop_record",
                            "toggle_record",
                            "start_replay",
                            "stop_replay",
                            "save_replay"
                        ],
                        "description": "Действие: 'start_record' (начать запись экрана), 'stop_record' (остановить запись), 'toggle_record' (переключить запись), 'save_replay' (сохранить откат/повтор), 'start_replay' (включить буфер повторов), 'stop_replay' (выключить буфер повторов).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "setup_obs_hotkeys",
            "description": "Автоматическая настройка и прописывание горячих клавиш в профилях OBS Studio (запись: Ctrl+Shift+R, буфер повторов: Ctrl+Shift+P, сохранение отката/повтора: Ctrl+Shift+S, включение буфера повторов). Вызывай, когда пользователь просит настроить, восстановить или прописать хоткеи для OBS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "restart_if_running": {
                        "type": "boolean",
                        "description": "Перезапустить ли OBS, если он сейчас запущен (по умолчанию true, чтобы изменения применились сразу).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Управление общей системной громкостью Windows (колонки/наушники ПК) или громкостью других приложений в системном микшере (discord, chrome, telegram). ВНИМАНИЕ: Для музыки или Spotify ВСЕГДА используй инструмент set_spotify_volume, а НЕ set_volume!",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["up", "down", "set"],
                        "description": "Направление изменения: 'down' (убавить/потише на 10%), 'up' (прибавить/погромче на 10%), 'set' (установить точный уровень).",
                    },
                    "delta": {
                        "type": "integer",
                        "description": "Шаг изменения громкости в процентах при action='up' или 'down' (по умолчанию 10).",
                    },
                    "level": {
                        "type": "integer",
                        "description": "Точный уровень громкости в процентах (0-100), если указан конкретный процент (например, 'поставь громкость на 40%').",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "app_name": {
                        "type": "string",
                        "description": "Имя процесса приложения на английском (например, discord, chrome, telegram). ВНИМАНИЕ: Для музыки и Spotify используй инструмент set_spotify_volume!",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "Управление мультимедиа: play_pause, next, prev.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play_pause", "next", "prev"],
                        "description": "Действие воспроизведения.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_window",
            "description": "Мгновенное управление и тайлинг окон: minimize, maximize, restore, focus, close, snap_left (прикрепить к левой половине), snap_right (к правой половине), center (по центру). Для текущего активного окна передай app_name='active'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Имя программы на английском (например, chrome, discord, telegram, obs, notepad, steam) или 'active' для текущего окна.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["close", "minimize", "maximize", "restore", "focus", "snap_left", "snap_right", "center"],
                        "description": "Действие с окном: close, minimize, maximize, restore, focus, snap_left, snap_right, center.",
                    },
                },
                "required": ["app_name", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_program",
            "description": "Запуск новых экземпляров программ (Chrome, Telegram, Discord, Steam, Notepad и др.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Название программы (например, Chrome, Telegram, Discord, Steam).",
                    },
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_os_command",
            "description": "Выполнить системную команду Windows PowerShell.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "Краткое описание намерения.",
                    },
                    "command": {
                        "type": "string",
                        "description": "Команда PowerShell.",
                    },
                },
                "required": ["intent", "command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Получить точное текущее время, дату, день недели и часовой пояс. Поддерживает системное время ПК пользователя (по умолчанию), а также время в любом городе мира, стране или часовом поясе (МСК, Токио, Лондон, Нью-Йорк, UTC, Владивосток и др.) со сравнением с системным временем. Отвечай в чате, НЕ открывай браузер!",
            "parameters": {
                "type": "object",
                "properties": {
                    "location_or_timezone": {
                        "type": "string",
                        "description": "Город, страна или часовой пояс (например: 'Токио', 'МСК', 'Нью-Йорк', 'UTC', 'Владивосток'). Если не указано или 'системное' — возвращает локальное системное время пользователя.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Получить актуальный прогноз погоды (температура, ощущается как, облачность/осадки, влажность, скорость ветра, давление) напрямую в чате без открытия браузера. Если город не указан — выдаёт погоду для города пользователя (Казань). Отвечай в чате, НЕ открывай браузер!",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "Название города на русском или английском (например: 'Казань', 'Москва', 'Сочи', 'Tokyo', 'London'). Если пусто — использует город проживания пользователя (Казань).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_screen",
            "description": "Сделать скриншот экрана пользователя и проанализировать его с помощью мультимодального зрения (OCR, поиск ошибок в коде, объяснение интерфейса, перевод текста с экрана).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Конкретный вопрос или просьба пользователя по поводу того, что изображено на экране.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_screen_element",
            "description": "Интеллектуальное наведение и нажатие мыши на любую кнопку, иконку, меню, вкладку или элемент интерфейса на экране компьютера (например: нажать 'Библиотека' в Steam, нажать кнопку 'Настройки', 'Магазин', 'Обновить', кликнуть по ссылке или кнопке в любой программе). Находит элемент с помощью компьютерного зрения и физически кликает по нему курсором.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Название или описание кнопки/элемента на экране, на который нужно нажать (например: 'кнопка Библиотека', 'Магазин', 'Настройки', 'Play', 'кнопка Обновить', 'крестик закрыть').",
                    },
                    "click_type": {
                        "type": "string",
                        "enum": ["click", "double_click", "right_click"],
                        "description": "Тип клика: 'click' (левый клик, по умолчанию), 'double_click' (двойной клик), 'right_click' (клик правой кнопкой).",
                    },
                    "app_name": {
                        "type": "string",
                        "description": "Опционально: название программы (например, Steam, Chrome, Discord), если нужно сначала сфокусировать её окно.",
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_screen",
            "description": "Прокрутка (скролл) колесом мыши страницы, каталога товаров, ленты новостей, списка игр или окна приложения. Позволяет листать контент вниз или вверх, предварительно наведя курсор на нужную область экрана при необходимости.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up"],
                        "description": "Направление прокрутки: 'down' (вниз, по умолчанию) или 'up' (вверх).",
                    },
                    "amount": {
                        "type": "string",
                        "enum": ["small", "medium", "large"],
                        "description": "Величина прокрутки: 'small' (чуть-чуть), 'medium' (обычно, пол-экрана), 'large' (много / далеко).",
                    },
                    "target": {
                        "type": "string",
                        "description": "Опционально: описание области экрана или окна, над которым нужно крутить колесо (например: 'окно с товарами', 'каталог', 'лента', 'список игр', 'чат'). О.Л.Е.Г. наведёт мышь в эту область перед прокруткой.",
                    },
                    "app_name": {
                        "type": "string",
                        "description": "Опционально: название приложения (например, Steam, Chrome, Telegram).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Быстрый поиск файлов и папок на компьютере по имени или расширению. Поддерживает поиск последнего скачанного файла ('последний скачанный', 'недавний файл').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Имя файла, ключевое слово или расширение (например: 'реферат', '.pdf', 'discord', 'последний скачанный').",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Опциональный путь к папке для поиска. Если не указан — ищет в стандартных пользовательских папках (Desktop, Downloads, Documents и др.).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file_or_folder",
            "description": "Открыть файл в ассоциированной программе ('open') или показать в Проводнике Windows с выделением ('reveal').",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Полный путь к файлу или папке на компьютере.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["open", "reveal"],
                        "description": "'open' — запустить файл/открыть папку; 'reveal' — показать объект в Проводнике с выделением.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_power",
            "description": "Управление питанием компьютера: мгновенная блокировка экрана Win+L ('lock'), спящий режим ('sleep'), выключение с таймером в минутах ('shutdown'), перезагрузка ('restart'), отмена выключения ('cancel').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["lock", "sleep", "shutdown", "restart", "cancel"],
                        "description": "Действие питания.",
                    },
                    "delay_minutes": {
                        "type": "integer",
                        "description": "Задержка в минутах для выключения или перезагрузки (по умолчанию 0 — немедленно).",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_system_theme",
            "description": "Переключить тему оформления Windows (тёмная, светлая или переключение toggle).",
            "parameters": {
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "enum": ["dark", "light", "toggle"],
                        "description": "Желаемая тема оформления: 'dark', 'light' или 'toggle'.",
                    },
                },
                "required": ["theme"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "empty_recycle_bin",
            "description": "Очистить Корзину Windows от удалённых файлов.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_throne_vpn",
            "description": "Управление VPN-клиентом Throne: включение/выключение VPN, переключение сервера (профиля) с автоматической проверкой доступности, замер пинга всех серверов ('ping_servers'), переключение режима (TUN / Прокси / Оба), смена конфигурации/маршрутизации, просмотр статуса соединения и списка серверов, сохранение ключа API ('set_key').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "on", "off", "set_mode", "switch_server", "switch_route", "list_servers", "list_routes", "ping_servers", "set_key"],
                        "description": "Действие: 'status' (текущее состояние), 'on' (включить), 'off' (выключить), 'set_mode' (выбрать режим tun/proxy/both), 'switch_server' (сменить сервер), 'ping_servers' (пропинговать серверы), 'switch_route' (сменить конфигурацию маршрутизации), 'list_servers' (список серверов с пингом), 'list_routes' (список конфигураций маршрутизации), 'set_key' (сохранить ключ Throne API).",
                    },
                    "target": {
                        "type": "string",
                        "description": "Параметр действия: для set_mode — 'tun', 'proxy' или 'both'; для switch_server — название сервера/страны (например 'Нидерланды', 'Германия', 'США'), ID сервера или 'best'/'лучший'; для ping_servers — 'все' или название конкретной страны/сервера; для switch_route — имя конфигурации (например 'Default') или ID; для set_key — строка с ключом или скопированный текст настроек.",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


_shared_clients: dict[str, AsyncOpenAI] = {}
_proxy_cooldown_until: float = 0.0


def _get_client(force_direct: bool = False) -> AsyncOpenAI:
    global _proxy_cooldown_until
    import psutil
    vpn_active = False
    try:
        stats = psutil.net_if_stats()
        vpn_active = any("throne" in ifc.lower() and s.isup for ifc, s in stats.items())
    except Exception:
        pass

    now = time.time()
    proxy_in_cooldown = (now < _proxy_cooldown_until)

    # Если системный VPN уже активен, принудительно прямое или прокси в кулдауне — прямое подключение!
    use_proxy = get_oleg_proxy() if (not force_direct and not vpn_active and not proxy_in_cooldown) else None

    cache_key = f"proxy_{use_proxy}" if use_proxy else "direct"
    if cache_key in _shared_clients:
        return _shared_clients[cache_key]

    if use_proxy:
        http_client = httpx.AsyncClient(
            proxy=use_proxy,
            http2=True,
            trust_env=False,
            timeout=httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=15, keepalive_expiry=120.0),
        )
    else:
        http_client = httpx.AsyncClient(
            http2=True,
            trust_env=False,
            timeout=httpx.Timeout(connect=5.0, read=25.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=15, keepalive_expiry=120.0),
        )

    headers = None
    if "openrouter.ai" in OPENROUTER_BASE_URL:
        headers = {
            "HTTP-Referer": "https://oleg.local",
            "X-Title": "OLEG",
        }

    client = AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        default_headers=headers,
        http_client=http_client,
        max_retries=1,
    )
    _shared_clients[cache_key] = client
    return client


async def _safe_chat_completion(client: AsyncOpenAI, **kwargs):
    """Выполняет chat.completions.create с мгновенным отводом на прямое соединение при сбое/таймауте прокси и фолбэком модели при 404."""
    global _proxy_cooldown_until, MODEL
    try:
        return await client.chat.completions.create(**kwargs)
    except Exception as e:
        err_lower = str(e).lower()
        if any(k in err_lower for k in ["timeout", "timed out", "connect", "proxy", "connection refused", "remoteprotocolerror"]):
            print(f"[Brain] Сетевой сбой ({type(e).__name__}) -> кулдаун прокси и мгновенный переход на прямое подключение")
            _proxy_cooldown_until = time.time() + 180.0
            direct_client = _get_client(force_direct=True)
            return await direct_client.chat.completions.create(**kwargs)
        if ("404" in err_lower or "not_found" in err_lower or "does not exist" in err_lower) and kwargs.get("model") != "gemini-3.5-flash-lite":
            print(f"[Brain] Модель '{kwargs.get('model')}' недоступна (404) -> автоматический резерв на 'gemini-3.5-flash-lite'")
            MODEL = "gemini-3.5-flash-lite"
            kwargs["model"] = "gemini-3.5-flash-lite"
            return await client.chat.completions.create(**kwargs)
        raise e


async def _execute_single_tool(func_name: str, args: dict) -> dict | str:
    """Диспетчер вызова инструментов."""
    if func_name == "get_system_info":
        return await asyncio.to_thread(tools.get_system_info)
    elif func_name == "play_spotify_music":
        return await asyncio.to_thread(tools.play_spotify_music, args.get("query", ""))
    elif func_name == "control_spotify_playback":
        return await asyncio.to_thread(tools.control_spotify_playback, args.get("action", "play_pause"))
    elif func_name == "play_spotify_playlist":
        return await asyncio.to_thread(tools.play_spotify_playlist, args.get("query", ""))
    elif func_name == "control_spotify_mode":
        return await asyncio.to_thread(tools.control_spotify_mode, args.get("mode", ""), bool(args.get("state", True)))
    elif func_name == "get_current_track":
        return await asyncio.to_thread(tools.get_current_track)
    elif func_name == "manage_memory":
        return await asyncio.to_thread(tools.manage_memory, args.get("action", "read"), args.get("fact", None))
    elif func_name == "set_spotify_volume":
        raw_level = args.get("level")
        level_int = int(raw_level) if raw_level is not None else None
        raw_delta = args.get("delta")
        delta_int = int(raw_delta) if raw_delta is not None else 10
        return await asyncio.to_thread(
            tools.set_spotify_volume,
            level=level_int,
            action=args.get("action"),
            delta=delta_int,
        )
    elif func_name == "start_timer":
        return await asyncio.to_thread(tools.start_timer, int(args.get("seconds", 60)), args.get("message", "Время вышло!"))
    elif func_name == "launch_steam_game":
        return await asyncio.to_thread(tools.launch_steam_game, args.get("game_name", ""))
    elif func_name == "open_or_focus_browser":
        return await asyncio.to_thread(tools.open_or_focus_browser, args.get("url", None))
    elif func_name == "open_in_browser":
        q_lower = _current_user_query.lower()
        if any(w in q_lower for w in ["нажми", "кликни", "щелкни", "щёлкни", "нажать"]):
            target_text = re.sub(r"(?i)\b(нажми|кликни|щелкни|щёлкни|нажать|на|по|кнопку|кнопка|кнопке|в|стиме|steam)\b", " ", _current_user_query).strip()
            return await tools.click_screen_element(target=target_text or args.get("query", ""))
        return await asyncio.to_thread(tools.open_in_browser, args.get("query", ""))
    elif func_name == "get_current_time":
        return await asyncio.to_thread(tools.get_current_time, args.get("location_or_timezone", ""))
    elif func_name == "get_weather":
        return await asyncio.to_thread(tools.get_weather, args.get("city", ""))
    elif func_name == "analyze_screen":
        return await tools.analyze_screen(args.get("query", ""))
    elif func_name == "search_files":
        return await asyncio.to_thread(tools.search_files, args.get("query", ""), args.get("folder", ""), int(args.get("limit", 5)))
    elif func_name == "open_file_or_folder":
        return await asyncio.to_thread(tools.open_file_or_folder, args.get("path", ""), args.get("action", "open"))
    elif func_name == "control_power":
        return await asyncio.to_thread(tools.control_power, args.get("action", "lock"), int(args.get("delay_minutes", 0)))
    elif func_name == "set_system_theme":
        return await asyncio.to_thread(tools.set_system_theme, args.get("theme", "toggle"))
    elif func_name == "empty_recycle_bin":
        return await asyncio.to_thread(tools.empty_recycle_bin)
    elif func_name == "read_clipboard":
        return await asyncio.to_thread(tools.read_clipboard)
    elif func_name == "write_clipboard":
        return await asyncio.to_thread(tools.write_clipboard, args.get("text", ""))
    elif func_name == "get_clipboard_history":
        return await asyncio.to_thread(tools.get_clipboard_history, limit=args.get("limit", 5), query=args.get("query", ""))
    elif func_name == "obs_control":
        return await asyncio.to_thread(tools.obs_control, args.get("action", "toggle_record"))
    elif func_name == "setup_obs_hotkeys":
        return await asyncio.to_thread(tools.setup_obs_hotkeys, args.get("restart_if_running", True))
    elif func_name == "set_volume":
        raw_level = args.get("level")
        level_int = int(raw_level) if raw_level is not None else None
        raw_delta = args.get("delta")
        delta_int = int(raw_delta) if raw_delta is not None else 10
        return await asyncio.to_thread(
            tools.set_volume,
            level=level_int,
            action=args.get("action"),
            delta=delta_int,
            app_name=args.get("app_name"),
        )
    elif func_name == "media_control":
        return await asyncio.to_thread(tools.media_control, args.get("action", "play_pause"))
    elif func_name == "open_program":
        raw_name = str(args.get("app_name", "")).strip()
        app_clean = raw_name.lower().replace(".lnk", "").replace(".exe", "").strip()
        q_lower = _current_user_query.lower()

        # 1. Если пользователь просит записать экран / откат, а вызвали open_program для OBS:
        is_obs = any(k in app_clean for k in ("obs", "обс", "broadcaster"))
        has_rec = any(w in q_lower for w in ["запис", "record", "рекорд", "откат", "повтор", "replay"])
        if is_obs and has_rec:
            act = "start_record" if any(w in q_lower for w in ["запис", "record", "рекорд"]) else "save_replay"
            return await asyncio.to_thread(tools.obs_control, act)

        # 2. Если пользователь просит запустить игру в Steam, а вызвали open_program для Steam:
        is_steam = any(k in app_clean for k in ("steam", "стим"))
        has_game = any(w in q_lower for w in ["запусти", "включи", "сыграй", "поиграй", "play", "game", "играть"])
        if is_steam and has_game:
            candidate = re.sub(
                r"(?i)\b(открой|запусти|вруби|включи|стим|steam|и|пожалуйста|давай|хочу|сыграть|поиграть|в|game|play)\b",
                " ",
                _current_user_query
            ).strip()
            if candidate:
                return await asyncio.to_thread(tools.launch_steam_game, candidate)

        return await asyncio.to_thread(tools.open_program, raw_name)
    elif func_name == "manage_window":
        return await asyncio.to_thread(tools.manage_window, args.get("app_name", ""), args.get("action", ""))
    elif func_name == "execute_os_command":
        return await tools.execute_os_command(intent=args.get("intent", ""), command=args.get("command", ""))
    elif func_name == "manage_throne_vpn":
        return await asyncio.to_thread(tools.manage_throne_vpn, args.get("action", "status"), args.get("target", ""))
    elif func_name == "get_hardware_status":
        return await asyncio.to_thread(tools.get_hardware_status, args.get("target", "all"))
    elif func_name == "manage_reminders":
        return await asyncio.to_thread(
            tools.manage_reminders,
            action=args.get("action", "add"),
            text=args.get("text", ""),
            delay_seconds=int(args.get("delay_seconds") or 0),
            target_time=args.get("target_time", ""),
            query=args.get("query", ""),
        )
    elif func_name == "process_clipboard_text":
        return await tools.process_clipboard_text(
            action=args.get("action", "translate"),
            target_lang=args.get("target_lang", "русский"),
            custom_instruction=args.get("custom_instruction", ""),
            copy_to_clipboard=bool(args.get("copy_to_clipboard", False)),
        )
    elif func_name == "execute_routine":
        return await asyncio.to_thread(
            tools.execute_routine,
            routine_name=args.get("routine_name", ""),
            target=args.get("target", ""),
        )
    elif func_name == "play_sound_effect":
        return await asyncio.to_thread(tools.play_sound_effect, args.get("sound_type", "notification"))
    elif func_name == "schedule_action":
        return await asyncio.to_thread(
            tools.schedule_action,
            action_description=args.get("action_description", ""),
            tool_name=args.get("tool_name", ""),
            tool_args=args.get("tool_args", {}),
            delay_seconds=int(args.get("delay_seconds") or 0),
            target_time=args.get("target_time", ""),
        )
    elif func_name == "manage_scheduled_actions":
        return await asyncio.to_thread(
            tools.manage_scheduled_actions,
            action=args.get("action", "list"),
            query=args.get("query", ""),
        )
    elif func_name == "click_screen_element":
        return await tools.click_screen_element(
            target=args.get("target", ""),
            click_type=args.get("click_type", "click"),
            app_name=args.get("app_name", ""),
        )
    elif func_name == "scroll_screen":
        return await tools.scroll_screen(
            direction=args.get("direction", "down"),
            amount=args.get("amount", "medium"),
            target=args.get("target", ""),
            app_name=args.get("app_name", ""),
        )
    else:
        return {"error": f"Неизвестная функция: {func_name}"}


def _format_tool_result_for_user(result) -> str:
    """Извлекает лаконичный человекочитаемый текст из результата инструмента."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ["summary", "message", "status", "text", "reply"]:
            val = result.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return json.dumps(result, ensure_ascii=False)
    return str(result)


DIRECT_ACTION_TOOLS = {
    "open_program", "open_or_focus_browser", "open_in_browser",
    "launch_steam_game", "manage_window", "media_control",
    "set_volume", "control_power", "set_system_theme",
    "empty_recycle_bin", "write_clipboard", "read_clipboard", "get_clipboard_history",
    "start_timer", "manage_throne_vpn", "play_spotify_music",
    "control_spotify_playback", "set_spotify_volume", "control_spotify_mode",
    "play_spotify_playlist", "get_current_track", "get_current_time",
    "obs_control", "setup_obs_hotkeys",
    "get_hardware_status", "manage_reminders", "execute_routine", "process_clipboard_text",
    "play_sound_effect", "schedule_action", "manage_scheduled_actions",
    "click_screen_element", "scroll_screen",
}


async def ask_llm(user_message: str, voice_mode: bool = False) -> str:
    """Высокоскоростной синхронный запрос к модели с поддержкой вызова инструментов и голосового режима."""
    global conversation_history, _current_user_query

    prompt_content = user_message
    if voice_mode:
        prompt_content = (
            f"{user_message}\n\n"
            "[ГОЛОСОВОЙ РЕЖИМ]: Ответь максимально кратко, живо и емко "
            "(1-2 естественных предложения для чтения голосом вслух, без списков, без таблиц, без цитат и без markdown-разметки)."
        )

    async with _llm_lock:
        _current_user_query = user_message

        if not OPENROUTER_API_KEY:
            return (
                "⚠️ API-ключ не настроен.\n\n"
                "Укажите ваш `OPENROUTER_API_KEY` в файле `backend/.env` и перезапустите бэкенд."
            )

        conversation_history.append({"role": "user", "content": prompt_content})
        trim_history(user_message)

        client = _get_client()
        history_snapshot = list(conversation_history)

    try:
        for _ in range(3):
            response = await _safe_chat_completion(
                client,
                model=MODEL,
                messages=conversation_history,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=MAX_TOKENS,
            )

            msg = response.choices[0].message
            tool_calls = msg.tool_calls

            if not tool_calls:
                final_text = msg.content or ""
                conversation_history.append({"role": "assistant", "content": final_text})
                if voice_mode:
                    for entry in reversed(conversation_history):
                        if entry.get("role") == "user" and entry.get("content") == prompt_content:
                            entry["content"] = user_message
                            break
                return final_text
                conversation_history.append({"role": "assistant", "content": final_text})
                return final_text

            assistant_dict = msg.model_dump(exclude_none=True)
            conversation_history.append(assistant_dict)

            all_direct = True
            executed_results = []

            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except Exception:
                    fn_args = {}

                if fn_name not in DIRECT_ACTION_TOOLS:
                    all_direct = False

                res = await _execute_single_tool(fn_name, fn_args)
                content_str = json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res)
                user_facing_str = _format_tool_result_for_user(res)

                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content_str,
                })
                executed_results.append((fn_name, user_facing_str))

                if fn_name == "manage_throne_vpn" and str(fn_args.get("action", "")).lower() == "off":
                    all_direct = True

            # БЫСТРЫЙ ПУТЬ: Для прямых действий отдаем результат без 2-го запроса к ИИ
            if all_direct and executed_results:
                tool_names = [name for name, _ in executed_results]
                suppress_open = any(k in tool_names for k in [
                    "launch_steam_game", "obs_control",
                    "play_spotify_music", "control_spotify_playback", "play_spotify_playlist"
                ])
                if suppress_open and "open_program" in tool_names:
                    filtered = [msg for name, msg in executed_results if name != "open_program"]
                    combined = "\n\n".join(filtered) if filtered else "\n\n".join(msg for _, msg in executed_results)
                else:
                    combined = "\n\n".join(msg for _, msg in executed_results)
                conversation_history.append({"role": "assistant", "content": combined})
                return combined

        # Финальный ответ после завершения инструментов (если требовался синтез данных)
        try:
            final_res = await _safe_chat_completion(
                client,
                model=MODEL,
                messages=conversation_history,
                temperature=0.2,
                max_tokens=MAX_TOKENS,
            )
            reply = final_res.choices[0].message.content or "Команда выполнена."
            conversation_history.append({"role": "assistant", "content": reply})
            return reply
        except Exception as post_tool_err:
            fallback_reply = "\n\n".join(msg for _, msg in executed_results) or "Команда успешно выполнена."
            conversation_history.append({"role": "assistant", "content": fallback_reply})
            return fallback_reply

    except Exception as e:
        conversation_history = history_snapshot
        err_msg = str(e)
        err_body = str(getattr(e, "body", "")) + " " + str(getattr(getattr(e, "response", None), "text", ""))
        err_full = (err_msg + " " + err_body).lower()

        if "402" in err_msg or "requires more credits" in err_full:
            return "⚠️ Недостаточно кредитов на аккаунте (Error 402). Проверьте баланс или переключитесь на бесплатный ключ Google Gemini API в backend/.env."
        if "403" in err_msg or "access denied" in err_full:
            return "⚠️ Ошибка доступа 403: Access Denied. Проверьте правильность API ключа или параметры прокси в backend/.env."
        if "404" in err_msg or "not found" in err_full:
            return "⚠️ Модель нейросети не найдена (Error 404). В настройках установлена рабочая модель gemini-3.5-flash-lite."
        if "400" in err_msg and any(w in err_full for w in ["location", "unsupported", "failed_precondition", "user location"]):
            return "⚠️ Google Gemini API недоступен в вашем регионе без VPN (Error 400). Пожалуйста, включите Throne VPN или укажите рабочий прокси в backend/.env."

        # Автоматическое самовосстановление при повреждении контекста диалога
        if "400" in err_msg and not getattr(ask_llm, "_retrying", False):
            print(f"[Brain] Ошибка формата истории ({err_msg}) -> автоматический сброс контекста и повтор")
            conversation_history = [
                {"role": "system", "content": get_current_system_prompt(user_message)},
                {"role": "user", "content": user_message}
            ]
            try:
                ask_llm._retrying = True
                return await ask_llm(user_message)
            finally:
                ask_llm._retrying = False

        return f"[Ошибка ИИ]: {err_msg}"


async def stream_ask_llm(user_message: str) -> AsyncGenerator[str, None]:
    """Высокоскоростная потоковая генерация ответа с мгновенным выводом токенов и быстрым выполнением команд."""
    global conversation_history, _current_user_query

    async with _llm_lock:
        _current_user_query = user_message

        if not OPENROUTER_API_KEY:
            yield "⚠️ API-ключ не настроен. Укажите OPENROUTER_API_KEY в backend/.env."
            return

        conversation_history.append({"role": "user", "content": user_message})
        trim_history(user_message)

        client = _get_client()
        history_snapshot = list(conversation_history)

    try:
        # Прямой потоковый запрос с первого токена (температура 0.2 для ультрабыстрой генерации)
        stream = await _safe_chat_completion(
            client,
            model=MODEL,
            messages=conversation_history,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=MAX_TOKENS,
            stream=True,
        )

        full_content = ""
        tool_calls_list = []
        current_entry = None

        is_tool_call = False
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if not delta:
                continue

            # Сбор параметров вызова инструментов с поддержкой параллельных вызовов и Gemini extra_content
            if delta.tool_calls:
                is_tool_call = True
                for tc in delta.tool_calls:
                    entry = None
                    if tc.index is not None:
                        while len(tool_calls_list) <= tc.index:
                            tool_calls_list.append({"id": "", "name": "", "arguments": "", "extra_content": None})
                        entry = tool_calls_list[tc.index]
                    elif tc.id:
                        for existing in tool_calls_list:
                            if existing["id"] == tc.id:
                                entry = existing
                                break
                        if not entry:
                            entry = {"id": tc.id, "name": "", "arguments": "", "extra_content": None}
                            tool_calls_list.append(entry)
                        current_entry = entry
                    else:
                        if current_entry is not None:
                            entry = current_entry
                        else:
                            entry = {"id": f"call_{len(tool_calls_list)}", "name": "", "arguments": "", "extra_content": None}
                            tool_calls_list.append(entry)
                            current_entry = entry

                    if tc.id and not entry.get("id"):
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] = tc.function.name
                        if tc.function.arguments:
                            entry["arguments"] += tc.function.arguments
                    ec = getattr(tc, "extra_content", None)
                    if ec:
                        entry["extra_content"] = ec
            elif delta.content:
                full_content += delta.content
                # Моментальный стриминг токенов пользователю в реальном времени (отклик 200-300мс!)
                if not is_tool_call:
                    yield delta.content

        tool_calls_list = [item for item in tool_calls_list if item.get("name")]

        # Если не было вызовов инструментов — это обычный разговор, все токены уже переданы в реальном времени
        if not tool_calls_list:
            conversation_history.append({"role": "assistant", "content": full_content})
            return

        # Если модель вызвала инструменты:
        formatted_tool_calls = []
        for idx, item in enumerate(tool_calls_list):
            tc_dict = {
                "id": item["id"] or f"call_{idx}",
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": item["arguments"],
                }
            }
            if item.get("extra_content"):
                tc_dict["extra_content"] = item["extra_content"]
            formatted_tool_calls.append(tc_dict)

        conversation_history.append({
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": formatted_tool_calls,
        })

        all_direct = True
        executed_results = []

        for item in formatted_tool_calls:
            fn_name = item["function"]["name"]
            try:
                fn_args = json.loads(item["function"]["arguments"]) if item["function"]["arguments"] else {}
            except Exception:
                fn_args = {}

            if fn_name not in DIRECT_ACTION_TOOLS:
                all_direct = False

            res = await _execute_single_tool(fn_name, fn_args)
            content_str = json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res)
            user_facing_str = _format_tool_result_for_user(res)

            conversation_history.append({
                "role": "tool",
                "tool_call_id": item["id"],
                "content": content_str,
            })
            executed_results.append((fn_name, user_facing_str))

            if fn_name == "manage_throne_vpn" and str(fn_args.get("action", "")).lower() == "off":
                all_direct = True

        # БЫСТРЫЙ ПУТЬ: Для действий возвращаем результат напрямую (скорость ответа 1-1.5 сек!)
        if all_direct and executed_results:
            tool_names = [name for name, _ in executed_results]
            suppress_open = any(k in tool_names for k in [
                "launch_steam_game", "obs_control",
                "play_spotify_music", "control_spotify_playback", "play_spotify_playlist"
            ])
            if suppress_open and "open_program" in tool_names:
                filtered = [msg for name, msg in executed_results if name != "open_program"]
                combined_reply = "\n\n".join(filtered) if filtered else "\n\n".join(msg for _, msg in executed_results)
            else:
                combined_reply = "\n\n".join(msg for _, msg in executed_results)
            conversation_history.append({"role": "assistant", "content": combined_reply})
            yield combined_reply
            return

        # Если требуется синтез (погода, поиск файлов, анализ):
        try:
            post_stream = await _safe_chat_completion(
                client,
                model=MODEL,
                messages=conversation_history,
                temperature=0.2,
                max_tokens=MAX_TOKENS,
                stream=True,
            )

            final_reply = ""
            async for chunk in post_stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    final_reply += token
                    yield token

            conversation_history.append({"role": "assistant", "content": final_reply})
        except Exception as post_tool_err:
            fallback_reply = "\n\n".join(msg for _, msg in executed_results) or "Команда успешно выполнена."
            conversation_history.append({"role": "assistant", "content": fallback_reply})
            yield fallback_reply
            return

    except Exception as e:
        conversation_history = history_snapshot
        err_msg = str(e)
        err_body = str(getattr(e, "body", "")) + " " + str(getattr(getattr(e, "response", None), "text", ""))
        err_full = (err_msg + " " + err_body).lower()

        if "402" in err_msg or "requires more credits" in err_full:
            yield "⚠️ Недостаточно кредитов на аккаунте (Error 402). Проверьте баланс или переключитесь на бесплатный ключ Google Gemini API в backend/.env."
        elif "403" in err_msg or "access denied" in err_full:
            yield "⚠️ Ошибка доступа 403: Access Denied. Проверьте правильность API ключа или параметры прокси в backend/.env."
        elif "404" in err_msg or "not found" in err_full:
            yield "⚠️ Модель нейросети не найдена (Error 404). В настройках установлена рабочая модель gemini-3.5-flash-lite."
        elif "400" in err_msg and any(w in err_full for w in ["location", "unsupported", "failed_precondition", "user location"]):
            yield "⚠️ Google Gemini API недоступен в вашем регионе без VPN (Error 400). Пожалуйста, включите Throne VPN или укажите рабочий прокси в backend/.env."
        elif "400" in err_msg and not getattr(stream_ask_llm, "_retrying", False):
            print(f"[Brain] Ошибка формата истории ({err_msg}) -> автоматический сброс контекста и повтор")
            conversation_history = [
                {"role": "system", "content": get_current_system_prompt(user_message)},
                {"role": "user", "content": user_message}
            ]
            try:
                stream_ask_llm._retrying = True
                async for chunk in stream_ask_llm(user_message):
                    yield chunk
                return
            finally:
                stream_ask_llm._retrying = False
        else:
            yield f"[Ошибка ИИ]: {err_msg}"


async def transcribe_audio(audio_base64: str, mime_type: str = "audio/wav", hint_text: str | None = None) -> str:
    """
    Высокоскоростная транскрибация речи (STT) в текст на русском языке.
    Использует прямую интеграцию с Google Gemini Multimodal или OpenRouter.
    Принимает опциональный черновик (hint_text) от Vosk для предотвращения галлюцинаций.
    """
    if not audio_base64 or not audio_base64.strip():
        return ""

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not OPENROUTER_API_KEY and not groq_key:
        return ""

    # Очищаем base64 от возможных data URI заголовков
    if "," in audio_base64:
        audio_base64 = audio_base64.split(",", 1)[1]

    proxy = get_oleg_proxy()

    # 0. Ультрабыстрое облачное распознавание через Groq Whisper Large v3 Turbo (0 МБ на диске, отклик 150мс)
    if groq_key:
        try:
            import base64
            audio_bytes = base64.b64decode(audio_base64)
            async with httpx.AsyncClient(proxy=proxy, timeout=12.0) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    files={"file": ("audio.wav", audio_bytes, mime_type)},
                    data={
                        "model": "whisper-large-v3-turbo",
                        "language": "ru",
                        "temperature": 0.0,
                    },
                )
                if resp.status_code == 200:
                    text_res = resp.json().get("text", "").strip()
                    if text_res:
                        clean_text = text_res.strip("\"'` ")
                        if clean_text.lower() not in ("тишина", "[тишина]", "silence", "[silence]", "...", "нет речи", "тишина."):
                            return clean_text
                else:
                    print(f"[STT Warning] Groq Whisper returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[STT Exception] Groq Whisper: {e}")

    clean_hint = (hint_text or "").strip()
    hint_instruction = f"\nФонетический черновик речи: «{clean_hint}»." if clean_hint else ""

    stt_prompt = (
        "Ты профессиональный сервис точной транскрибации русской речи.\n"
        f"Транскрибируй аудиозапись пользователя СТРОГО ДОСЛОВНО (слово в слово).{hint_instruction}\n"
        "ВАЖНЫЕ ПРАВИЛА:\n"
        "1. Пользователь может произносить названия песен, треков, игр, компьютерных программ, сленг, мат и неформальные выражения (например: 'покури со мной говно', 'открой стим', 'включи фонк').\n"
        "2. СТРОЖАЙШЕ ЗАПРЕЩЕНО заменять слова на цензурные аналоги, придумывать другие слова или менять смысл.\n"
        "3. Записывай ровно то, что произнес человек. Расставь естественные знаки препинания.\n"
        "4. Верни ТОЛЬКО произнесенные слова без кавычек, без приветствий и без пояснений.\n"
        "5. Если аудио не содержит речи (тишина, шум, шорох), верни пустую строку."
    )

    # 1. Прямой запрос к Google Gemini Audio API (для Gemini ключей)
    if (
        OPENROUTER_API_KEY.startswith(("AQ.", "AIzaSy"))
        or "generativelanguage.googleapis.com" in OPENROUTER_BASE_URL
    ):
        stt_model = MODEL if "gemini" in MODEL.lower() else "gemini-3.5-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{stt_model}:generateContent?key={OPENROUTER_API_KEY}"

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": audio_base64,
                            }
                        },
                        {
                            "text": stt_prompt
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 256,
            },
        }

        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        raw_text = "".join(p.get("text", "") for p in parts).strip()
                        clean_text = raw_text.strip("\"'` ")
                        if clean_text in (
                            "00:00", "0:00", "тишина", "[тишина]", "silence",
                            "[silence]", "...", "нет речи", "тишина.", "шум"
                        ):
                            return ""
                        return clean_text
                else:
                    print(f"[STT Error] Gemini returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"[STT Exception] Gemini: {e}")

    # 2. OpenRouter / OpenAI аудио транскрибация
    try:
        client = _get_client()
        resp = await client.chat.completions.create(
            model=MODEL if "gemini" in MODEL.lower() else "google/gemini-2.5-flash",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": stt_prompt,
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_base64,
                                "format": "wav" if "wav" in mime_type else "mp3",
                            },
                        },
                    ],
                }
            ],
            temperature=0.0,
            max_tokens=256,
        )
        if resp.choices and resp.choices[0].message.content:
            raw_text = resp.choices[0].message.content.strip().strip("\"'` ")
            if raw_text not in ("00:00", "0:00", "тишина", "silence"):
                return raw_text
    except Exception as e:
        print(f"[STT Exception] OpenRouter/OpenAI: {e}")

    return ""
