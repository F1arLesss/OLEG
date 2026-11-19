"""
О.Л.Е.Г. — Tools Module (God Mode)
Универсальный исполнитель системных команд, поиска приложений, управления окнами и звуком.
"""

import asyncio
import base64
import configparser
import ctypes
from ctypes import cast, POINTER, wintypes
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
from PIL import ImageGrab
import urllib.parse
import urllib.request
import webbrowser
import websockets
import winreg
import winsound
import zoneinfo
import comtypes
from comtypes import CLSCTX_ALL
from plyer import notification
import psutil
import pyperclip
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import urllib.parse
import webbrowser
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, ISimpleAudioVolume

import requests

# ---- Загрузка переменных окружения и глобальная настройка прокси ----
def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()


def is_vpn_active() -> bool:
    """Мгновенно проверяет, активен ли VPN (адаптер throne-tun включен) за ~1мс через psutil."""
    try:
        stats = psutil.net_if_stats()
        for ifc, s in stats.items():
            if "throne" in ifc.lower() and s.isup:
                return True
    except Exception:
        pass
    return False


def normalize_proxy_url(proxy: str) -> str:
    """Приводит прокси к стандартному виду: scheme://ip:port или scheme://user:pass@ip:port."""
    if not proxy:
        return ""
    p = proxy.strip()
    if not p:
        return ""

    scheme = "http"
    m_scheme = re.match(r"^([a-zA-Z0-9+.-]+)://(.*)$", p)
    if m_scheme:
        raw_scheme = m_scheme.group(1).lower()
        scheme = "http" if raw_scheme == "scheme" else raw_scheme
        body = m_scheme.group(2).strip()
    else:
        body = p

    body = body.rstrip("/")
    if "@" in body:
        auth_part, host_part = body.split("@", 1)
        return f"{scheme}://{auth_part}@{host_part}"

    parts = body.split(":")
    def is_port(s):
        return s.isdigit() and 1 <= int(s) <= 65535

    if len(parts) == 4:
        if is_port(parts[3]):
            user, pwd, host, port = parts[0], parts[1], parts[2], parts[3]
            return f"{scheme}://{user}:{pwd}@{host}:{port}"
        elif is_port(parts[1]):
            host, port, user, pwd = parts[0], parts[1], parts[2], parts[3]
            return f"{scheme}://{user}:{pwd}@{host}:{port}"
        else:
            user, pwd, host, port = parts[0], parts[1], parts[2], parts[3]
            return f"{scheme}://{user}:{pwd}@{host}:{port}"
    elif len(parts) == 2:
        return f"{scheme}://{parts[0]}:{parts[1]}"

    return f"{scheme}://{body}"


def get_oleg_proxy() -> str | None:
    """Возвращает нормализованный URL независимого прокси для О.Л.Е.Г.а из .env при наличии."""
    _load_env()
    raw = os.getenv("OLEG_PROXY") or os.getenv("PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or ""
    raw = raw.strip()
    if not raw:
        return None
    normalized = normalize_proxy_url(raw)
    return normalized if normalized else None


def apply_oleg_proxy_globally() -> str | None:
    """
    Очищает os.environ от глобального перехвата прокси, чтобы не ломать сетевой стек
    при переключении VPN, и возвращает настроенный URL прокси при необходимости.
    """
    for var in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        os.environ.pop(var, None)
    return get_oleg_proxy()


_load_env()
apply_oleg_proxy_globally()

# ---- Spotify Web API (с поддержкой прокси) ----
_spotify_client = None
_spotify_cached_proxy = None

def get_spotify_client():
    """Ленивая и безопасная инициализация клиента Spotify с учетом состояния VPN."""
    global _spotify_client, _spotify_cached_proxy

    vpn_on = is_vpn_active()
    proxy_url = None if vpn_on else get_oleg_proxy()

    # Если клиент уже создан с этим же состоянием прокси, возвращаем его
    if _spotify_client is not None and _spotify_cached_proxy == proxy_url:
        return _spotify_client

    client_id = os.getenv("SPOTIPY_CLIENT_ID") or os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET") or os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI") or os.getenv("SPOTIFY_REDIRECT_URI", "https://example.com/callback")

    if not client_id or not client_secret:
        return None

    try:
        cache_path = os.path.join(os.path.dirname(__file__), ".cache")
        proxies_dict = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        session = requests.Session()
        session.trust_env = False
        if proxies_dict:
            session.proxies = proxies_dict

        auth_manager = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope="user-modify-playback-state user-read-playback-state user-library-read",
            cache_path=cache_path,
            open_browser=False,
            proxies=proxies_dict,
            requests_session=session,
        )
        _spotify_client = spotipy.Spotify(
            auth_manager=auth_manager,
            proxies=proxies_dict,
            requests_session=session,
        )
        _spotify_cached_proxy = proxy_url
        return _spotify_client
    except Exception as e:
        print(f"[Spotify] Ошибка инициализации (прокси: {proxy_url}): {e}")
        return None


def is_spotify_configured() -> bool:
    """Проверяет наличие Client ID и Client Secret для Spotify в .env."""
    _load_env()
    cid = (os.getenv("SPOTIPY_CLIENT_ID") or os.getenv("SPOTIFY_CLIENT_ID") or "").strip()
    secret = (os.getenv("SPOTIPY_CLIENT_SECRET") or os.getenv("SPOTIFY_CLIENT_SECRET") or "").strip()
    return bool(cid and secret)


def reset_spotify_client():
    """Сбрасывает закешированный экземпляр Spotify клиента при обновлении ключей."""
    global _spotify_client, _spotify_cached_proxy
    _spotify_client = None
    _spotify_cached_proxy = None


# ---- Стоп-лист деструктивных команд/паттернов PowerShell ----
BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bdel\b\s+", re.IGNORECASE),
    re.compile(r"\bremove-item\b", re.IGNORECASE),
    re.compile(r"\brm\s+(-rf?|/)", re.IGNORECASE),
    re.compile(r"\bstop-process\b", re.IGNORECASE),
    re.compile(r"\bstop-computer\b", re.IGNORECASE),
    re.compile(r"\brestart-computer\b", re.IGNORECASE),
    re.compile(r"\bclear-disk\b", re.IGNORECASE),
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
]

# Список распространенных GUI-приложений Windows
GUI_APPS = {
    "notepad", "notepad.exe",
    "calc", "calc.exe",
    "mspaint", "mspaint.exe",
    "explorer", "explorer.exe",
    "cmd", "cmd.exe",
    "taskmgr", "taskmgr.exe",
}

# Словарь синонимов и сленговых названий программ
COMMON_APP_ALIASES = {
    "браузер": "firefox",
    "browser": "firefox",
    "брейв": "brave",
    "хром": "chrome",
    "гугл": "chrome",
    "яндекс": "yandex",
    "дс": "discord",
    "дискорд": "discord",
    "discord": "discord",
    "обс": "obs",
    "obs": "obs",
    "obs studio": "obs",
    "телега": "telegram",
    "телеграм": "telegram",
    "telegram": "telegram",
    "стим": "steam",
    "steam": "steam",
    "спотифай": "spotify",
    "спотик": "spotify",
    "spotify": "spotify",
    "блокнот": "notepad",
    "калькулятор": "calc",
    "проводник": "explorer",
    "терминал": "wt",
    "код": "code",
    "вскод": "code",
}


def get_default_browser() -> tuple[str | None, str]:
    """
    Определяет путь к .exe и имя процесса браузера по умолчанию в Windows из реестра.
    Возвращает (exe_path, proc_name), например:
    ('C:\\Program Files\\Mozilla Firefox\\firefox.exe', 'firefox')
    """
    try:
        reg_path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")

        cmd_path = rf"{prog_id}\shell\open\command"
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, cmd_path) as key:
            cmd, _ = winreg.QueryValueEx(key, "")

        match = re.search(r'"([^"]+\.exe)"', cmd, re.IGNORECASE) or re.search(r'([^\s]+\.exe)', cmd, re.IGNORECASE)
        if match:
            exe_path = match.group(1)
            proc_name = os.path.splitext(os.path.basename(exe_path))[0].lower()
            return exe_path, proc_name
    except Exception as e:
        print(f"[Browser] Ошибка чтения дефолтного браузера из реестра: {e}")

    for candidate in ["firefox", "chrome", "msedge", "brave", "yandex", "opera"]:
        cand_path = _find_in_app_paths(candidate)
        if cand_path:
            return cand_path, candidate

    return None, "firefox"


# Автоматически привязываем алиас "браузер" к реальному браузеру по умолчанию
try:
    _def_exe, _def_proc = get_default_browser()
    COMMON_APP_ALIASES["браузер"] = _def_proc
    COMMON_APP_ALIASES["browser"] = _def_proc
except Exception:
    pass


def is_command_safe(script: str) -> tuple[bool, str]:
    """Проверяет команду по стоп-листу опасных инструкций."""
    for pattern in BLOCKED_PATTERNS:
        match = pattern.search(script)
        if match:
            return False, f"Заблокировано политикой безопасности: обнаружена запрещённая команда «{match.group()}»"
    return True, ""


async def execute_os_command(intent: str, command: str, timeout: int = 30) -> dict:
    """Выполняет системную команду Windows через PowerShell с таймаутом и проверкой безопасности."""
    clean_cmd = command.strip()

    if clean_cmd.lower() in GUI_APPS or (
        not clean_cmd.startswith("Start-Process")
        and clean_cmd.endswith(".exe")
        and " " not in clean_cmd
    ):
        clean_cmd = f"Start-Process {clean_cmd}"

    safe, reason = is_command_safe(clean_cmd)
    if not safe:
        return {
            "success": False,
            "intent": intent,
            "command": clean_cmd,
            "error": reason,
            "output": "",
        }

    ps_cmd = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        f"{clean_cmd}"
    )

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()
        success = proc.returncode == 0

        if success and not output:
            output = f"Команда '{clean_cmd}' успешно выполнена ({intent or 'действие завершено'})."

        return {
            "success": success,
            "intent": intent,
            "command": clean_cmd,
            "output": output,
            "error": errors if errors else None,
            "exit_code": proc.returncode,
        }
    except asyncio.TimeoutError:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        return {
            "success": False,
            "intent": intent,
            "command": clean_cmd,
            "error": f"Таймаут: команда не завершилась за {timeout} сек.",
            "output": "",
        }
    except Exception as e:
        return {
            "success": False,
            "intent": intent,
            "command": clean_cmd,
            "error": str(e),
            "output": "",
        }


# ---- Быстрый поиск в реестре App Paths ----
def _find_in_app_paths(app_name: str) -> str | None:
    """Ищет исполняемый файл приложения в реестре App Paths."""
    target = app_name.lower().strip()
    if not target.endswith(".exe"):
        target_exe = f"{target}.exe"
    else:
        target_exe = target

    for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        key_path = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
        try:
            with winreg.OpenKey(root_key, key_path) as k:
                subkeys_count, _, _ = winreg.QueryInfoKey(k)
                for i in range(subkeys_count):
                    try:
                        subkey_name = winreg.EnumKey(k, i)
                        if subkey_name.lower() == target_exe or target in subkey_name.lower():
                            with winreg.OpenKey(k, subkey_name) as app_key:
                                exe_path, _ = winreg.QueryValueEx(app_key, "")
                                if exe_path and os.path.exists(exe_path.strip('"')):
                                    return exe_path.strip('"')
                    except OSError:
                        continue
        except OSError:
            continue
    return None


# ---- Высокопроизводительное управление окнами через Win32 API ----
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOW = 5
SW_RESTORE = 9
WM_CLOSE = 0x0010

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
ASFW_ANY = -1
LSFW_UNLOCK = 2

user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL


def _attach_to_input_desktop():
    """Прикрепляет текущий поток к интерактивному рабочему столу ввода Windows."""
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


def _run_on_input_desktop(func, *args, **kwargs):
    """
    Выполняет Win32-функцию на потоке, гарантированно привязанном к интерактивному рабочему столу.
    Если текущий поток уже занят COM (ошибка 170 ERROR_BUSY), выполняет функцию в чистом потоке.
    """
    hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    if hdesk:
        if user32.SetThreadDesktop(hdesk):
            return func(*args, **kwargs)

    res_list, err_list = [], []

    def _worker():
        try:
            d = user32.OpenInputDesktop(0, False, 0x01FF)
            if d:
                user32.SetThreadDesktop(d)
            res_list.append(func(*args, **kwargs))
        except Exception as e:
            err_list.append(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=3.0)
    if err_list:
        raise err_list[0]
    return res_list[0] if res_list else None


