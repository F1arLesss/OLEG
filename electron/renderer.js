// ============================================================
// О.Л.Е.Г. 3.1 — Renderer Process
// Потоковый вывод (Streaming), поддержка Markdown, история команд (Up/Down)
// ============================================================

const input = document.getElementById("prompt-input");
const container = document.getElementById("oleg-container");
const responseWrapper = document.querySelector(".response-wrapper");
const responseContent = document.querySelector(".response-content") || document.getElementById("response-area");
const userMessage = document.getElementById("user-message");
const responseText = document.getElementById("response-text");
const responseArea = document.getElementById("response-area");
const loader = document.getElementById("loader");
const statusDot = document.getElementById("status-dot");
const quickCalcBar = document.getElementById("quick-calc-bar");
const micBtn = document.getElementById("mic-btn");

let isLoading = false;
let currentRawResponse = "";
let currentCalcValue = null;

// ---- История запросов (стрелки Вверх / Вниз) ----
const commandHistory = [];
let historyIndex = -1;
let currentDraft = "";

// ---- ResizeObserver для плавного изменения высоты ----
const resizeObserver = new ResizeObserver((entries) => {
  for (let entry of entries) {
    if (!container.classList.contains("expanded")) {
      responseWrapper.style.height = "0px";
      continue;
    }
    const newHeight = entry.target.scrollHeight;
    responseWrapper.style.height = `${newHeight}px`;
  }
});

if (responseContent) {
  resizeObserver.observe(responseContent);
}

// ---- Автосброс состояния при неактивности ----
let resetTimer = null;
const RESET_TIMEOUT_MS = 10000;

// ---- Простой и безопасный Markdown-парсер ----
function renderMarkdown(rawText) {
  if (!rawText) return "";

  // 1. Экранирование HTML
  let escaped = rawText
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // 2. Блоки кода: ```lang ... ```
  escaped = escaped.replace(/```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g, (match, lang, code) => {
    return `<pre class="code-block"><code class="lang-${lang}">${code.trim()}</code></pre>`;
  });

  // 3. Инлайн-код: `code`
  escaped = escaped.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  // 4. Жирный шрифт: **text**
  escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // 5. Курсив: *text*
  escaped = escaped.replace(/\*([^*]+)\*/g, "<em>$1</em>");

  // 6. Ссылки: [title](url)
  escaped = escaped.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" class="chat-link">$1</a>');

  // 7. Списки
  escaped = escaped.replace(/^[•\-\*]\s+(.*)$/gm, '<li class="list-item">$1</li>');
  escaped = escaped.replace(/(<li[\s\S]*?<\/li>)/g, '<ul class="chat-list">$1</ul>');
  // Убираем дублирование соседних ul
  escaped = escaped.replace(/<\/ul>\s*<ul class="chat-list">/g, "");

  // 8. Заголовки (### h3, ## h2, # h1)
  escaped = escaped.replace(/^###\s+(.+)$/gm, '<h3 class="chat-h3">$1</h3>');
  escaped = escaped.replace(/^##\s+(.+)$/gm, '<h2 class="chat-h2">$1</h2>');
  escaped = escaped.replace(/^#\s+(.+)$/gm, '<h1 class="chat-h1">$1</h1>');

  // 9. Переносы строк (кроме блоков pre, ul и заголовков)
  escaped = escaped.replace(/\n/g, "<br>");
  escaped = escaped.replace(/<\/(h[1-3]|ul|pre)>\s*<br>/g, "</$1>");

  return escaped;
}

// ---- Плавный потоковый рендерер текста (Typewriter Effect с неоновым курсором) ----
function prepareMarkdownForStreaming(raw) {
  if (!raw) return "";
  let text = raw;

  // Автозакрытие незавершенного жирного шрифта во время печати
  const boldMatches = text.match(/\*\*/g);
  if (boldMatches && boldMatches.length % 2 === 1) {
    text += "**";
  }

  // Автозакрытие незавершенных блоков кода
  const codeBlockMatches = text.match(/```/g);
  if (codeBlockMatches && codeBlockMatches.length % 2 === 1) {
    text += "\n```";
  } else {
    // Автозакрытие инлайн-кода
    const inlineMatches = text.match(/`/g);
    if (inlineMatches && inlineMatches.length % 2 === 1) {
      text += "`";
    }
  }

  return text;
}

function injectTypingCursor(html) {
  const cursorHtml = '<span class="typing-cursor"></span>';
  if (!html) return cursorHtml;

  // Если текст заканчивается списком </li></ul> — вставляем внутрь последнего элемента
  if (html.endsWith("</li></ul>")) {
    return html.slice(0, -10) + cursorHtml + "</li></ul>";
  }
  // Если блок кода </code></pre>
  if (html.endsWith("</code></pre>")) {
    return html.slice(0, -13) + cursorHtml + "</code></pre>";
  }
  return html + cursorHtml;
}

class SmoothTextRenderer {
  constructor(targetElement, scrollContainer) {
    this.target = targetElement;
    this.scrollContainer = scrollContainer;
    this.queue = [];
    this.displayedText = "";
    this.isStreaming = false;
    this.timer = null;
    this.onCompleteCallback = null;
  }

  start() {
    this.stop();
    this.queue = [];
    this.displayedText = "";
    this.isStreaming = true;
    this.target.className = "typing";
    this.target.innerHTML = "";
    this._tick();
  }

  append(chunk) {
    if (!chunk) return;
    for (let i = 0; i < chunk.length; i++) {
      this.queue.push(chunk[i]);
    }
  }

  finish(onComplete) {
    this.isStreaming = false;
    this.onCompleteCallback = onComplete;
    if (this.queue.length === 0) {
      this._finalize();
    }
  }

  stop() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.queue = [];
    this.isStreaming = false;
  }

  _finalize() {
    this.stop();
    this.target.classList.remove("typing");
    this.target.innerHTML = renderMarkdown(this.displayedText);
    this.target.classList.add("revealed");

    if (this.scrollContainer) {
      this.scrollContainer.scrollTop = this.scrollContainer.scrollHeight;
    }

    if (this.onCompleteCallback) {
      const cb = this.onCompleteCallback;
      this.onCompleteCallback = null;
      cb();
    }
  }

  _tick() {
    if (this.queue.length > 0) {
      // Высокоскоростной адаптивный рендеринг: мгновенно выводим пришедшие токены
      const qLen = this.queue.length;
      let count = Math.max(6, Math.ceil(qLen / 2));
      if (!this.isStreaming || qLen > 25) {
        count = qLen;
      }

      for (let i = 0; i < count && this.queue.length > 0; i++) {
        this.displayedText += this.queue.shift();
      }

      const safeMd = prepareMarkdownForStreaming(this.displayedText);
      const renderedHtml = renderMarkdown(safeMd);
      this.target.innerHTML = injectTypingCursor(renderedHtml);

      if (this.scrollContainer) {
        this.scrollContainer.scrollTop = this.scrollContainer.scrollHeight;
      }

      // Мгновенный 2мс тик для плавной передачи в DOM без искусственных задержек
      this.timer = setTimeout(() => this._tick(), 2);
    } else {
      if (!this.isStreaming) {
        this._finalize();
      } else {
        this.timer = setTimeout(() => this._tick(), 10);
      }
    }
  }
}

const smoothRenderer = new SmoothTextRenderer(responseText, responseArea);

// ---- Полная очистка состояния UI ----
function resetUI() {
  // Защита: в режиме мастера первоначальной настройки никогда не сбрасываем состояние и гайды
  if (setupStep > 0) {
    return;
  }

  cancelVoiceRecording();
  if (smoothRenderer) {
    smoothRenderer.stop();
  }
  isLoading = false;
  currentRawResponse = "";
  currentCalcValue = null;

  if (quickCalcBar) {
    quickCalcBar.classList.add("hidden");
    quickCalcBar.innerHTML = "";
  }

  if (resetTimer) {
    clearTimeout(resetTimer);
    resetTimer = null;
  }

  const sPanel = document.getElementById("settings-panel");
  if (sPanel && !sPanel.classList.contains("hidden")) {
    sPanel.classList.add("hidden");
  }

  container.classList.remove("expanded");
  if (responseWrapper) {
    responseWrapper.style.height = "0px";
  }

  input.value = "";
  if (setupStep === 1) {
    input.placeholder = KEY_PLACEHOLDER;
  } else if (setupStep === 2) {
    input.placeholder = getModelStepPlaceholder();
  } else if (setupStep === 3) {
    input.placeholder = GROQ_PLACEHOLDER;
  } else if (setupStep === 4) {
    input.placeholder = PROXY_PLACEHOLDER;
  } else {
    input.placeholder = DEFAULT_PLACEHOLDER;
  }
  userMessage.textContent = "";
  userMessage.classList.remove("animate");
  responseText.innerHTML = "";
  responseText.className = "";
  loader.classList.add("hidden");
}

