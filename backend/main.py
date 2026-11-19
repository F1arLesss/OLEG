"""
О.Л.Е.Г. — FastAPI Backend
Локальный сервер на порту 8000.
Принимает запросы от Electron UI и маршрутизирует к brain/tools.
Поддерживает стандартный JSON-ответ и высокоскоростной стриминг (StreamingResponse).
"""

import asyncio
import json
import os
import re
from fastapi import FastAPI, HTTPException, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from brain import ask_llm, stream_ask_llm, clear_history, update_env_file, reload_brain_config, transcribe_audio
from tools import apply_oleg_proxy_globally

# Инициализируем прокси для всех исходящих сетевых запросов бэкенда (Spotify, LLM, погода)
_active_proxy = apply_oleg_proxy_globally()
if _active_proxy:
    print(f"[OLEG] Global proxy initialized: {_active_proxy}")

# ---- App ----
app = FastAPI(title="О.Л.Е.Г. Backend", version="3.1.0")

@app.on_event("startup")
async def on_startup():
    import reminders_manager
    import task_scheduler
    import clipboard_assistant
    import tts_engine
    asyncio.create_task(reminders_manager.reminder_worker())
    asyncio.create_task(task_scheduler.scheduler_worker())
    asyncio.create_task(tts_engine.warmup_tts_cache())
    asyncio.create_task(clipboard_assistant.clipboard_monitor_worker())

# Безопасный CORS: разрешаем запросы только от Electron (file:// и локальный хост)
# Блокирует CSRF/RCE атаки от сторонних сайтов в браузере пользователя
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|null|file://.*)$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Опциональный токен авторизации
API_TOKEN = os.getenv("OLEG_API_TOKEN", "")


def verify_token(x_oleg_token: str | None = Header(None)):
    if API_TOKEN and x_oleg_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="Неверный токен доступа O.L.E.G.")


# ---- Schemas ----
class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


class SetupConfigRequest(BaseModel):
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None
    groq_key: str | None = None
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    throne_key: str | None = None
    throne_addr: str | None = None
    proxy: str | None = None
    tts_enabled: bool | None = None
    tts_voice: str | None = None
    tts_rate: str | None = None
    tts_pitch: str | None = None


class STTRequest(BaseModel):
    audio_base64: str
    mime_type: str = "audio/wav"
    hint_text: str | None = None


class SoundPlayRequest(BaseModel):
    sound_type: str = "notification"
    duration_sec: float = 4.0


class SoundSelectRequest(BaseModel):
    sound_type: str
    filename: str


class STTResponse(BaseModel):
    text: str
    status: str = "ok"


# ---- Endpoints ----
@app.get("/health")
async def health():
    """Health-check для Electron UI."""
    return {"status": "ok", "version": "3.1.0"}


from tools import normalize_proxy_url


@app.get("/api/config/status")
async def config_status():
    """Проверяет состояние первоначальной настройки О.Л.Е.Г.а и подключенных сервисов."""
    from tools import is_spotify_configured, is_throne_configured, get_oleg_proxy
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    setup_completed = False
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "").strip()
    proxy = get_oleg_proxy()

    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("SETUP_COMPLETED="):
                    setup_completed = line.split("=", 1)[1].strip().lower() in ("true", "1", "yes")

    # Первоначальная настройка нужна, если SETUP_COMPLETED не true или если ключ отсутствует
    setup_needed = (not setup_completed) or (not api_key)

    import tts_engine
    tts_on = tts_engine.is_tts_enabled()
    tts_v = os.getenv("TTS_VOICE", "ru-RU-DmitryNeural")

    groq_key = os.getenv("GROQ_API_KEY", "").strip()

    return {
        "setup_needed": setup_needed,
        "has_key": bool(api_key),
        "api_key": api_key or "",
        "model": model,
        "setup_completed": setup_completed,
        "has_groq_key": bool(groq_key),
        "groq_key": groq_key or "",
        "spotify_configured": is_spotify_configured(),
        "spotify_client_id": os.getenv("SPOTIPY_CLIENT_ID", ""),
        "spotify_client_secret": os.getenv("SPOTIPY_CLIENT_SECRET", ""),
        "throne_configured": is_throne_configured(),
        "throne_key": os.getenv("THRONE_KEY", ""),
        "proxy_configured": bool(proxy),
        "proxy_url": normalize_proxy_url(proxy) if proxy else "",
        "tts_enabled": tts_on,
        "tts_voice": tts_v,
        "tts_rate": os.getenv("TTS_RATE", "+8%").strip() or "+8%",
        "tts_pitch": os.getenv("TTS_PITCH", "-1Hz").strip() or "-1Hz",
        "base_url": os.getenv("OPENROUTER_BASE_URL", ""),
    }


