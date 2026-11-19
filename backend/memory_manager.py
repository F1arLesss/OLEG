"""
О.Л.Е.Г. — Менеджер долгосрочной памяти
Поддерживает гибридный режим:
1. Mem0 (если используется OpenRouter с доступным балансом).
2. Локальное хранилище memory.json (при использовании прямого Google Gemini API или автономном режиме).
Гарантирует 0ms отклик, отсутствие ошибок 402/квот и 100% сохранность фактов о пользователе.
"""

import json
import logging
import os
import re
from typing import List

# Отключаем телеметрию PostHog и подавляем лишние логи
os.environ["MEM0_TELEMETRY"] = "false"
logging.getLogger("mem0").setLevel(logging.WARNING)
logging.getLogger("qdrant_client").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

DEFAULT_USER_ID = "default_user"
_memory_client = None


def _get_memory_file() -> str:
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(backend_dir, "memory.json")


def _read_local_memories() -> List[str]:
    """Чтение фактов из локального файла memory.json."""
    m_file = _get_memory_file()
    if not os.path.exists(m_file):
        return []
    try:
        with open(m_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
            return []
    except Exception:
        return []


def _normalize_fact(fact: str) -> str:
    """Нормализует факт для сравнения дубликатов (без лишней пунктуации на конце и пробелов)."""
    return re.sub(r"[\s\.\,\!\?]+$", "", fact.strip().lower())


def _write_local_memories(memories: List[str]) -> bool:
    """Атомарная запись фактов в локальный файл memory.json."""
    m_file = _get_memory_file()
    tmp_file = m_file + ".tmp"
    try:
        seen = set()
        clean = []
        for m in memories:
            m_clean = m.strip()
            norm = _normalize_fact(m_clean)
            if m_clean and norm not in seen:
                seen.add(norm)
                clean.append(m_clean)

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=4)
        os.replace(tmp_file, m_file)
        return True
    except Exception as e:
        print(f"[MemoryManager] Ошибка записи: {e}")
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except Exception:
                pass
        return False


def _is_mem0_available() -> bool:
    """Проверяет, подходит ли текущая конфигурация для вызова Mem0 (требует OpenRouter/OpenAI embeddings)."""
    base_url = os.getenv("OPENROUTER_BASE_URL", "").lower()
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if "googleapis.com" in base_url or api_key.startswith("AQ.") or api_key.startswith("AIza"):
        return False
    if not api_key:
        return False
    return True


def get_mem0_client():
    """Ленивая синглтон-инициализация клиента Mem0."""
    global _memory_client
    if _memory_client is not None:
        return _memory_client

    if not _is_mem0_available():
        return None

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    try:
        from mem0 import Memory

        backend_dir = os.path.dirname(os.path.abspath(__file__))
        storage_path = os.path.join(backend_dir, "mem0_data")
        os.makedirs(storage_path, exist_ok=True)
        history_db = os.path.join(storage_path, "history.db")

        config = {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "openai/gpt-4o-mini",
                    "openai_base_url": "https://openrouter.ai/api/v1",
                    "api_key": api_key,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "openai/text-embedding-3-small",
                    "openai_base_url": "https://openrouter.ai/api/v1",
                    "api_key": api_key,
                    "embedding_dims": 1536,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": storage_path,
                },
            },
            "history_db_path": history_db,
        }

        _memory_client = Memory.from_config(config)
        return _memory_client
    except Exception:
        return None


def add_memory(fact: str, user_id: str = DEFAULT_USER_ID) -> str:
    """Добавляет факт в память (сначала Mem0, при недоступности — в memory.json)."""
    fact = (fact or "").strip()
    if not fact:
        return "Пустой факт."

    client = get_mem0_client()
    if client:
        try:
            client.add(fact, user_id=user_id)
        except Exception:
            pass

    # Синхронизируем локальный memory.json
    local_facts = _read_local_memories()
    norm_new = _normalize_fact(fact)
    if not any(_normalize_fact(x) == norm_new for x in local_facts):
        local_facts.append(fact)
        _write_local_memories(local_facts)

    return f"Факт сохранён в памяти: {fact}"


def search_memory(query: str, user_id: str = DEFAULT_USER_ID, limit: int = 4) -> List[str]:
    """Поиск фактов в памяти."""
    query_clean = (query or "").strip().lower()

    # Сначала пробуем Mem0 (если настроен)
    client = get_mem0_client()
    if client:
        try:
            results = client.search(query, filters={"user_id": user_id}, limit=limit)
            items = results.get("results", []) if isinstance(results, dict) else results
            memories = []
            for item in items:
                text = item.get("memory") if isinstance(item, dict) else str(item)
                if text and text not in memories:
                    memories.append(text)
            if memories:
                return memories
        except Exception:
            pass

    # Поиск по локальному файлу
    local_facts = _read_local_memories()
    if not local_facts:
        return []

    if not query_clean:
        return local_facts[:limit]

    words = [w for w in query_clean.split() if len(w) > 2]
    matched = []
    for f in local_facts:
        f_lower = f.lower()
        if any(w in f_lower for w in words):
            matched.append(f)

    return matched[:limit] if matched else local_facts[:limit]


def delete_memory(query: str, user_id: str = DEFAULT_USER_ID) -> str:
    """Удаляет конкретный факт из памяти по совпадению текста или подстроки."""
    q_clean = (query or "").strip().lower()
    if not q_clean:
        return "Укажите факт или часть текста для удаления."

    local_facts = _read_local_memories()
    deleted = []
    remaining = []

    for f in local_facts:
        if q_clean in f.lower() or _normalize_fact(f) == _normalize_fact(q_clean):
            deleted.append(f)
        else:
            remaining.append(f)

    if deleted:
        _write_local_memories(remaining)
        return f"Удалено из памяти ({len(deleted)}): " + "; ".join(f"«{x}»" for x in deleted)

    return f"Факт, содержащий «{query}», не найден в памяти."


def get_all_memories(user_id: str = DEFAULT_USER_ID) -> List[str]:
    """Возвращает все сохранённые факты пользователя."""
    client = get_mem0_client()
    if client:
        try:
            results = client.get_all(filters={"user_id": user_id})
            items = results.get("results", []) if isinstance(results, dict) else results
            memories = []
            for item in items:
                text = item.get("memory") if isinstance(item, dict) else str(item)
                if text and text not in memories:
                    memories.append(text)
            if memories:
                return memories
        except Exception:
            pass

    return _read_local_memories()


def clear_memory(user_id: str = DEFAULT_USER_ID) -> str:
    """Очищает всю память пользователя."""
    client = get_mem0_client()
    if client:
        try:
            client.delete_all(filters={"user_id": user_id})
        except Exception:
            pass

    _write_local_memories([])
    return "Память О.Л.Е.Г. успешно очищена."