// ---- Быстрый калькулятор и конвертер на лету (0ms без запроса к ИИ) ----
function evaluateQuickExpression(text) {
  const raw = (text || "").trim();
  if (!raw || raw.length < 2) return null;

  // 1. Проценты: "15% от 84000" или "15% of 84000"
  const percentMatch = raw.match(/^(\d+(?:[.,]\d+)?)\s*%\s*(?:от|of)\s*(\d+(?:[.,]\d+)?)$/i);
  if (percentMatch) {
    const p = parseFloat(percentMatch[1].replace(",", "."));
    const n = parseFloat(percentMatch[2].replace(",", "."));
    if (!isNaN(p) && !isNaN(n)) {
      const res = (p / 100) * n;
      const formatted = Number.isInteger(res) ? res.toLocaleString("ru-RU") : res.toFixed(2);
      return { expr: `${p}% от ${n.toLocaleString("ru-RU")}`, val: formatted };
    }
  }

  // 2. Конвертер валют (приблизительный системный курс)
  const currMatch = raw.match(/^(\d+(?:[.,]\d+)?)\s*(usd|доллар(?:ов|а)?|\$|eur|евро|€)\s*(?:в|to|in)\s*(руб(?:лей|ля)?|rub|₽)$/i);
  if (currMatch) {
    const amount = parseFloat(currMatch[1].replace(",", "."));
    const cur = currMatch[2].toLowerCase();
    const rate = (cur.startsWith("e") || cur === "€" || cur.startsWith("евр")) ? 104 : 98;
    const res = Math.round(amount * rate);
    return { expr: `${amount} ${currMatch[2]} в рублях`, val: `~${res.toLocaleString("ru-RU")} ₽` };
  }

  // 3. Конвертер единиц измерения
  const kmToMi = raw.match(/^(\d+(?:[.,]\d+)?)\s*(?:км|km)\s*(?:в|to)\s*(?:мили|миль|miles?)$/i);
  if (kmToMi) {
    const val = parseFloat(kmToMi[1].replace(",", ".")) * 0.621371;
    return { expr: `${kmToMi[1]} км в милях`, val: `${val.toFixed(2)} миль` };
  }
  const lbToKg = raw.match(/^(\d+(?:[.,]\d+)?)\s*(?:фунт(?:ов|а)?|lbs?)\s*(?:в|to)\s*(?:кг|kg)$/i);
  if (lbToKg) {
    const val = parseFloat(lbToKg[1].replace(",", ".")) * 0.453592;
    return { expr: `${lbToKg[1]} фунтов в кг`, val: `${val.toFixed(2)} кг` };
  }
  const cToF = raw.match(/^([+-]?\d+(?:[.,]\d+)?)\s*(?:c|градус(?:ов)?\s*c?)\s*(?:в|to)\s*(?:f|фаренгейт(?:а)?)$/i);
  if (cToF) {
    const val = (parseFloat(cToF[1].replace(",", ".")) * 9/5) + 32;
    return { expr: `${cToF[1]} °C в Фаренгейтах`, val: `${val.toFixed(1)} °F` };
  }
  const mbToGb = raw.match(/^(\d+(?:[.,]\d+)?)\s*(?:мб|mb)\s*(?:в|to)\s*(?:гб|gb)$/i);
  if (mbToGb) {
    const val = parseFloat(mbToGb[1].replace(",", ".")) / 1024;
    return { expr: `${mbToGb[1]} МБ в ГБ`, val: `${val.toFixed(2)} ГБ` };
  }

  // 4. Математические выражения: 250 * 1.2, (10 + 5) / 3, sqrt(144), 2^8
  const cleaned = raw.replace(/,/g, ".").replace(/\^/g, "**").replace(/корень\s*из\s*(\d+)/gi, "Math.sqrt($1)").replace(/sqrt\((\d+)\)/gi, "Math.sqrt($1)");
  if (/^[0-9+\-*/().\s]|Math\.sqrt/i.test(cleaned) && /[0-9]/.test(cleaned) && /[+\-*/^]|Math\.sqrt/i.test(cleaned)) {
    const safeCheck = cleaned.replace(/Math\.sqrt/g, "");
    if (!/[a-zA-Z_$]/.test(safeCheck)) {
      try {
        const fn = new Function(`return (${cleaned})`);
        const res = fn();
        if (typeof res === "number" && isFinite(res)) {
          const formatted = Number.isInteger(res) ? res.toLocaleString("ru-RU") : parseFloat(res.toFixed(4)).toString();
          return { expr: raw, val: formatted };
        }
      } catch (e) {
        return null;
      }
    }
  }

  return null;
}

// ---- Click-Through управление мыши ----
container.addEventListener("mouseenter", () => {
  window.oleg.setIgnoreMouseEvents(false);
});

container.addEventListener("mouseleave", () => {
  window.oleg.setIgnoreMouseEvents(true, { forward: true });
});

window.addEventListener("mousemove", (event) => {
  if (container.contains(event.target)) {
    window.oleg.setIgnoreMouseEvents(false);
  } else {
    window.oleg.setIgnoreMouseEvents(true, { forward: true });
  }
});

// Клик по внешним ссылкам через стандартный браузер
responseText.addEventListener("click", (e) => {
  const target = e.target.closest("a");
  if (target && target.href) {
    e.preventDefault();
    if (window.oleg && window.oleg.openExternal) {
      window.oleg.openExternal(target.href);
    }
  }
});

// ---- IPC события окна ----
window.oleg.onWindowShown(() => {
  // Гарантируем, что панель настроек закрыта при открытии строки
  const sPanel = document.getElementById("settings-panel");
  if (sPanel && !sPanel.classList.contains("hidden")) {
    closeSettings(true);
    if (setupStep === 0) {
      resetUI();
    }
  }

  // Если активен шаг 3 или шаг 4 мастера настройки — гарантируем, что соответствующий гайд на месте и развернут!
  if (setupStep === 3) {
    if (typeof renderGroqStepGuide === "function") {
      renderGroqStepGuide();
    }
  } else if (setupStep === 4) {
    if (typeof renderProxyStepGuide === "function") {
      renderProxyStepGuide();
    }
  } else if (setupStep === 0) {
    // Если нет активного ответа, возвращаем окно в компактное состояние с пустой строкой
    if (!userMessage.textContent && !responseText.innerHTML) {
      container.classList.remove("expanded");
      if (responseWrapper) {
        responseWrapper.style.height = "0px";
      }
      input.value = "";
    }
  }

  container.classList.remove("hidden");

  if (resetTimer) {
    clearTimeout(resetTimer);
    resetTimer = null;
  }

  input.focus();
  window.oleg.setIgnoreMouseEvents(false);
});

window.oleg.onWindowHidden(() => {
  container.classList.add("hidden");

  // ВАЖНО: Во время мастера первоначальной настройки никогда не запускаем таймер сброса!
  // Пользователь может переключаться в браузер для копирования ключей сколько угодно времени.
  if (setupStep > 0) {
    if (resetTimer) {
      clearTimeout(resetTimer);
      resetTimer = null;
    }
    return;
  }

  // Если были открыты настройки — немедленно закрываем их и сбрасываем состояние,
  // чтобы при следующем открытии окна всегда была чистая пустая строка ввода
  const sPanel = document.getElementById("settings-panel");
  if (sPanel && !sPanel.classList.contains("hidden")) {
    closeSettings(true);
    resetUI();
    return;
  }

  if (resetTimer) {
    clearTimeout(resetTimer);
  }
  resetTimer = setTimeout(() => {
    resetUI();
  }, RESET_TIMEOUT_MS);
});

window.oleg.onResetState(() => {
  resetUI();
});

// ---- Мастер первоначальной настройки (Setup Wizard) ----
function normalizeProxyUrl(raw) {
  if (!raw) return "";
  const p = raw.trim();
  if (!p) return "";

  let scheme = "http";
  const mScheme = p.match(/^([a-zA-Z0-9+.-]+):\/\/(.*)$/);
  let body = p;
  if (mScheme) {
    const rawScheme = mScheme[1].toLowerCase();
    scheme = rawScheme === "scheme" ? "http" : rawScheme;
    body = mScheme[2].trim();
  }

  body = body.replace(/\/+$/, "");
  if (body.includes("@")) {
    const parts = body.split("@");
    return `${scheme}://${parts[0]}@${parts.slice(1).join("@")}`;
  }

  const parts = body.split(":");
  const isPort = (s) => /^\d+$/.test(s) && parseInt(s, 10) >= 1 && parseInt(s, 10) <= 65535;

  if (parts.length === 4) {
    if (isPort(parts[3])) {
      const [user, pwd, host, port] = parts;
      return `${scheme}://${user}:${pwd}@${host}:${port}`;
    } else if (isPort(parts[1])) {
      const [host, port, user, pwd] = parts;
      return `${scheme}://${user}:${pwd}@${host}:${port}`;
    } else {
      const [user, pwd, host, port] = parts;
      return `${scheme}://${user}:${pwd}@${host}:${port}`;
    }
  } else if (parts.length === 2) {
    return `${scheme}://${parts[0]}:${parts[1]}`;
  }

  return `${scheme}://${body}`;
}

const DEFAULT_PLACEHOLDER = "Спроси или дай команду О.Л.Е.Г.у";
const KEY_PLACEHOLDER = "Шаг 1/4: Вставьте API-ключ нейросети (OpenRouter или Gemini)";
const GROQ_PLACEHOLDER = "Шаг 3/4: Вставьте Groq API-ключ (gsk_)";
const PROXY_PLACEHOLDER = "Шаг 4/4: Прокси для API (или Enter для пропуска)";

let currentKeyProvider = "google"; // "google" или "openrouter"

