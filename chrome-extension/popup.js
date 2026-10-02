// Explicit user-facing handoff. It only targets the loopback Chronos process.

const LOCAL_ENDPOINT = "http://127.0.0.1:8765/v1/browser-observation";

const output = document.getElementById("output");
const downloads = document.createElement("div");
output.before(downloads);

function offerDownloads(tabId, materials) {
  downloads.replaceChildren();
  for (const material of materials) {
    const button = document.createElement("button");
    button.textContent = `下載並保存：${material.filename}`;
    downloads.append(button);
    button.addEventListener("click", () => {
      button.disabled = true;
      show("正在下載；請保持此視窗開啟。尚未建立摘要或上傳至雲端。");
      chrome.tabs.sendMessage(tabId, { type: "chronos.download_visible_pdf", source_id: material.source_id }, async (download) => {
        try {
          if (chrome.runtime.lastError || !download) throw new Error("download unavailable");
          if (download.status !== "downloaded") {
            show(download.status === "reauth_required" ? "請重新登入 TronClass，再重新取得清單。" : "附件暫時無法下載；尚未保存。");
            return;
          }
          const response = await fetch("http://127.0.0.1:8765/v1/browser-pdf", {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-Chronos-Bridge": "1" },
            body: JSON.stringify({ course_id: material.course_id, download }),
          });
          if (!response.ok) throw new Error("persistence rejected");
          const receipt = await response.json();
          if (receipt.status !== "persisted" || receipt.source_id !== material.source_id) throw new Error("invalid receipt");
          show({ local_status: "persisted", filename: material.filename, byte_count: receipt.byte_count });
        } catch {
          show("未確認保存成功：請檢查本機 receiver 後重試。不要把下載完成當成保存完成。");
        } finally {
          button.disabled = false;
        }
      });
    });
  }
}

// Material transfer is explicit and separate from the session observation.
const materialButton = document.createElement("button");
materialButton.textContent = "傳送此活動的 PDF 清單";
output.before(materialButton);
materialButton.addEventListener("click", () => {
  downloads.replaceChildren();
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
        offerDownloads(tab.id, response.materials);
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