def _bring_window_to_foreground_impl(hwnd: int) -> bool:
    if not user32.IsWindow(hwnd):
        return False

    try:
        user32.AllowSetForegroundWindow(ASFW_ANY)
        user32.LockSetForegroundWindow(LSFW_UNLOCK)
    except Exception:
        pass

    fg_hwnd = user32.GetForegroundWindow()
    cur_thread = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    attached_fg = False
    attached_target = False
    try:
        # Объединяем очереди ввода текущего потока, активного окна и целевого окна
        if fg_thread and fg_thread != cur_thread:
            attached_fg = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
        if target_thread and target_thread != cur_thread:
            attached_target = bool(user32.AttachThreadInput(cur_thread, target_thread, True))

        # Имитируем нажатие и отпускание ALT (снимает ограничение Windows на передачу фокуса)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

        # Восстанавливаем свернутое окно или показываем текущее без сброса полноэкранного режима
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        elif user32.IsZoomed(hwnd):
            user32.ShowWindow(hwnd, SW_SHOW)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        # Пробиваем Z-Order (сначала TOPMOST, затем снимаем постоянный TOPMOST)
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SwitchToThisWindow(hwnd, True)
    finally:
        # Корректно отсоединяем очереди ввода
        if attached_fg:
            user32.AttachThreadInput(cur_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(cur_thread, target_thread, False)

    return True


def _bring_window_to_foreground(hwnd: int) -> bool:
    """
    Принудительно выводит окно поверх всех других окон (даже если оно было позади активных приложений)
    и передает ему фокус, обходя защитную блокировку Windows (предотвращает мигание на панели задач).
    Сохраняет состояние: если окно было развернуто на весь экран — оставляет его полноэкранным.
    """
    return bool(_run_on_input_desktop(_bring_window_to_foreground_impl, hwnd))


def open_or_focus_browser(url: str = None) -> str:
    """
    Открывает браузер по умолчанию в Windows или выводит уже открытый браузер поверх всех окон.
    Если браузер уже запущен — активирует его окно поверх всех окон с сохранением режима (полноэкранный/оконный).
    """
    _attach_to_input_desktop()
    exe_path, proc_name = get_default_browser()

    # 1. Проверяем, есть ли уже открытые окна браузера по умолчанию
    windows = _find_windows_for_app(proc_name)

    # Если для дефолтного окна не найдены, проверим другие популярные браузеры
    if not windows:
        for b in ["firefox", "chrome", "msedge", "brave", "yandex", "opera"]:
            wins = _find_windows_for_app(b)
            if wins:
                windows = wins
                proc_name = b
                break

    if windows:
        # Браузер уже открыт — сортируем и выводим главное окно поверх всех окон
        windows.sort(key=lambda w: (len(w[2].strip()) > 0, not user32.IsIconic(w[0])), reverse=True)
        main_hwnd = windows[0][0]

        if url:
            webbrowser.open(url)
            time.sleep(0.15)

        _bring_window_to_foreground(main_hwnd)

        if url:
            return f"Вывел открытый браузер {proc_name.capitalize()} поверх всех окон и открыл: {url}"

        return f"Браузер {proc_name.capitalize()} уже запущен — вывел его окно поверх всех окон."

    # 2. Браузер еще не открыт — запускаем
    if exe_path and os.path.exists(exe_path):
        try:
            if url:
                subprocess.Popen([exe_path, url])
            else:
                subprocess.Popen([exe_path])
            return f"Запустил браузер по умолчанию ({proc_name.capitalize()})."
        except Exception as e:
            print(f"[Browser] Ошибка прямого запуска: {e}")

    if url:
        webbrowser.open(url)
    else:
        if exe_path and os.path.exists(exe_path):
            os.startfile(exe_path)
        else:
            webbrowser.open("about:blank")

    return f"Запустил браузер по умолчанию ({proc_name.capitalize()})."


FAST_BUILTIN_APPS = {
    "calc": ("calc.exe", "калькулятор"),
    "калькулятор": ("calc.exe", "калькулятор"),
    "notepad": ("notepad.exe", "блокнот"),
    "блокнот": ("notepad.exe", "блокнот"),
    "mspaint": ("mspaint.exe", "Paint"),
    "paint": ("mspaint.exe", "Paint"),
    "taskmgr": ("taskmgr.exe", "диспетчер задач"),
    "диспетчер задач": ("taskmgr.exe", "диспетчер задач"),
    "explorer": ("explorer.exe", "проводник"),
    "проводник": ("explorer.exe", "проводник"),
    "cmd": ("cmd.exe", "командную строку"),
    "командная строка": ("cmd.exe", "командную строку"),
    "командную строку": ("cmd.exe", "командную строку"),
    "wt": ("wt.exe", "терминал"),
    "терминал": ("wt.exe", "терминал"),
    "snippingtool": ("snippingtool.exe", "ножницы"),
    "ножницы": ("snippingtool.exe", "ножницы"),
}


def open_program(app_name: str) -> str:
    """Поиск и ультрабыстрый запуск пользовательских программ (Direct Shell -> Registry -> Start Menu .lnk)."""
    clean_name = app_name.strip().replace('"', '').replace("'", "")
    clean_name = COMMON_APP_ALIASES.get(clean_name.lower(), clean_name)
    if not clean_name:
        return "Ошибка: не указано имя программы."

    # Если запрошен браузер — используем интеллектуальный open_or_focus_browser
    _, default_proc = get_default_browser()
    if clean_name.lower() in ("браузер", "browser", default_proc.lower()):
        return open_or_focus_browser()

    # 0. Мгновенный прямой запуск стандартных приложений Windows (<10мс без поиска)
    fast_target = FAST_BUILTIN_APPS.get(clean_name.lower()) or FAST_BUILTIN_APPS.get(app_name.lower().strip())
    if fast_target:
        exe_file, label = fast_target
        try:
            subprocess.Popen(exe_file, shell=True)
            return f"Открываю {label}."
        except Exception as e:
            return f"Ошибка при запуске {label}: {e}"

    # Проверяем, не запущено ли уже окно этой программы
    _attach_to_input_desktop()
    existing_windows = _find_windows_for_app(clean_name)
    if existing_windows:
        manage_window(clean_name, "focus")
        return f"Вывел {clean_name} на передний план."

    # 1. Сначала проверяем App Paths в реестре (мгновенно)
    reg_path = _find_in_app_paths(clean_name)
    if reg_path:
        try:
            os.startfile(reg_path)
            return f"Открываю {clean_name}."
        except Exception as e:
            return f"Ошибка при запуске {reg_path}: {e}"

    # 2. Если в реестре нет — ищем ярлык .lnk
    ps_script = f"""
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    $appName = "{clean_name}"
    $escaped = [regex]::Escape($appName)

    $paths = @(
        "$env:USERPROFILE\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs",
        "$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs",
        "$env:USERPROFILE\\Desktop",
        "$env:PUBLIC\\Desktop"
    )

    $shortcut = Get-ChildItem -Path $paths -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue |
        Where-Object {{ $_.Name -match "(?i)$escaped" -or $_.BaseName -match "(?i)$escaped" }} |
        Select-Object -First 1

    if ($shortcut) {{
        Invoke-Item -LiteralPath $shortcut.FullName
        Write-Output "Открываю $appName."
    }} else {{
        try {{
            Start-Process $appName -ErrorAction Stop
            Write-Output "Открываю $appName."
        }} catch {{
            Write-Output "Не удалось найти программу: $appName"
        }}
    }}
    """

    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        output = res.stdout.strip()
        return output if output else f"Открываю {clean_name}."
    except subprocess.TimeoutExpired:
        return f"Таймаут при поиске программы: {clean_name}"
    except Exception as e:
        return f"Ошибка при запуске программы: {e}"




def _get_window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        return buff.value
    return ""


def _find_windows_for_app(app_query: str) -> list[tuple[int, str, str]]:
    """Находит видимые HWND окон по имени процесса или заголовку окна (Win32 API)."""
    target = app_query.lower().strip()
    if target in ("active", "текущее", "активное", "this", "foreground", ""):
        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd and user32.IsWindow(fg_hwnd):
            return [(fg_hwnd, "active", _get_window_title(fg_hwnd))]

    target = COMMON_APP_ALIASES.get(target, target)
    matching_windows = []

    def enum_cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _get_window_title(hwnd)

        # Отсекаем нулевые или скрытые вспомогательные окна
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if (rect.right - rect.left) <= 0 or (rect.bottom - rect.top) <= 0:
            return True

        # Получаем PID окна
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        proc_name = ""
        try:
            if pid.value:
                proc = psutil.Process(pid.value)
                proc_name = proc.name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        title_lower = title.lower()
        if (target in proc_name or (title and target in title_lower)) and (title or proc_name):
            matching_windows.append((hwnd, proc_name, title))
        return True

    cb = WNDENUMPROC(enum_cb)
    hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    if hdesk:
        user32.EnumDesktopWindows(hdesk, cb, 0)
        user32.CloseDesktop(hdesk)
    else:
        user32.EnumWindows(cb, 0)

    return matching_windows


class _RECT_STRUCT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]

class _MONITORINFO_STRUCT(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT_STRUCT),
        ("rcWork", _RECT_STRUCT),
        ("dwFlags", wintypes.DWORD),
    ]

def _get_window_monitor_work_area(hwnd: int):
    hmon = user32.MonitorFromWindow(hwnd, 2)
    mi = _MONITORINFO_STRUCT()
    mi.cbSize = ctypes.sizeof(_MONITORINFO_STRUCT)
    if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return mi.rcWork.left, mi.rcWork.top, mi.rcWork.right - mi.rcWork.left, mi.rcWork.bottom - mi.rcWork.top
    return 0, 0, 1920, 1080