function getModelStepPlaceholder() {
  if (currentKeyProvider === "openrouter") {
    return "Шаг 2/4: Напишите модель ИИ (или нажмите Enter для выбора бесплатной qwen3.7-flash)";
  }
  return "Шаг 2/4: Напишите модель ИИ (или нажмите Enter для выбора бесплатной gemini-3.5-flash-lite)";
}

let setupStep = 0; // 0: стандартный режим, 1: ввод ключа ИИ, 2: ввод модели, 3: ввод Groq ключа, 4: ввод прокси, 5: завершение

// ---- Динамическая подгонка ширины строки под длину серого текста (плейсхолдера) с отскоком ----
let _measureCanvas = null;

function getPlaceholderTextWidth(text) {
  if (!text) return 0;
  if (!_measureCanvas) {
    _measureCanvas = document.createElement("canvas");
  }
  const ctx = _measureCanvas.getContext("2d");
  if (input) {
    const style = window.getComputedStyle(input);
    const fontSize = style.fontSize || "17.5px";
    const fontFamily = style.fontFamily || "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
    const fontWeight = style.fontWeight || "400";
    ctx.font = `${fontWeight} ${fontSize} ${fontFamily}`;
  } else {
    ctx.font = "400 17.5px -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  }
  return ctx.measureText(text).width;
}

function updateContainerWidthForText(text) {
  if (!container) return;
  const BASE_WIDTH = 660;
  const MAX_WIDTH = 940;

  if (!text || !text.trim()) {
    container.style.width = `${BASE_WIDTH}px`;
    return;
  }

  const textW = getPlaceholderTextWidth(text);
  // Отступы контейнера (40px) + лупа (20px) + gap (14px) + кнопки справа и индикатор (~80px) + промежутки (~42px) + комфортный запас (~24px) ≈ 220px
  const requiredWidth = Math.ceil(textW + 220);

  const targetWidth = Math.min(MAX_WIDTH, Math.max(BASE_WIDTH, requiredWidth));
  container.style.width = `${targetWidth}px`;
}

// Автоматический перехват смены placeholder у input для плавного расширения с пружинистым отскоком
let isTypingPlaceholder = false;
try {
  const originalDescriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "placeholder");
  if (originalDescriptor) {
    Object.defineProperty(input, "placeholder", {
      get() {
        return originalDescriptor.get.call(this);
      },
      set(val) {
        originalDescriptor.set.call(this, val);
        if (!isTypingPlaceholder) {
          updateContainerWidthForText(val);
        }
      },
    });
  }
} catch (e) {
  console.warn("Could not intercept input.placeholder:", e);
}

function typewriterPlaceholder(targetText, speed = 20, callback = null) {
  let idx = 0;
  isTypingPlaceholder = true;
  // Сразу запускаем анимацию расширения контейнера под полный размер целевого текста с красивым отскоком
  updateContainerWidthForText(targetText);
  input.placeholder = "";
  const interval = setInterval(() => {
    if (idx < targetText.length) {
      input.placeholder += targetText[idx];
      idx++;
    } else {
      clearInterval(interval);
      isTypingPlaceholder = false;
      if (callback) callback();
    }
  }, speed);
}

function shakeInput(warningPlaceholder) {
  const row = document.querySelector(".input-row");
  if (row) {
    row.classList.add("shake");
    setTimeout(() => row.classList.remove("shake"), 400);
  }
  input.placeholder = warningPlaceholder;
  setTimeout(() => {
    if (setupStep === 1) input.placeholder = KEY_PLACEHOLDER;
    else if (setupStep === 2) input.placeholder = getModelStepPlaceholder();
    else if (setupStep === 3) input.placeholder = GROQ_PLACEHOLDER;
    else if (setupStep === 4) input.placeholder = PROXY_PLACEHOLDER;
  }, 2200);
}

function renderGroqStepGuide() {
  container.classList.add("expanded");
  loader.classList.add("hidden");
  userMessage.textContent = "";
  userMessage.classList.remove("animate");

  const guideMd = [
    "### ⚡ Шаг 3/4: Подключение Groq Whisper Large v3 Turbo (Обязательно)",
    "Для быстрого и точного распознавания речи без нагрузки на ваш диск (0 МБ):",
    "1. Перейдите по ссылке: [console.groq.com/keys](https://console.groq.com/keys)",
    "2. Войдите через Google или GitHub в 1 клик",
    "3. Нажмите кнопку **Create API Key**, укажите имя `OLEG` и скопируйте ключ",
    "4. **Вставьте ключ `gsk_...` в строку ввода выше и нажмите Enter**",
    "",
    "> ⚡ **Бесплатно:** до 2 000 команд в день. Ключ необходим для работы голосового ввода.",
  ].join("\n");

  responseText.className = "revealed";
  responseText.innerHTML = renderMarkdown(guideMd);

  requestAnimationFrame(() => {
    if (responseWrapper && responseArea) {
      responseWrapper.style.height = `${responseArea.scrollHeight}px`;
    }
  });

  updateContainerWidthForText(GROQ_PLACEHOLDER);
}

function renderProxyStepGuide() {
  container.classList.add("expanded");
  loader.classList.add("hidden");
  userMessage.textContent = "";
  userMessage.classList.remove("animate");

  const guideMd = [
    "### 🌐 Шаг 4/4: Прокси для API (Опционально)",
    "",
    "Примеры форматов адреса:",
    "• scheme://ip:port",
    "• scheme://user:pass:ip:port",
  ].join("\n");

  responseText.className = "revealed";
  responseText.innerHTML = renderMarkdown(guideMd);

  requestAnimationFrame(() => {
    if (responseWrapper && responseArea) {
      responseWrapper.style.height = `${responseArea.scrollHeight}px`;
    }
  });

  updateContainerWidthForText(PROXY_PLACEHOLDER);
}

function startSetupWizard() {
  setupStep = 1;
  input.value = "";
  container.classList.remove("expanded");
  if (responseWrapper) responseWrapper.style.height = "0px";
  userMessage.textContent = "";
  responseText.innerHTML = "";
  typewriterPlaceholder(KEY_PLACEHOLDER, 18);
  statusDot.className = "dot setup";
  statusDot.title = "Требуется ввод API-ключа нейросети (Шаг 1/4)";
  const row = document.querySelector(".input-row");
  if (row) {
    row.classList.remove("setup-done");
    row.classList.add("setup-active");
  }
  input.focus();
}

function completeSetupAnimation() {
  input.placeholder = "";
  input.value = "";

  // 1. Индикатор вспыхивает изумрудным, контейнер мягко светится (без резких линий)
  statusDot.className = "dot online success-flash";
  statusDot.title = "О.Л.Е.Г. готов к работе (ИИ подключен)";

  const row = document.querySelector(".input-row");
  container.classList.add("setup-complete-glow");

  // Фиксируем завершение настройки в localStorage
  localStorage.setItem("oleg_setup_completed", "true");

  // 2. Мягкое проявление первой фразы "✨ Всё готово!" с неоновым изумрудным свечением
  input.className = "setup-success";
  input.value = "✨ Всё готово!";

  setTimeout(() => {
    // 3. Мягкий переход ко второй фразе "Для дополнительных настроек нажмите ⚙️"
    input.className = "";
    void input.offsetWidth;
    input.className = "setup-hint";
    input.value = "Для дополнительных настроек нажмите ⚙️";

    // 4. Плавно и красиво возвращаем кнопки голоса и настроек с отскоком
    if (row) {
      row.classList.remove("setup-active");
    }

    setTimeout(() => {
      // 5. Завершение анимации: мягкое скрытие и плавный вывод плейсхолдера
      input.className = "";
      input.value = "";
      container.classList.remove("setup-complete-glow");

      typewriterPlaceholder(DEFAULT_PLACEHOLDER, 18, () => {
        setupStep = 0;
        statusDot.className = "dot online";
        statusDot.title = "О.Л.Е.Г. на связи (бэкенд активен)";
        input.focus();
      });
    }, 1800);
  }, 1300);
}

async function checkSetupStatus() {
  try {
    if (window.oleg.getConfigStatus) {
      const status = await window.oleg.getConfigStatus();
      if (status.api_key && (status.api_key.startsWith("sk-or-") || status.api_key.startsWith("sk-"))) {
        currentKeyProvider = "openrouter";
      } else {
        currentKeyProvider = "google";
      }

      const localDone = localStorage.getItem("oleg_setup_completed");
      const setupNeeded = Boolean(
        status.setup_needed ||
        !status.has_key ||
        !status.has_groq_key ||
        localDone === "false"
      );
      if (setupNeeded) {
        if (!status.has_key) {
          // Если нет API-ключа нейросети — запускаем мастер с Шага 1
          startSetupWizard();
        } else if (!status.has_groq_key) {
          // Ключ нейросети есть, но нет обязательного Groq ключа — переходим сразу к Шагу 3 с гайдом
          setupStep = 3;
          renderGroqStepGuide();
          typewriterPlaceholder(GROQ_PLACEHOLDER, 18);
          statusDot.className = "dot setup";
          statusDot.title = "Требуется ввод Groq API-ключа (Шаг 3/4)";
          const row = document.querySelector(".input-row");
          if (row) {
            row.classList.remove("setup-done");
            row.classList.add("setup-active");
          }
          input.focus();
        }
      }
    }
  } catch (e) {
    console.warn("Could not check config status:", e);
  }
}

