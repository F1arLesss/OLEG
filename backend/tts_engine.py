"""
О.Л.Е.Г. — Движок синтеза речи (Text-to-Speech / TTS)
Использует высококачественные нейросетевые голоса Microsoft Edge Neural (edge-tts).
Полностью бесплатен, не требует API-ключей, поддерживает мужской (Dmitry) и женский (Svetlana) голоса.
"""

import asyncio
import hashlib
import io
import os
import re
import tempfile
import sound_manager

DEFAULT_VOICE = "ru-RU-DmitryNeural"

_TTS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "data", "tts_cache")
os.makedirs(_TTS_CACHE_DIR, exist_ok=True)
_TTS_MEM_CACHE: dict[str, bytes] = {}


def _get_cache_hash(voice: str, text: str, rate: str = "", pitch: str = "") -> str:
    r = rate or os.getenv("TTS_RATE", "+8%").strip() or "+8%"
    p = pitch or os.getenv("TTS_PITCH", "-1Hz").strip() or "-1Hz"
    return hashlib.sha256(f"{voice}:{r}:{p}:{text}".encode("utf-8")).hexdigest()[:16]


def is_tts_enabled() -> bool:
    """Проверяет, включена ли голосовая озвучка ответов в настройках."""
    val = os.getenv("TTS_ENABLED", "true").strip().lower()
    return val in ("true", "1", "yes", "on")


