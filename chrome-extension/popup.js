// Explicit user-facing handoff. It only targets the loopback Chronos process.

const LOCAL_ENDPOINT = "http://127.0.0.1:8765/v1/browser-observation";

const output = document.getElementById("output");

// Material transfer is explicit and separate from the session observation.
const materialButton = document.createElement("button");
materialButton.textContent = "傳送此活動的 PDF 清單";
output.before(materialButton);
materialButton.addEventListener("click", () => {
  materialButton.disabled = true;
  chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
    if (!tab?.id) {
      show("No active tab.");
      materialButton.disabled = false;
      return;
    }
    chrome.tabs.sendMessage(tab.id, { type: "chronos.list_visible_materials" }, async (response) => {
      if (chrome.runtime.lastError || !response) {
        show("請重新載入擴充功能與 TronClass 活動頁，再試一次。");
        materialButton.disabled = false;
        return;
      }
      try {
        const result = await fetch("http://127.0.0.1:8765/v1/browser-materials", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Chronos-Bridge": "1" },
          body: JSON.stringify(response),
        });
        if (!result.ok) throw new Error("metadata rejected");
        show({ local_status: "accepted", material_status: response.status,
          material_count: response.materials.length });
      } catch {
        show("PDF 清單未交付：請確認本機 Chronos receiver 已啟動。");
      } finally {
        materialButton.disabled = false;
      }
    });
  });
});

function show(value) {
  output.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
  if (!tab?.id) {
    show("No active tab.");
    return;
  }

  chrome.tabs.sendMessage(tab.id, { type: "chronos.observe_read_only" }, (response) => {
    if (chrome.runtime.lastError) {
      show("Open a supported TronClass or CAS page, then try again.");
      return;
    }
    if (!response) {
      show("No observation returned.");
      return;
    }
    fetch(LOCAL_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Chronos-Bridge": "1",
      },
      body: JSON.stringify(response),
    })
      .then((result) => {
        if (!result.ok) throw new Error("local receiver rejected observation");
        show({ local_status: "accepted", observation: response });
      })
      .catch(() => {
        show({ local_status: "unavailable", observation: response });
      });
  });
});