// ---- Health-Check бэкенда ----
async function checkHealth() {
  const isHealthy = await window.oleg.checkBackend();
  // Если активен мастер первоначальной настройки — держим индикатор в статусе настройки
  if (setupStep > 0 && setupStep <= 4) {
    statusDot.className = isHealthy ? "dot setup" : "dot offline";
    statusDot.title = isHealthy ? "Требуется первоначальная настройка ключей" : "Бэкенд недоступен";
    return;
  }
  statusDot.classList.toggle("online", isHealthy);
  statusDot.classList.toggle("offline", !isHealthy);
  statusDot.title = isHealthy ? "О.Л.Е.Г. на связи (бэкенд активен)" : "Бэкенд недоступен";
}

checkHealth();
setInterval(checkHealth, 5000);
checkSetupStatus();

// ---- Голосовой ввод (Web Audio API -> 16kHz PCM WAV -> STT) ----
let mediaStream = null;
let audioContext = null;
let scriptProcessor = null;
let audioChunks = [];
let isRecording = false;
let isAudioSpeaking = false;
let silentSamplesCount = 0;
let sttSocket = null;
let lastLiveText = "";
const MAX_SILENCE_SAMPLES = 16000 * 0.75; // 750мс естественной тишины после речи для моментального завершения (было 2.2 сек)

// Качественный антиалиасинг ресемплер любого аппаратного битрейта (48кГц/44.1кГц) в 16кГц
function downsampleBuffer(buffer, inputSampleRate, targetSampleRate = 16000) {
  if (inputSampleRate === targetSampleRate) {
    return buffer;
  }
  const sampleRateRatio = inputSampleRate / targetSampleRate;
  const newLength = Math.round(buffer.length / sampleRateRatio);
  const result = new Float32Array(newLength);
  let offsetResult = 0;
  let offsetBuffer = 0;
  while (offsetResult < result.length) {
    const nextOffsetBuffer = Math.round((offsetResult + 1) * sampleRateRatio);
    let accum = 0;
    let count = 0;
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
      accum += buffer[i];
      count++;
    }
    result[offsetResult] = count > 0 ? accum / count : 0;
    offsetResult++;
    offsetBuffer = nextOffsetBuffer;
  }
  return result;
}

function encodeWAV(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  function writeString(offset, string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // ByteRate
  view.setUint16(32, 2, true); // BlockAlign
  view.setUint16(34, 16, true); // 16-bit
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }

  return new Blob([view], { type: "audio/wav" });
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const b64 = (reader.result || "").split(",")[1];
      resolve(b64 || "");
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function cancelVoiceRecording() {
  if (!isRecording) return;
  isRecording = false;
  if (sttSocket) {
    try { sttSocket.close(); } catch (e) {}
    sttSocket = null;
  }
  lastLiveText = "";
  if (micBtn) {
    micBtn.classList.remove("recording", "processing");
    micBtn.title = "Голосовой ввод (нажмите, чтобы говорить)";
  }
  input.placeholder = DEFAULT_PLACEHOLDER;
  if (scriptProcessor) {
    try { scriptProcessor.disconnect(); } catch (e) {}
    scriptProcessor = null;
  }
  if (audioContext) {
    try { audioContext.close(); } catch (e) {}
    audioContext = null;
  }
  if (mediaStream) {
    try { mediaStream.getTracks().forEach((t) => t.stop()); } catch (e) {}
    mediaStream = null;
  }
  audioChunks = [];
}

async function startVoiceRecording() {
  if (isRecording) return;
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const inputSampleRate = audioContext.sampleRate || 48000;

    const source = audioContext.createMediaStreamSource(mediaStream);
    scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);
    audioChunks = [];
    isRecording = true;
    isAudioSpeaking = false;
    silentSamplesCount = 0;
    lastLiveText = "";
    input.value = "";

    if (micBtn) {
      micBtn.classList.remove("processing");
      micBtn.classList.add("recording");
      micBtn.title = "О.Л.Е.Г. слушает (нажмите еще раз, чтобы остановить)";
    }
    input.placeholder = "🎙️ Слушаю, говорите (слова печатаются на лету)";

    // Моментальный WebSocket поток для живого вывода слов во время говорения
    try {
      sttSocket = new WebSocket("ws://127.0.0.1:8000/ws/stt");
      sttSocket.binaryType = "arraybuffer";

      sttSocket.onmessage = (event) => {
        if (!isRecording) return;
        try {
          const data = JSON.parse(event.data);
          if (data.text) {
            lastLiveText = data.text;
            input.value = data.text;
            input.setSelectionRange(input.value.length, input.value.length);
            input.dispatchEvent(new Event("input"));
          }
        } catch (e) {}
      };
      sttSocket.onerror = (e) => {
        console.warn("[STT WebSocket] Connection error, will use REST STT fallback:", e);
      };
    } catch (wsErr) {
      console.warn("[STT WebSocket Init Error]:", wsErr);
    }

    scriptProcessor.onaudioprocess = (e) => {
      if (!isRecording) return;
      const rawChannel = e.inputBuffer.getChannelData(0);
      const channel16k = downsampleBuffer(rawChannel, inputSampleRate, 16000);
      const copy = new Float32Array(channel16k.length);
      copy.set(channel16k);
      audioChunks.push(copy);

      // Отправляем 16-битный PCM чанк 16кГц по WebSocket для мгновенного Vosk распознавания
      if (sttSocket && sttSocket.readyState === WebSocket.OPEN) {
        const pcm16 = new Int16Array(channel16k.length);
        for (let i = 0; i < channel16k.length; i++) {
          const s = Math.max(-1, Math.min(1, channel16k[i]));
          pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        sttSocket.send(pcm16.buffer);
      }

      // Детекция речи (Voice Activity Detection по среднеквадратичной амплитуде RMS)
      let sum = 0;
      for (let i = 0; i < channel16k.length; i++) {
        sum += channel16k[i] * channel16k[i];
      }
      const rms = Math.sqrt(sum / channel16k.length);
      if (rms > 0.02) {
        isAudioSpeaking = true;
        silentSamplesCount = 0;
      } else if (isAudioSpeaking) {
        silentSamplesCount += channel16k.length;
        if (silentSamplesCount >= MAX_SILENCE_SAMPLES) {
          stopVoiceRecording();
        }
      }
    };

    source.connect(scriptProcessor);
    scriptProcessor.connect(audioContext.destination);
  } catch (err) {
    console.error("Audio recording error:", err);
    if (micBtn) {
      micBtn.classList.remove("recording", "processing");
      micBtn.title = "Голосовой ввод (нажмите, чтобы говорить)";
    }
    input.placeholder = "⚠️ Ошибка доступа к микрофону: " + err.message;
    setTimeout(() => {
      input.placeholder = DEFAULT_PLACEHOLDER;
    }, 3000);
  }
}

async function stopVoiceRecording() {
  if (!isRecording) return;
  isRecording = false;

  // Оповещаем WebSocket о завершении
  if (sttSocket && sttSocket.readyState === WebSocket.OPEN) {
    try {
      sttSocket.send(JSON.stringify({ action: "finalize" }));
    } catch (e) {}
    setTimeout(() => {
      if (sttSocket) {
        try { sttSocket.close(); } catch (e) {}
        sttSocket = null;
      }
    }, 250);
  }

  if (micBtn) {
    micBtn.classList.remove("recording");
    micBtn.classList.add("processing");
    micBtn.title = "Распознавание речи...";
  }

  if (scriptProcessor) {
    try { scriptProcessor.disconnect(); } catch (e) {}
    scriptProcessor = null;
  }
  if (audioContext) {
    try { audioContext.close(); } catch (e) {}
    audioContext = null;
  }
  if (mediaStream) {
    try { mediaStream.getTracks().forEach((t) => t.stop()); } catch (e) {}
    mediaStream = null;
  }

  let totalLength = 0;
  for (const chunk of audioChunks) {
    totalLength += chunk.length;
  }

  if (totalLength < 16000 * 0.25) {
    if (micBtn) {
      micBtn.classList.remove("processing");
      micBtn.title = "Голосовой ввод (нажмите, чтобы говорить)";
    }
    if (lastLiveText && lastLiveText.trim()) {
      input.value = lastLiveText.trim();
    }
    input.placeholder = DEFAULT_PLACEHOLDER;
    return;
  }

  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (const chunk of audioChunks) {
    merged.set(chunk, offset);
    offset += chunk.length;
  }

  const wavBlob = encodeWAV(merged, 16000);
  const reader = new FileReader();

  reader.onloadend = async () => {
    try {
      const base64Audio = reader.result.split(",")[1];
      const res = await window.oleg.transcribeAudio(base64Audio, "audio/wav", "");

      if (micBtn) {
        micBtn.classList.remove("processing");
        micBtn.title = "Голосовой ввод (нажмите, чтобы говорить)";
      }

      if (res && res.text && res.text.trim()) {
        const recognized = res.text.trim();
        input.value = recognized;
        input.placeholder = DEFAULT_PLACEHOLDER;
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        input.dispatchEvent(new Event("input"));
      } else if (lastLiveText && lastLiveText.trim()) {
        let cap = lastLiveText.trim();
        cap = cap[0].toUpperCase() + cap.slice(1);
        input.value = cap;
        input.placeholder = DEFAULT_PLACEHOLDER;
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        input.dispatchEvent(new Event("input"));
      } else {
        input.placeholder = "Не удалось разобрать речь, попробуйте еще раз";
        setTimeout(() => {
          input.placeholder = DEFAULT_PLACEHOLDER;
        }, 2200);
      }
    } catch (err) {
      if (micBtn) {
        micBtn.classList.remove("processing");
        micBtn.title = "Голосовой ввод (нажмите, чтобы говорить)";
      }
      if (lastLiveText && lastLiveText.trim()) {
        input.value = lastLiveText.trim();
      } else {
        input.placeholder = "Ошибка распознавания: " + (err.message || String(err));
        setTimeout(() => {
          input.placeholder = DEFAULT_PLACEHOLDER;
        }, 3000);
      }
    }
  };

  reader.readAsDataURL(wavBlob);
}