@app.post("/api/config/setup")
async def config_setup(req: SetupConfigRequest):
    """Сохраняет параметры (ИИ, Spotify, Throne, Прокси) в .env и конфигурацию."""
    updates = {}
    clean_key = (req.api_key or "").strip()
    clean_model = (req.model or "").strip()
    clean_base_url = (req.base_url or "").strip()

    if clean_key:
        updates["OPENROUTER_API_KEY"] = clean_key
        # Автоматическое определение base_url по типу ключа
        if not clean_base_url:
            if clean_key.startswith("AQ.") or clean_key.startswith("AIzaSy"):
                updates["OPENROUTER_BASE_URL"] = "https://generativelanguage.googleapis.com/v1beta/openai/"
            elif clean_key.startswith("sk-or-"):
                updates["OPENROUTER_BASE_URL"] = "https://openrouter.ai/api/v1"

    if clean_base_url:
        updates["OPENROUTER_BASE_URL"] = clean_base_url

    if clean_model:
        updates["OPENROUTER_MODEL"] = clean_model
        updates["SETUP_COMPLETED"] = "true"

    if req.groq_key is not None:
        clean_gkey = req.groq_key.strip()
        updates["GROQ_API_KEY"] = clean_gkey
        os.environ["GROQ_API_KEY"] = clean_gkey

    if req.spotify_client_id is not None:
        updates["SPOTIPY_CLIENT_ID"] = req.spotify_client_id.strip()
    if req.spotify_client_secret is not None:
        updates["SPOTIPY_CLIENT_SECRET"] = req.spotify_client_secret.strip()
        from tools import reset_spotify_client
        reset_spotify_client()

    if req.throne_key is not None:
        clean_tkey = req.throne_key.strip()
        updates["THRONE_KEY"] = clean_tkey
        try:
            from tools import save_throne_api_credentials
            save_throne_api_credentials(key=clean_tkey)
        except Exception:
            pass

    if req.throne_addr is not None:
        updates["THRONE_ADDR"] = req.throne_addr.strip()

    if req.proxy is not None:
        clean_proxy = normalize_proxy_url(req.proxy)
        updates["OLEG_PROXY"] = clean_proxy
        os.environ["OLEG_PROXY"] = clean_proxy

    if req.tts_enabled is not None:
        updates["TTS_ENABLED"] = "true" if req.tts_enabled else "false"

    if req.tts_voice is not None:
        clean_voice = req.tts_voice.strip()
        updates["TTS_VOICE"] = clean_voice
        os.environ["TTS_VOICE"] = clean_voice

    if req.tts_rate is not None:
        clean_rate = req.tts_rate.strip()
        updates["TTS_RATE"] = clean_rate
        os.environ["TTS_RATE"] = clean_rate

    if req.tts_pitch is not None:
        clean_pitch = req.tts_pitch.strip()
        updates["TTS_PITCH"] = clean_pitch
        os.environ["TTS_PITCH"] = clean_pitch

    if updates:
        update_env_file(updates)

    return {
        "status": "ok",
        "updated": list(updates.keys()),
        "message": "Настройки успешно сохранены в .env",
    }


