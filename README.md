# O.L.E.G.

> **О**тветственный **Л**иквидатор **Е**жедневного **Г**еморроя — локальный десктопный ИИ-ассистент для Windows.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-33-47848F?logo=electron&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6?logo=windows&logoColor=white)

Олег — это не просто чат-бот в окне, а системный оверлей с голосовым управлением, компьютерным зрением и **40 встроенными инструментами** для контроля Windows, Spotify, OBS Studio, Steam, VPN и буфера обмена. Работает поверх любых приложений и полноэкранных игр, не перехватывая фокус.

<!-- Добавь скриншот или GIF интерфейса сюда:
![Preview](assets/preview.png)
-->

---

## Что он умеет

### Интерфейс и скорость
- **Spotlight-оверлей (`Ctrl + Space`)** — безрамочное окно с эффектом стекла (Glassmorphism), плавным авторазмером и потоковым выводом текста.
- **In-Game HUD** (в разработке) — отдельный полупрозрачный индикатор (110×110 px) в углу экрана, который реагирует на голос (RMS), обработку и речь. Работает с `focusable: false` и `setIgnoreMouseEvents(true)`, поэтому не мешает в играх.
- **Калькулятор и конвертер на лету (0 мс)** — считает математику (`30% от 4500`, `корень из 144`), валюты (`100 usd в rub`), температуру, вес, длину и объемы памяти прямо в строке ввода без обращения к сети.
- **Быстрый отклик** — типовые команды («пауза», «сделай громче», «сколько времени», «очисти корзину») перехватываются локальным диспетчером за **0 мс**, а системные действия выполняются напрямую без лишнего прогона через LLM (**0.8–1.5 с**).

### Голос (Wake Word, STT & TTS)
- **Офлайн-активация (Wake Word):** постоянная локальная прослушка через **Vosk** (`vosk-model-small-ru-0.22`) по WebSocket. Реагирует на «Олег» (и вариации), выполняет команду в фоне без открытия окна.
- **Распознавание речи (STT):** облачный **Groq Whisper Large v3 Turbo** (~150 мс) с автоотсечением тишины (VAD 750 мс) + резерв на Gemini / OpenRouter / локальный Vosk.
- **Озвучка (TTS):** бесплатный **Microsoft Edge-TTS** (`DmitryNeural` / `SvetlanaNeural`) с двухуровневым кэшем и фильтром текста (убирает Markdown, код и смайлы, нормально читает «ГБ», «ФПС», «ПК»).

### Системные возможности и интеграции
- **Windows и железо:** запуск ПО и поиск файлов, управление окнами и питанием, переключение темы Windows, очистка корзины, безопасное выполнение PowerShell/CMD, мониторинг CPU/RAM/GPU Nvidia RTX (`nvidia-smi`).
- **Звук и Spotify:** раздельная громкость приложений через Windows Core Audio (`pycaw`), медиа-клавиши, полный контроль Spotify (треки, плейлисты, shuffle/repeat, громкость плеера).
- **Игры и запись (Steam + OBS):** автопоиск и запуск игр по манифестам Steam на всех дисках; управление записью и буфером повторов в OBS Studio (включая автозапуск и настройку хоткеев).
- **Зрение (Vision AI):** анализ экрана (ошибки, код, интерфейс), клики мышью по элементам интерфейса с плавной траекторией и скроллинг.
- **Умный буфер обмена:** история копирований + ИИ-обработка текста из буфера (перевод, TL;DR, исправление ошибок, разбор кода).
- **Планировщик и напоминания:** таймеры со звуком, напоминания с нативными Windows Toast-уведомлениями и отложенный запуск любых действий («через час выключи ПК»).
- **VPN (Throne):** переключение серверов, замер пинга, выбор режима (`tun` / `proxy`) и предохранитель от случайного отключения без резервного прокси.
- **Гибридная память:** краткосрочное окно диалога (14 сообщений) + долгосрочная векторная память (**Mem0 + Qdrant**) при работе через OpenRouter или автономный `memory.json` при прямом Gemini API.
- **Сценарии (Routines):** запуск цепочек действий одной фразой (`game`, `relax`, `morning`) + поддержка кастомных макросов в `backend/data/custom_routines.json`.