if (micBtn) {
  micBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (isRecording) {
      stopVoiceRecording();
    } else {
      startVoiceRecording();
    }
  });
}

// ---- Клавиатурные события (Enter, Escape, Стрелки) ----
// Живое вычисление выражения на лету (0ms без запроса к ИИ)
input.addEventListener("input", () => {
  const calc = evaluateQuickExpression(input.value);
  if (calc) {
    currentCalcValue = calc.val;
    quickCalcBar.innerHTML = `<span>💡 ${calc.expr} = <strong class="calc-result">${calc.val}</strong></span><span class="calc-hint">[Tab или Клик — подставить]</span>`;
    quickCalcBar.classList.remove("hidden");
  } else {
    currentCalcValue = null;
    quickCalcBar.classList.add("hidden");
  }
});

// Клик по панели быстрого калькулятора подставляет значение
if (quickCalcBar) {
  quickCalcBar.addEventListener("click", () => {
    if (currentCalcValue) {
      input.value = currentCalcValue.replace(/[~₽\s]/g, "").replace(",", ".");
      quickCalcBar.classList.add("hidden");
      input.focus();
    }
  });
}

input.addEventListener("keydown", async (event) => {
  // Tab — быстрая подстановка вычисленного значения
  if (event.key === "Tab" && currentCalcValue) {
    input.value = currentCalcValue.replace(/[~₽\s]/g, "").replace(",", ".");
    quickCalcBar.classList.add("hidden");
    currentCalcValue = null;
    event.preventDefault();
    return;
  }

  // Стрелка ВВЕРХ — предыдущая команда из истории
  if (event.key === "ArrowUp") {
    if (commandHistory.length > 0) {
      if (historyIndex === -1) {
        currentDraft = input.value;
        historyIndex = commandHistory.length - 1;
      } else if (historyIndex > 0) {
        historyIndex--;
      }
      input.value = commandHistory[historyIndex];
      // Перемещаем курсор в конец строки
      setTimeout(() => input.setSelectionRange(input.value.length, input.value.length), 0);
      event.preventDefault();
    }
    return;
  }

  // Стрелка ВНИЗ — следующая команда из истории
  if (event.key === "ArrowDown") {
    if (historyIndex !== -1) {
      if (historyIndex < commandHistory.length - 1) {
        historyIndex++;
        input.value = commandHistory[historyIndex];
      } else {
        historyIndex = -1;
        input.value = currentDraft;
      }
      setTimeout(() => input.setSelectionRange(input.value.length, input.value.length), 0);
      event.preventDefault();
    }
    return;
  }

  // Escape внутри строки ввода
  if (event.key === "Escape") {
    const sPanel = document.getElementById("settings-panel");
    if (sPanel && !sPanel.classList.contains("hidden")) {
      closeSettings();
      resetUI();
      event.preventDefault();
      return;
    }
    if (isRecording) {
      cancelVoiceRecording();
      event.preventDefault();
      return;
    }
    if (input.value.trim().length > 0) {
      input.value = "";
      event.preventDefault();
    } else if (container.classList.contains("expanded")) {
      resetUI();
      input.focus();
      event.preventDefault();
    }
    return;
  }

  // Отправка по Enter
  if (event.key === "Enter" && !isLoading) {
    const text = input.value.trim();

    // ---- РЕЖИМ ПЕРВОНАЧАЛЬНОЙ НАСТРОЙКИ (SETUP WIZARD) ----
    if (setupStep === 1) {
      // Шаг 1: ввод API-ключа нейросети (ОБЯЗАТЕЛЬНО!)
      if (!text) {
        shakeInput("Пожалуйста, вставьте ваш API-ключ нейросети");
        event.preventDefault();
        return;
      }

      event.preventDefault();
      input.value = "";
      input.placeholder = "Сохраняю API-ключ";

      if (text.startsWith("sk-or-") || text.startsWith("sk-")) {
        currentKeyProvider = "openrouter";
      } else {
        currentKeyProvider = "google";
      }

      if (window.oleg.saveConfigKey) {
        await window.oleg.saveConfigKey(text);
      }

      // Переход к шагу 2: ввод названия модели
      setupStep = 2;
      statusDot.title = "Выбор модели (Шаг 2/4)";
      typewriterPlaceholder(getModelStepPlaceholder(), 18);
      return;
    }

    if (setupStep === 2) {
      // Шаг 2: ввод названия модели (или Enter для выбора дефолтной)
      event.preventDefault();
      const defaultModel = currentKeyProvider === "openrouter" ? "qwen3.7-flash" : "gemini-3.5-flash-lite";
      const modelName = text || defaultModel;
      input.value = "";
      input.placeholder = "Применяю модель";

      if (window.oleg.saveConfigModel) {
        await window.oleg.saveConfigModel(modelName);
      }

      // Переход к шагу 3: обязательный ввод Groq API-ключа с интерактивным гайдом
      setupStep = 3;
      statusDot.title = "Требуется ввод Groq API-ключа (Шаг 3/4)";
      renderGroqStepGuide();
      typewriterPlaceholder(GROQ_PLACEHOLDER, 18);
      return;
    }

    if (setupStep === 3) {
      // Шаг 3: ввод Groq ключа (ОБЯЗАТЕЛЬНО!)
      event.preventDefault();
      if (!text || !text.startsWith("gsk_")) {
        shakeInput("Обязательно вставьте корректный Groq API-ключ (начинается на gsk_)!");
        return;
      }

      input.value = "";
      input.placeholder = "Сохраняю Groq ключ";

      if (window.oleg.saveFullConfig) {
        await window.oleg.saveFullConfig({ groq_key: text });
      }

      // Переход к шагу 4: добавление прокси (с карточкой примеров)
      setupStep = 4;
      statusDot.title = "Настройка прокси (Шаг 4/4)";
      renderProxyStepGuide();
      typewriterPlaceholder(PROXY_PLACEHOLDER, 18);
      return;
    }

    if (setupStep === 4) {
      // Шаг 4: ввод прокси (опционально, Enter для пропуска)
      event.preventDefault();
      const rawProxy = text.trim();
      input.value = "";
      setupStep = 5;

      if (rawProxy) {
        input.placeholder = "Сохраняю прокси";
        const normalizedProxy = normalizeProxyUrl(rawProxy);
        if (window.oleg.saveFullConfig) {
          await window.oleg.saveFullConfig({ proxy: normalizedProxy });
        }
      }

      // Сворачиваем гайд шага 4
      container.classList.remove("expanded");
      if (responseWrapper) responseWrapper.style.height = "0px";
      userMessage.textContent = "";
      responseText.innerHTML = "";

      // Завершение мастера настройки: надпись "Всё готово!", зеленый индикатор и красивый переход
      completeSetupAnimation();
      return;
    }

    const trimmedLower = text.toLowerCase().trim();

    // Тест HUD индикатора: /hud, /test-hud
    if (trimmedLower === "/hud" || trimmedLower === "/test-hud") {
      event.preventDefault();
      input.value = "";
      userMessage.textContent = text;
      userMessage.classList.remove("animate");
      void userMessage.offsetWidth;
      userMessage.classList.add("animate");
      container.classList.add("expanded");
      loader.classList.add("hidden");
      responseText.className = "revealed";
      responseText.innerHTML = renderMarkdown("🎮 **Запуск демонстрации HUD индикатора в правом верхнем углу экрана...**\n- 1. Молчание (плавное дыхание сферы)\n- 2. Речь (динамическая пульсация под ритм голоса)\n- 3. Обработка (футуристическая анимация загрузки)\n- 4. Завершено (изумрудная вспышка и скрытие)");
      if (responseWrapper && responseArea) {
        responseWrapper.style.height = `${responseArea.scrollHeight}px`;
      }

      if (window.oleg && window.oleg.setHudState) {
        window.oleg.setHudState("listening-idle");
        setTimeout(() => {
          window.oleg.setHudState("listening-voice");
          let simInterval = setInterval(() => {
            const fakeRms = 0.03 + Math.random() * 0.09;
            window.oleg.sendHudAudioLevel(fakeRms);
          }, 80);
          setTimeout(() => {
            clearInterval(simInterval);
            window.oleg.setHudState("processing");
            setTimeout(() => {
              window.oleg.setHudState("done");
              setTimeout(() => {
                window.oleg.setHudState("hide");
              }, 1200);
            }, 2500);
          }, 2500);
        }, 2200);
      }
      return;
    }

    // Ручной перезапуск мастера настройки
    if (text === "/setup" || text.toLowerCase() === "настройка апи" || text.toLowerCase() === "смени ключ") {
      event.preventDefault();
      startSetupWizard();
      return;
    }

    if (!text) return;

    // Сохраняем в историю команд
    commandHistory.push(text);
    historyIndex = -1;
    currentDraft = "";

    if (resetTimer) {
      clearTimeout(resetTimer);
      resetTimer = null;
    }

    isLoading = true;
    currentRawResponse = "";

    // 1. Очищаем строку ввода и скрываем калькулятор
    input.value = "";
    if (quickCalcBar) {
      quickCalcBar.classList.add("hidden");
    }

    // 2. Отображаем сообщение пользователя
    userMessage.textContent = text;
    userMessage.classList.remove("animate");
    void userMessage.offsetWidth;
    userMessage.classList.add("animate");

    // 3. Раскрываем контейнер
    if (responseWrapper) {
      responseWrapper.style.height = "0px";
    }
    container.classList.add("expanded");

    // 4. Показываем лоадер и готовим блок ответа
    responseText.innerHTML = "";
    responseText.className = "";
    loader.classList.remove("hidden");

    // 5. Потоковый вызов бэкенда (SSE Streaming с плавным выводом)
    let isFirstChunk = true;

    await window.oleg.streamMessage(
      text,
      (chunk) => {
        // Первый чанк: скрываем лоадер и активируем рендерер текста
        if (isFirstChunk) {
          isFirstChunk = false;
          loader.classList.add("hidden");
          smoothRenderer.start();
        }
        currentRawResponse += chunk;
        smoothRenderer.append(chunk);
      },
      () => {
        // Поток завершен сервером
        loader.classList.add("hidden");
        if (isFirstChunk) {
          smoothRenderer.start();
        }
        smoothRenderer.finish(() => {
          isLoading = false;
          if (!currentRawResponse) {
            responseText.innerHTML = renderMarkdown("Команда выполнена.");
          }
        });
      },
      (errorMsg) => {
        // Ошибка потока
        smoothRenderer.stop();
        loader.classList.add("hidden");
        isLoading = false;
        responseText.className = "error";
        responseText.innerHTML = renderMarkdown(`⚠️ **Ошибка:** ${errorMsg}`);
      }
    );
  }
});