def manage_window(app_name: str, action: str) -> str:
    """Мгновенное управление состоянием и тайлингом окон через нативный Win32 API."""
    action_lower = action.strip().lower()
    valid_actions = ["close", "minimize", "maximize", "restore", "focus", "snap_left", "snap_right", "center", "left", "right"]
    if action_lower not in valid_actions:
        return f"Неизвестное действие: '{action}'. Допустимые: {', '.join(valid_actions)}"

    windows = _find_windows_for_app(app_name)
    if not windows:
        return f"Окно приложения '{app_name}' не найдено или не имеет активных видимых окон."

    count = 0
    for hwnd, proc_name, title in windows:
        if action_lower == "close":
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            count += 1
        elif action_lower == "minimize":
            user32.ShowWindowAsync(hwnd, SW_SHOWMINIMIZED)
            count += 1
        elif action_lower == "maximize":
            user32.ShowWindowAsync(hwnd, SW_SHOWMAXIMIZED)
            user32.SetForegroundWindow(hwnd)
            count += 1
        elif action_lower == "focus":
            _bring_window_to_foreground(hwnd)
            count += 1
        elif action_lower == "restore":
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SwitchToThisWindow(hwnd, True)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            count += 1
        elif action_lower in ("snap_left", "left"):
            user32.ShowWindow(hwnd, SW_RESTORE)
            mx, my, mw, mh = _get_window_monitor_work_area(hwnd)
            user32.SetWindowPos(hwnd, 0, mx, my, mw // 2, mh, 0x0004 | 0x0040)
            user32.SetForegroundWindow(hwnd)
            count += 1
        elif action_lower in ("snap_right", "right"):
            user32.ShowWindow(hwnd, SW_RESTORE)
            mx, my, mw, mh = _get_window_monitor_work_area(hwnd)
            user32.SetWindowPos(hwnd, 0, mx + (mw // 2), my, mw - (mw // 2), mh, 0x0004 | 0x0040)
            user32.SetForegroundWindow(hwnd)
            count += 1
        elif action_lower == "center":
            user32.ShowWindow(hwnd, SW_RESTORE)
            mx, my, mw, mh = _get_window_monitor_work_area(hwnd)
            w = int(mw * 0.75)
            h = int(mh * 0.8)
            cx = mx + (mw - w) // 2
            cy = my + (mh - h) // 2
            user32.SetWindowPos(hwnd, 0, cx, cy, w, h, 0x0004 | 0x0040)
            user32.SetForegroundWindow(hwnd)
            count += 1

    return f"Действие '{action_lower}' успешно выполнено для {count} окна(окон) '{app_name}'."


# ---- Управление звуком Windows (pycaw) ----
def set_volume(level: int = None, action: str = None, delta: int = 10, app_name: str = None) -> str:
    """
    Управление системной громкостью или громкостью конкретного приложения.
    Поддерживает:
    - action='down' (потише/убавить): уменьшает громкость на delta% (по умолчанию 10%)
    - action='up' (погромче/прибавить): увеличивает громкость на delta% (по умолчанию 10%)
    - level=<число>: устанавливает точную громкость (0-100%)
    """
    try:
        comtypes.CoInitialize()
        try:
            step = abs(delta) if delta is not None and delta > 0 else 10
            act = (action or "").strip().lower()

            # Если запрос касается Spotify или музыки — перенаправляем на внутреннюю громкость плеера Spotify
            if app_name:
                clean_app = app_name.strip().lower()
                clean_app = COMMON_APP_ALIASES.get(clean_app, clean_app)
                if clean_app in ["spotify", "музыка", "музыку", "музыки", "music", "спотифай", "спотик"]:
                    return set_spotify_volume(level=level, action=action, delta=delta)

            if not app_name:
                devices = AudioUtilities.GetSpeakers()
                if hasattr(devices, "Activate"):
                    interface = devices.Activate(IAudioEndpointVolume.iid, CLSCTX_ALL, None)
                    volume = cast(interface, POINTER(IAudioEndpointVolume))
                elif hasattr(devices, "EndpointVolume"):
                    volume = devices.EndpointVolume
                else:
                    interface = devices._dev.Activate(IAudioEndpointVolume.iid, CLSCTX_ALL, None)
                    volume = cast(interface, POINTER(IAudioEndpointVolume))

                cur_scalar = volume.GetMasterVolumeLevelScalar()
                cur_pct = int(round(cur_scalar * 100))

                if act in ["down", "decrease", "lower", "тише", "убавить", "потише", "уменьшить"]:
                    target_pct = max(0, cur_pct - step)
                    volume.SetMasterVolumeLevelScalar(target_pct / 100.0, None)
                    return f"Громкость уменьшена на {step}% (текущий уровень: {target_pct}%)"
                elif act in ["up", "increase", "raise", "громче", "прибавить", "погромче", "увеличить"]:
                    target_pct = min(100, cur_pct + step)
                    volume.SetMasterVolumeLevelScalar(target_pct / 100.0, None)
                    return f"Громкость увеличена на {step}% (текущий уровень: {target_pct}%)"
                elif level is not None:
                    target_pct = max(0, min(100, int(level)))
                    volume.SetMasterVolumeLevelScalar(target_pct / 100.0, None)
                    return f"Общая системная громкость установлена на {target_pct}%"
                else:
                    return f"Текущая системная громкость: {cur_pct}%"
            else:
                target_app = app_name.strip().lower()
                target_app = COMMON_APP_ALIASES.get(target_app, target_app)
                sessions = AudioUtilities.GetAllSessions()
                found = False
                for session in sessions:
                    if session.Process:
                        proc_name = session.Process.name().lower()
                        if target_app in proc_name:
                            if hasattr(session, "SimpleAudioVolume"):
                                volume = session.SimpleAudioVolume
                            else:
                                volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                            
                            cur_scalar = volume.GetMasterVolume()
                            cur_pct = int(round(cur_scalar * 100))

                            if act in ["down", "decrease", "lower", "тише", "убавить", "потише", "уменьшить"]:
                                target_pct = max(0, cur_pct - step)
                                volume.SetMasterVolume(target_pct / 100.0, None)
                                found = True
                                return f"Громкость приложения {app_name} уменьшена на {step}% (текущий уровень: {target_pct}%)"
                            elif act in ["up", "increase", "raise", "громче", "прибавить", "погромче", "увеличить"]:
                                target_pct = min(100, cur_pct + step)
                                volume.SetMasterVolume(target_pct / 100.0, None)
                                found = True
                                return f"Громкость приложения {app_name} увеличена на {step}% (текущий уровень: {target_pct}%)"
                            elif level is not None:
                                target_pct = max(0, min(100, int(level)))
                                volume.SetMasterVolume(target_pct / 100.0, None)
                                found = True
                                return f"Громкость приложения {app_name} установлена на {target_pct}%"
                            else:
                                return f"Текущая громкость приложения {app_name}: {cur_pct}%"
                if not found:
                    return f"Приложение {app_name} не найдено или не выводит звук в данный момент."
        finally:
            comtypes.CoUninitialize()
    except Exception as e:
        return f"Ошибка настройки звука: {e}"


def media_control(action: str) -> str:
    """
    Управление мультимедиа.
    При настроенном Spotify автоматически управляет им через Spotify Web API,
    чтобы не перехватывать и не ставить на паузу видео пользователя в браузере (YouTube и др.).
    """
    # 1. Если Spotify настроен — отдаем безусловный приоритет Spotify API!
    if is_spotify_configured():
        try:
            res = control_spotify_playback(action)
            if not res.startswith("⚠️") and "Ошибка" not in res and "Не удалось" not in res:
                return res
        except Exception:
            pass

    # 2. Fallback на системные мультимедиа-клавиши Windows
    keys = {
        "next": 0xB0, "prev": 0xB1, "play_pause": 0xB3,
        "pause": 0xB3, "play": 0xB3, "stop": 0xB2,
    }
    action_lower = action.strip().lower()
    if action_lower not in keys:
        return "Неизвестное действие. Доступно: next, prev, play_pause, pause, play"

    vk = keys[action_lower]
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    return f"Медиа-команда '{action_lower}' отправлена в систему."


def _send_hotkey_combo(vks: list[int], hold_time: float = 0.15):
    """
    Отправка комбинации клавиш через Win32 API keybd_event с гарантированной
    привязкой к интерактивному рабочему столу ввода (OpenInputDesktop) и реальными скан-кодами.
    """
    def _impl():
        user32 = ctypes.windll.user32
        for vk in vks:
            scan = user32.MapVirtualKeyW(vk, 0)
            user32.keybd_event(vk, scan, 0, 0)
        time.sleep(hold_time)
        for vk in reversed(vks):
            scan = user32.MapVirtualKeyW(vk, 0)
            user32.keybd_event(vk, scan, 2, 0)

    _run_on_input_desktop(_impl)


def _get_obs_ws_config() -> dict:
    """Считывает настройки встроенного obs-websocket из AppData."""
    appdata = os.getenv("APPDATA", "")
    p = os.path.join(appdata, "obs-studio", "plugin_config", "obs-websocket", "config.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


async def _obs_ws_call(request_type: str, request_data: dict = None) -> tuple[bool, str, dict]:
    """Выполняет прямой RPC-вызов к OBS Studio через встроенный WebSocket v5."""
    cfg = _get_obs_ws_config()
    port = cfg.get("server_port", 4455)
    url = f"ws://127.0.0.1:{port}"
    try:
        async with websockets.connect(url, open_timeout=0.6, close_timeout=0.6) as ws:
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=0.8)
            hello = json.loads(hello_raw)
            d = hello.get("d", {})
            auth = d.get("authentication")

            identify_d = {"rpcVersion": 1}
            if auth:
                server_pass = cfg.get("server_password", "")
                if server_pass:
                    salt = auth.get("salt", "")
                    challenge = auth.get("challenge", "")
                    secret = base64.b64encode(hashlib.sha256((server_pass + salt).encode("utf-8")).digest()).decode("utf-8")
                    auth_resp = base64.b64encode(hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode("utf-8")
                    identify_d["authentication"] = auth_resp

            await ws.send(json.dumps({"op": 1, "d": identify_d}))
            ident_raw = await asyncio.wait_for(ws.recv(), timeout=0.8)
            ident = json.loads(ident_raw)
            if ident.get("op") != 2:
                return False, "Ошибка авторизации в OBS WebSocket", {}

            req_payload = {
                "op": 6,
                "d": {
                    "requestType": request_type,
                    "requestId": "oleg_req_1",
                }
            }
            if request_data:
                req_payload["d"]["requestData"] = request_data

            await ws.send(json.dumps(req_payload))
            res_raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
            res = json.loads(res_raw)
            d_res = res.get("d", {})
            status = d_res.get("requestStatus", {})
            return status.get("result", False), status.get("comment", ""), d_res.get("responseData", {})
    except Exception as e:
        return False, str(e), {}


def obs_control(action: str) -> str:
    """
    Управление OBS Studio:
    1. Автоматически запускает OBS, если он закрыт, и пользователь просит начать запись.
    2. Пробует прямой вызов через встроенный obs-websocket (100% надежность, реальный статус).
    3. Если WebSocket недоступен, безопасно отправляет проверенный хоткей с Win32 скан-кодами на интерактивный рабочий стол.
    """
    action_clean = action.strip().lower()

    # 1. Проверяем, запущен ли OBS
    obs_running = False
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() in ("obs64.exe", "obs32.exe", "obs.exe"):
                obs_running = True
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    was_auto_launched = False
    if not obs_running:
        if action_clean in ("start_record", "toggle_record", "start_replay", "save_replay"):
            # Автоматически запускаем OBS и ждем готовности к приему команд
            open_program("obs")
            start_t = time.time()
            while time.time() - start_t < 4.5:
                time.sleep(0.4)
                for p in psutil.process_iter(["name"]):
                    try:
                        if (p.info["name"] or "").lower() in ("obs64.exe", "obs32.exe", "obs.exe"):
                            obs_running = True
                            was_auto_launched = True
                            break
                    except Exception:
                        pass
                if obs_running:
                    break

            if obs_running:
                # Даем OBS создать окно и инициализировать сокет/хоткеи
                start_w = time.time()
                while time.time() - start_w < 5.0:
                    wins = _find_windows_for_app("obs")
                    if wins:
                        time.sleep(1.2)
                        break
                    time.sleep(0.4)
            else:
                return "Не удалось запустить OBS Studio. Проверьте установку программы."
        else:
            return "OBS сейчас не запущен."

    # Если OBS только что запустился — убеждаемся в наличии окна
    obs_windows = _find_windows_for_app("obs")
    if not obs_windows:
        time.sleep(1.0)
        obs_windows = _find_windows_for_app("obs")

    # Проверяем, не открыто ли модальное окно «Настройки» в OBS, блокирующее управление
    for _, _, title in obs_windows:
        if title.lower() in ("настройки", "settings"):
            return "В OBS сейчас открыто окно настроек — закройте его, чтобы команды записи сработали."

    # 2. Пытаемся выполнить через obs-websocket
    ws_map = {
        "toggle_record": "ToggleRecord",
        "start_record": "StartRecord",
        "stop_record": "StopRecord",
        "start_replay": "StartReplayBuffer",
        "stop_replay": "StopReplayBuffer",
        "save_replay": "SaveReplayBuffer",
    }
    ws_req = ws_map.get(action_clean)
    if ws_req:
        try:
            created_loop = False
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                created_loop = True

            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    success, comment, data = pool.submit(asyncio.run, _obs_ws_call(ws_req)).result(timeout=2.0)
            else:
                try:
                    success, comment, data = loop.run_until_complete(_obs_ws_call(ws_req))
                finally:
                    if created_loop:
                        try:
                            loop.close()
                        except Exception:
                            pass

            if success:
                if was_auto_launched:
                    if ws_req in ("ToggleRecord", "StartRecord"):
                        return "Запустил OBS Studio и включил запись экрана."
                    elif ws_req == "SaveReplayBuffer":
                        return "Запустил OBS Studio и сохранил откат."
                    elif ws_req == "StartReplayBuffer":
                        return "Запустил OBS Studio и включил буфер повторов."

                if ws_req == "ToggleRecord":
                    is_active = data.get("outputActive", False)
                    return "Запустил запись экрана." if is_active else "Остановил запись, видео сохранено."
                elif ws_req == "StartRecord":
                    return "Запустил запись экрана."
                elif ws_req == "StopRecord":
                    return "Остановил запись, видео сохранено."
                elif ws_req == "StartReplayBuffer":
                    return "Включил буфер повторов."
                elif ws_req == "StopReplayBuffer":
                    return "Выключил буфер повторов."
                elif ws_req == "SaveReplayBuffer":
                    return "Сохранил откат."
        except Exception:
            pass

    # 3. Fallback: отправка через горячие клавиши Win32 API
    hotkeys = {
        "toggle_record": ([0x11, 0x10, 0x52], "Ctrl+Shift+R", "переключение записи экрана"),
        "start_record": ([0x11, 0x10, 0x52], "Ctrl+Shift+R", "старт записи экрана"),
        "stop_record": ([0x11, 0x10, 0x52], "Ctrl+Shift+R", "остановка записи экрана"),
        "start_replay": ([0x11, 0x10, 0x50], "Ctrl+Shift+P", "запуск буфера повторов"),
        "stop_replay": ([0x11, 0x10, 0x50], "Ctrl+Shift+P", "остановка буфера повторов"),
        "save_replay": ([0x11, 0x10, 0x53], "Ctrl+Shift+S", "сохранение отката / повтора"),
    }
    if action_clean not in hotkeys:
        return f"Неизвестное действие: '{action}'. Допустимо: toggle_record, start_record, stop_record, start_replay, stop_replay, save_replay"

    if was_auto_launched:
        replies = {
            "toggle_record": "Запустил OBS Studio и включил запись экрана.",
            "start_record": "Запустил OBS Studio и включил запись экрана.",
            "stop_record": "Остановил запись, видео сохранено.",
            "start_replay": "Запустил OBS Studio и включил буфер повторов.",
            "stop_replay": "Выключил буфер повторов.",
            "save_replay": "Запустил OBS Studio и сохранил откат.",
        }
    else:
        replies = {
            "toggle_record": "Переключил запись экрана.",
            "start_record": "Запустил запись экрана.",
            "stop_record": "Остановил запись, видео сохранено.",
            "start_replay": "Включил буфер повторов.",
            "stop_replay": "Выключил буфер повторов.",
            "save_replay": "Сохранил откат.",
        }

    vks, name, desc = hotkeys[action_clean]
    try:
        _send_hotkey_combo(vks, hold_time=0.15)
        time.sleep(0.3)
        return replies.get(action_clean, "Сделано.")
    except Exception as e:
        return f"Не удалось выполнить команду для OBS: {e}"


def setup_obs_hotkeys(restart_if_running: bool = True) -> str:
    """
    Автоматически настраивает и прописывает горячие клавиши в профилях OBS Studio.
    Задает:
    - Запись (старт/стоп): Ctrl+Shift+R
    - Буфер повторов (старт/стоп): Ctrl+Shift+P
    - Сохранить откат (Replay): Ctrl+Shift+S
    Также активирует буфер повторов (RecRB=true) в настройках вывода профиля.
    """
    appdata = os.getenv("APPDATA", "")
    profiles_base = os.path.join(appdata, "obs-studio", "basic", "profiles")
    if not os.path.exists(profiles_base):
        return (
            "Папка настроек OBS Studio не найдена (%APPDATA%\\obs-studio\\basic\\profiles).\n"
            "Убедитесь, что OBS Studio установлена и хотя бы один раз запускалась, чтобы создать профиль."
        )

    # 1. Проверяем, запущен ли OBS
    obs_proc = None
    for p in psutil.process_iter(["pid", "name"]):
        try:
            name = (p.info["name"] or "").lower()
            if name in ("obs64.exe", "obs32.exe", "obs.exe"):
                obs_proc = p
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    relaunch = False
    if obs_proc:
        try:
            obs_proc.terminate()
            obs_proc.wait(timeout=3)
        except Exception:
            try:
                obs_proc.kill()
            except Exception:
                pass
        relaunch = True
        time.sleep(1)

    # 2. Очищаем .sentinel и safe_mode папки, чтобы не появлялся диалог safe mode после завершения
    for d in [".sentinel", "safe_mode"]:
        p = os.path.join(appdata, "obs-studio", d)
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)

    # 3. Обновляем basic.ini во всех профилях
    hotkeys_to_set = {
        "OBSBasic.StartRecording": '{"bindings":[{"control":true,"shift":true,"key":"OBS_KEY_R"}]}',
        "OBSBasic.StopRecording": '{"bindings":[{"control":true,"shift":true,"key":"OBS_KEY_R"}]}',
        "OBSBasic.StartReplayBuffer": '{"bindings":[{"control":true,"shift":true,"key":"OBS_KEY_P"}]}',
        "OBSBasic.StopReplayBuffer": '{"bindings":[{"control":true,"shift":true,"key":"OBS_KEY_P"}]}',
        "OBSBasic.SaveReplayBuffer": '{"bindings":[{"control":true,"shift":true,"key":"OBS_KEY_S"}]}',
    }

    profiles_updated = []
    for prof_name in os.listdir(profiles_base):
        prof_dir = os.path.join(profiles_base, prof_name)
        if not os.path.isdir(prof_dir):
            continue
        ini_path = os.path.join(prof_dir, "basic.ini")
        if not os.path.isfile(ini_path):
            continue

        try:
            # Резервная копия на случай сбоя
            shutil.copyfile(ini_path, ini_path + ".bak")

            cfg = configparser.RawConfigParser(strict=False)
            cfg.optionxform = str
            with open(ini_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                cfg.read_file(f)

            if not cfg.has_section("Hotkeys"):
                cfg.add_section("Hotkeys")

            for k, v in hotkeys_to_set.items():
                cfg.set("Hotkeys", k, v)

            # Активируем буфер повторов (RecRB=true) в обоих режимах вывода
            if cfg.has_section("SimpleOutput"):
                cfg.set("SimpleOutput", "RecRB", "true")
            if cfg.has_section("AdvOut"):
                cfg.set("AdvOut", "RecRB", "true")

            with open(ini_path, "w", encoding="utf-8-sig") as f:
                cfg.write(f, space_around_delimiters=False)

            profiles_updated.append(prof_name)
        except Exception as e:
            print(f"[OBS Setup] Ошибка обновления профиля {prof_name}: {e}")

    # Включаем obs-websocket в config.json для прямого управления
    ws_cfg_path = os.path.join(appdata, "obs-studio", "plugin_config", "obs-websocket", "config.json")
    if os.path.exists(os.path.dirname(ws_cfg_path)):
        try:
            ws_cfg = {}
            if os.path.isfile(ws_cfg_path):
                with open(ws_cfg_path, "r", encoding="utf-8") as f:
                    ws_cfg = json.load(f)
            ws_cfg["server_enabled"] = True
            ws_cfg["auth_required"] = False
            ws_cfg["server_port"] = 4455
            with open(ws_cfg_path, "w", encoding="utf-8") as f:
                json.dump(ws_cfg, f, indent=2)
        except Exception as e:
            print(f"[OBS Setup] Ошибка активации obs-websocket: {e}")

    # 4. Перезапуск OBS, если он был активен
    if relaunch and restart_if_running:
        time.sleep(0.5)
        open_program("obs")

    if not profiles_updated:
        return "Не удалось найти файл basic.ini ни в одном профиле OBS Studio."

    return (
        f"Горячие клавиши OBS Studio успешно настроены (профиль: {', '.join(profiles_updated)})!\n\n"
        "Назначенные хоткеи:\n"
        "• Запись (старт / стоп): Ctrl + Shift + R\n"
        "• Буфер повторов (старт / стоп): Ctrl + Shift + P\n"
        "• Сохранить откат (Replay): Ctrl + Shift + S\n\n"
        "Буфер повторов также активирован в настройках OBS. Теперь голосовые и текстовые команды для записи и откатов работают без ручной настройки!"
    )


def read_clipboard() -> str:
    """Чтение текста из буфера обмена."""
    try:
        text = pyperclip.paste()
        if not text:
            return "Буфер обмена пуст или содержит не текстовые данные."
        return text
    except Exception as e:
        return f"Ошибка при чтении буфера: {e}"


def write_clipboard(text: str) -> str:
    """Запись текста в буфер обмена."""
    try:
        pyperclip.copy(text)
        return "Текст успешно скопирован в системный буфер обмена."
    except Exception as e:
        return f"Ошибка при записи в буфер: {e}"


def get_clipboard_history(limit: int = 5, query: str = "") -> str:
    """Возвращает историю недавно скопированных фрагментов текста."""
    import clipboard_assistant
    return clipboard_assistant.get_clipboard_history(limit=limit, query=query)


# ---- Время и часовые пояса ----
DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

TIMEZONE_ALIASES = {
    "мск": "Europe/Moscow", "москва": "Europe/Moscow", "moscow": "Europe/Moscow",
    "санкт-петербург": "Europe/Moscow", "питер": "Europe/Moscow", "казань": "Europe/Moscow", "kazan": "Europe/Moscow",
    "калининград": "Europe/Kaliningrad", "самара": "Europe/Samara", "екатеринбург": "Europe/Yekaterinburg",
    "омск": "Europe/Omsk", "новосибирск": "Europe/Novosibirsk", "красноярск": "Europe/Krasnoyarsk",
    "иркутск": "Europe/Irkutsk", "якутск": "Europe/Yakutsk", "владивосток": "Asia/Vladivostok",
    "магадан": "Asia/Magadan", "камчатка": "Asia/Kamchatka", "минск": "Europe/Minsk",
    "киев": "Europe/Kyiv", "астана": "Asia/Almaty", "алматы": "Asia/Almaty", "ташкент": "Asia/Tashkent",
    "тбилиси": "Asia/Tbilisi", "ереван": "Asia/Yerevan", "баку": "Asia/Baku",
    "токио": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "нью-йорк": "America/New_York", "new york": "America/New_York",
    "лондон": "Europe/London", "london": "Europe/London", "париж": "Europe/Paris", "paris": "Europe/Paris",
    "берлин": "Europe/Berlin", "рим": "Europe/Rome", "дубай": "Asia/Dubai", "dubai": "Asia/Dubai",
    "пекин": "Asia/Shanghai", "сидней": "Australia/Sydney", "лос-анджелес": "America/Los_Angeles",
    "чикаго": "America/Chicago", "варшава": "Europe/Warsaw", "прага": "Europe/Prague",
    "стамбул": "Europe/Istanbul", "анталья": "Europe/Istanbul", "сеул": "Asia/Seoul",
    "utc": "UTC", "гринвич": "UTC", "gmt": "UTC",
}


def _resolve_timezone(query: str):
    q = (query or "").strip().lower()
    if not q or q in ("системное", "local", "моё", "сейчас", "здесь", "тут", "пк", "компьютер"):
        return None, "Системное время (ваш ПК)"
    if q in TIMEZONE_ALIASES:
        try:
            return zoneinfo.ZoneInfo(TIMEZONE_ALIASES[q]), query.strip()
        except Exception:
            pass

    # Проверка формата UTC+3 / UTC-5 / GMT+3
    m = re.match(r"^(utc|gmt)([+-]\d{1,2})$", q)
    if m:
        offset_hours = int(m.group(2))
        return datetime.timezone(datetime.timedelta(hours=offset_hours)), f"UTC{m.group(2)}"

    try:
        return zoneinfo.ZoneInfo(query.strip()), query.strip()
    except Exception:
        pass

    # Поиск через геокодирование Open-Meteo
    try:
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(query.strip())}&count=1&language=ru&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "OLEG-Assistant/1.0"})
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
            if data.get("results"):
                item = data["results"][0]
                tz_name = item.get("timezone")
                city_name = item.get("name", query.strip())
                if tz_name:
                    return zoneinfo.ZoneInfo(tz_name), city_name
    except Exception:
        pass

    return None, query.strip()


def get_current_time(location_or_timezone: str = "") -> dict:
    """
    Возвращает точное системное время пользователя и/или время в запрошенном часовом поясе/городе мира со сравнением.
    """
    now_sys = datetime.datetime.now().astimezone()
    sys_day = DAYS_RU[now_sys.weekday()]
    sys_date = f"{now_sys.day} {MONTHS_RU[now_sys.month - 1]} {now_sys.year}"
    sys_time = now_sys.strftime("%H:%M:%S")
    sys_offset = now_sys.strftime("UTC%z")
    sys_offset_fmt = sys_offset[:6] + (":" + sys_offset[6:] if len(sys_offset) > 6 else "")

    tz, target_label = _resolve_timezone(location_or_timezone)
    if tz is None:
        return {
            "success": True,
            "target": "Системное время (ваш ПК)",
            "time": sys_time,
            "day_of_week": sys_day,
            "date": sys_date,
            "timezone": sys_offset_fmt,
            "summary": f"По твоему системному времени сейчас {sys_time}, {sys_day}, {sys_date} ({sys_offset_fmt}).",
        }

    now_target = datetime.datetime.now(tz)
    tgt_day = DAYS_RU[now_target.weekday()]
    tgt_date = f"{now_target.day} {MONTHS_RU[now_target.month - 1]} {now_target.year}"
    tgt_time = now_target.strftime("%H:%M:%S")
    tgt_offset = now_target.strftime("UTC%z")
    tgt_offset_fmt = tgt_offset[:6] + (":" + tgt_offset[6:] if len(tgt_offset) > 6 else "")

    diff_seconds = (now_target.utcoffset() - now_sys.utcoffset()).total_seconds()
    diff_hours = diff_seconds / 3600.0
    if diff_hours == 0:
        diff_str = "совпадает с твоим системным временем"
    elif diff_hours > 0:
        diff_val = int(diff_hours) if diff_hours.is_integer() else diff_hours
        diff_str = f"+{diff_val} ч. от твоего системного времени"
    else:
        diff_val = int(diff_hours) if diff_hours.is_integer() else diff_hours
        diff_str = f"{diff_val} ч. от твоего системного времени"

    return {
        "success": True,
        "target": target_label,
        "target_time": tgt_time,
        "target_day": tgt_day,
        "target_date": tgt_date,
        "target_timezone": tgt_offset_fmt,
        "system_time": f"{sys_time} ({sys_offset_fmt})",
        "difference": diff_str,
        "summary": f"В {target_label} сейчас {tgt_time} ({tgt_offset_fmt}). По твоему системному времени — {sys_time} (разница: {diff_str}).",
    }


# ---- Погода без открытия браузера ----
WMO_WEATHER_CODES = {
    0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность", 3: "Пасмурно",
    45: "Туман", 48: "Ледяной туман", 51: "Лёгкая морось", 53: "Морось", 55: "Плотная морось",
    61: "Небольшой дождь", 63: "Умеренный дождь", 65: "Сильный дождь",
    71: "Небольшой снегопад", 73: "Снегопад", 75: "Сильный снегопад",
    80: "Кратковременный дождь", 81: "Ливень", 82: "Сильный ливень",
    85: "Снегопад", 86: "Сильный снегопад", 95: "Гроза"
}


def get_weather(city: str = "") -> dict:
    """
    Получает прогноз погоды для указанного города или города по умолчанию пользователя (Казань).
    Работает напрямую через API без открытия браузера.
    """
    clean_city = (city or "").strip()
    default_city = os.getenv("DEFAULT_CITY", "Казань")
    if not clean_city or clean_city.lower() in ("мой город", "текущий город", "за окном", "здесь", "тут", "погода", "на улице"):
        clean_city = default_city

    # 1. Сначала пробуем wttr.in (русскоязычные подробные описания)
    try:
        url = f"https://wttr.in/{urllib.parse.quote(clean_city)}?format=j1&lang=ru"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            curr = data["current_condition"][0]
            area = data["nearest_area"][0]
            city_name = area["areaName"][0]["value"]
            country = area.get("country", [{}])[0].get("value", "")
            temp = curr["temp_C"]
            feels = curr["FeelsLikeC"]
            desc = curr.get("lang_ru", [{}])[0].get("value", curr["weatherDesc"][0]["value"])
            humidity = curr.get("humidity", "")
            wind_kmh = curr.get("windspeedKmph", "0")
            wind_ms = round(float(wind_kmh) / 3.6, 1)
            pressure_hpa = float(curr.get("pressure", "1013"))
            pressure_mm = round(pressure_hpa * 0.750062)

            return {
                "success": True,
                "city": city_name,
                "country": country,
                "temperature": f"{temp}°C",
                "feels_like": f"{feels}°C",
                "description": desc,
                "humidity": f"{humidity}%",
                "wind": f"{wind_ms} м/с",
                "pressure": f"{pressure_mm} мм рт. ст.",
                "summary": f"Погода в {city_name}: {temp}°C (ощущается как {feels}°C), {desc.lower()}. Ветер {wind_ms} м/с, влажность {humidity}%, давление {pressure_mm} мм рт. ст.",
            }
    except Exception as e_wttr:
        print(f"[Weather] wttr.in не ответил ({e_wttr}), пробуем Open-Meteo...")

    # 2. Резервный источник: Open-Meteo
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(clean_city)}&count=1&language=ru&format=json"
        req = urllib.request.Request(geo_url, headers={"User-Agent": "OLEG-Assistant/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            g = json.loads(r.read().decode("utf-8"))
            if not g.get("results"):
                return {"success": False, "error": f"Город '{clean_city}' не найден."}
            item = g["results"][0]
            lat, lon = item["latitude"], item["longitude"]
            name = item["name"]
            country = item.get("country", "")

        w_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
        req2 = urllib.request.Request(w_url, headers={"User-Agent": "OLEG-Assistant/1.0"})
        with urllib.request.urlopen(req2, timeout=4) as r2:
            w = json.loads(r2.read().decode("utf-8"))
            c = w["current"]
            desc = WMO_WEATHER_CODES.get(c.get("weather_code", 0), "Облачно")
            temp_val = round(c.get("temperature_2m", 0))
            feels_val = round(c.get("apparent_temperature", 0))
            wind_ms = round(c.get("wind_speed_10m", 0) / 3.6, 1)
            hum_val = c.get("relative_humidity_2m", 0)

            return {
                "success": True,
                "city": name,
                "country": country,
                "temperature": f"{temp_val}°C",
                "feels_like": f"{feels_val}°C",
                "description": desc,
                "humidity": f"{hum_val}%",
                "wind": f"{wind_ms} м/с",
                "summary": f"Погода в {name}: {temp_val}°C (ощущается как {feels_val}°C), {desc.lower()}. Ветер {wind_ms} м/с, влажность {hum_val}%.",
            }
    except Exception as e_om:
        return {"success": False, "error": f"Не удалось получить погоду для '{clean_city}': {e_om}"}


def open_in_browser(query: str = "") -> str:
    """Открывает URL или выполняет поисковый запрос в браузере по умолчанию (с фокусом окна)."""
    try:
        clean_query = (query or "").strip()
        if not clean_query or clean_query.lower() in ("браузер", "browser", "открой браузер"):
            return open_or_focus_browser()

        lower_q = clean_query.lower()
        # Защитная блокировка: если случайно вызвано для погоды или времени — отвечаем напрямую без браузера!
        if any(w in lower_q for w in ("погода", "weather", "температура", "градус")):
            city = re.sub(r"(?i)\b(погода|weather|в|in|на|сегодня|сейчас|какая|узнай|открой|покажи|гугл|google)\b", "", clean_query).strip()
            w_res = get_weather(city)
            if w_res.get("success"):
                return w_res.get("summary", str(w_res))
            return str(w_res)
        if any(w in lower_q for w in ("время", "time", "часовой пояс", "который час", "сколько времени")):
            loc = re.sub(r"(?i)\b(время|time|в|in|по|сейчас|сколько|который|час|часовой|пояс|узнай|открой|покажи)\b", "", clean_query).strip()
            t_res = get_current_time(loc)
            return t_res.get("summary", str(t_res))

        if clean_query.startswith("http://") or clean_query.startswith("https://"):
            url = clean_query
        elif "." in clean_query and " " not in clean_query:
            url = f"https://{clean_query}"
        else:
            encoded_query = urllib.parse.quote(clean_query)
            url = f"https://www.google.com/search?q={encoded_query}"

        return open_or_focus_browser(url)
    except Exception as e:
        return f"Ошибка открытия браузера: {e}"


COMMON_STEAM_ALIASES = {
    "кс": "counter-strike",
    "кс2": "counter-strike 2",
    "cs": "counter-strike",
    "cs2": "counter-strike 2",
    "csgo": "counter-strike",
    "дота": "dota 2",
    "дота2": "dota 2",
    "гта": "grand theft auto v",
    "гта5": "grand theft auto v",
    "gta": "grand theft auto v",
    "gta5": "grand theft auto v",
    "пабг": "pubg: battlegrounds",
    "pubg": "pubg: battlegrounds",
    "апекс": "apex legends",
    "тф2": "team fortress 2",
    "tf2": "team fortress 2",
    "раст": "rust",
    "дейз": "dayz",
    "ведьмак": "the witcher 3: wild hunt",
    "ведьмак 3": "the witcher 3: wild hunt",
}


def _norm_game_title(s: str) -> str:
    """Удаляет пробелы, дефисы и знаки пунктуации для устойчивого сопоставления названий игр."""
    return re.sub(r"[^a-z0-9а-яё]", "", (s or "").lower())


def launch_steam_game(game_name: str) -> str:
    """Поиск и запуск установленной игры Steam по названию с интеллектуальным нечётким поиском."""
    query = game_name.lower().strip()
    query = COMMON_STEAM_ALIASES.get(query, query)
    norm_query = _norm_game_title(query)
    try:
        steam_path = None
        for reg_key in [
            r"SOFTWARE\WOW6432Node\Valve\Steam",
            r"SOFTWARE\Valve\Steam",
        ]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_key) as k:
                    steam_path, _ = winreg.QueryValueEx(k, "InstallPath")
                    if steam_path and os.path.exists(steam_path):
                        break
            except (OSError, FileNotFoundError):
                continue

        if not steam_path:
            steam_path = r"C:\Program Files (x86)\Steam"

        library_folders = [steam_path]
        vdf_path = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf_path):
            with open(vdf_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    match = re.search(r'"path"\s+"([^"]+)"', line)
                    if match:
                        folder = match.group(1).replace(r"\\", "\\")
                        if folder not in library_folders and os.path.exists(folder):
                            library_folders.append(folder)

        installed_games = {}
        for lib in library_folders:
            steamapps_dir = os.path.join(lib, "steamapps")
            if not os.path.isdir(steamapps_dir):
                continue
            for fname in os.listdir(steamapps_dir):
                if fname.startswith("appmanifest_") and fname.endswith(".acf"):
                    acf_path = os.path.join(steamapps_dir, fname)
                    try:
                        with open(acf_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                            appid_match = re.search(r'"appid"\s+"(\d+)"', content)
                            name_match = re.search(r'"name"\s+"([^"]+)"', content)
                            if appid_match and name_match:
                                app_id = appid_match.group(1)
                                name = name_match.group(1)
                                installed_games[name.lower()] = (name, app_id)
                    except Exception:
                        continue

        if not installed_games:
            return "Не удалось найти установленные игры Steam."

        target = None
        # 1. Прямое совпадение
        if query in installed_games:
            target = installed_games[query]

        # 2. Нормализованное совпадение без пробелов и знаков (RobRun == ROB RUN)
        if not target and norm_query:
            for g_lower, (g_name, g_id) in installed_games.items():
                if norm_query == _norm_game_title(g_name):
                    target = (g_name, g_id)
                    break

        # 3. Подстрока без пробелов
        if not target and norm_query:
            for g_lower, (g_name, g_id) in installed_games.items():
                g_norm = _norm_game_title(g_name)
                if norm_query in g_norm or g_norm in norm_query:
                    target = (g_name, g_id)
                    break

        # 4. Пословное совпадение
        if not target:
            q_words = [w for w in re.findall(r"\w+", query) if len(w) > 1]
            if q_words:
                for g_lower, (g_name, g_id) in installed_games.items():
                    if all(w in g_lower for w in q_words):
                        target = (g_name, g_id)
                        break

        # 5. Нечеткое сопоставление через difflib (опечатки)
        if not target and norm_query:
            import difflib
            best_score = 0.0
            for g_lower, (g_name, g_id) in installed_games.items():
                score = difflib.SequenceMatcher(None, norm_query, _norm_game_title(g_name)).ratio()
                if score > best_score and score >= 0.65:
                    best_score = score
                    target = (g_name, g_id)

        if not target:
            games_list = ", ".join([name for name, _ in list(installed_games.values())[:7]])
            return f"Игра '{game_name}' не найдена в библиотеке Steam. Установленные игры: {games_list}..."

        found_name, app_id = target
        cmd = f"start steam://run/{app_id}"
        subprocess.run(["cmd.exe", "/c", cmd], shell=True)
        time.sleep(0.8)
        return f"Запустил {found_name} в Steam."

    except Exception as e:
        return f"Ошибка при запуске игры Steam: {e}"


# ---- Spotify Tools ----
def _get_pc_device_id(sp) -> str:
    devices = sp.devices()
    if not devices or not devices.get('devices'):
        try:
            open_program("spotify")
            time.sleep(1.0)
            devices = sp.devices()
        except Exception:
            pass

    if not devices or not devices.get('devices'):
        raise Exception("Нет доступных устройств Spotify. Открой Spotify на ПК.")

    computers = [d for d in devices['devices'] if d['type'] == 'Computer']
    if not computers:
        return devices['devices'][0]['id']

    active_computers = [c for c in computers if c['is_active']]
    if active_computers:
        return active_computers[0]['id']

    pc_id = computers[0]['id']
    try:
        sp.transfer_playback(device_id=pc_id, force_play=False)
        time.sleep(0.3)
    except Exception:
        pass
    return pc_id


def control_spotify_playback(action: str = "play_pause") -> str:
    """
    Управление воспроизведением в плеере Spotify:
    - 'pause' (или 'stop'): поставить на паузу
    - 'play' (или 'resume'): продолжить воспроизведение
    - 'play_pause' (или 'toggle'): переключить воспроизведение/паузу
    - 'next' (или 'skip'): следующий трек
    - 'prev' (или 'previous', 'back'): предыдущий трек
    """
    if not is_spotify_configured():
        return "⚠️ **Spotify не настроен в О.Л.Е.Г.е.** Настроить интеграцию можно в панели настроек ⚙️."
    sp = get_spotify_client()
    if not sp:
        return "⚠️ **Не удалось подключиться к Spotify.** Проверьте настройки авторизации в панели настроек ⚙️."

    act = (action or "play_pause").strip().lower()
    try:
        device_id = _get_pc_device_id(sp)
        playback = sp.current_playback()
        is_playing = bool(playback and playback.get("is_playing"))

        if act in ["pause", "stop", "останови", "пауза", "стоп", "выключи"]:
            if is_playing:
                sp.pause_playback(device_id=device_id)
                return "⏸️ Музыка в Spotify поставлена на паузу."
            return "Музыка в Spotify уже на паузе."

        elif act in ["play", "resume", "продолжи", "включи", "плей", "старт"]:
            if not is_playing:
                try:
                    sp.start_playback(device_id=device_id)
                except Exception:
                    sp.transfer_playback(device_id=device_id, force_play=True)
                cur_item = playback.get("item") if playback else None
                name = f" ({cur_item['artists'][0]['name']} - {cur_item['name']})" if (cur_item and cur_item.get("name")) else ""
                return f"▶️ Воспроизведение в Spotify возобновлено{name}."
            return "Музыка в Spotify уже играет."

        elif act in ["next", "skip", "следующий", "след", "переключи", "перемотай", "скип", "перемотка", "вперед", "forward"]:
            sp.next_track(device_id=device_id)
            time.sleep(0.5)
            if not is_playing:
                try:
                    sp.start_playback(device_id=device_id)
                except Exception:
                    pass
            new_pb = sp.current_playback()
            item = new_pb.get("item") if new_pb else None
            name = f": {item['artists'][0]['name']} - {item['name']}" if (item and item.get("name")) else ""
            return f"⏭️ Включен следующий трек в Spotify{name}."

        elif act in ["prev", "previous", "back", "предыдущий", "назад", "верни", "пред"]:
            sp.previous_track(device_id=device_id)
            time.sleep(0.5)
            if not is_playing:
                try:
                    sp.start_playback(device_id=device_id)
                except Exception:
                    pass
            new_pb = sp.current_playback()
            item = new_pb.get("item") if new_pb else None
            name = f": {item['artists'][0]['name']} - {item['name']}" if (item and item.get("name")) else ""
            return f"⏮️ Включен предыдущий трек в Spotify{name}."

        else: # play_pause / toggle
            if is_playing:
                sp.pause_playback(device_id=device_id)
                return "⏸️ Музыка в Spotify поставлена на паузу."
            else:
                try:
                    sp.start_playback(device_id=device_id)
                except Exception:
                    sp.transfer_playback(device_id=device_id, force_play=True)
                return "▶️ Воспроизведение в Spotify продолжено."

    except spotipy.exceptions.SpotifyException as e:
        return f"Ошибка Spotify: {e}"
    except Exception as e:
        return f"Не удалось изменить воспроизведение Spotify: {e}"


def play_spotify_music(query: str = "") -> str:
    if not is_spotify_configured():
        return "⚠️ **Spotify не настроен в О.Л.Е.Г.е.** В настройках отсутствуют данные авторизации (Client ID / Secret). Настроить интеграцию можно в панели настроек ⚙️."
    sp = get_spotify_client()
    if not sp:
        return "⚠️ **Не удалось подключиться к Spotify.** Проверьте настройки авторизации в панели настроек ⚙️."

    q = (query or "").strip()
    # Если название не указано или передано общее слово — возобновляем воспроизведение
    if not q or q.lower() in ["музыка", "музыку", "плей", "play", "трек", "песню", "что-нибудь", "музычка", "музон", "музыку включи"]:
        return control_spotify_playback(action="play")

    # Если пользователь сказал "включи следующий трек" или "включи предыдущий"
    if q.lower() in ["следующий трек", "следующий", "след трек", "следующую песню", "переключи трек", "перемотай трек", "перемотай"]:
        return control_spotify_playback(action="next")
    if q.lower() in ["предыдущий трек", "предыдущий", "пред трек", "предыдущую песню", "назад"]:
        return control_spotify_playback(action="prev")

    try:
        result = sp.search(q=q, type='track', limit=1)
        tracks = result['tracks']['items']
        if not tracks:
            return f"Трек по запросу '{q}' не найден."
        track_uri = tracks[0]['uri']
        track_name = f"{tracks[0]['artists'][0]['name']} - {tracks[0]['name']}"

        device_id = _get_pc_device_id(sp)
        # Добавляем трек в очередь и сразу перематываем на него с надежной паузой
        sp.add_to_queue(track_uri, device_id=device_id)
        time.sleep(0.6)
        sp.next_track(device_id=device_id)

        # Если плеер стоял на паузе — запускаем воспроизведение
        time.sleep(0.2)
        pb = sp.current_playback()
        if pb and not pb.get("is_playing"):
            try:
                sp.start_playback(device_id=device_id)
            except Exception:
                pass

        return f"Включаю трек: {track_name}"
    except spotipy.exceptions.SpotifyException as e:
        return f"Ошибка Spotify: убедись, что плеер активен на ПК. Детали: {e}"
    except Exception as e:
        return f"Ошибка при запуске музыки: {e}"


def set_spotify_volume(level: int = None, action: str = None, delta: int = 10) -> str:
    if not is_spotify_configured():
        return "⚠️ **Spotify не настроен.** Настроить интеграцию можно в панели настроек ⚙️."
    sp = get_spotify_client()
    if not sp:
        return "⚠️ **Не удалось подключиться к Spotify.** Проверьте настройки авторизации в панели настроек ⚙️."
    try:
        cur_pct = 50
        try:
            cur_play = sp.current_playback()
            if cur_play and cur_play.get("device"):
                cur_pct = cur_play["device"].get("volume_percent", 50)
        except Exception:
            pass

        step = abs(delta) if delta is not None and delta > 0 else 10
        act = (action or "").strip().lower()

        if act in ["down", "decrease", "lower", "тише", "убавить", "потише", "уменьшить"]:
            target = max(0, cur_pct - step)
            sp.volume(volume_percent=target)
            return f"Громкость Spotify уменьшена на {step}% (текущий уровень: {target}%)"
        elif act in ["up", "increase", "raise", "громче", "прибавить", "погромче", "увеличить"]:
            target = min(100, cur_pct + step)
            sp.volume(volume_percent=target)
            return f"Громкость Spotify увеличена на {step}% (текущий уровень: {target}%)"
        elif level is not None:
            volume = max(0, min(100, int(level)))
            sp.volume(volume_percent=volume)
            return f"Внутренняя громкость Spotify установлена на {volume}%"
        else:
            return f"Текущая громкость Spotify: {cur_pct}%"
    except Exception as e:
        return f"Ошибка Spotify: {e}"


def control_spotify_mode(mode: str, state: bool) -> str:
    if not is_spotify_configured():
        return "⚠️ **Spotify не настроен.** Настроить интеграцию можно в панели настроек ⚙️."
    sp = get_spotify_client()
    if not sp:
        return "⚠️ **Не удалось подключиться к Spotify.** Проверьте настройки авторизации в панели настроек ⚙️."
    try:
        device_id = _get_pc_device_id(sp)
        if mode == "shuffle":
            sp.shuffle(state, device_id=device_id)
            return "Перемешивание включено" if state else "Перемешивание выключено"
        elif mode == "repeat":
            repeat_state = 'track' if state else 'off'
            sp.repeat(repeat_state, device_id=device_id)
            return "Зацикливание трека включено" if state else "Зацикливание выключено"
        return "Неизвестный режим"
    except Exception as e:
        return f"Ошибка смены режима Spotify: {e}"


def play_spotify_playlist(query: str) -> str:
    if not is_spotify_configured():
        return "⚠️ **Spotify не настроен.** Настроить интеграцию можно в панели настроек ⚙️."
    sp = get_spotify_client()
    if not sp:
        return "⚠️ **Не удалось подключиться к Spotify.** Проверьте настройки авторизации в панели настроек ⚙️."
    try:
        device_id = _get_pc_device_id(sp)
        query_lower = query.lower().strip()

        if query_lower in ["любимые треки", "любимые", "лайки", "мои треки", "любимое"]:
            results = sp.current_user_saved_tracks(limit=50)
            uris = [item['track']['uri'] for item in results['items']]
            if not uris:
                return "В любимых треках пока ничего нет."
            sp.start_playback(device_id=device_id, uris=uris)
            return "Включаю ваши любимые треки."

        result = sp.search(q=query, type='playlist', limit=1)
        playlists = result['playlists']['items']
        if not playlists:
            return f"Плейлист '{query}' не найден."

        playlist_uri = playlists[0]['uri']
        playlist_name = playlists[0]['name']
        sp.start_playback(device_id=device_id, context_uri=playlist_uri)
        return f"Включаю плейлист: {playlist_name}"
    except Exception as e:
        return f"Ошибка воспроизведения плейлиста Spotify: {e}"


def get_current_track() -> str:
    if not is_spotify_configured():
        return "⚠️ **Spotify не настроен.** Настроить интеграцию можно в панели настроек ⚙️."
    sp = get_spotify_client()
    if not sp:
        return "⚠️ **Не удалось подключиться к Spotify.** Проверьте настройки авторизации в панели настроек ⚙️."
    try:
        playback = sp.current_playback()
        if playback and playback.get('is_playing') and playback.get('item'):
            item = playback['item']
            track_name = item['name']
            artists = ", ".join([artist['name'] for artist in item['artists']])
            return f"Сейчас играет: {artists} - {track_name}"
        return "В данный момент музыка в Spotify не играет."
    except Exception as e:
        return f"Ошибка Spotify: {e}"


# ---- Долгосрочная память (Mem0 AI Memory Layer) ----
import memory_manager


def manage_memory(action: str, fact: str = None) -> str:
    """Управление долгосрочной памятью через интеллектуальный слой Mem0."""
    act = (action or "").lower().strip()
    if act in ("add", "save", "remember", "запомни", "сохрани") and fact:
        return memory_manager.add_memory(fact)
    elif act in ("delete", "remove", "удали", "удалить", "забудь") and fact:
        return memory_manager.delete_memory(fact)
    elif act in ("read", "get", "list", "все", "список"):
        memories = memory_manager.get_all_memories()
        if not memories:
            return "База долгосрочной памяти пока пуста."
        return "Содержимое долгосрочной памяти:\n" + "\n".join(f"• {m}" for m in memories)
    elif act in ("clear", "reset", "delete_all", "очисти"):
        return memory_manager.clear_memory()
    return "Неизвестное действие. Доступно: 'add', 'read', 'delete', 'clear'."


# ---- Таймеры ----
_active_timers = {}

def _run_timer_thread(timer_id: str, seconds: int, message: str):
    time.sleep(seconds)
    if timer_id not in _active_timers or not _active_timers[timer_id]["active"]:
        return

    try:
        import reminders_manager
        reminders_manager.show_toast("О.Л.Е.Г. ⏱️ Таймер!", message, play_sound_type=None)
    except Exception:
        pass

    try:
        import sound_manager
        sound_manager.play_sound("alarm", duration_sec=7.0)
    except Exception:
        try:
            for _ in range(3):
                winsound.Beep(1000, 300)
                time.sleep(0.05)
        except Exception:
            pass

    _active_timers.pop(timer_id, None)


def start_timer(seconds: int, message: str) -> str:
    """Запускает фоновый таймер."""
    try:
        timer_id = f"timer_{int(time.time())}_{seconds}"
        _active_timers[timer_id] = {"seconds": seconds, "message": message, "active": True}
        t = threading.Thread(target=_run_timer_thread, args=(timer_id, seconds, message), daemon=True)
        t.start()
        return f"Таймер на {seconds} сек. запущен."
    except Exception as e:
        return f"Ошибка запуска таймера: {e}"


# ---- Мониторинг ресурсов ПК ----
def get_system_info() -> dict:
    """Возвращает информацию о загрузке системы (CPU, RAM, Диск, Батарея)."""
    try:
        cpu_usage = psutil.cpu_percent(interval=0.2)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('C:\\')
        battery = psutil.sensors_battery()

        battery_info = None
        if battery:
            battery_info = {
                "percent": round(battery.percent),
                "power_plugged": battery.power_plugged,
            }

        return {
            "cpu_percent": cpu_usage,
            "ram_total_gb": round(ram.total / (1024 ** 3), 1),
            "ram_used_percent": ram.percent,
            "ram_available_gb": round(ram.available / (1024 ** 3), 1),
            "disk_free_gb": round(disk.free / (1024 ** 3), 1),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "battery": battery_info,
        }
    except Exception as e:
        return {"error": str(e)}


# ---- Зрение (Vision / Анализ экрана) ----
def _capture_screen_bytes() -> bytes:
    hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)
    try:
        img = ImageGrab.grab(all_screens=True)
    except Exception:
        img = ImageGrab.grab()
    img.thumbnail((1280, 720))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def capture_screen_b64() -> str:
    """Делает снимок активного рабочего стола и возвращает его в формате JPEG base64."""
    data = _run_on_input_desktop(_capture_screen_bytes)
    if not data:
        return ""
    return base64.b64encode(data).decode("utf-8")


async def analyze_screen(query: str = "") -> str:
    """
    Делает снимок экрана и анализирует его с помощью мультимодальной нейросети OpenRouter.
    Помогает найти ошибки, перевести текст, объяснить интерфейс или решить задачу.
    """
    try:
        b64_img = capture_screen_b64()
        if not b64_img:
            return "Не удалось сделать снимок экрана. Возможно, рабочий стол заблокирован."

        import brain
        clean_q = (query or "").strip() or "Что изображено на экране? Опиши и помоги с текущей задачей или ошибкой."
        client = brain._get_client()

        resp = await brain._safe_chat_completion(
            client,
            model=brain.MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Пользователь спрашивает по поводу своего экрана: '{clean_q}'.\n"
                                "Внимательно изучи изображение экрана, найди ошибки, код, текст или элементы интерфейса "
                                "и ответь максимально конкретно, полезно и ёмко на русском языке."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                        },
                    ],
                }
            ],
            max_tokens=brain.MAX_TOKENS,
            temperature=0.4,
        )
        return resp.choices[0].message.content or "Анализ экрана завершён, но модель не вернула текст."
    except Exception as e:
        return f"Ошибка при анализе экрана: {e}"


