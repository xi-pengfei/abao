const chat = document.querySelector("#chat");
const input = document.querySelector("#input");
const composer = document.querySelector("#composer");
const statusText = document.querySelector("#statusText");
const settingsPanel = document.querySelector("#settingsPanel");
const settingsButton = document.querySelector("#settingsButton");
const closeSettings = document.querySelector("#closeSettings");

const demoReplies = [
  "我会把这句话先当成一次界面测试。真正接上后端以后，这里会一边生成一边出现，而不是等整段话写完。",
  "如果我们保留这种安静的排版，后面加记忆、成长日记和状态页时，也不会把它做成控制台。",
  "现在还只是外壳，但气质应该先定下来：私人、克制、可长期打开，而不是一个临时聊天窗口。"
];

let replyIndex = 0;

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("./service-worker.js").catch(() => {});
}

settingsButton.addEventListener("click", () => {
  settingsPanel.classList.add("open");
  settingsPanel.setAttribute("aria-hidden", "false");
});

closeSettings.addEventListener("click", closePanel);
settingsPanel.addEventListener("click", (event) => {
  if (event.target === settingsPanel) closePanel();
});

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  appendMessage("user", "你", text);
  input.value = "";
  resizeInput();
  statusText.textContent = "正在想";

  const reply = demoReplies[replyIndex % demoReplies.length];
  replyIndex += 1;
  await streamMessage(reply);
  statusText.textContent = "已连接 · 正在听";
});

input.addEventListener("input", resizeInput);

function closePanel() {
  settingsPanel.classList.remove("open");
  settingsPanel.setAttribute("aria-hidden", "true");
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
}

function appendMessage(kind, name, text) {
  const article = document.createElement("article");
  article.className = `message ${kind}`;
  article.innerHTML = `
    <div class="message-meta"></div>
    <p></p>
  `;
  article.querySelector(".message-meta").textContent = name;
  article.querySelector("p").textContent = text;
  chat.appendChild(article);
  scrollToBottom();
  return article.querySelector("p");
}

async function streamMessage(text) {
  const target = appendMessage("abao", "阿宝", "");
  for (const char of text) {
    target.textContent += char;
    scrollToBottom();
    await wait(22);
  }
}

function scrollToBottom() {
  chat.scrollTop = chat.scrollHeight;
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