// Автофокус на строку ввода при кликах вне интерактивных элементов и текста
document.addEventListener("click", (e) => {
  // 1. Если пользователь выделил текст мышью — ни в коем случае не сбрасываем выделение!
  const sel = window.getSelection();
  if (sel && sel.toString().trim().length > 0) {
    return;
  }
  // 2. Не перехватываем фокус, если клик был по области текста ответа, гайда, коду, ссылкам или панели настроек
  if (e.target && e.target.closest("#settings-panel, #response-area, #response-text, .response-wrapper, a, code, pre, li, p, h1, h2, h3, input, button, select, textarea")) {
    return;
  }
  if (input) input.focus();
});
document.addEventListener("keydown", (e) => {
  const active = document.activeElement;
  if (active && (active.tagName === "INPUT" || active.tagName === "SELECT" || active.tagName === "TEXTAREA" || active.closest("#settings-panel"))) {
    return;
  }
  if (input) {
    input.focus();
  }
});

// Начальное состояние
container.classList.add("hidden");

// ============================================================
// О.Л.Е.Г. — Фоновая голосовая активация по слову «Олег» (Wake Word Engine)
// Работает в фоне без открытия строки ввода, воспроизводит отклик и выполняет команду.
// ============================================================
let wakeMediaStream = null;
let wakeAudioContext = null;
let wakeScriptProcessor = null;
let wakeSocket = null;
let isWakeListening = false;
let isCommandListening = false;
let wakeCommandTimer = null;
let wakeSilenceTimer = null;
let wakeLastSpokenText = "";
let wakeCommandAudioChunks = [];
let wakeRollingAudioBuffer = [];
const WAKE_ROLLING_MAX_CHUNKS = 45; // ~3.8 сек скользящего кольцевого буфера для пре-ролла речи

const wakeChime = new Audio("sounds/notifications/уведомление.mp3");

function playWakeChime() {
  try {
    wakeChime.currentTime = 0;
    wakeChime.play().catch((e) => console.warn("[WakeChime Error]:", e));
  } catch (e) {}
}

function initWakeWordListener() {
  if (isWakeListening) return;

  navigator.mediaDevices
    .getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
    .then((stream) => {
      wakeMediaStream = stream;
      wakeAudioContext = new (window.AudioContext || window.webkitAudioContext)();
      const inputSampleRate = wakeAudioContext.sampleRate || 48000;
      const source = wakeAudioContext.createMediaStreamSource(wakeMediaStream);
      wakeScriptProcessor = wakeAudioContext.createScriptProcessor(4096, 1, 1);

      function connectWakeSocket() {
        try {
          wakeSocket = new WebSocket("ws://127.0.0.1:8000/ws/wakeword");
          wakeSocket.binaryType = "arraybuffer";

          wakeSocket.onopen = () => {
            console.log("[WakeWord] Фоновый слушатель слова «Олег» успешно подключен.");
          };

          wakeSocket.onmessage = (event) => {
            try {
              const data = JSON.parse(event.data);

              if (data.type === "wake_detected") {
                console.log("[WakeWord] ⚡ Распознано обращение «Олег»:", data.text);
                onWakeDetected(data.text);
              } else if (data.type === "command_speech") {
                if (isCommandListening && data.text) {
                  wakeLastSpokenText = data.text;
                  resetSilenceTimer(900);
                }
              } else if (data.type === "final_command") {
                const fullText = data.text || wakeLastSpokenText;
                finishVoiceCommand(fullText);
              }
            } catch (e) {}
          };

          wakeSocket.onclose = () => {
            setTimeout(connectWakeSocket, 2000);
          };
          wakeSocket.onerror = () => {
            try { wakeSocket.close(); } catch (e) {}
          };
        } catch (e) {
          setTimeout(connectWakeSocket, 3000);
        }
      }

      connectWakeSocket();

      wakeScriptProcessor.onaudioprocess = (e) => {
        // Если пользователь вручную нажал микрофон в строке поиска — пропускаем
        if (isRecording) return;

        const rawChannel = e.inputBuffer.getChannelData(0);
        const channel16k = downsampleBuffer(rawChannel, inputSampleRate, 16000);

        const copy = new Float32Array(channel16k.length);
        copy.set(channel16k);

        // Непрерывный кольцевой буфер (пре-ролл): сохраняет последние ~3.8 сек звука,
        // чтобы слова команды, произнесенные сразу за словом «Олег», не терялись
        wakeRollingAudioBuffer.push(copy);
        if (wakeRollingAudioBuffer.length > WAKE_ROLLING_MAX_CHUNKS) {
          wakeRollingAudioBuffer.shift();
        }

        // Буферизируем аудио во время произнесения команды для облачного Whisper Large v3 Turbo
        if (isCommandListening) {
          wakeCommandAudioChunks.push(copy);
        }

        // VAD детекция громкости (RMS)
        let sum = 0;
        for (let i = 0; i < channel16k.length; i++) {
          sum += channel16k[i] * channel16k[i];
        }
        const rms = Math.sqrt(sum / channel16k.length);

        // Передаем живую громкость в HUD индикатор во время прослушивания
        if (isCommandListening && window.oleg && window.oleg.sendHudAudioLevel) {
          window.oleg.sendHudAudioLevel(rms);
        }

        // ВАЖНО: Непрерывная передача аудиопотока в Vosk (включая микропаузы и тишину комнаты).
        // Модель Vosk требует шлейф тишины сразу после произнесения слова «Олег», чтобы
        // моментально закрыть фонему и выдать результат, не ожидая дальнейших слов!
        if (wakeSocket && wakeSocket.readyState === WebSocket.OPEN) {
          const pcm16 = new Int16Array(channel16k.length);
          for (let i = 0; i < channel16k.length; i++) {
            const s = Math.max(-1, Math.min(1, channel16k[i]));
            pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
          }
          wakeSocket.send(pcm16.buffer);
        }

        // Во время речи пользователя обновляем быстрый таймер тишины (900мс для естественной паузы между словами)
        if (isCommandListening && rms > 0.014) {
          resetSilenceTimer(900);
        }
      };

      source.connect(wakeScriptProcessor);
      wakeScriptProcessor.connect(wakeAudioContext.destination);
      isWakeListening = true;
    })
    .catch((err) => {
      console.warn("[WakeWord] Микрофон недоступен для фонового ожидания:", err);
    });
}

function onWakeDetected(initialText) {
  if (isCommandListening) return;

  // 1. Моментальный приятный звуковой отклик
  playWakeChime();

  // 2. Активируем футуристический HUD в углу экрана (без перехвата фокуса и мыши!)
  if (window.oleg && window.oleg.setHudState) {
    window.oleg.setHudState("listening-idle");
  }

  // 3. Строка ввода ОСТАЁТСЯ СКРЫТОЙ (не открывая окно поиска!)
  isCommandListening = true;
  wakeLastSpokenText = "";
  // Переносим весь накопленный пре-ролл буфер (последние ~3.8 сек речи), чтобы Whisper получил фразу целиком
  wakeCommandAudioChunks = [...wakeRollingAudioBuffer];

  // Пользователь произнес имя «Олег» — даем время на формулировку вопроса
  wakeCommandTimer = setTimeout(() => {
    stopListeningCommand();
  }, 7000);
  // Первоначальный таймер тишины: 2.5 секунды ожидания первого слова команды
  resetSilenceTimer(2500);
}