def smooth_move_and_click(target_x: int, target_y: int, click_type: str = "click"):
    """Плавно перемещает курсор к целевой точке на интерактивном рабочем столе и нажимает мышь."""
    def _worker():
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            start_x, start_y = pt.x, pt.y
        except Exception:
            start_x, start_y = target_x, target_y

        steps = 12
        duration = 0.14
        import math
        for i in range(1, steps + 1):
            t = i / steps
            ease = math.sin(t * math.pi / 2)
            cur_x = int(start_x + (target_x - start_x) * ease)
            cur_y = int(start_y + (target_y - start_y) * ease)
            user32.SetCursorPos(cur_x, cur_y)
            time.sleep(duration / steps)

        user32.SetCursorPos(target_x, target_y)
        time.sleep(0.04)

        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP = 0x0010

        if click_type in ("none", "move_only", "hover"):
            pass
        elif click_type == "right_click":
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, target_x, target_y, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, target_x, target_y, 0, 0)
        elif click_type == "double_click":
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, target_x, target_y, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, target_x, target_y, 0, 0)
            time.sleep(0.08)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, target_x, target_y, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, target_x, target_y, 0, 0)
        else:
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, target_x, target_y, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, target_x, target_y, 0, 0)

        time.sleep(0.2)

    _run_on_input_desktop(_worker)


