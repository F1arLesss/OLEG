// ============================================================
// О.Л.Е.Г. — Preload Script (Context Bridge)
// Безопасный мост между renderer и main process / backend
// ============================================================

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("oleg", {
  // ---- IPC events от main process ----
  onWindowShown: (callback) =>
    ipcRenderer.on("window-shown", () => callback()),
  onWindowHidden: (callback) =>
    ipcRenderer.on("window-hidden", () => callback()),
  onResetState: (callback) =>
    ipcRenderer.on("reset-state", () => callback()),

  // ---- Пробитие кликов сквозь прозрачные области ----
  setIgnoreMouseEvents: (ignore, options) => {
    ipcRenderer.send("set-ignore-mouse-events", ignore, options);
  },

  // ---- Безопасное открытие внешних ссылок в нативном браузере ----
  openExternal: (url) => ipcRenderer.invoke("open-external", url),

  // ---- Потоковое общение с бэкендом (High-Speed Streaming) ----
  streamMessage: async (text, onChunk, onComplete, onError) => {
    try {
      const response = await fetch("http://127.0.0.1:8000/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });

      if (!response.ok) {
        throw new Error(`Ошибка бэкенда: HTTP ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunkText = decoder.decode(value, { stream: true });
        if (chunkText) {
          onChunk(chunkText);
        }
      }

      if (onComplete) onComplete();
    } catch (err) {
      if (onError) onError(err.message || String(err));
    }
  },

  // ---- Обычный синхронный запрос ----
  sendMessage: async (text) => {
    try {
      const response = await fetch("http://127.0.0.1:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!response.ok) {
        throw new Error(`Ошибка бэкенда: HTTP ${response.status}`);
      }
      return await response.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- Алиас ask для полной совместимости ----
  ask: async (text) => {
    try {
      const response = await fetch("http://127.0.0.1:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      if (!response.ok) {
        throw new Error(`Ошибка бэкенда: HTTP ${response.status}`);
      }
      const data = await response.json();
      return data.reply || "";
    } catch (err) {
      throw err;
    }
  },

  // ---- Очистка истории ----
  clearHistory: async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/clear", { method: "POST" });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- Health-check бэкенда ----
  checkBackend: async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/health", {
        signal: AbortSignal.timeout(2000),
      });
      return res.ok;
    } catch {
      return false;
    }
  },

  // ---- Первоначальная настройка и конфигурация ----
  getConfigStatus: async () => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/config/status", {
        signal: AbortSignal.timeout(2500),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      return await res.json();
    } catch {
      return { setup_needed: false, has_key: false };
    }
  },

  saveConfigKey: async (apiKey) => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/config/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey }),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  saveConfigModel: async (model) => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/config/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: model }),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  saveFullConfig: async (configObj) => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/config/setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configObj),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- Голосовой ввод (Speech-to-Text) ----
  transcribeAudio: async (audioBase64, mimeType = "audio/wav", hintText = "") => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/stt", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio_base64: audioBase64, mime_type: mimeType, hint_text: hintText }),
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- Синтез речи (TTS) ----
  speakText: async (text, voice = "") => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/tts/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice }),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- История буфера обмена ----
  getClipboardHistory: async (limit = 10, query = "") => {
    try {
      const res = await fetch(`http://127.0.0.1:8000/api/clipboard/history?limit=${limit}&query=${encodeURIComponent(query)}`);
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- Удаление факта из памяти ----
  deleteMemory: async (query) => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/memory/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- Фоновое выполнение голосовых команд (Wake Word «Олег») ----
  executeVoiceCommand: async (text) => {
    try {
      const res = await fetch("http://127.0.0.1:8000/api/voice/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text }),
      });
      return await res.json();
    } catch (err) {
      return { error: err.message };
    }
  },

  // ---- In-Game HUD Overlay (Индикатор в углу экрана) ----
  setHudState: (state) => {
    ipcRenderer.send("hud-set-state", state);
  },
  sendHudAudioLevel: (rms) => {
    ipcRenderer.send("hud-audio-level", rms);
  },
  onHudUpdate: (callback) => {
    ipcRenderer.on("hud-update", (event, data) => callback(data));
  },
});
