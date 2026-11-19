// ============================================================
// О.Л.Е.Г. — Electron Main Process
// Invisible frameless window + global hotkey Ctrl+Space
// ============================================================

const { app, BrowserWindow, globalShortcut, Tray, Menu, screen, ipcMain, session, shell } = require("electron");
const path = require("path");

// ---- Singleton lock — only one instance allowed ----
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
}

// Динамическое переключение игнорирования мыши для прозрачных областей окна
ipcMain.on("set-ignore-mouse-events", (event, ignore, options) => {
  const targetWin = BrowserWindow.fromWebContents(event.sender);
  if (targetWin) {
    targetWin.setIgnoreMouseEvents(ignore, options);
  }
});

// Безопасное открытие внешних ссылок в нативном браузере
ipcMain.handle("open-external", async (event, url) => {
  if (url && (url.startsWith("http://") || url.startsWith("https://") || url.startsWith("steam://") || url.startsWith("mailto:"))) {
    await shell.openExternal(url);
    return true;
  }
  return false;
});

let win = null;
let hudWin = null;
let tray = null;

// ---- Window geometry ----
const WIN_WIDTH = 980;
const WIN_HEIGHT = 600;
const HUD_SIZE = 110;

// ---- In-Game HUD Overlay Window (Правый верхний угол экрана) ----
function createHudWindow() {
  const display = screen.getPrimaryDisplay();
  const screenW = display.bounds.width;
  const screenX = display.bounds.x;
  const screenY = display.bounds.y;

  hudWin = new BrowserWindow({
    width: HUD_SIZE,
    height: HUD_SIZE,
    x: screenX + screenW - HUD_SIZE - 20,
    y: screenY + 20,
    frame: false,
    transparent: true,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    focusable: false, // ГАРАНТИРУЕТ: окно никогда не забирает фокус у игр и приложений!
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });

  hudWin.loadFile("hud.html");
  hudWin.setAlwaysOnTop(true, "screen-saver");
  hudWin.setVisibleOnAllWorkspaces(true);
  hudWin.setIgnoreMouseEvents(true, { forward: true });

  hudWin.webContents.on("console-message", (event, level, message, line, sourceId) => {
    console.log(`[HUD Console L${level}]`, message);
  });
  hudWin.webContents.on("did-fail-load", (event, errorCode, errorDescription) => {
    console.error("[HUD Fail Load]", errorCode, errorDescription);
  });
  hudWin.webContents.on("did-finish-load", () => {
    console.log("[HUD Loaded Successfully]");
  }); // Клики проходят сквозь индикатор прямо в игру

  hudWin.on("close", (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      hudWin.hide();
    }
  });
}

function setHudState(state) {
  console.log("[setHudState]", state);
  if (!hudWin || hudWin.isDestroyed()) return;
  if (state === "hide" || state === "hidden") {
    hudWin.webContents.send("hud-update", { state: "hide" });
    setTimeout(() => {
      if (hudWin && !hudWin.isDestroyed()) {
        hudWin.hide();
      }
    }, 380);
  } else {
    if (!hudWin.isVisible()) {
      const display = screen.getPrimaryDisplay();
      hudWin.setPosition(display.bounds.x + display.bounds.width - HUD_SIZE - 20, display.bounds.y + 20);
      hudWin.showInactive(); // Показывает окно БЕЗ перехвата фокуса и без разрыва мыши
      hudWin.setAlwaysOnTop(true, "screen-saver");
      hudWin.setIgnoreMouseEvents(true, { forward: true });
    }
    hudWin.webContents.send("hud-update", { state });
  }
}

// IPC диспетчер состояния и звука HUD
ipcMain.on("hud-set-state", (event, state) => {
  setHudState(state);
});

ipcMain.on("hud-audio-level", (event, rms) => {
  if (hudWin && !hudWin.isDestroyed() && hudWin.isVisible()) {
    hudWin.webContents.send("hud-update", { type: "audio-level", rms });
  }
});

// Локальный HTTP контроллер HUD (127.0.0.1:8001)
const http = require("http");
const localHudServer = http.createServer(async (req, res) => {
  // Защита от CSRF: разрешаем только локальные запросы
  const origin = req.headers.origin || "";
  if (origin && !origin.includes("127.0.0.1") && !origin.includes("localhost")) {
    res.writeHead(403);
    res.end(JSON.stringify({ error: "Access denied" }));
    return;
  }
  res.setHeader("Access-Control-Allow-Origin", origin || "http://127.0.0.1");

  try {
    const parsedUrl = new URL(req.url, `http://${req.headers.host}`);
    if (parsedUrl.pathname === "/spotlight/toggle") {
      if (win && win.isVisible()) {
        hideWindow();
      } else {
        showWindow();
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, visible: win ? win.isVisible() : false }));
      return;
    }
    if (parsedUrl.pathname === "/hud/capture") {
      if (hudWin && !hudWin.isDestroyed()) {
        const image = await hudWin.webContents.capturePage();
        const pngBuf = image.toPNG();
        res.writeHead(200, { "Content-Type": "image/png" });
        res.end(pngBuf);
        return;
      }
    }
    if (parsedUrl.pathname === "/hud/state") {
      const state = parsedUrl.searchParams.get("state") || "listening-idle";
      setHudState(state);
      res.writeHead(200, { "Content-Type": "application/json" });
      const hwndHex = hudWin ? "0x" + hudWin.getNativeWindowHandle().readBigUInt64LE(0).toString(16) : null;
      res.end(JSON.stringify({ ok: true, state, bounds: hudWin ? hudWin.getBounds() : null, visible: hudWin ? hudWin.isVisible() : false, hwnd: hwndHex }));
      return;
    }
    if (parsedUrl.pathname === "/hud/audio") {
      const rms = parseFloat(parsedUrl.searchParams.get("rms") || "0.05");
      if (hudWin && !hudWin.isDestroyed() && hudWin.isVisible()) {
        hudWin.webContents.send("hud-update", { type: "audio-level", rms });
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, rms }));
      return;
    }
  } catch (err) {
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: String(err) }));
    return;
  }
  res.writeHead(404);
  res.end();
});