async def click_screen_element(target: str, click_type: str = "click", app_name: str = "") -> str:
    """
    Интеллектуальный поиск и клик по любому элементу интерфейса, кнопке или ссылке на экране.
    Использует мультимодальное зрение для определения точных экранных координат.
    """
    clean_target = (target or "").strip()
    if not clean_target:
        return "Не указан элемент для нажатия."

    clean_click = (click_type or "click").strip().lower()
    if clean_click not in ("click", "double_click", "right_click"):
        clean_click = "click"

    # Если указано приложение — переключаемся на него
    if app_name:
        manage_window(app_name, "focus")
        time.sleep(0.4)

    # 1. Снимок экрана
    b64_img = capture_screen_b64()
    if not b64_img:
        return "Не удалось сделать снимок экрана. Возможно, рабочий стол заблокирован."

    try:
        import brain

        client = brain._get_client()

        prompt = (
            f"Внимательно найди на экране следующий элемент интерфейса, кнопку или ссылку: '{clean_target}'.\n"
            "Верни ТОЧНЫЕ координаты центра этой кнопки/элемента в нормализованном масштабе от 0 до 1000 "
            "(где x=0, y=0 — левый верхний угол экрана, а x=1000, y=1000 — правый нижний угол экрана).\n"
            "Ответь ИСКЛЮЧИТЕЛЬНО в формате JSON:\n"
            '{"found": true, "x": 123, "y": 45, "element_name": "..."}\n'
            'Если элемент не виден на текущем экране: {"found": false, "reason": "Элемент не найден"}'
        )

        resp = await brain._safe_chat_completion(
            client,
            model=brain.MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                ]
            }],
            temperature=0.0,
            response_format={"type": "json_object"}
        )

        raw_json = resp.choices[0].message.content or "{}"
        data = json.loads(raw_json)

        if not data.get("found"):
            # Попытка фолбэка: если в названии цели упомянута известная программа (стим, браузер, дискорд),
            # фокусируем её и пробуем ещё раз
            fallback_apps = {
                "стим": "Steam", "steam": "Steam",
                "дискорд": "Discord", "discord": "Discord",
                "браузер": "browser", "chrome": "Chrome",
                "телеграм": "Telegram", "telegram": "Telegram",
            }
            detected_app = None
            if not app_name:
                for k, v in fallback_apps.items():
                    if k in clean_target.lower():
                        detected_app = v
                        break

            if detected_app:
                manage_window(detected_app, "focus")
                time.sleep(0.5)
                # Повторный снимок
                b64_retry = capture_screen_b64()
                if b64_retry:
                    resp_retry = await brain._safe_chat_completion(
                        client,
                        model=brain.MODEL,
                        messages=[{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_retry}"}},
                            ]
                        }],
                        temperature=0.0,
                        response_format={"type": "json_object"}
                    )
                    data = json.loads(resp_retry.choices[0].message.content or "{}")

        if not data.get("found"):
            return f"Не удалось найти «{clean_target}» на экране. Убедитесь, что нужное окно открыто."

        # Преобразуем 0..1000 в реальные пиксели экрана с поддержкой мультимониторов
        vx = user32.GetSystemMetrics(76)
        vy = user32.GetSystemMetrics(77)
        vw = user32.GetSystemMetrics(78) or user32.GetSystemMetrics(0) or 1920
        vh = user32.GetSystemMetrics(79) or user32.GetSystemMetrics(1) or 1080

        norm_x = float(data.get("x", 0))
        norm_y = float(data.get("y", 0))

        px = int(round(vx + (norm_x / 1000.0) * vw))
        py = int(round(vy + (norm_y / 1000.0) * vh))

        # Выполняем физический клик мышью
        smooth_move_and_click(px, py, click_type=clean_click)

        label = data.get("element_name") or clean_target
        if clean_click == "double_click":
            return f"Дважды кликнул на «{label}»."
        elif clean_click == "right_click":
            return f"Кликнул правой кнопкой мыши по «{label}»."
        return f"Нажал на «{label}»."

    except Exception as e:
        return f"Ошибка при нажатии на элемент: {e}"


