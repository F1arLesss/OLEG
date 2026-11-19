"""
О.Л.Е.Г. — Модуль аппаратного мониторинга (Hardware Monitor)
Сбор данных о температуре, частоте, загрузке GPU (Nvidia RTX), CPU, RAM, дисков и процессах.
"""

import os
import shutil
import subprocess
import psutil


def get_gpu_info() -> dict:
    """Получает детальные метрики Nvidia GPU через nvidia-smi."""
    nvidia_smi = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    if not os.path.exists(nvidia_smi) and not shutil.which("nvidia-smi"):
        return {"available": False, "error": "nvidia-smi не найден"}

    try:
        cmd = [
            nvidia_smi,
            "--query-gpu=name,temperature.gpu,utilization.gpu,utilization.memory,memory.total,memory.used,fan.speed",
            "--format=csv,noheader,nounits",
        ]
        out = subprocess.check_output(cmd, timeout=3, stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore").strip()
        if not out:
            return {"available": False, "error": "Пустой ответ nvidia-smi"}

        parts = [p.strip() for p in out.split(",")]
        name = parts[0] if len(parts) > 0 else "NVIDIA GPU"
        temp = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        gpu_util = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        mem_util = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        mem_total = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        mem_used = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
        fan_speed = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else None

        return {
            "available": True,
            "name": name,
            "temperature_c": temp,
            "gpu_utilization_percent": gpu_util,
            "memory_utilization_percent": mem_util,
            "vram_total_mb": mem_total,
            "vram_used_mb": mem_used,
            "fan_speed_percent": fan_speed,
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


def get_cpu_info() -> dict:
    """Метрики процессора (загрузка, частота, ядра)."""
    cpu_util = psutil.cpu_percent(interval=0.15)
    freq = psutil.cpu_freq()
    cores_physical = psutil.cpu_count(logical=False)
    cores_logical = psutil.cpu_count(logical=True)

    return {
        "cpu_utilization_percent": cpu_util,
        "frequency_current_mhz": round(freq.current) if freq else None,
        "frequency_max_mhz": round(freq.max) if freq and freq.max else None,
        "physical_cores": cores_physical,
        "logical_cores": cores_logical,
    }


def get_ram_info() -> dict:
    """Метрики оперативной памяти."""
    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / (1024 ** 3), 1),
        "used_gb": round(vm.used / (1024 ** 3), 1),
        "free_gb": round(vm.available / (1024 ** 3), 1),
        "percent": vm.percent,
    }


def get_top_processes(limit: int = 3) -> dict:
    """Топ самых требовательных процессов по CPU и по RAM."""
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            if info["name"] and info["name"].lower() not in ("system idle process", "system"):
                procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    top_cpu = sorted(procs, key=lambda x: x.get("cpu_percent") or 0, reverse=True)[:limit]
    top_ram = sorted(procs, key=lambda x: x.get("memory_percent") or 0, reverse=True)[:limit]

    return {
        "top_cpu": [{"name": p["name"], "cpu": p.get("cpu_percent", 0)} for p in top_cpu if (p.get("cpu_percent") or 0) > 0.5],
        "top_ram": [{"name": p["name"], "ram_percent": round(p.get("memory_percent", 0), 1)} for p in top_ram if (p.get("memory_percent") or 0) > 1.0],
    }


def get_hardware_status(target: str = "all") -> str:
    """
    Формирует красивую сводку состояния ПК для пользователя.
    target: "all", "gpu", "cpu", "ram", "top_processes"
    """
    target = (target or "all").lower().strip()
    gpu = get_gpu_info()
    cpu = get_cpu_info()
    ram = get_ram_info()

    lines = []

    # 1. Видеокарта
    if target in ("all", "gpu", "видеокарта", "видюха", "температура"):
        if gpu.get("available"):
            g_name = gpu.get("name", "NVIDIA GPU")
            g_temp = gpu.get("temperature_c")
            g_load = gpu.get("gpu_utilization_percent", 0)
            vram_u = round(gpu.get("vram_used_mb", 0) / 1024, 1)
            vram_t = round(gpu.get("vram_total_mb", 0) / 1024, 1)
            fan = gpu.get("fan_speed_percent")

            temp_str = f"{g_temp}°C" if g_temp is not None else "Н/Д"
            fan_str = f", кулеры: {fan}%" if fan is not None else ""

            # Оценка состояния температуры
            status_badge = "🟢 В норме"
            if g_temp and g_temp > 82:
                status_badge = "🔴 Высокая температура!"
            elif g_temp and g_temp > 75:
                status_badge = "🟡 Под нагрузкой"

            lines.append(f"🎮 **Видеокарта ({g_name}):**")
            lines.append(f"• Температура: **{temp_str}** ({status_badge}{fan_str})")
            lines.append(f"• Загрузка чипа: **{g_load}%**")
            lines.append(f"• Видеопамять (VRAM): **{vram_u} ГБ** / {vram_t} ГБ")
        else:
            lines.append("🎮 **Видеокарта:** Метрики Nvidia GPU сейчас недоступны.")

    # 2. Процессор
    if target in ("all", "cpu", "проц", "процессор"):
        c_util = cpu.get("cpu_utilization_percent", 0)
        c_freq = cpu.get("frequency_current_mhz")
        freq_str = f" ({round(c_freq / 1000, 2)} ГГц)" if c_freq else ""
        cores = f"{cpu.get('physical_cores', '')}я/{cpu.get('logical_cores', '')}п"

        lines.append(f"⚡ **Процессор ({cores}):**")
        lines.append(f"• Загрузка: **{c_util}%**{freq_str}")

    # 3. Оперативная память
    if target in ("all", "ram", "память", "озу"):
        r_used = ram.get("used_gb", 0)
        r_total = ram.get("total_gb", 0)
        r_pct = ram.get("percent", 0)

        lines.append(f"🧠 **Оперативная память (RAM):**")
        lines.append(f"• Занято: **{r_used} ГБ** из {r_total} ГБ ({r_pct}%)")

    # 4. Процессы (если запрашивалось или при общей сводке)
    if target in ("all", "top_processes", "процессы"):
        top = get_top_processes(limit=2)
        top_items = []
        for p in top.get("top_cpu", []):
            top_items.append(f"{p['name']} (CPU: {round(p['cpu'])}%)")
        for p in top.get("top_ram", []):
            top_items.append(f"{p['name']} (RAM: {p['ram_percent']}%)")
        if top_items:
            lines.append("📊 **Топ потребителей ресурсов:** " + ", ".join(top_items))

    return "\n".join(lines)