<details>
<summary><strong>Полный список всех 40 инструментов (Function Calling)</strong></summary>

| Категория | Инструменты |
|---|---|
| **Система и Windows** | `get_system_info`, `get_hardware_status`, `open_program`, `open_or_focus_browser`, `open_in_browser`, `manage_window`, `control_power`, `set_system_theme`, `empty_recycle_bin`, `execute_os_command`, `search_files`, `open_file_or_folder` |
| **Звук и медиа** | `set_volume`, `media_control`, `play_sound_effect` |
| **Spotify** | `control_spotify_playback`, `play_spotify_music`, `play_spotify_playlist`, `set_spotify_volume`, `control_spotify_mode`, `get_current_track` |
| **OBS Studio** | `obs_control`, `setup_obs_hotkeys` |
| **Steam** | `launch_steam_game` |
| **Vision AI & GUI** | `analyze_screen`, `click_screen_element`, `scroll_screen` |
| **Буфер обмена** | `read_clipboard`, `write_clipboard`, `get_clipboard_history`, `process_clipboard_text` |
| **Таймеры и задачи** | `start_timer`, `manage_reminders`, `schedule_action`, `manage_scheduled_actions` |
| **Сеть и VPN** | `manage_throne_vpn` |
| **Память** | `manage_memory` |
| **Инфо и сценарии** | `get_current_time`, `get_weather`, `execute_routine` |

</details>

---

## Горячие клавиши

| Комбинация | Действие |
|---|---|
| `Ctrl + Space` | Открыть / скрыть строку Олега поверх всех окон |
| `Escape` | Скрыть активное окно ассистента |
| `Enter` | Отправить запрос / подтвердить шаг мастера настройки |
| `↑` / `↓` | Навигация по истории введённых команд |
| Клик вне окна | Скрыть оверлей |
| Иконка микрофона / трея | Ручная запись голоса / управление окном и выход |

---

## Стек и архитектура

- **Backend:** Python 3.10+, FastAPI, Uvicorn, OpenAI SDK (OpenRouter / Google Gemini), WebSockets, `pycaw`, `psutil`, `spotipy`, `vosk`, `edge-tts`, `mem0ai` (Qdrant + SQLite), `Pillow`, Win32 API (`ctypes`).
- **Frontend:** Electron 33, JavaScript (ES6+), HTML5/CSS3, Web Audio API, локальный HTTP-контроллер HUD (порт `8001`).

<details>
<summary><strong>Структура проекта</strong></summary>

```text
OLEG/
├── start.bat                 # Запуск бэкенда и Electron в 1 клик
├── stop.bat                  # Остановка всех процессов (порты 8000 / 8001)
├── backend/
│   ├── main.py               # Точка входа FastAPI (REST + WebSockets)
│   ├── brain.py              # LLM-ядро, системный промпт, схемы инструментов, STT
│   ├── tools.py              # Реализация всех 40 системных инструментов
│   ├── memory_manager.py     # Гибридная память (Mem0 Qdrant + memory.json)
│   ├── task_scheduler.py     # Планировщик отложенных задач
│   ├── reminders_manager.py  # Напоминания и Windows Toast-уведомления
│   ├── clipboard_assistant.py# Монитор и ИИ-обработка буфера обмена
│   ├── hardware_monitor.py   # Метрики CPU, RAM, дисков и GPU (nvidia-smi)
│   ├── sound_manager.py      # Мультиканальный звук через Windows MCI
│   ├── tts_engine.py         # Edge-TTS + фильтрация текста + кэш
│   ├── routines.py           # Готовые и кастомные сценарии
│   ├── requirements.txt      # Зависимости Python
│   └── .env.example          # Шаблон переменных окружения
└── electron/
    ├── main.js               # Окно Spotlight, HUD, трей, глобальные хоткеи
    ├── preload.js            # Изолированный IPC-мост
    ├── renderer.js           # Логика UI, стриминг, калькулятор, аудио
    ├── index.html            # Разметка оверлея и настроек
    ├── hud.html              # In-Game HUD индикатор
    └── style.css             # Тёмная неоновая тема и анимации
```