def clean_text_for_tts(text: str) -> str:
    """
    Очищает ответ ассистента от markdown, ссылок, спецсимволов и лишних знаков,
    а также делает речь естественной и разговорной для нейросетевого диктора.
    """
    if not text:
        return ""

    # Убираем блоки кода ```...```
    t = re.sub(r"```[\s\S]*?```", " фрагмент кода ", text)
    # Убираем инлайн-код `...`
    t = re.sub(r"`[^`]+`", " ", t)
    # Заменяем ссылки [текст](url) на текст
    t = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", t)
    # Убираем чистые URL
    t = re.sub(r"https?://\S+", " ссылка ", t)
    # Заменяем технические системные сообщения на естественную русскую речь
    t = re.sub(r"(?i)Запущено через системный вызов:\s*(\w+)", r"Открываю \1.", t)
    t = re.sub(r"(?i)Программа\s+['\"]?([\w\s-]+?)['\"]?\s+уже запущена.*", r"Вывел \1 на передний план.", t)
    t = re.sub(r"(?i)Программа\s+([\w\s-]+?)\s+найдена и запущена.*", r"Открываю \1.", t)
    # Убираем markdown символы (*, #, _, ~, >, -)
    t = re.sub(r"[\*#_~>]+", " ", t)
    # Убираем emoji
    t = re.sub(r"[\U00010000-\U0010ffff]", "", t)

    # Человеческая озвучка частых компьютерных терминов и аббревиатур
    t = re.sub(r"(?i)\bо\.?л\.?е\.?г\.?", "Олег", t)
    t = re.sub(r"(?i)\bна\s+пк\b", "на компьютере", t)
    t = re.sub(r"(?i)\bтвоем\s+пк\b", "твоем компьютере", t)
    t = re.sub(r"(?i)\bтвой\s+пк\b", "твой компьютер", t)
    t = re.sub(r"(?i)\bпк\b", "компьютер", t)
    t = re.sub(r"(?i)\bфпс\b", "кадров в секунду", t)
    t = re.sub(r"(?i)\b(\d+)\s*(?:гб|gb)\b", r"\1 гигабайт", t)
    t = re.sub(r"(?i)\b(\d+)\s*(?:мб|mb)\b", r"\1 мегабайт", t)
    t = re.sub(r"(?i)\bwi-?fi\b", "вай-фай", t)
    t = re.sub(r"(?i)\bцпу\b", "процессор", t)
    t = re.sub(r"(?i)\bгпу\b", "видеокарта", t)

    # Преобразуем формат времени (например 19:30:15 -> 19 30) для безопасного чтения Edge TTS
    t = re.sub(r"(\d{1,2}):(\d{2})(?::\d{2})?", r"\1 \2", t)
    # Нормализуем восклицательные знаки (в Edge TTS DmitryNeural они вызывают NoAudioReceived)
    t = re.sub(r"!+", ".", t)
    # Убираем многоточия и повторяющиеся точки
    t = re.sub(r"\.{2,}", ".", t)
    # Заменяем длинные тире (—, –) на запятые с паузой (в Edge-TTS они часто вызывают NoAudioReceived)
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    # Убираем кавычки
    t = re.sub(r"[«»\"']", " ", t)

    # Интонационные микропаузы перед союзами, если запятая пропущена нейросетью
    t = re.sub(r"([^\s,;])\s+(но|а|однако|потому что|так как|чтобы)\b", r"\1, \2", t)

    # Убираем технические спецсимволы, оставляя буквы, цифры, дефисы и знаки препинания
    t = re.sub(r"[^\w\s.,?\-]+", " ", t, flags=re.UNICODE)
    # Нормализуем пробелы и запятые
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r",+", ",", t)
    t = re.sub(r"\s*\.\s*", ". ", t)
    t = re.sub(r"\.+", ".", t)
    t = re.sub(r"\s+([.,?])", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^,\s*", "", t)
    t = re.sub(r",\s*\.", ".", t)
    # Гарантируем корректное завершение предложения
    if t and not t.endswith((".", "?", "!")):
        t += "."
    return t


async def _synthesize_single_voice(clean_text: str, voice_name: str, rate: str = "", pitch: str = "") -> bytes:
    import edge_tts
    import tools
    proxy = tools.get_oleg_proxy()
    actual_rate = rate or os.getenv("TTS_RATE", "+8%").strip() or "+8%"
    actual_pitch = pitch or os.getenv("TTS_PITCH", "-1Hz").strip() or "-1Hz"
    communicate = edge_tts.Communicate(
        clean_text,
        voice_name,
        rate=actual_rate,
        pitch=actual_pitch,
        proxy=proxy,
        connect_timeout=10,
        receive_timeout=30,
    )
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


async def generate_speech_bytes(text: str, voice: str = "", rate: str = "", pitch: str = "") -> bytes:
    """Генерирует MP3 аудиобайты речи с интеллектуальным двухуровневым кэшированием (RAM + SSD) и резервированием голоса."""
    clean_text = clean_text_for_tts(text)
    if not clean_text:
        return b""

    # Динамически получаем выбранный пользователем голос из переменной окружения
    env_voice = os.getenv("TTS_VOICE", "").strip()
    selected_voice = voice or env_voice or DEFAULT_VOICE
    actual_rate = rate or os.getenv("TTS_RATE", "+8%").strip() or "+8%"
    actual_pitch = pitch or os.getenv("TTS_PITCH", "-1Hz").strip() or "-1Hz"

    # 1. Сверхбыстрая проверка в памяти (0.00001 сек)
    cache_hash = _get_cache_hash(selected_voice, clean_text, rate=actual_rate, pitch=actual_pitch)
    if cache_hash in _TTS_MEM_CACHE:
        return _TTS_MEM_CACHE[cache_hash]

    # 2. Проверка дискового кэша на SSD (0.001 сек)
    cache_file = os.path.join(_TTS_CACHE_DIR, f"{cache_hash}.mp3")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as f:
                data = f.read()
                if data:
                    _TTS_MEM_CACHE[cache_hash] = data
                    return data
        except Exception:
            pass

    voices_to_try = [selected_voice]
    if selected_voice != "ru-RU-DmitryNeural":
        voices_to_try.append("ru-RU-DmitryNeural")
    elif selected_voice != "ru-RU-SvetlanaNeural":
        voices_to_try.append("ru-RU-SvetlanaNeural")

    for v in voices_to_try:
        try:
            # Таймаут 15.0 сек для стабильного синтеза и скачивания аудиопотока через сеть
            data = await asyncio.wait_for(_synthesize_single_voice(clean_text, v, rate=actual_rate, pitch=actual_pitch), timeout=15.0)
            if data:
                _TTS_MEM_CACHE[cache_hash] = data
                try:
                    with open(cache_file, "wb") as f:
                        f.write(data)
                except Exception:
                    pass
                return data
        except Exception as e:
            print(f"[TTS Warning] Сбой синтеза голосом {v}: {e}. Пробуем альтернативный голос...")
            continue

    return b""


async def warmup_tts_cache():
    """Фоновый разогрев кэша голосовых фраз на старте сервера для мгновенного отклика (0ms)."""
    phrases = [
        "Слушаю тебя.",
        "Привет, Саня! Чем могу помочь?",
        "Всегда пожалуйста!",
        "Я твой личный голосовой ассистент О.Л.Е.Г.",
        "Открываю калькулятор.",
        "Открываю блокнот.",
        "Открываю диспетчер задач.",
        "Открываю проводник.",
        "Открываю браузер.",
        "Открываю Spotify.",
        "Вывел Spotify на передний план.",
        "Открываю Steam.",
        "Вывел Steam на передний план.",
        "Открываю Discord.",
        "Вывел Discord на передний план.",
        "Открываю Telegram.",
        "Вывел Telegram на передний план.",
        "Поставил на паузу.",
        "Продолжаю воспроизведение.",
        "Переключил на следующий трек.",
        "Корзина очищена.",
        "Звук выключен.",
        "Звук включен.",
    ]
    cur_voice = os.getenv("TTS_VOICE", DEFAULT_VOICE).strip() or DEFAULT_VOICE
    for phrase in phrases:
        try:
            ch = _get_cache_hash(cur_voice, phrase)
            cf = os.path.join(_TTS_CACHE_DIR, f"{ch}.mp3")
            if not os.path.exists(cf):
                await generate_speech_bytes(phrase, voice=cur_voice)
        except Exception:
            pass


async def speak_text(text: str, voice: str = "") -> bool:
    """
    Синтезирует речь и немедленно воспроизводит её через звуковую подсистему.
    """
    clean = clean_text_for_tts(text)
    if not clean:
        return False

    try:
        audio_data = await generate_speech_bytes(clean, voice=voice)
        if not audio_data:
            return False

        # Сохраняем во временный mp3 файл и воспроизводим
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name
            f.write(audio_data)

        # Воспроизводим через sound_manager с аппаратным контролем длительности Windows MCI и автоудалением
        sound_manager.play_sound(
            sound_type="notification",
            custom_filename=tmp_path,
            duration_sec=None,
            delete_on_finish=True,
        )
        return True
    except Exception as e:
        print(f"[TTS Error]: {e}")
        return False