def perform_mouse_scroll(direction: str = "down", steps: int = 8, speed: int = 2):
    """Выполняет плавную серию тиков прокрутки колеса мыши в указанном направлении."""
    is_down = str(direction).lower().strip() in ("down", "вниз", "снизу", "bottom", "ниже")
    delta = -120 if is_down else 120
    MOUSEEVENTF_WHEEL = 0x0800

    def _worker():
        for _ in range(max(1, steps)):
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta * speed, 0)
            time.sleep(0.035)
        time.sleep(0.15)

    _run_on_input_desktop(_worker)


async def scroll_screen(
    direction: str = "down",
    amount: str = "medium",
    target: str = "",
    app_name: str = "",
) -> str:
    """
    Прокручивает (листает) содержимое страницы, окна, ленты или каталога.
    Может предварительно переместить курсор на указанную область или окно.
    """
    clean_dir = "down" if any(w in str(direction).lower() for w in ["вниз", "down", "снизу", "ниже"]) else "up"

    amount_str = str(amount).lower().strip()
    if any(k in amount_str for k in ("мало", "чуть", "немного", "small")):
        steps = 4
        speed = 2
    elif any(k in amount_str for k in ("много", "сильно", "до конца", "large", "больш")):
        steps = 16
        speed = 3
    else:
        steps = 8
        speed = 2

    # Если указано приложение — переключаемся на него
    if app_name:
        manage_window(app_name, "focus")
        time.sleep(0.35)

    # Если указана конкретная область/элемент — наводим курсор на неё с помощью компьютерного зрения
    if target and target.strip():
        clean_target = target.strip()
        b64_img = capture_screen_b64()
        if b64_img:
            try:
                from openai import AsyncOpenAI
                import brain

                client = AsyncOpenAI(base_url=brain.OPENROUTER_BASE_URL, api_key=brain.OPENROUTER_API_KEY)
                prompt = (
                    f"Внимательно найди на экране область контента, окно, ленту или список для прокрутки: '{clean_target}'.\n"
                    "Верни координаты центра этой области в нормализованном масштабе от 0 до 1000:\n"
                    '{"found": true, "x": 500, "y": 500, "area_name": "..."}\n'
                    'Если область не ясна: {"found": false}'
                )
                resp = await client.chat.completions.create(
                    model=brain.MODEL,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                        ]
                    }],
                    temperature=0.0,
                    response_format={"type": "json_object"}
                )
                data = json.loads(resp.choices[0].message.content or "{}")
                if data.get("found"):
                    sw = user32.GetSystemMetrics(0) or 1920
                    sh = user32.GetSystemMetrics(1) or 1080
                    px = max(0, min(sw - 1, int(round((float(data.get("x", 500)) / 1000.0) * sw))))
                    py = max(0, min(sh - 1, int(round((float(data.get("y", 500)) / 1000.0) * sh))))
                    smooth_move_and_click(px, py, click_type="none")
                    time.sleep(0.08)
            except Exception:
                pass

    # Выполняем физическую прокрутку колеса мыши
    perform_mouse_scroll(direction=clean_dir, steps=steps, speed=speed)

    dir_word = "вниз" if clean_dir == "down" else "вверх"
    if target and target.strip():
        return f"Пролистал {dir_word} в области «{target.strip()}»."
    return f"Пролистал страницу {dir_word}."


# ---- Поиск и открытие файлов на компьютере ----
def search_files(query: str, folder: str = "", limit: int = 5) -> dict:
    """
    Быстрый поиск файлов на компьютере по названию или типу.
    Поддерживает поиск последнего скачанного файла ('последний скачанный', 'недавний файл').
    """
    q = (query or "").strip().lower()
    if not q:
        return {"success": False, "error": "Не указано имя файла для поиска."}

    # Поиск последнего скачанного файла в папке Загрузки
    if any(k in q for k in ("последн", "свеж", "недавн", "скачан", "download")):
        downloads_dir = os.path.expanduser("~/Downloads")
        if os.path.exists(downloads_dir):
            try:
                files = [
                    os.path.join(downloads_dir, f)
                    for f in os.listdir(downloads_dir)
                    if os.path.isfile(os.path.join(downloads_dir, f)) and not f.startswith(".")
                ]
                if files:
                    latest_file = max(files, key=os.path.getmtime)
                    stat = os.stat(latest_file)
                    mtime_str = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
                    size_mb = round(stat.st_size / (1024 * 1024), 2)
                    return {
                        "success": True,
                        "query": query,
                        "count": 1,
                        "results": [{
                            "name": os.path.basename(latest_file),
                            "path": latest_file,
                            "size_mb": size_mb,
                            "modified": mtime_str,
                            "is_dir": False,
                        }],
                        "summary": f"Последний скачанный файл: **{os.path.basename(latest_file)}** ({size_mb} МБ, изменён: {mtime_str}).\nПуть: `{latest_file}`",
                    }
            except Exception:
                pass

    search_dirs = []
    if folder and os.path.exists(folder):
        search_dirs.append(folder)
    else:
        user_dirs = [
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~/Downloads"),
            os.path.expanduser("~/Documents"),
            os.path.expanduser("~/Pictures"),
            os.path.expanduser("~/Videos"),
            os.path.expanduser("~/Music"),
        ]
        search_dirs = [d for d in user_dirs if os.path.exists(d)]

    matches = []

    def scan_dir(path: str, depth: int = 0):
        if depth > 3 or len(matches) >= limit:
            return
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if entry.name.startswith(".") or entry.name in ("node_modules", "AppData", "__pycache__", "$Recycle.Bin"):
                        continue
                    if q in entry.name.lower():
                        try:
                            stat = entry.stat()
                            mtime_str = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
                            matches.append({
                                "name": entry.name,
                                "path": entry.path,
                                "is_dir": entry.is_dir(),
                                "size_mb": round(stat.st_size / (1024 * 1024), 2) if not entry.is_dir() else 0,
                                "modified": mtime_str,
                            })
                            if len(matches) >= limit:
                                return
                        except Exception:
                            pass
                    if entry.is_dir(follow_symlinks=False):
                        scan_dir(entry.path, depth + 1)
        except (PermissionError, FileNotFoundError):
            pass

    for d in search_dirs:
        scan_dir(d)

    if not matches:
        return {"success": False, "query": query, "message": f"Файлы по запросу '{query}' не найдены в пользовательских папках."}

    lines = []
    for m in matches:
        kind = "📁 Папка" if m["is_dir"] else f"📄 Файл ({m['size_mb']} МБ)"
        lines.append(f"• **{m['name']}** — {kind}, изменён: {m['modified']}\n  `{m['path']}`")

    return {
        "success": True,
        "query": query,
        "count": len(matches),
        "results": matches,
        "summary": f"Найдено {len(matches)} совпадений:\n" + "\n".join(lines),
    }


def open_file_or_folder(path: str, action: str = "open") -> str:
    """
    Открывает файл/папку ('open') или показывает объект в Проводнике Windows с выделением ('reveal').
    """
    clean_path = path.strip().strip('"').strip("'")
    if not os.path.exists(clean_path):
        return f"Ошибка: файл или папка '{clean_path}' не существует."

    try:
        if action == "reveal":
            subprocess.Popen(f'explorer.exe /select,"{clean_path}"')
            return f"Показал в Проводнике: {os.path.basename(clean_path)}"
        else:
            os.startfile(clean_path)
            return f"Открыл: {os.path.basename(clean_path)}"
    except Exception as e:
        return f"Ошибка при открытии '{clean_path}': {e}"


# ---- Автоматизация и системные переключатели Windows ----
def control_power(action: str, delay_minutes: int = 0) -> str:
    """
    Управление питанием компьютера:
    - 'lock': мгновенная блокировка экрана (Win + L)
    - 'sleep': перевод в спящий режим
    - 'shutdown': выключение (с таймером в минутах)
    - 'restart': перезагрузка
    - 'cancel': отмена запланированного выключения
    """
    act = action.strip().lower()
    if act == "lock":
        user32.LockWorkStation()
        return "Экран компьютера заблокирован (Win+L)."

    if act == "sleep":
        try:
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            return "Компьютер переводится в спящий режим."
        except Exception as e:
            return f"Ошибка перехода в спящий режим: {e}"

    if act == "cancel":
        res = subprocess.run(["shutdown.exe", "/a"], capture_output=True, text=True)
        if res.returncode == 0:
            return "Запланированное выключение/перезагрузка успешно отменены."
        return "Нет активного запланированного выключения для отмены."

    delay_sec = max(0, int(delay_minutes * 60))
    if act == "shutdown":
        subprocess.Popen(["shutdown.exe", "/s", "/t", str(delay_sec)])
        if delay_minutes > 0:
            return f"Компьютер выключится через {delay_minutes} мин. (Для отмены скажи: 'отмени выключение')."
        return "Компьютер выключается..."

    if act == "restart":
        subprocess.Popen(["shutdown.exe", "/r", "/t", str(delay_sec)])
        if delay_minutes > 0:
            return f"Компьютер перезагрузится через {delay_minutes} мин."
        return "Компьютер перезагружается..."

    return f"Неизвестное действие питания: '{action}'. Доступно: lock, sleep, shutdown, restart, cancel."


def set_system_theme(theme: str) -> str:
    """
    Переключает тему оформления Windows (тёмная / светлая / toggle).
    """
    t = theme.strip().lower()
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            cur_apps, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")

            if t in ("dark", "темная", "тёмная"):
                new_val = 0
            elif t in ("light", "светлая"):
                new_val = 1
            else: # toggle
                new_val = 0 if cur_apps == 1 else 1

            winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, new_val)
            winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, new_val)

        # Оповещаем Windows об изменении темы
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "ImmersiveColorSet", 0x0002, 100, None)

        mode_str = "светлую" if new_val == 1 else "тёмную"
        return f"Тема Windows успешно переключена на {mode_str}."
    except Exception as e:
        return f"Ошибка переключения темы: {e}"


def empty_recycle_bin() -> str:
    """
    Очищает Корзину Windows без всплывающих диалоговых окон.
    """
    try:
        shell32 = ctypes.windll.shell32
        res = shell32.SHEmptyRecycleBinW(None, None, 7)
        if res == 0 or (res & 0xFFFFFFFF) in (0x80041002, 0x8000FFFF):
            return "Корзина успешно очищена (или уже пуста)."
        return f"Корзина очищена (код: {hex(res & 0xFFFFFFFF)})."
    except Exception as e:
        return f"Ошибка очистки корзины: {e}"


# ---- Управление Throne VPN ----
import sqlite3

THRONE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\user\AppData\Local"), "Throne")
THRONE_DB_PATH = os.path.join(THRONE_DIR, "config", "throne.db")
THRONECTL_EXE = os.path.join(THRONE_DIR, "thronectl.exe")
THRONE_API_KEY_FILE = os.path.join(THRONE_DIR, "config", "api.key")


def is_oleg_proxy_connected() -> tuple[bool, str]:
    """
    Проверяет, настроен ли независимый прокси для О.Л.Е.Г.а в .env,
    и синхронизирует его глобально со всеми сетевыми библиотеками.
    """
    proxy = apply_oleg_proxy_globally()
    if not proxy:
        return False, ""
    return True, proxy


def is_throne_configured() -> bool:
    """Проверяет, настроен ли ключ доступа к Throne API в конфигурации (.env)."""
    _load_env()
    key = (os.getenv("THRONE_KEY") or "").strip()
    return bool(key and len(key) >= 10)


def _run_thronectl(cmd_args: list[str], timeout: int = 15) -> tuple[bool, str]:
    """Запускает CLI-утилиту thronectl с передачей api.key / THRONE_KEY / THRONE_ADDR."""
    if not os.path.exists(THRONECTL_EXE):
        return False, "thronectl.exe не найден"

    _load_env()
    env = os.environ.copy()
    throne_key = os.getenv("THRONE_KEY", "").strip()
    throne_addr = os.getenv("THRONE_ADDR", "").strip()

    full_cmd = [THRONECTL_EXE]
    if throne_addr:
        full_cmd.extend(["-addr", throne_addr])

    if throne_key:
        env["THRONE_KEY"] = throne_key
    elif os.path.exists(THRONE_API_KEY_FILE):
        full_cmd.extend(["--key-file", THRONE_API_KEY_FILE])

    full_cmd.extend(cmd_args)
    try:
        res = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout, env=env)
        if res.returncode == 0:
            return True, res.stdout.strip()
        return False, res.stderr.strip() or res.stdout.strip()
    except Exception as e:
        return False, str(e)