</details>

---

## Установка и запуск

### Требования
- **Windows 10 / 11** (64-bit)
- **Python 3.10+** *(с галочкой `Add Python to PATH`)*
- **Node.js 18+** и `npm`
- **Git**

### Пошаговая установка

1. Клонируй репозиторий:
```powershell
git clone https://github.com/f1arlesss/OLEG.git
cd OLEG
```

2. Создай виртуальное окружение и установи Python-зависимости:
```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
cd ..
```

3. Установи зависимости Electron:
```powershell
cd electron
npm install
cd ..
```

4. Запусти ассистента:
```powershell
.\start.bat
```
> Скрипт сам освободит порты `8000` и `8001`, поднимет FastAPI-бэкенд и откроет Electron-оверлей. Для полной остановки всех процессов запусти `.\stop.bat`.

---

## Первоначальная настройка

1. **Setup Wizard:** при первом запуске откроется встроенный мастер из 4 шагов (все ключи автоматически сохранятся в `backend/.env`):
   - API-ключ нейросети ([Google AI Studio](https://aistudio.google.com/) или [OpenRouter](https://openrouter.ai/keys));
   - выбор модели (например, `gemini-2.5-flash` или `gemini-3.5-flash-lite`);
   - бесплатный ключ [Groq](https://console.groq.com/keys) для быстрого распознавания речи Whisper Turbo *(опционально, но рекомендуется)*;
   - прокси, если нужен доступ к API в регионе с ограничениями.
2. **Spotify *(опционально)*:** создай приложение в [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), укажи Redirect URI `https://example.com/callback` и введи `Client ID` / `Client Secret` в настройках Олега (иконка шестерёнки). При первой музыкальной команде подтверди доступ в открывшемся браузере и вставь итоговый URL в консоль бэкенда — токен сохранится в `backend/.cache`.
3. **Микрофон:** при первом включении голосового ввода разреши Windows доступ к микрофону для Electron.

<details>
<summary><strong>Справочник переменных окружения (backend/.env)</strong></summary>

| Переменная | Обязательно | Назначение | Пример / по умолчанию |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Да | Ключ OpenRouter (`sk-or-...`) или Google Gemini (`AIzaSy...`) | `your_api_key_here` |
| `OPENROUTER_MODEL` | Да | Модель LLM | `gemini-2.5-flash` |
| `OPENROUTER_BASE_URL` | Да | Эндпоинт OpenAI-совместимого API | `https://openrouter.ai/api/v1` |
| `OPENROUTER_MAX_TOKENS` | Нет | Лимит токенов ответа | `1024` |
| `GROQ_API_KEY` | Нет | Ключ Groq (`gsk_...`) для Whisper Large v3 Turbo | — |
| `TTS_ENABLED` | Нет | Включить озвучку ответов | `true` |
| `TTS_VOICE` | Нет | Голос Edge-TTS (`DmitryNeural` / `SvetlanaNeural`) | `ru-RU-DmitryNeural` |
| `TTS_RATE` / `TTS_PITCH` | Нет | Скорость и тон голоса | `+8%` / `-1Hz` |
| `OLEG_PROXY` | Нет | HTTP / HTTPS / SOCKS5 прокси для API | `http://127.0.0.1:1080` |
| `DEFAULT_CITY` | Нет | Город по умолчанию для погоды | `Москва` |
| `SPOTIPY_CLIENT_ID` | Нет | Spotify Client ID | — |
| `SPOTIPY_CLIENT_SECRET` | Нет | Spotify Client Secret | — |
| `SPOTIPY_REDIRECT_URI` | Нет | Spotify OAuth Redirect URI | `https://example.com/callback` |
| `THRONE_KEY` / `THRONE_ADDR` | Нет | API-ключ и адрес контроллера Throne VPN | `127.0.0.1:9095` |
| `OLEG_API_TOKEN` | Нет | Опциональный токен защиты локального API | — |

</details>