function resetSilenceTimer(timeoutMs = 900) {
  if (wakeSilenceTimer) clearTimeout(wakeSilenceTimer);
  wakeSilenceTimer = setTimeout(() => {
    stopListeningCommand();
  }, timeoutMs);
}

function stopListeningCommand() {
  if (!isCommandListening) return;
  if (wakeCommandTimer) clearTimeout(wakeCommandTimer);
  if (wakeSilenceTimer) clearTimeout(wakeSilenceTimer);

  // Переключаем HUD в режим загрузки (обрабатывает запрос)
  if (window.oleg && window.oleg.setHudState) {
    window.oleg.setHudState("processing");
  }

  if (wakeSocket && wakeSocket.readyState === WebSocket.OPEN) {
    try {
      wakeSocket.send(JSON.stringify({ action: "finalize" }));
    } catch (e) {}
  } else {
    finishVoiceCommand(wakeLastSpokenText);
  }
}

async function finishVoiceCommand(fullText) {
  isCommandListening = false;
  if (wakeCommandTimer) clearTimeout(wakeCommandTimer);
  if (wakeSilenceTimer) clearTimeout(wakeSilenceTimer);

  // Сбрасываем фоновый сокет в режим ожидания «Олег»
  if (wakeSocket && wakeSocket.readyState === WebSocket.OPEN) {
    try {
      wakeSocket.send(JSON.stringify({ action: "reset" }));
    } catch (e) {}
  }

  // Облачная высокоточная транскрибация команды через Groq Whisper Large v3 Turbo (0 МБ на диске)
  const recordedChunks = wakeCommandAudioChunks;
  wakeCommandAudioChunks = [];

  let totalLen = 0;
  for (const c of recordedChunks) totalLen += c.length;

  if (totalLen >= 16000 * 0.35) {
    try {
      const merged = new Float32Array(totalLen);
      let offset = 0;
      for (const c of recordedChunks) {
        merged.set(c, offset);
        offset += c.length;
      }
      const wavBlob = encodeWAV(merged, 16000);
      const b64 = await blobToBase64(wavBlob);
      if (b64 && window.oleg && window.oleg.transcribeAudio) {
        // Чистый запрос в Whisper без искаженного черновика Vosk
        const res = await window.oleg.transcribeAudio(b64, "audio/wav", "");
        if (res && res.text && res.text.trim()) {
          console.log("[WakeWord Whisper] Распознано через Whisper Large v3 Turbo:", res.text.trim());
          fullText = res.text.trim();
        }
      }
    } catch (whisperErr) {
      console.warn("[WakeWord Whisper Error, falling back to local draft]:", whisperErr);
    }
  }

  const cleanText = (fullText || "")
    .replace(/^(?:олег\w*|алег\w*|олежк\w*)[\s,.:!]*/i, "")
    .replace(/^[,\s.:!]+|[,\s.:!]+$/g, "")
    .trim();

  if (!cleanText) {
    console.log("[WakeWord] Распознано только имя «Олег», отправляем запрос на голосовой отклик.");
    try {
      await fetch("http://127.0.0.1:8000/api/voice/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: "олег" }),
      });
    } catch (err) {}
    if (window.oleg && window.oleg.setHudState) {
      window.oleg.setHudState("done");
      setTimeout(() => {
        if (window.oleg && window.oleg.setHudState) {
          window.oleg.setHudState("hide");
        }
      }, 1200);
    }
    return;
  }

  console.log("[WakeWord] 🎙️ Выполнение фоновой команды:", cleanText);

  try {
    if (window.oleg && window.oleg.executeVoiceCommand) {
      await window.oleg.executeVoiceCommand(cleanText);
    } else {
      await fetch("http://127.0.0.1:8000/api/voice/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: cleanText }),
      });
    }

    // Успешное выполнение: мягкая вспышка и плавное скрытие HUD
    if (window.oleg && window.oleg.setHudState) {
      window.oleg.setHudState("done");
      setTimeout(() => {
        if (window.oleg && window.oleg.setHudState) {
          window.oleg.setHudState("hide");
        }
      }, 1200);
    }
  } catch (err) {
    console.error("[WakeWord Execution Error]:", err);
    if (window.oleg && window.oleg.setHudState) {
      window.oleg.setHudState("hide");
    }
  }
}

// Запускаем фоновый слушатель при старте
initWakeWordListener();

// ============================================================
// Панель настроек (Settings UI Controller)
// ============================================================
const settingsBtn = document.getElementById("settings-btn");
const settingsPanel = document.getElementById("settings-panel");
const settingsCloseBtn = document.getElementById("settings-close-btn");

const cfgApiKey = document.getElementById("cfg-api-key");
const cfgModel = document.getElementById("cfg-model");
const cfgBaseUrl = document.getElementById("cfg-base-url");
const cfgGroqKey = document.getElementById("cfg-groq-key");
const groqGuideBtn = document.getElementById("groq-guide-btn");
const groqGuideCard = document.getElementById("groq-guide-card");
const openGroqLink = document.getElementById("open-groq-link");
const cfgTtsEnabled = document.getElementById("cfg-tts-enabled");
const cfgTtsVoice = document.getElementById("cfg-tts-voice");
const cfgTtsRate = document.getElementById("cfg-tts-rate");
const cfgProxy = document.getElementById("cfg-proxy");
const cfgSpotifyId = document.getElementById("cfg-spotify-id");
const cfgSpotifySecret = document.getElementById("cfg-spotify-secret");
const cfgThroneKey = document.getElementById("cfg-throne-key");

const cfgBtnSave = document.getElementById("cfg-btn-save");
const cfgBtnClipboard = document.getElementById("cfg-btn-clipboard");
const cfgBtnClearMem = document.getElementById("cfg-btn-clear-mem");

// SVG иконки для глазика (показать / скрыть секретные данные)
const EYE_ICON_SVG = `<svg class="eye-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"></path><circle cx="12" cy="12" r="3"></circle></svg>`;
const EYE_OFF_ICON_SVG = `<svg class="eye-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"></path><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"></path><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"></path><line x1="2" y1="2" x2="22" y2="22"></line></svg>`;

let isSettingsClosing = false;
let settingsTimer = null;

async function openSettings() {
  if (!settingsPanel) return;
  if (settingsTimer) {
    clearTimeout(settingsTimer);
    settingsTimer = null;
  }
  isSettingsClosing = false;

  settingsPanel.classList.remove("hidden");
  container.classList.add("expanded");

  // Принудительно вызываем layout flush, чтобы браузер зафиксировал height: 0 перед плавным переходом к 480px
  void settingsPanel.offsetHeight;

  settingsPanel.classList.add("open");

  // Загружаем текущие параметры
  try {
    if (window.oleg && window.oleg.getConfigStatus) {
      const status = await window.oleg.getConfigStatus();
      if (status) {
        if (cfgModel) cfgModel.value = status.model || "";
        if (cfgBaseUrl) cfgBaseUrl.value = status.base_url || "";
        if (cfgTtsEnabled) cfgTtsEnabled.checked = Boolean(status.tts_enabled);
        if (cfgTtsVoice && status.tts_voice) cfgTtsVoice.value = status.tts_voice;
        if (cfgTtsRate && status.tts_rate) cfgTtsRate.value = status.tts_rate;
        if (cfgProxy) cfgProxy.value = normalizeProxyUrl(status.proxy_url || "");
        
        // Секретные данные: по умолчанию показываются точками (type="password")
        if (cfgApiKey) {
          cfgApiKey.value = status.api_key || "";
          cfgApiKey.type = "password";
        }
        if (cfgSpotifyId) {
          cfgSpotifyId.value = status.spotify_client_id || "";
          cfgSpotifyId.type = "password";
        }
        if (cfgSpotifySecret) {
          cfgSpotifySecret.value = status.spotify_client_secret || "";
          cfgSpotifySecret.type = "password";
        }
        if (cfgThroneKey) {
          cfgThroneKey.value = status.throne_key || "";
          cfgThroneKey.type = "password";
        }
        if (cfgGroqKey) {
          cfgGroqKey.value = status.groq_key || "";
          cfgGroqKey.type = "password";
        }

        // Сбрасываем состояние глазиков и обновляем видимость (скрываем у пустых полей)
        document.querySelectorAll(".password-toggle-btn").forEach((btn) => {
          btn.classList.remove("revealed");
          btn.innerHTML = EYE_ICON_SVG;
          btn.title = "Показать значение";
        });
        updatePasswordToggleButtons();
      }
    }
  } catch (err) {
    console.warn("[Settings] Не удалось получить статус конфигурации:", err);
  }
}