def save_throne_api_credentials(key: str = "", addr: str = "") -> str:
    """Сохраняет ключ и адрес Throne API в .env, api.key и throne.db."""
    clean_key = (key or "").strip()
    clean_addr = (addr or "").strip()

    # Если передана строка вида 'THRONE_ADDR=... THRONE_KEY=...'
    if "THRONE_KEY=" in clean_key or "THRONE_ADDR=" in clean_key:
        for part in clean_key.replace("\r", "\n").split("\n"):
            part = part.strip()
            if part.startswith("export "):
                part = part[7:].strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k == "THRONE_KEY":
                    clean_key = v
                elif k == "THRONE_ADDR":
                    clean_addr = v

    updates_env = {}
    if clean_key:
        updates_env["THRONE_KEY"] = clean_key
        try:
            os.makedirs(os.path.dirname(THRONE_API_KEY_FILE), exist_ok=True)
            with open(THRONE_API_KEY_FILE, "w", encoding="utf-8") as f:
                f.write(clean_key)
        except Exception as e:
            print(f"[Throne API] Ошибка записи в api.key: {e}")

        _update_throne_db_settings({"remote_api_key": clean_key})

    if clean_addr:
        updates_env["THRONE_ADDR"] = clean_addr
        if ":" in clean_addr:
            port = clean_addr.split(":")[-1]
            if port.isdigit():
                _update_throne_db_settings({"remote_api_port": port})

    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path) and updates_env:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            existing_keys = set()
            new_lines = []
            for line in lines:
                matched = False
                for k, v in updates_env.items():
                    if line.strip().startswith(f"{k}="):
                        new_lines.append(f"{k}={v}\n")
                        existing_keys.add(k)
                        matched = True
                        break
                if not matched:
                    new_lines.append(line)

            for k, v in updates_env.items():
                if k not in existing_keys:
                    new_lines.append(f"{k}={v}\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            _load_env()
        except Exception as e:
            print(f"[Throne API] Ошибка обновления .env: {e}")

    ok, out = _run_thronectl(["status"])
    if ok:
        return f"✅ Ключ и адрес Throne API успешно сохранены! Связь с Throne установлена:\n{out}"
    else:
        key_preview = f"`{clean_key[:6]}...{clean_key[-4:]}`" if len(clean_key) >= 10 else f"`{clean_key}`"
        return (
            f"✅ Ключ Throne API {key_preview} успешно сохранен в конфигурации О.Л.Е.Г.а!\n\n"
            f"⚠️ Статус подключения API: {out}\n\n"
            "**Следующий шаг:**\n"
            "В окне Throne нажмите **ОК / Применить** и перезапустите программу (**Программа -> Перезапустить программу**)."
        )


def _throne_api_error_response(err_text: str, action_desc: str = "") -> str:
    """Формирует понятный и точный ответ пользователю при ошибке взаимодействия с Throne."""
    err_lower = (err_text or "").lower()
    if "connectex" in err_lower or "refused" in err_lower or "no answer" in err_lower or "context deadline exceeded" in err_lower:
        return (
            f"⚠️ **Не удалось {action_desc}**: встроенный Throne API (порт 9095) сейчас не отвечает.\n\n"
            "**Причина:** Приложение Throne запущено, но модуль интеграции (Throne API) ещё не активен на порту 9095.\n\n"
            "**Как включить мгновенное управление (1 действие):**\n"
            "1. В окне Throne откройте **Настройки -> Настройки интеграции -> Throne API**.\n"
            "2. Убедитесь, что переключатель **Throne API** включен.\n"
            "3. Нажмите кнопку **«Скопировать для скриптов»** (Copy for scripts) и отправьте скопированное мне (или вставьте ключ в `.env`).\n"
            "4. Внизу окна настроек нажмите **ОК** и в меню выберите **Программа -> Перезапустить программу**.\n\n"
            "После этого все команды управления VPN будут применяться мгновенно."
        )
    return f"⚠️ Ошибка выполнения команды в Throne ({action_desc}): {err_text}"


def _set_windows_proxy(enable: bool, server: str = "127.0.0.1:2080") -> bool:
    """Включает или выключает системный прокси Windows через реестр и InternetSetOption."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
            if enable:
                winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
        # Оповещаем подсистему WinINet
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        wininet = ctypes.windll.wininet
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        return True
    except Exception as e:
        print(f"[Windows Proxy] Ошибка настройки прокси: {e}")
        return False


def _set_tun_adapter(enable: bool) -> bool:
    """Включает или выключает виртуальный сетевой адаптер throne-tun."""
    action = "Enable-NetAdapter" if enable else "Disable-NetAdapter"
    cmd = ["powershell", "-NoProfile", "-Command", f"{action} -Name 'throne-tun' -Confirm:$false"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        return res.returncode == 0
    except Exception as e:
        print(f"[TUN Adapter] Ошибка управления адаптером: {e}")
        return False


def _update_throne_db_settings(updates: dict[str, str]) -> bool:
    """Обновляет параметры в таблице settings базы данных throne.db."""
    if not os.path.exists(THRONE_DB_PATH):
        return False
    try:
        conn = sqlite3.connect(THRONE_DB_PATH)
        c = conn.cursor()
        for k, v in updates.items():
            c.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, v)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[Throne DB] Ошибка обновления settings: {e}")
        return False


def _get_throne_status_dict() -> dict:
    """Сбор полной информации о состоянии Throne VPN."""
    throne_procs = [p.name() for p in psutil.process_iter(['name']) if 'throne' in p.info['name'].lower()]
    is_running = len(throne_procs) > 0

    tun_up = False
    try:
        cmd = ["powershell", "-NoProfile", "-Command", "(Get-NetAdapter -Name 'throne-tun' -ErrorAction SilentlyContinue).Status"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout.strip()
        tun_up = (out == "Up")
    except Exception:
        pass

    proxy_enabled = False
    proxy_server = ""
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, "ProxyEnable")
            proxy_enabled = bool(val)
            try:
                proxy_server, _ = winreg.QueryValueEx(k, "ProxyServer")
            except FileNotFoundError:
                pass
    except Exception:
        pass

    if tun_up and proxy_enabled:
        active_mode = "both"
    elif tun_up:
        active_mode = "tun"
    elif proxy_enabled:
        active_mode = "proxy"
    else:
        active_mode = "off"

    hwnds = _find_windows_for_app("throne")
    window_title = hwnds[0][2] if hwnds else ""

    active_profile_id = None
    active_profile_name = "Неизвестно"
    active_profile_latency = None
    active_route = "Default"

    if os.path.exists(THRONE_DB_PATH):
        try:
            conn = sqlite3.connect(f"file:{THRONE_DB_PATH}?mode=ro", uri=True)
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='remember_id'")
            r = c.fetchone()
            if r and r[0].isdigit():
                active_profile_id = int(r[0])

            c.execute("SELECT value FROM settings WHERE key='active_routing'")
            r2 = c.fetchone()
            if r2:
                active_route = r2[0]

            if active_profile_id:
                c.execute("SELECT name, latency FROM profiles WHERE id=?", (active_profile_id,))
                p = c.fetchone()
                if p:
                    active_profile_name, active_profile_latency = p[0], p[1]
            conn.close()
        except Exception:
            pass

    # Живой статус из thronectl имеет наивысший приоритет
    ok_ctl, out_ctl = _run_thronectl(["status"])
    if ok_ctl and out_ctl:
        for line in out_ctl.splitlines():
            line_str = line.strip()
            if line_str.startswith("Profile:"):
                p_val = line_str.split(":", 1)[1].strip()
                parts = p_val.split(None, 1)
                if parts and parts[0].isdigit():
                    active_profile_id = int(parts[0])
                    active_profile_name = parts[1].rsplit("(", 1)[0].strip()
            elif line_str.startswith("Route:"):
                r_val = line_str.split(":", 1)[1].strip()
                parts = r_val.split(None, 1)
                if len(parts) > 1:
                    active_route = parts[1].strip()

    return {
        "is_running": is_running,
        "tun_up": tun_up,
        "proxy_enabled": proxy_enabled,
        "proxy_server": proxy_server,
        "active_mode": active_mode,
        "window_title": window_title,
        "active_profile_id": active_profile_id,
        "active_profile_name": active_profile_name,
        "active_profile_latency": active_profile_latency,
        "active_route": active_route,
    }


def _list_throne_servers() -> list[dict]:
    """Чтение списка профилей/серверов: сперва live через thronectl -json profiles, затем fallback на throne.db."""
    ok, out = _run_thronectl(["-json", "profiles"], timeout=4)
    if ok and out:
        try:
            data = json.loads(out)
            raw_profiles = data.get("profiles", [])
            if raw_profiles:
                servers = []
                for p in raw_profiles:
                    lat = p.get("latency_ms", -1)
                    servers.append({
                        "id": p.get("id"),
                        "name": p.get("name", ""),
                        "type": p.get("type", ""),
                        "latency": lat if lat is not None else -1,
                        "country": p.get("country", ""),
                        "running": p.get("running", False),
                        "address": p.get("address", ""),
                    })
                return servers
        except Exception as e:
            print(f"[Throne Servers] Ошибка парсинга json профилей: {e}")

    servers = []
    if not os.path.exists(THRONE_DB_PATH):
        return servers
    try:
        conn = sqlite3.connect(f"file:{THRONE_DB_PATH}?mode=ro", uri=True)
        c = conn.cursor()
        c.execute("SELECT id, name, type, latency, test_country FROM profiles")
        for row in c.fetchall():
            servers.append({
                "id": row[0],
                "name": row[1],
                "type": row[2],
                "latency": row[3],
                "country": row[4],
                "running": False,
                "address": "",
            })
        conn.close()
    except Exception:
        pass
    return servers


def _match_profiles_by_query(query: str, servers: list[dict]) -> tuple[list[dict], str]:
    """
    Интеллектуальный поиск серверов Throne по названию, стране, флагу или ID.
    Возвращает (список_найденных_серверов, нормализованное_название_страны_или_запроса).
    """
    q = (query or "").strip().lower()
    if not q:
        return [], ""

    # Если передан точный ID
    if q.isdigit():
        target_id = int(q)
        matched = [s for s in servers if s["id"] == target_id]
        if matched:
            return matched, matched[0]["name"]
        return [], q

    # Карта стран и ключевых корней для умного распознавания
    COUNTRY_MAP = {
        "Финляндия": ["финлянд", "финск", "finland", "suomi"],
        "Германия": ["герман", "немецк", "germany", "deutschland"],
        "Нидерланды": ["нидерланд", "голланди", "голланд", "netherlands", "holland"],
        "США": ["сша", "америк", "штаты", "usa", "america", "united states"],
        "Польша": ["польш", "польск", "poland"],
        "Швеция": ["швеци", "шведск", "sweden"],
        "Эстония": ["эстони", "эстонск", "estonia"],
        "Нигерия": ["нигери", "nigeria"],
        "Япония": ["япони", "японск", "japan"],
    }

    FLAG_MAP = {
        "🇫🇮": "Финляндия",
        "🇩🇪": "Германия",
        "🇳🇱": "Нидерланды",
        "🇺🇸": "США",
        "🇵🇱": "Польша",
        "🇸🇪": "Швеция",
        "🇪🇪": "Эстония",
        "🇳🇬": "Нигерия",
        "🇯🇵": "Япония",
    }

    # 1. Проверяем совпадение запроса с известной страной
    matched_country = None
    for country, roots in COUNTRY_MAP.items():
        if any(r in q for r in roots):
            matched_country = country
            break

    if matched_country:
        roots = COUNTRY_MAP[matched_country]
        # Ищем профили, где в названии есть текстовый корень страны (например 'финлянд')
        direct_matches = [s for s in servers if any(r in s["name"].lower() for r in roots)]
        if direct_matches:
            return direct_matches, matched_country
        # Если по тексту не нашли, проверяем по флагу
        for flag, c_name in FLAG_MAP.items():
            if c_name == matched_country:
                flag_matches = [s for s in servers if flag in s["name"]]
                if flag_matches:
                    return flag_matches, matched_country
        return [], matched_country

    # 2. Прямой поиск по подстроке в названии профиля
    direct = [s for s in servers if q in s["name"].lower()]
    if direct:
        return direct, q

    # 3. Поиск по отдельным словам
    words = [w for w in q.split() if len(w) > 2]
    for w in words:
        w_matches = [s for s in servers if w in s["name"].lower()]
        if w_matches:
            return w_matches, w

    return [], q


def manage_throne_vpn(action: str, target: str = "") -> str:
    """
    Управление VPN-клиентом Throne:
    - 'status': текущее состояние подключения, активный сервер, маршрутизация, режим (TUN/Proxy), пинг.
    - 'list_servers': список всех серверов с пингом и флагами.
    - 'ping_servers': замер пинга всех серверов или конкретной страны/сервера (target).
    - 'list_routes': список доступных конфигураций маршрутизации.
    - 'set_mode': переключение режима ('tun', 'proxy', 'both').
    - 'off': выключение VPN (без завершения процесса приложения).
    - 'on': включение VPN.
    - 'switch_server': смена сервера по названию, стране, ID или 'best' (с проверкой доступности).
    - 'switch_route': смена конфигурации/маршрутизации (например, 'Default').
    """
    act = action.strip().lower()
    has_proxy, proxy_url = is_oleg_proxy_connected()

    if act in ["set_key", "key", "save_key", "set_credentials"]:
        return save_throne_api_credentials(key=target)

    # Проверка наличия данных для Throne VPN
    if not is_throne_configured():
        return (
            "⚠️ **Throne VPN не настроен в О.Л.Е.Г.е.**\n\n"
            "В конфигурации отсутствует ключ доступа Throne API (`THRONE_KEY`).\n\n"
            "🛠️ **Как настроить:**\n"
            "Вы можете настроить интеграцию в панели настроек ⚙️ (или передать ключ фразой: «сохрани ключ трона <ваш_ключ>»)."
        )

    if act == "status":
        info = _get_throne_status_dict()
        if not info["is_running"]:
            return "⚠️ Процесс Throne VPN не запущен на компьютере."

        mode_names = {
            "tun": "🚀 TUN (виртуальный сетевой адаптер throne-tun)",
            "proxy": "🌐 Системный Прокси (127.0.0.1:2080)",
            "both": "⚡ Оба режима (TUN + Системный Прокси)",
            "off": "⚪ Выключен (прямое подключение)",
        }
        mode_desc = mode_names.get(info["active_mode"], info["active_mode"])
        is_connected = (info["active_mode"] != "off")

        status_text = "🟢 **Подключено** (VPN активен)" if is_connected else "⚪ **Отключено** (прямое интернет-соединение)"
        ping_str = f"{info['active_profile_latency']} мс" if info['active_profile_latency'] and info['active_profile_latency'] > 0 else "—"
        fuse_text = f"🟢 **Снят** (прокси активен: `{proxy_url}`)" if has_proxy else "🔒 **АКТИВЕН** (настройки заблокированы, укажите `OLEG_PROXY` в `.env`)"

        lines = [
            "### 🛡️ Состояние Throne VPN:",
            f"- **Статус:** {status_text}",
            f"- **Предохранитель:** {fuse_text}",
            f"- **Текущий режим:** {mode_desc}",
            f"- **Активный сервер:** {info['active_profile_name']} (ID: {info['active_profile_id']}, пинг: {ping_str})",
            f"- **Конфигурация (маршрутизация):** {info['active_route']}",
            f"- **Адаптер throne-tun:** {'Включен (Up)' if info['tun_up'] else 'Отключен'}",
            f"- **Системный прокси Windows:** {'Включен (' + info['proxy_server'] + ')' if info['proxy_enabled'] else 'Отключен'}",
        ]
        if info["window_title"]:
            lines.append(f"- **Окно Throne:** `{info['window_title']}`")

        ok_ctl, _ = _run_thronectl(["status"])
        if not ok_ctl:
            lines.append(
                "\n⚠️ *Внимание: встроенный Throne API (порт 9095) пока не отвечает. "
                "Чтобы О.Л.Е.Г. мог переключать серверы на лету, перезапустите Throne (в окне программы: 'Программа' -> 'Перезапустить программу').*"
            )

        return "\n".join(lines)

    elif act == "list_servers":
        servers = _list_throne_servers()
        if not servers:
            return "Не удалось получить список серверов Throne. База данных throne.db не найдена."

        info = _get_throne_status_dict()
        active_id = info.get("active_profile_id")

        tested = [s for s in servers if s["latency"] is not None and s["latency"] > 0]
        tested.sort(key=lambda x: x["latency"])
        untested = [s for s in servers if s["latency"] is None or s["latency"] <= 0]
        all_sorted = tested + untested

        lines = [
            "### 🌐 Доступные серверы Throne VPN:\n",
            "| ID | Сервер | Пинг | Статус |",
            "|:---:|:---|:---:|:---:|",
        ]
        for s in all_sorted:
            is_active = (s["id"] == active_id)
            active_mark = "🟢 **Активен**" if is_active else ""
            if s["latency"] is not None and s["latency"] > 0:
                ping_val = f"{s['latency']} мс"
            elif s["latency"] == -1:
                ping_val = "❌ Недоступен"
            else:
                ping_val = "—"
            name_val = f"**{s['name']}**" if is_active else s["name"]
            lines.append(f"| {s['id']} | {name_val} | {ping_val} | {active_mark} |")

        if not has_proxy:
            lines.append("\n🔒 *Предохранитель активен: смена серверов заблокирована, пока не указан OLEG_PROXY в .env.*")
        else:
            lines.append("\n*Чтобы переключить сервер, скажите: 'переключи на Нидерланды' или укажите ID. Для проверки пинга: 'пропингуй все сервера'.*")
        return "\n".join(lines)

    elif act in ["ping_servers", "ping", "test_servers", "check_ping", "пропингуй"]:
        target_clean = (target or "").strip().lower()
        is_all = not target_clean or target_clean in ["all", "все", "всё", "серверы", "сервера", "группа"]

        if is_all:
            ok_test, out_test = _run_thronectl(["-json", "test", "latency", "--group", "1"], timeout=25)
            if not ok_test or not out_test:
                return _throne_api_error_response(out_test, action_desc="пропинговать серверы Throne")

            try:
                data = json.loads(out_test)
                results = data.get("results", [])
            except Exception as e:
                return f"Ошибка обработки результатов пинга: {e}"

            if not results:
                return "Не удалось получить результаты проверки пинга серверов."

            available = [r for r in results if r.get("latency_ms", -1) > 0]
            unavailable = [r for r in results if r.get("latency_ms", -1) <= 0]
            available.sort(key=lambda x: x["latency_ms"])

            lines = [
                f"### 🏓 Результаты проверки пинга серверов Throne VPN (всего: {len(results)}):\n",
                f"🟢 **Доступно серверов:** {len(available)}  |  ❌ **Недоступно:** {len(unavailable)}\n",
                "#### ⚡ Топ самых быстрых доступных серверов:",
            ]
            for idx, s in enumerate(available[:6], 1):
                lines.append(f"{idx}. {s['name']} — **{s['latency_ms']} мс** (ID: {s['id']})")

            if unavailable:
                lines.append("\n#### ❌ Недоступные серверы (offline / таймаут):")
                for s in unavailable:
                    lines.append(f"• **{s['name']}** (ID: {s['id']}) — *нет ответа*")

            lines.append(
                "\n💡 *Чтобы переключиться на любой сервер, просто скажите: "
                "«переключи на Германию» или «поставь лучший сервер».*"
            )
            return "\n".join(lines)
        else:
            servers = _list_throne_servers()
            matched, c_name = _match_profiles_by_query(target_clean, servers)
            if not matched:
                return f"Сервер или страна '{target}' не найдены для проверки пинга."

            pids = [str(s["id"]) for s in matched]
            ok_test, out_test = _run_thronectl(["-json", "test", "latency"] + pids, timeout=12)
            if not ok_test or not out_test:
                return _throne_api_error_response(out_test, action_desc=f"проверить пинг {c_name}")

            try:
                data = json.loads(out_test)
                results = data.get("results", [])
            except Exception as e:
                return f"Ошибка обработки пинга: {e}"

            lines = [f"### 🏓 Пинг серверов для «{c_name}»:\n"]
            for r in results:
                lat = r.get("latency_ms", -1)
                if lat > 0:
                    lines.append(f"• 🟢 **{r['name']}** (ID: {r['id']}) — **{lat} мс**")
                else:
                    lines.append(f"• ❌ **{r['name']}** (ID: {r['id']}) — **недоступен** (таймаут)")

            return "\n".join(lines)

    elif act == "list_routes":
        if not os.path.exists(THRONE_DB_PATH):
            return "База данных Throne не найдена."
        try:
            conn = sqlite3.connect(f"file:{THRONE_DB_PATH}?mode=ro", uri=True)
            c = conn.cursor()
            c.execute("SELECT id, name FROM route_profiles")
            routes = c.fetchall()
            conn.close()
        except Exception as e:
            return f"Ошибка чтения конфигураций: {e}"

        info = _get_throne_status_dict()
        active_route = info.get("active_route", "Default")

        lines = ["### 🔀 Конфигурации маршрутизации Throne:\n"]
        for r in routes:
            r_id, r_name = r[0], r[1]
            is_active = (r_name.lower() == active_route.lower())
            mark = " 🟢 **(активна)**" if is_active else ""
            lines.append(f"- **{r_name}** (ID: {r_id}){mark}")

        if not has_proxy:
            lines.append("\n🔒 *Предохранитель активен: смена конфигураций заблокирована, пока не указан OLEG_PROXY в .env.*")
        return "\n".join(lines)

    elif act == "set_mode":
        m = target.strip().lower()
        if m in ["тун", "tun", "vpn", "впн", "tunnel"]:
            ok1, out1 = _run_thronectl(["tun", "on", "--no-wait"])
            ok2, out2 = _run_thronectl(["proxy", "off", "--no-wait"])
            if not ok1 and not ok2:
                return _throne_api_error_response(out1 or out2, action_desc="переключить режим на TUN")
            _set_tun_adapter(True)
            _set_windows_proxy(False)
            _update_throne_db_settings({"tun_mode_enabled": "true", "system_proxy_enabled": "false"})
            return "Режим Throne успешно переключен на: **TUN (системный виртуальный сетевой адаптер)**. Трафик всех программ на ПК направляется через виртуальный туннель throne-tun."

        elif m in ["прокси", "proxy", "системный прокси"]:
            ok1, out1 = _run_thronectl(["tun", "off", "--no-wait"])
            ok2, out2 = _run_thronectl(["proxy", "on", "--no-wait"])
            if not ok1 and not ok2:
                return _throne_api_error_response(out1 or out2, action_desc="переключить режим на Прокси")
            _set_tun_adapter(False)
            _set_windows_proxy(True, "127.0.0.1:2080")
            _update_throne_db_settings({"tun_mode_enabled": "false", "system_proxy_enabled": "true"})
            return "Режим Throne успешно переключен на: **Системный Прокси** (127.0.0.1:2080). TUN-адаптер отключен, браузеры и системный стек Windows переведены на локальный прокси."

        elif m in ["оба", "both", "all", "все", "tun+proxy", "proxy+tun"]:
            ok1, out1 = _run_thronectl(["tun", "on", "--no-wait"])
            ok2, out2 = _run_thronectl(["proxy", "on", "--no-wait"])
            if not ok1 and not ok2:
                return _throne_api_error_response(out1 or out2, action_desc="включить оба режима (TUN + Прокси)")
            _set_tun_adapter(True)
            _set_windows_proxy(True, "127.0.0.1:2080")
            _update_throne_db_settings({"tun_mode_enabled": "true", "system_proxy_enabled": "true"})
            return "Режим Throne успешно переключен на: **Оба режима (TUN + Прокси)**. Активированы как системный виртуальный адаптер throne-tun, так и системный прокси Windows."

        return f"Неизвестный режим: '{target}'. Допустимые варианты: 'tun', 'proxy', 'both'."

    elif act == "off":
        ok, out = _run_thronectl(["disconnect"])
        if not ok:
            return _throne_api_error_response(out, action_desc="отключить VPN")
        _set_tun_adapter(False)
        _set_windows_proxy(False)
        _update_throne_db_settings({"tun_mode_enabled": "false", "system_proxy_enabled": "false"})
        return "VPN Throne **выключен**. Трафик направлен напрямую через стандартное сетевое подключение без VPN и прокси."

    elif act == "on":
        if target:
            return manage_throne_vpn(action="switch_server", target=target)

        ok, out = _run_thronectl(["connect", "last", "--no-wait"])
        if not ok:
            return _throne_api_error_response(out, action_desc="включить VPN")
        _set_tun_adapter(True)
        _update_throne_db_settings({"tun_mode_enabled": "true"})
        return "VPN Throne **включен** (режим TUN активирован, трафик защищен)."

    elif act == "switch_server":
        servers = _list_throne_servers()
        if not servers:
            return "Не удалось получить список серверов из базы Throne."

        t_clean = target.strip()
        if not t_clean:
            return "Укажите сервер или страну для подключения (например, 'Германия', 'США' или 'best')."

        # Вариант "лучший" / "самый быстрый"
        if t_clean.lower() in ["best", "лучший", "самый быстрый", "быстрый", "минимальный пинг"]:
            valid_pings = [s for s in servers if s.get("latency") and s["latency"] > 0]
            if not valid_pings:
                _run_thronectl(["-json", "test", "latency", "--group", "1"], timeout=20)
                servers = _list_throne_servers()
                valid_pings = [s for s in servers if s.get("latency") and s["latency"] > 0]

            if not valid_pings:
                return "Не удалось определить доступный сервер с минимальным пингом. Попробуйте сказать «пропингуй все сервера»."

            valid_pings.sort(key=lambda x: x["latency"])
            selected = valid_pings[0]

            ok, out = _run_thronectl(["connect", str(selected["id"]), "--restart", "--no-wait"])
            if not ok:
                return _throne_api_error_response(out, action_desc=f"подключить лучший сервер {selected['name']}")

            _set_tun_adapter(True)
            _update_throne_db_settings({"remember_id": str(selected["id"]), "tun_mode_enabled": "true"})
            return f"Сервер VPN успешно переключен на самый быстрый: **{selected['name']}** (ID: {selected['id']}, пинг: {selected['latency']} мс)."

        # Поиск совпадений по стране, названию или ID
        matched, matched_name = _match_profiles_by_query(t_clean, servers)
        if not matched:
            return (
                f"Сервер по запросу '{target}' не найден среди серверов Throne.\n"
                "Скажите «покажи серверы» или выберите страну: Германия, Нидерланды, Швеция, США, Эстония, Польша, Япония."
            )

        # Проверяем доступность найденных серверов
        # Если у профилей latency <= 0, делаем быстрый живой пинг для подтверждения
        down_candidates = [s for s in matched if s.get("latency") is None or s.get("latency") <= 0]
        if down_candidates:
            pids = [str(s["id"]) for s in down_candidates]
            ok_test, out_test = _run_thronectl(["-json", "test", "latency"] + pids, timeout=8)
            if ok_test and out_test:
                try:
                    t_data = json.loads(out_test)
                    for r_item in t_data.get("results", []):
                        r_id = r_item.get("id")
                        r_lat = r_item.get("latency_ms", -1)
                        for s in matched:
                            if s["id"] == r_id:
                                s["latency"] = r_lat
                except Exception:
                    pass

        available = [s for s in matched if s.get("latency") and s.get("latency") > 0]

        # ЕСЛИ ВСЕ СЕРВЕРЫ ЭТОЙ СТРАНЫ / ЗАПРОСА НЕДОСТУПНЫ:
        if not available:
            # Собираем список работающих альтернатив
            all_live = [s for s in servers if s.get("latency") and s.get("latency") > 0]
            all_live.sort(key=lambda x: x["latency"])

            top_suggestions = []
            seen = set()
            for s in all_live:
                c_clean = s["name"].split("•")[0].strip()
                if c_clean not in seen and len(top_suggestions) < 4:
                    top_suggestions.append(f"• **{s['name']}** (пинг: {s['latency']} мс)")
                    seen.add(c_clean)

            sugg_str = "\n".join(top_suggestions) if top_suggestions else "• 🇩🇪 Германия\n• 🇳🇱 Нидерланды\n• 🇸🇪 Швеция\n• 🇺🇸 США"

            return (
                f"⚠️ **Сервер «{matched_name}» сейчас недоступен!** (Сервер не отвечает на запросы / таймаут).\n\n"
                f"Подключение **не выполнено**, чтобы вы не остались без интернет-соединения.\n\n"
                f"**Доступные альтернативы с быстрым пингом:**\n{sugg_str}\n\n"
                f"Пожалуйста, назовите другой сервер или страну — какой подключить?"
            )

        # Выбираем лучший доступный из найденных
        available.sort(key=lambda x: x["latency"])
        selected = available[0]

        ok, out = _run_thronectl(["connect", str(selected["id"]), "--restart", "--no-wait"])
        if not ok:
            return _throne_api_error_response(out, action_desc=f"переключить сервер на {selected['name']}")

        _set_tun_adapter(True)
        _update_throne_db_settings({"remember_id": str(selected["id"]), "tun_mode_enabled": "true"})

        ping_str = f", пинг: {selected['latency']} мс" if selected.get("latency") and selected["latency"] > 0 else ""
        return f"Сервер VPN успешно переключен на: **{selected['name']}** (ID: {selected['id']}{ping_str})."

    elif act == "switch_route":
        if not os.path.exists(THRONE_DB_PATH):
            return "База данных Throne не найдена."
        try:
            conn = sqlite3.connect(f"file:{THRONE_DB_PATH}?mode=ro", uri=True)
            c = conn.cursor()
            c.execute("SELECT id, name FROM route_profiles")
            routes = [{"id": r[0], "name": r[1]} for r in c.fetchall()]
            conn.close()
        except Exception as e:
            return f"Ошибка чтения маршрутов: {e}"

        t_clean = target.strip().lower()
        selected = None
        for r in routes:
            if str(r["id"]) == t_clean or t_clean in r["name"].lower():
                selected = r
                break

        if not selected:
            avail = ", ".join(f"'{r['name']}' (ID {r['id']})" for r in routes)
            return f"Конфигурация '{target}' не найдена. Доступно: {avail}."

        ok, out = _run_thronectl(["route", str(selected["id"]), "--no-wait"])
        if not ok:
            return _throne_api_error_response(out, action_desc=f"переключить маршрутизацию на {selected['name']}")

        _update_throne_db_settings({
            "current_route_id": str(selected["id"]),
            "active_routing": selected["name"],
        })
        return f"Конфигурация маршрутизации успешно переключена на: **{selected['name']}** (ID: {selected['id']})."

    else:
        return (
            f"Неизвестное действие для manage_throne_vpn: '{action}'. "
            "Доступные действия: 'status', 'on', 'off', 'set_mode', 'switch_server', 'switch_route', 'list_servers', 'list_routes'."
        )


def get_hardware_status(target: str = "all") -> str:
    """Мониторинг аппаратного состояния ПК: видеокарта (Nvidia RTX), процессор, ОЗУ, кулеры, процессы."""
    import hardware_monitor
    return hardware_monitor.get_hardware_status(target)


def manage_reminders(action: str = "add", text: str = "", delay_seconds: int = 0, target_time: str = "", query: str = "") -> str:
    """Управление напоминаниями Windows: создание (add), список (list), отмена (cancel)."""
    import reminders_manager
    act = (action or "add").lower().strip()
    if act == "add":
        res = reminders_manager.add_reminder(text=text, delay_seconds=delay_seconds, target_time=target_time)
        return res.get("message", "Напоминание установлено.")
    elif act in ("list", "show"):
        active = reminders_manager.list_active_reminders()
        if not active:
            return "У вас нет активных напоминаний."
        lines = ["📌 **Активные напоминания:**"]
        for r in active:
            lines.append(f"• **{r['due_str']}**: {r['text']} (ID: `{r['id']}`)")
        return "\n".join(lines)
    elif act in ("cancel", "remove", "delete"):
        return reminders_manager.cancel_reminder(query or text)
    else:
        return f"Неизвестное действие для manage_reminders: {action}"


async def process_clipboard_text(action: str = "translate", target_lang: str = "русский", custom_instruction: str = "", copy_to_clipboard: bool = False) -> str:
    """Интеллектуальная обработка текста из буфера обмена (перевод, выжимка, исправление ошибок, объяснение)."""
    import clipboard_assistant
    return await clipboard_assistant.process_clipboard(
        action=action,
        target_lang=target_lang,
        custom_instruction=custom_instruction,
        copy_to_clipboard=copy_to_clipboard,
    )


def execute_routine(routine_name: str, target: str = "") -> str:
    """Запуск комплексных сценариев: game (игровой), relax (отдых/кино), morning (сводка дня)."""
    import routines
    return routines.execute_routine(routine_name=routine_name, target=target)


def play_sound_effect(sound_type: str = "notification") -> str:
    """Воспроизводит звуковой эффект: 'notification' (уведомление), 'alarm' (будильник), 'timer' (таймер)."""
    import sound_manager
    ok = sound_manager.play_sound(sound_type)
    if ok:
        name_ru = "будильника" if "alarm" in sound_type or "будильник" in sound_type else "уведомления"
        return f"🔔 Воспроизвел звук {name_ru}."
    return f"Не удалось воспроизвести звук '{sound_type}'."


def schedule_action(
    action_description: str,
    tool_name: str,
    tool_args: dict | None = None,
    delay_seconds: int = 0,
    target_time: str = "",
) -> str:
    """Планирует выполнение любого действия через N секунд/минут или в точное время (например 18:42)."""
    import task_scheduler
    res = task_scheduler.schedule_task(
        description=action_description,
        tool_name=tool_name,
        tool_args=tool_args or {},
        delay_seconds=delay_seconds,
        target_time=target_time,
    )
    return res.get("message", "Действие успешно запланировано.")


def manage_scheduled_actions(action: str = "list", query: str = "") -> str:
    """Управление списком запланированных действий: 'list' (показать) или 'cancel' (отменить)."""
    import task_scheduler
    act = (action or "list").lower().strip()
    if act in ("list", "show"):
        active = task_scheduler.list_scheduled_tasks()
        if not active:
            return "У вас нет активных запланированных действий."
        lines = ["⏰ **Запланированные действия:**"]
        for t in active:
            lines.append(f"• **{t['due_str']}**: {t['description']} (ID: `{t['id']}`)")
        return "\n".join(lines)
    elif act in ("cancel", "remove", "delete"):
        return task_scheduler.cancel_scheduled_task(query)
    return f"Неизвестное действие для manage_scheduled_actions: {action}"