def match_fast_local_command(text: str) -> str | None:
    """
    Ультрабыстрый локальный диспетчер для типовых команд Windows и управления ПК (0ms без обращения к облачному ИИ).
    Мгновенно выполняет действие и возвращает готовый лаконичный ответ.
    """
    if not text:
        return None
    raw = text.strip().lower()
    clean = re.sub(r"^(?:олег\w*|алег\w*|олежк\w*)[\s,.:!]*", "", raw, flags=re.IGNORECASE).strip()
    clean = clean.strip(".?!,:; \t\r\n")
    if not clean:
        return "Слушаю тебя."

    import tools
    from brain import _format_tool_result_for_user

    # 1. Время и системная дата
    if re.search(r"\b(?:сколько\s+времени|который\s+час|время|какое\s+число|какой\s+день\s+недели|дата|текущее\s+время)\b", clean):
        return _format_tool_result_for_user(tools.get_current_time())

    # 2. Управление громкостью звука
    if re.search(r"\b(?:сделай\s+)?(?:по)?(?:громче|прибавь\s+(?:звук|громкость)|звук\s+громче)\b", clean):
        return _format_tool_result_for_user(tools.set_volume("+15"))
    if re.search(r"\b(?:сделай\s+)?(?:по)?(?:тише|убавь\s+(?:звук|громкость)|звук\s+тише)\b", clean):
        return _format_tool_result_for_user(tools.set_volume("-15"))
    if re.search(r"\b(?:выключи\s+звук|без\s+звука|мут)\b", clean):
        return _format_tool_result_for_user(tools.set_volume("mute"))
    if re.search(r"\b(?:включи\s+звук|размуть)\b", clean):
        return _format_tool_result_for_user(tools.set_volume("unmute"))
    m_vol = re.search(r"\b(?:громкость|звук)\s+(?:на\s+)?(\d{1,3})%?\b", clean)
    if m_vol:
        return _format_tool_result_for_user(tools.set_volume(m_vol.group(1)))

    # 3. Медиа и плеер
    if re.search(r"\b(?:пауза|поставь\s+на\s+паузу|останови\s+музыку|плей|продолжи\s+музыку|продолжить)\b", clean):
        return _format_tool_result_for_user(tools.media_control("play_pause"))
    if re.search(r"\b(?:следующий\s+трек|переключи\s+трек|песню\s+вперед|следующая\s+песня)\b", clean):
        return _format_tool_result_for_user(tools.media_control("next"))
    if re.search(r"\b(?:предыдущий\s+трек|песню\s+назад|предыдущая\s+песня)\b", clean):
        return _format_tool_result_for_user(tools.media_control("previous"))

    # 4. Запуск программ
    if re.search(r"\b(?:открой|запусти|покажи)\s+(?:браузер|browser)\b", clean) or clean in ("браузер", "browser"):
        return _format_tool_result_for_user(tools.open_or_focus_browser())
    m_prog = re.search(
        r"\b(?:запусти|открой|включи)\s+(калькулятор|блокнот|диспетчер\s+задач|проводник|paint|ножницы|терминал|командную\s+строку|стим|steam|дискорд|discord|спотифай|спотик|spotify|телеграм|telegram|хром|chrome|яндекс|yandex|браузер|browser)\b",
        clean,
    )
    if m_prog:
        return _format_tool_result_for_user(tools.open_program(m_prog.group(1)))
    if clean in (
        "калькулятор", "блокнот", "диспетчер задач", "проводник", "ножницы", "терминал",
        "paint", "стим", "steam", "дискорд", "discord", "спотифай", "спотик", "spotify",
        "телеграм", "telegram"
    ):
        return _format_tool_result_for_user(tools.open_program(clean))

    # 5. Окна рабочего стола
    if re.search(r"\b(?:закрой\s+окно|закрой\s+приложение|закрой\s+программу)\b", clean):
        return _format_tool_result_for_user(tools.manage_window("", "close"))
    if re.search(r"\b(?:сверни\s+все\s+окна|сверни\s+окна|покажи\s+рабочий\s+стол)\b", clean):
        return _format_tool_result_for_user(tools.manage_window("", "minimize_all"))

    # 6. Очистка корзины
    if re.search(r"\b(?:очисти|очистить)\s+корзину\b", clean):
        return _format_tool_result_for_user(tools.empty_recycle_bin())

    # 7. Блокировка и питание
    if re.search(r"\b(?:заблокируй\s+пк|заблокируй\s+компьютер|заблокируй\s+экран)\b", clean):
        return _format_tool_result_for_user(tools.control_power("lock"))
    if re.search(r"\b(?:спящий\s+режим|усыпи\s+пк|усыпи\s+компьютер)\b", clean):
        return _format_tool_result_for_user(tools.control_power("sleep"))

    # 8. VPN Throne
    if re.search(r"\b(?:включи|подключи)\s+(?:vpn|впн)\b", clean):
        return _format_tool_result_for_user(tools.manage_throne_vpn("on"))
    if re.search(r"\b(?:выключи|отключи)\s+(?:vpn|впн)\b", clean):
        return _format_tool_result_for_user(tools.manage_throne_vpn("off"))
    if re.search(r"\b(?:статус|состояние)\s+(?:vpn|впн)\b", clean):
        return _format_tool_result_for_user(tools.manage_throne_vpn("status"))

    # 9. Мгновенные приветствия и ответы (0ms)
    if re.search(r"^(?:привет|здравствуй|добрый\s+(?:день|вечер|утро)|хай|ку)\b", clean):
        return "Привет, Саня! Чем могу помочь?"
    if re.search(r"^(?:как\s+дела|как\s+ты|как\s+жизнь|как\s+оно)\b", clean):
        return "Всё отлично, готов к работе! А у тебя как?"
    if re.search(r"^(?:что\s+умеешь|что\s+ты\s+умеешь|на\s+что\s+способен)\b", clean):
        return "Управляю компьютером, программами, звуком, музыкой, окнами, таймерами и отвечаю на любые вопросы."
    if re.search(r"^(?:спокойной\s+ночи|доброй\s+ночи)\b", clean):
        return "Спокойной ночи, Саня! Приятных снов."
    if re.search(r"^(?:спасибо|благодарю|от\s+души)\b", clean):
        return "Всегда пожалуйста!"
    if re.search(r"^(?:кто\s+ты|как\s+тебя\s+зовут)\b", clean):
        return "Я твой личный голосовой ассистент О.Л.Е.Г."
    if re.search(r"^(?:отмена|отбой|хватит|стоп)\b", clean):
        return "Понял, отменяю."

    return None


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Синхронный эндпоинт (полный ответ за один запрос)."""
    fast_reply = match_fast_local_command(req.message)
    if fast_reply:
        return ChatResponse(reply=fast_reply)
    reply = await ask_llm(req.message)
    return ChatResponse(reply=reply)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    """Потоковый эндпоинт (мгновенный вывод токенов по мере генерации)."""
    fast_reply = match_fast_local_command(req.message)
    if fast_reply:
        async def fast_chunk_generator():
            yield fast_reply
        return StreamingResponse(fast_chunk_generator(), media_type="text/plain; charset=utf-8")

    async def chunk_generator():
        async for chunk in stream_ask_llm(req.message):
            yield chunk

    return StreamingResponse(chunk_generator(), media_type="text/plain; charset=utf-8")


@app.post("/api/clear")
async def clear():
    """Сброс контекстной памяти диалога (Short-term memory)."""
    clear_history()
    return {"status": "ok", "message": "История диалога очищена."}


# ---- Настройки звуков и рингтонов ----
@app.get("/api/sounds/list")
async def get_sounds_list():
    import sound_manager
    return {
        "status": "ok",
        "config": sound_manager.get_sounds_config(),
        "available_sounds": sound_manager.list_available_sounds(),
    }


@app.post("/api/sounds/play")
async def play_sound_endpoint(req: SoundPlayRequest):
    import sound_manager
    ok = sound_manager.play_sound(req.sound_type, duration_sec=req.duration_sec)
    return {"status": "ok" if ok else "error"}


@app.post("/api/sounds/select")
async def select_sound_endpoint(req: SoundSelectRequest):
    import sound_manager
    cfg = sound_manager.get_sounds_config()
    st = req.sound_type.lower()
    if st in ("notification", "notification_sound", "уведомление"):
        cfg["notification_sound"] = req.filename
    elif st in ("alarm", "alarm_sound", "timer", "будильник", "таймер"):
        cfg["alarm_sound"] = req.filename
    else:
        cfg[req.sound_type] = req.filename
    sound_manager.save_sounds_config(cfg)
    return {"status": "ok", "config": cfg}


# ---- Запланированные отложенные действия ----
@app.get("/api/schedule/list")
async def get_scheduled_tasks():
    import task_scheduler
    return {"status": "ok", "tasks": task_scheduler.list_scheduled_tasks()}


@app.post("/api/schedule/cancel")
async def cancel_scheduled_task_endpoint(req: dict):
    import task_scheduler
    res = task_scheduler.cancel_scheduled_task(req.get("query", ""))
    return {"status": "ok", "message": res}


# ---- Синтез речи (TTS) ----
class TTSRequest(BaseModel):
    text: str
    voice: str | None = None


@app.post("/api/tts/speak")
async def tts_speak_endpoint(req: TTSRequest):
    import tts_engine
    ok = await tts_engine.speak_text(req.text, voice=req.voice or "")
    return {"status": "ok" if ok else "error"}


# ---- История буфера обмена ----
@app.get("/api/clipboard/history")
async def get_clipboard_history_endpoint(limit: int = 10, query: str = ""):
    import clipboard_assistant
    formatted = clipboard_assistant.get_clipboard_history(limit=limit, query=query)
    items = clipboard_assistant.get_clipboard_raw_items(limit=limit, query=query)
    return {"status": "ok", "formatted": formatted, "items": items, "history": items}


# ---- Точечное удаление факта из памяти ----
@app.post("/api/memory/delete")
async def delete_memory_endpoint(req: dict):
    import memory_manager
    res = memory_manager.delete_memory(req.get("query", ""))
    return {"status": "ok", "message": res}


@app.post("/api/stt", response_model=STTResponse)
async def speech_to_text(req: STTRequest):
    """Распознавание речи через голосовую нейросеть (Gemini / Whisper / OpenRouter)."""
    text = await transcribe_audio(req.audio_base64, req.mime_type, req.hint_text)
    return STTResponse(text=text, status="ok")


# ---- Потоковое распознавание речи в реальном времени (Live STT via Vosk) ----
_vosk_model = None

def get_vosk_model():
    global _vosk_model
    if _vosk_model is None:
        try:
            import vosk
            vosk.SetLogLevel(-1)
            _vosk_model = vosk.Model(model_name="vosk-model-small-ru-0.22")
        except Exception as e:
            print("[Vosk Init Error]:", e)
            return None
    return _vosk_model


@app.websocket("/ws/stt")
async def websocket_stt(ws: WebSocket):
    """Потоковый WebSocket для моментального вывода слов прямо во время речи."""
    await ws.accept()
    model = get_vosk_model()
    if not model:
        await ws.send_json({"type": "error", "message": "Модель Vosk не загружена"})
        await ws.close()
        return

    import vosk
    rec = vosk.KaldiRecognizer(model, 16000)
    confirmed_chunks = []
    last_sent = ""

    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"]:
                data = msg["bytes"]
                if rec.AcceptWaveform(data):
                    res = json.loads(rec.Result())
                    t = res.get("text", "").strip()
                    if t:
                        confirmed_chunks.append(t)
                        current = " ".join(confirmed_chunks).strip()
                        if current != last_sent:
                            last_sent = current
                            await ws.send_json({"type": "partial", "text": current})
                else:
                    part = json.loads(rec.PartialResult()).get("partial", "").strip()
                    current = (" ".join(confirmed_chunks) + " " + part).strip()
                    if current and current != last_sent:
                        last_sent = current
                        await ws.send_json({"type": "partial", "text": current})
            elif "text" in msg and msg["text"]:
                try:
                    cmd = json.loads(msg["text"])
                    if cmd.get("action") == "finalize":
                        final = json.loads(rec.FinalResult()).get("text", "").strip()
                        if final:
                            confirmed_chunks.append(final)
                        full = " ".join(confirmed_chunks).strip()
                        await ws.send_json({"type": "final", "text": full})
                        break
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print("[WS STT Exception]:", e)


# ---- Фоновый детектор ключевого слова «Олег» (Wake Word Engine) ----
WAKE_PATTERNS = [
    r"(?:\b|\A)олег\w*(?:\b|\Z)",
    r"(?:\b|\A)алег\w*(?:\b|\Z)",
    r"(?:\b|\A)олежк\w*(?:\b|\Z)",
    r"(?:\b|\A)олежек(?:\b|\Z)",
    r"(?:\b|\A)алежек(?:\b|\Z)",
    r"(?:\b|\A)коллег\w*(?:\b|\Z)",
    r"(?:\b|\A)олех(?:\b|\Z)",
    r"(?:\b|\A)алех(?:\b|\Z)",
    r"(?:\b|\A)лёха(?:\b|\Z)",
    r"(?:\b|\A)леха(?:\b|\Z)",
]

def contains_wake_word(text: str) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in WAKE_PATTERNS)


def extract_vosk_text(json_str: str) -> str:
    """Извлекает распознанный текст из JSON Vosk любого формата (text, partial или массив)."""
    try:
        data = json.loads(json_str)
        val = data.get("partial") or data.get("text") or ""
        if isinstance(val, list):
            val = " ".join(item.get("word", "") for item in val if isinstance(item, dict))
        if not val and "partial_result" in data:
            val = " ".join(item.get("word", "") for item in data["partial_result"] if isinstance(item, dict))
        if not val and "result" in data:
            val = " ".join(item.get("word", "") for item in data["result"] if isinstance(item, dict))
        return str(val).lower().strip()
    except Exception:
        return ""


@app.websocket("/ws/wakeword")
async def websocket_wakeword(ws: WebSocket):
    """
    Высокоскоростной фоновый WebSocket:
    Моментально детектирует слово «Олег» по живым partial-результатам (без задержек на сборку FST),
    после чего непрерывно принимает вопрос пользователя.
    """
    await ws.accept()
    model = get_vosk_model()
    if not model:
        await ws.send_json({"type": "error", "message": "Модель Vosk не загружена"})
        await ws.close()
        return

    import vosk
    # Единый постоянный распознаватель (без блокирующих рекомпиляций)
    rec = vosk.KaldiRecognizer(model, 16000)
    rec.SetWords(True)
    rec.SetPartialWords(True)

    wake_triggered = False
    command_chunks = []
    current_text = ""

    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg and msg["bytes"]:
                data = msg["bytes"]
                if rec.AcceptWaveform(data):
                    t = extract_vosk_text(rec.Result())
                    is_final = True
                else:
                    t = extract_vosk_text(rec.PartialResult())
                    is_final = False

                if t:
                    if not wake_triggered:
                        if contains_wake_word(t):
                            wake_triggered = True
                            print(f"[WakeWord] ⚡ Мгновенно распознано имя «Олег» при первом звуке: '{t}'")
                            await ws.send_json({
                                "type": "wake_detected",
                                "text": t,
                            })
                    else:
                        if is_final:
                            command_chunks.append(t)
                            current_text = " ".join(command_chunks).strip()
                        else:
                            full = (" ".join(command_chunks) + " " + t).strip()
                            current_text = full

                        if current_text:
                            await ws.send_json({
                                "type": "command_speech",
                                "text": current_text,
                            })

            elif "text" in msg and msg["text"]:
                try:
                    cmd = json.loads(msg["text"])
                    action = cmd.get("action")
                    if action == "reset":
                        rec.Reset()
                        wake_triggered = False
                        command_chunks = []
                        current_text = ""
                        await ws.send_json({"type": "reset_done"})
                    elif action == "finalize":
                        final_res = extract_vosk_text(rec.FinalResult())
                        if final_res:
                            command_chunks.append(final_res)
                        full = (" ".join(command_chunks) or current_text).strip()
                        rec.Reset()
                        wake_triggered = False
                        command_chunks = []
                        current_text = ""
                        print(f"[WakeWord] 🎤 Завершена голосовая команда: '{full}'")
                        await ws.send_json({"type": "final_command", "text": full})
                except Exception:
                    pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print("[WakeWord WS Exception]:", e)


class VoiceCommandRequest(BaseModel):
    text: str = ""
    audio_base64: str | None = None


@app.post("/api/voice/command")
async def handle_voice_command(req: VoiceCommandRequest):
    """
    Выполняет голосовую команду в фоновом режиме (по вызову «Олег») без открытия поисковой строки.
    Действие выполняется на ПК первым, затем всплывает Windows Toast уведомление.
    """
    raw_text = (req.text or "").strip()
    if not raw_text and req.audio_base64:
        from brain import transcribe_audio
        try:
            raw_text = await transcribe_audio(req.audio_base64, "audio/wav")
        except Exception as stt_err:
            print(f"[Voice Command STT Error]: {stt_err}")

    clean_cmd = re.sub(r"^(?:олег\w*|алег\w*|олежк\w*)[\s,.:!]*", "", raw_text, flags=re.IGNORECASE).strip()
    import tts_engine
    tts_on = tts_engine.is_tts_enabled()

    if not clean_cmd:
        reply = "Слушаю тебя."
        spoken = False
        if tts_on:
            try:
                spoken = await tts_engine.speak_text(reply)
            except Exception as e:
                print(f"[Voice Name TTS Error]: {e}")
        if not spoken:
            try:
                import reminders_manager
                reminders_manager.show_toast("О.Л.Е.Г. 🎙️", reply, play_sound_type=None)
            except Exception:
                pass
        return {"status": "name_only", "reply": reply}

    # Быстрый путь: проверяем локальные системные команды без задержки сети
    fast_reply = match_fast_local_command(clean_cmd)
    if fast_reply:
        reply = fast_reply
    else:
        # Выполняем команду через ask_llm в голосовом режиме (емкий разговорный ответ для озвучки вслух)
        reply = await ask_llm(clean_cmd, voice_mode=True)

    # Если голос включен — озвучиваем голосом.
    # Если голос выключен или не сработал синтез речи — гарантированно выводим Windows Toast уведомление.
    spoken = False
    if tts_on:
        try:
            spoken = await tts_engine.speak_text(reply)
        except Exception as e:
            print(f"[Voice Command TTS Error]: {e}")
    if not spoken:
        try:
            import reminders_manager
            reminders_manager.show_toast("О.Л.Е.Г. 🎙️", reply, play_sound_type=None)
        except Exception:
            pass

    return {"status": "ok", "command": clean_cmd, "reply": reply}


# ---- Entry point ----
if __name__ == "__main__":
    import uvicorn

    print("[OLEG] Backend starting at http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