function closeSettings(immediate = false) {
  if (!settingsPanel) return;
  if (!settingsPanel.classList.contains("open") && settingsPanel.classList.contains("hidden")) return;

  if (settingsTimer) {
    clearTimeout(settingsTimer);
    settingsTimer = null;
  }

  // Возвращаем все поля паролей в скрытый вид (точками)
  document.querySelectorAll(".input-password-wrapper input").forEach((inp) => {
    inp.type = "password";
  });
  document.querySelectorAll(".password-toggle-btn").forEach((btn) => {
    btn.classList.remove("revealed");
    btn.innerHTML = EYE_ICON_SVG;
    btn.title = "Показать значение";
  });
  updatePasswordToggleButtons();

  if (immediate) {
    isSettingsClosing = false;
    settingsPanel.classList.remove("open");
    settingsPanel.classList.add("hidden");
    if (!userMessage.textContent && !responseText.innerHTML) {
      container.classList.remove("expanded");
      if (responseWrapper) {
        responseWrapper.style.height = "0px";
      }
    }
    if (input) {
      input.value = "";
      input.focus();
    }
    return;
  }

  isSettingsClosing = true;
  settingsPanel.classList.remove("open");

  settingsTimer = setTimeout(() => {
    settingsPanel.classList.add("hidden");
    isSettingsClosing = false;
    settingsTimer = null;

    if (!userMessage.textContent && !responseText.innerHTML) {
      container.classList.remove("expanded");
      if (responseWrapper) {
        responseWrapper.style.height = "0px";
      }
    }
    if (input) {
      input.value = "";
      input.focus();
    }
  }, 230);
}

function toggleSettings() {
  if (!settingsPanel) return;
  if (settingsPanel.classList.contains("hidden") || !settingsPanel.classList.contains("open") || isSettingsClosing) {
    openSettings();
  } else {
    closeSettings();
  }
}

if (settingsBtn) {
  settingsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleSettings();
  });
}

if (settingsPanel) {
  settingsPanel.addEventListener("click", (e) => {
    e.stopPropagation();
  });
}

if (settingsCloseBtn) {
  settingsCloseBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    closeSettings();
  });
}

// Динамическое обновление видимости глазиков (скрываем, если поле пустое)
function updatePasswordToggleButtons() {
  document.querySelectorAll(".password-toggle-btn").forEach((btn) => {
    const targetId = btn.getAttribute("data-target");
    const targetInput = document.getElementById(targetId);
    if (!targetInput) return;
    const hasValue = Boolean(targetInput.value && targetInput.value.trim().length > 0);
    if (hasValue) {
      btn.classList.remove("hidden");
    } else {
      btn.classList.add("hidden");
      targetInput.type = "password";
      btn.classList.remove("revealed");
      btn.innerHTML = EYE_ICON_SVG;
      btn.title = "Показать значение";
    }
  });
}

// Живое отслеживание ввода в секретных полях для мгновенного показа/скрытия глазика
document.querySelectorAll(".input-password-wrapper input").forEach((inp) => {
  inp.addEventListener("input", () => {
    const wrapper = inp.closest(".input-password-wrapper");
    if (!wrapper) return;
    const btn = wrapper.querySelector(".password-toggle-btn");
    if (!btn) return;
    const hasValue = Boolean(inp.value && inp.value.trim().length > 0);
    if (hasValue) {
      btn.classList.remove("hidden");
    } else {
      btn.classList.add("hidden");
      inp.type = "password";
      btn.classList.remove("revealed");
      btn.innerHTML = EYE_ICON_SVG;
      btn.title = "Показать значение";
    }
  });
});

// Переключение видимости секретных полей (пароли, API-ключи, ID) по клику на глазик
document.querySelectorAll(".password-toggle-btn").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const targetId = btn.getAttribute("data-target");
    const targetInput = document.getElementById(targetId);
    if (!targetInput || !targetInput.value || !targetInput.value.trim()) return;

    if (targetInput.type === "password") {
      targetInput.type = "text";
      btn.classList.add("revealed");
      btn.innerHTML = EYE_OFF_ICON_SVG;
      btn.title = "Скрыть значение";
    } else {
      targetInput.type = "password";
      btn.classList.remove("revealed");
      btn.innerHTML = EYE_ICON_SVG;
      btn.title = "Показать значение";
    }
  });
});

updatePasswordToggleButtons();

if (groqGuideBtn && groqGuideCard) {
  groqGuideBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    groqGuideCard.classList.toggle("hidden");
    if (!groqGuideCard.classList.contains("hidden")) {
      groqGuideBtn.textContent = "Скрыть гайд ▲";
    } else {
      groqGuideBtn.textContent = "Как получить бесплатно?";
    }
  });
}

if (openGroqLink) {
  openGroqLink.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (window.oleg && window.oleg.openExternal) {
      window.oleg.openExternal("https://console.groq.com/keys");
    }
  });
}

if (cfgBtnSave) {
  cfgBtnSave.addEventListener("click", async () => {
    const payload = {};
    if (cfgApiKey && cfgApiKey.value.trim()) payload.api_key = cfgApiKey.value.trim();
    if (cfgModel && cfgModel.value.trim()) payload.model = cfgModel.value.trim();
    if (cfgBaseUrl && cfgBaseUrl.value.trim()) payload.base_url = cfgBaseUrl.value.trim();
    if (cfgTtsEnabled) payload.tts_enabled = cfgTtsEnabled.checked;
    if (cfgTtsVoice) payload.tts_voice = cfgTtsVoice.value;
    if (cfgTtsRate) payload.tts_rate = cfgTtsRate.value;
    if (cfgProxy && cfgProxy.value.trim()) {
      const normProxy = normalizeProxyUrl(cfgProxy.value.trim());
      cfgProxy.value = normProxy;
      payload.proxy = normProxy;
    } else if (cfgProxy) {
      payload.proxy = "";
    }
    if (cfgSpotifyId && cfgSpotifyId.value.trim()) payload.spotify_client_id = cfgSpotifyId.value.trim();
    if (cfgSpotifySecret && cfgSpotifySecret.value.trim()) payload.spotify_client_secret = cfgSpotifySecret.value.trim();
    if (cfgThroneKey && cfgThroneKey.value.trim()) payload.throne_key = cfgThroneKey.value.trim();
    if (cfgGroqKey && cfgGroqKey.value.trim()) payload.groq_key = cfgGroqKey.value.trim();

    cfgBtnSave.textContent = "Сохраняю...";
    try {
      if (window.oleg && window.oleg.saveFullConfig) {
        await window.oleg.saveFullConfig(payload);
      }
      cfgBtnSave.textContent = "Сохранено! ✓";
      cfgBtnSave.classList.add("saved-success");
      await checkHealth();
      setTimeout(() => {
        cfgBtnSave.textContent = "Сохранить";
        cfgBtnSave.classList.remove("saved-success");
      }, 1500);
    } catch (err) {
      cfgBtnSave.textContent = "Ошибка!";
      setTimeout(() => { cfgBtnSave.textContent = "Сохранить"; }, 2000);
    }
  });
}

if (cfgProxy) {
  cfgProxy.addEventListener("blur", () => {
    if (cfgProxy.value.trim()) {
      cfgProxy.value = normalizeProxyUrl(cfgProxy.value.trim());
    }
  });
}

if (cfgBtnClipboard) {
  cfgBtnClipboard.addEventListener("click", async () => {
    closeSettings();
    userMessage.textContent = "📋 История буфера обмена";
    userMessage.classList.add("animate");
    container.classList.add("expanded");
    loader.classList.add("hidden");

    try {
      let items = [];
      let formattedText = "";
      if (window.oleg && window.oleg.getClipboardHistory) {
        const res = await window.oleg.getClipboardHistory(10);
        if (res) {
          items = Array.isArray(res.items) ? res.items : (Array.isArray(res.history) ? res.history : []);
          formattedText = typeof res.formatted === "string" ? res.formatted : "";
        }
      }
      if (!items.length) {
        responseText.className = "revealed";
        responseText.innerHTML = renderMarkdown(formattedText || "Буфер обмена пуст или новые записи еще не скопированы.");
      } else {
        let md = "### 📋 Последние записи в буфере обмена:\n\n";
        items.forEach((item, idx) => {
          const raw = item.text || item.preview || "";
          const preview = raw.length > 120 ? raw.slice(0, 120) + "..." : raw;
          const cleanPrev = preview.replace(/\n/g, " ");
          const t = item.time_str ? `\`[${item.time_str}]\` ` : "";
          md += `${idx + 1}. ${t}\`${cleanPrev}\`\n`;
        });
        md += "\n_Чтобы вставить текст, просто скопируйте нужный фрагмент или скажите: скопируй из буфера._";
        responseText.className = "revealed";
        responseText.innerHTML = renderMarkdown(md);
      }
    } catch (err) {
      responseText.className = "revealed";
      responseText.innerHTML = renderMarkdown(`Ошибка получения истории: ${err.message}`);
    }
  });
}

if (cfgBtnClearMem) {
  cfgBtnClearMem.addEventListener("click", async () => {
    if (!confirm("Очистить историю диалогов и локальную память ассистента?")) return;
    closeSettings();
    userMessage.textContent = "🧠 Очистка памяти";
    userMessage.classList.add("animate");
    container.classList.add("expanded");
    loader.classList.add("hidden");

    try {
      if (window.oleg && window.oleg.clearHistory) {
        await window.oleg.clearHistory();
      }
      responseText.className = "revealed";
      responseText.innerHTML = renderMarkdown("✅ **Память и история диалога успешно очищены!**\n\nО.Л.Е.Г. начинает общение с чистого листа.");
    } catch (err) {
      responseText.className = "revealed";
      responseText.innerHTML = renderMarkdown(`Ошибка очистки памяти: ${err.message}`);
    }
  });
}
