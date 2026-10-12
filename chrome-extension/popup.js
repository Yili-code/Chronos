// Explicit user-facing handoff. It only targets the loopback Chronos process.

const LOCAL_ENDPOINT = "http://127.0.0.1:8765/v1/browser-observation";
const output = document.getElementById("output");

function show(value) {
  output.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function addAction(label, messageType, endpoint, accept, success, unavailable) {
  const button = document.createElement("button");
  button.textContent = label;
  output.before(button);
  button.addEventListener("click", () => {
    button.disabled = true;
    chrome.tabs.query({active: true, currentWindow: true}, ([tab]) => {
      if (!tab?.id) {
        show("找不到目前分頁。");
        button.disabled = false;
        return;
      }
      chrome.tabs.sendMessage(tab.id, {type: messageType}, async payload => {
        try {
          if (chrome.runtime.lastError || !accept(payload)) {
            show(unavailable);
            return;
          }
          const response = await fetch(endpoint, {
            method: "POST",
            headers: {"Content-Type": "application/json", "X-Chronos-Bridge": "1"},
            body: JSON.stringify(payload),
          });
          if (!response.ok) throw new Error("handoff rejected");
          const receipt = await response.json();
          show(success(receipt));
        } catch {
          show(success({}));
        } finally {
          button.disabled = false;
        }
      });
    });
  });
}

addAction(
  "保存目前作業說明（僅本機）",
  "chronos.observe_assignment",
  "http://127.0.0.1:8765/v1/browser-assignment",
  payload => payload?.status === "observed",
  receipt => receipt.saved === true
    ? "作業觀察已保存至本機；尚未建立雲端代辦、生成草稿或提交作業。"
    : "未確認作業保存成功，請檢查本機接收程式。",
  "尚未取得可辨識的作業說明。請開啟作業內容頁，並確認擴充功能已重新載入。",
);

addAction(
  "保存目前公告（僅本機）",
  "chronos.observe_announcements",
  "http://127.0.0.1:8765/v1/browser-announcements",
  payload => payload?.status === "observed_partial",
  receipt => Number.isInteger(receipt.inserted) && receipt.inserted >= 0
    ? `公告已保存至本機：新增 ${receipt.inserted} 個內容版本。只涵蓋目前載入的公告，連結已移除；尚未傳到雲端或 Telegram。`
    : "未確認公告保存成功，請檢查本機接收程式。",
  "尚未取得公告。請開啟課程公告頁並等待載入；這不代表沒有公告。",
);

chrome.tabs.query({active: true, currentWindow: true}, ([tab]) => {
  if (!tab?.id) {
    show("No active tab.");
    return;
  }
  chrome.tabs.sendMessage(tab.id, {type: "chronos.observe_read_only"}, response => {
    if (chrome.runtime.lastError || !response) {
      show("Open a supported TronClass or CAS page, then try again.");
      return;
    }
    fetch(LOCAL_ENDPOINT, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Chronos-Bridge": "1"},
      body: JSON.stringify(response),
    })
      .then(result => {
        if (!result.ok) throw new Error("local receiver rejected observation");
        show({local_status: "accepted", observation: response});
      })
      .catch(() => show({local_status: "unavailable", observation: response}));
  });
});
