/* ── DOM refs ── */
const chat          = document.querySelector("#chat");
const emptyState    = document.querySelector("#emptyState");
const input         = document.querySelector("#input");
const composer      = document.querySelector("#composer");
const statusText    = document.querySelector("#statusText");
const agentName     = document.querySelector("#agentName");
const settingsPanel = document.querySelector("#settingsPanel");
const settingsButton= document.querySelector("#settingsButton");
const closeSettings = document.querySelector("#closeSettings");
const serverInput   = document.querySelector("#serverUrl");
const tokenInput    = document.querySelector("#accessToken");
const saveSettings  = document.querySelector("#saveSettings");
const testConnection= document.querySelector("#testConnection");

/* ── State ── */
let displayName = "阿宝";

/* ── Init ── */
const settings = loadSettings();
serverInput.value = settings.serverUrl;
tokenInput.value  = settings.token;

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./service-worker.js").catch(() => {});
}

// 启动：先拿显示名，再拉历史
(async () => {
  await initFromHealth();
  await loadHistory();
})();

/* ── Health：拿显示名并更新 UI ── */
async function initFromHealth() {
  const cfg = loadSettings();
  if (!cfg.serverUrl) return;
  try {
    const res = await fetch(`${cfg.serverUrl}/api/health`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.display_name) {
      displayName = data.display_name;
      agentName.textContent = displayName;
      document.title = displayName;
      input.placeholder = `和${displayName}说点什么`;
    }
    setStatus("已连接", "connected");
  } catch {
    setStatus("未连接", "");
  }
}

/* ── History：加载最近 20 条对话 ── */
async function loadHistory() {
  const cfg = loadSettings();
  if (!cfg.serverUrl) return;
  try {
    const res = await fetch(`${cfg.serverUrl}/api/history?limit=20`, {
      headers: cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {},
    });
    if (!res.ok) return;
    const data = await res.json();
    if (!data.messages || data.messages.length === 0) return;
    hideEmptyState();
    for (const msg of data.messages) {
      const kind = msg.role === "user" ? "user" : "abao";
      const name = msg.role === "user" ? "你" : displayName;
      appendMessage(kind, name, msg.text);
    }
    scrollToBottom();
  } catch {
    // 历史加载失败不影响聊天，空状态保持显示
  }
}

/* ── 事件绑定 ── */
settingsButton.addEventListener("click", () => {
  settingsPanel.classList.add("open");
  settingsPanel.setAttribute("aria-hidden", "false");
});

closeSettings.addEventListener("click", closePanel);
settingsPanel.addEventListener("click", (e) => {
  if (e.target === settingsPanel) closePanel();
});

composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  hideEmptyState();
  appendMessage("user", "你", text);
  input.value = "";
  resizeInput();
  setStatus("正在想", "thinking");

  try {
    await streamFromServer(text);
    setStatus("已连接", "connected");
  } catch (err) {
    appendMessage("abao", displayName, `连接失败：${err.message}`);
    setStatus("连接失败", "");
  }
});

input.addEventListener("input", resizeInput);

// Enter 发送，Shift+Enter 换行
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  }
});

saveSettings.addEventListener("click", () => {
  saveCurrentSettings();
  closePanel();
  // 重新初始化
  initFromHealth();
});

testConnection.addEventListener("click", testServer);

/* ── Helpers ── */
function closePanel() {
  settingsPanel.classList.remove("open");
  settingsPanel.setAttribute("aria-hidden", "true");
}

function setStatus(text, cls) {
  statusText.textContent = text;
  statusText.className = cls || "";
}

function hideEmptyState() {
  emptyState.classList.add("hidden");
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 130)}px`;
}

function appendMessage(kind, name, text) {
  const article = document.createElement("article");
  article.className = `message ${kind}`;
  const meta = document.createElement("div");
  meta.className = "message-meta";
  meta.textContent = name;
  const body = document.createElement("p");
  body.textContent = text;
  article.appendChild(meta);
  article.appendChild(body);
  chat.appendChild(article);
  scrollToBottom();
  return body;
}

async function streamFromServer(text) {
  const target = appendMessage("abao", displayName, "");
  const cfg    = loadSettings();

  const res = await fetch(`${cfg.serverUrl}/api/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(cfg.token ? { Authorization: `Bearer ${cfg.token}` } : {}),
    },
    body: JSON.stringify({ text }),
  });

  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const payload = JSON.parse(line.slice(6));
      if (payload.type === "delta") {
        target.textContent += payload.text;
        scrollToBottom();
      }
    }
  }
}

async function testServer() {
  saveCurrentSettings();
  const cfg = loadSettings();
  setStatus("测试中…", "");
  try {
    const res = await fetch(`${cfg.serverUrl}/api/health`);
    if (res.ok) {
      const data = await res.json();
      setStatus(`已连接 · ${data.display_name || ""}`, "connected");
    } else {
      setStatus(`异常 ${res.status}`, "");
    }
  } catch {
    setStatus("连接失败", "");
  }
}

function loadSettings() {
  return {
    serverUrl: localStorage.getItem("abao.serverUrl") || window.location.origin,
    token:     localStorage.getItem("abao.token") || "",
  };
}

function saveCurrentSettings() {
  localStorage.setItem("abao.serverUrl", serverInput.value.trim() || window.location.origin);
  localStorage.setItem("abao.token",     tokenInput.value.trim());
}

function scrollToBottom() {
  chat.scrollTop = chat.scrollHeight;
}