localHudServer.listen(8001, "127.0.0.1", () => {
  console.log("[Electron HUD Server] Listening on http://127.0.0.1:8001");
});

function createWindow() {
  const { width: screenW } = screen.getPrimaryDisplay().workAreaSize;

  win = new BrowserWindow({
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
    x: Math.round((screenW - WIN_WIDTH) / 2),
    y: 100, // положение Spotlight в верхней трети экрана
    frame: false,
    transparent: true,
    resizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false, // Фоновый микрофон и голосовой ассистент никогда не засыпают
    },
  });

  win.loadFile("index.html");

  // Безопасное открытие любых внешних ссылок в нативном браузере
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://") || url.startsWith("steam://") || url.startsWith("mailto:")) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });

  win.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith("file://")) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  win.webContents.on("console-message", (event, level, message, line, sourceId) => {
    console.log(`[Renderer Console L${level}]`, message);
  });

  // Обработка клавиш внутри окна:
  // 1. Escape скрывает окно ТОЛЬКО когда окно О.Л.Е.Г. активно
  // (никогда не регистрируем Escape глобально, чтобы не ломать игры и программы!)
  // 2. Блокируем Ctrl+R и F5, чтобы хоткеи записи OBS не сбрасывали интерфейс
  win.webContents.on("before-input-event", (event, input) => {
    if (input.key === "Escape" && input.type === "keyDown") {
      event.preventDefault();
      hideWindow();
      return;
    }
    if (input.control && input.key.toLowerCase() === "r") {
      event.preventDefault();
    }
    if (input.key === "F5") {
      event.preventDefault();
    }
  });

  // При потере фокуса — скрываем окно (аналог macOS Spotlight)
  win.on("blur", () => {
    hideWindow();
  });

  // Предотвращаем уничтожение окна при закрытии, просто скрываем
  win.on("close", (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      hideWindow();
    }
  });
}

// ---- Show / Hide ----
function showWindow() {
  if (!win) return;
  if (hudWin && hudWin.isVisible()) {
    hudWin.webContents.send("hud-update", { state: "hide" });
    setTimeout(() => {
      if (hudWin && !hudWin.isDestroyed()) hudWin.hide();
    }, 200);
  }
  const { width: screenW } = screen.getPrimaryDisplay().workAreaSize;
  win.setPosition(Math.round((screenW - WIN_WIDTH) / 2), 100);
  win.show();
  win.focus();
  win.setIgnoreMouseEvents(false);
  win.webContents.send("window-shown");
}

function hideWindow() {
  if (!win) return;
  win.webContents.send("window-hidden");
  setTimeout(() => {
    if (win) win.hide();
  }, 120);
}

// ---- Tray icon ----
function createTray() {
  const iconPath = path.join(__dirname, "icon.png");
  const fs = require("fs");

  if (!fs.existsSync(iconPath)) {
    const { nativeImage } = require("electron");
    const icon = nativeImage.createEmpty();
    tray = new Tray(icon);
  } else {
    tray = new Tray(iconPath);
  }

  const contextMenu = Menu.buildFromTemplate([
    {
      label: "Показать О.Л.Е.Г.",
      click: () => showWindow(),
    },
    { type: "separator" },
    {
      label: "Выход",
      click: () => {
        app.isQuitting = true;
        app.quit();
      },
    },
  ]);

  tray.setToolTip("О.Л.Е.Г. — AI Assistant");
  tray.setContextMenu(contextMenu);
  tray.on("click", () => showWindow());
}

// ---- App lifecycle ----
app.whenReady().then(() => {
  // Автоматическое разрешение доступа к микрофону для голосового ввода
  if (session && session.defaultSession) {
    session.defaultSession.setPermissionCheckHandler((webContents, permission) => {
      if (permission === "media") return true;
      return true;
    });

    session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
      if (permission === "media") return callback(true);
      callback(true);
    });
  }

  Menu.setApplicationMenu(null);

  createWindow();
  createHudWindow();
  createTray();

  // Глобальный хоткей Ctrl+Space — переключение видимости
  const registered = globalShortcut.register("CommandOrControl+Space", () => {
    console.log("[GlobalShortcut] Ctrl+Space pressed! win.isVisible():", win ? win.isVisible() : false);
    if (win && win.isVisible()) {
      hideWindow();
    } else {
      showWindow();
    }
  });
  console.log("[GlobalShortcut] Ctrl+Space registered:", registered);
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
});

app.on("window-all-closed", () => {
  // На Windows остаемся в трее
});

app.on("second-instance", () => {
  showWindow();
});
