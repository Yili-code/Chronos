// Explicit user-facing handoff. It only targets the loopback Chronos process.

const LOCAL_ENDPOINT = "http://127.0.0.1:8765/v1/browser-observation";

const output = document.getElementById("output");
const downloads = document.createElement("div");
output.before(downloads);

function downloadFailure(download) {
  if (download.status === "reauth_required") return "請重新登入 TronClass，再重新取得清單。";
  const reasons = {
    not_in_visible_catalog: "目前頁面的清單已不包含此附件；請重新傳送清單。",
    download_rejected: "伺服器未回傳可下載的內容。",
    size_limit: "附件超過目前 12 MiB 的限制。",
    invalid_pdf_envelope: "取得的內容未通過 PDF 格式檢查，可能是登入頁或其他內容。",
    download_unavailable: "網路、瀏覽器限制或資料讀取失敗；尚不能判定具體原因。",
    redirect_blocked: "伺服器要求重新導向；程式未跟隨目的地。需確認下載流程，不代表密碼錯誤。",
    download_timeout: "下載超過 30 秒，已中止。",
  };
  return "附件尚未保存。" + (reasons[download.reason] || "原因未辨識，請重新取得清單。");
}

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
          if (download.reason === "redirect_blocked") {
            show("改由 Chrome 原生下載；請保持視窗開啟，尚未確認 Chronos 保存成功。");
            const token = crypto.randomUUID().replaceAll("-", "");
            const native = await ChronosNative.nativeDownload(material.source_id, chrome.downloads, chrome.runtime.id, token);
            const result = await fetch("http://127.0.0.1:8765/v1/browser-native-pdf", {
              method:"POST", headers:{"Content-Type":"application/json","X-Chronos-Bridge":"1"},
              body:JSON.stringify({course_id:material.course_id,source_id:material.source_id,...native}),
            });
            if (!result.ok) throw new Error("native persistence rejected");
            const receipt = await result.json();
            if (receipt.status !== "persisted" || receipt.source_id !== material.source_id) throw new Error("invalid receipt");
            show({local_status:"persisted",filename:material.filename,byte_count:receipt.byte_count});
            return;
          }
          if (download.status !== "downloaded") {
            show(downloadFailure(download));
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
materialButton.textContent = "傳送目前教材頁／活動的 PDF 清單";
output.before(materialButton);

const assignmentButton = document.createElement("button");
assignmentButton.textContent = "保存目前作業說明（僅本機）";
output.before(assignmentButton);
assignmentButton.addEventListener("click", () => {
  assignmentButton.disabled = true;
  chrome.tabs.query({active:true, currentWindow:true}, ([tab]) => {
    if (!tab?.id) {
      show("找不到目前分頁。");
      assignmentButton.disabled = false;
      return;
    }
    chrome.tabs.sendMessage(tab.id, {type:"chronos.observe_assignment"}, async (payload) => {
      try {
        if (chrome.runtime.lastError || payload?.status !== "observed") {
          show("尚未取得可辨識的作業說明。請開啟作業內容頁，並確認擴充功能已重新載入。");
          return;
        }
        const response = await fetch("http://127.0.0.1:8765/v1/browser-assignment", {
          method:"POST", headers:{"Content-Type":"application/json","X-Chronos-Bridge":"1"},
          body:JSON.stringify(payload)
        });
        if (!response.ok) throw new Error("handoff rejected");
        const receipt = await response.json();
        if (receipt.saved !== true) throw new Error("not saved");
        show("作業觀察已保存至本機；尚未建立雲端代辦、生成草稿或提交作業。");
      } catch {
        show("未確認作業保存成功，請檢查本機接收程式。");
      } finally {
        assignmentButton.disabled = false;
      }
    });
  });
});
const announcementButton = document.createElement("button");
announcementButton.textContent = "保存目前公告（僅本機）";
output.before(announcementButton);
announcementButton.addEventListener("click", () => {
  announcementButton.disabled = true;
  chrome.tabs.query({active:true, currentWindow:true}, ([tab]) => {
    if (!tab?.id) {
      show("找不到目前分頁。");
      announcementButton.disabled = false;
      return;
    }
    chrome.tabs.sendMessage(tab.id, {type:"chronos.observe_announcements"}, async payload => {
      try {
        if (chrome.runtime.lastError || payload?.status !== "observed_partial") {
          show("尚未取得公告。請開啟課程公告頁並等待載入；這不代表沒有公告。");
          return;
        }
        const response = await fetch("http://127.0.0.1:8765/v1/browser-announcements", {
          method:"POST", headers:{"Content-Type":"application/json","X-Chronos-Bridge":"1"},
          body:JSON.stringify(payload)
        });
        if (!response.ok) throw new Error("handoff rejected");
        const receipt = await response.json();
        if (receipt.status !== "observed_partial" || !Number.isInteger(receipt.inserted) || receipt.inserted < 0)
          throw new Error("invalid receipt");
        show(`公告已保存至本機：新增 ${receipt.inserted} 個內容版本。只涵蓋目前載入的公告，連結已移除；尚未傳到雲端或 Telegram。`);
      } catch {
        show("未確認公告保存成功，請檢查本機接收程式。");
      } finally {
        announcementButton.disabled = false;
      }
    });
  });
});
materialButton.addEventListener("click", () => {
  downloads.replaceChildren();
  materialButton.disabled = true;
  chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
    if (!tab?.id) {
      show("No active tab.");
      materialButton.disabled = false;
      return;
    }
    const receive = async (response) => {
      if (chrome.runtime.lastError || !response) {
        show("請重新載入擴充功能與 TronClass 活動頁，再試一次。");
        materialButton.disabled = false;
        return;
      }
      try {
        const snapshots = response.snapshots || [response];
        if (!Array.isArray(snapshots) || snapshots.length > 100) throw new Error("invalid catalog");
        const accepted = [];
        let acceptedActivities = 0;
        let unavailableActivities = 0;
        for (const snapshot of snapshots) {
          if (snapshot.status !== "observed" || !snapshot.materials?.length) {
            unavailableActivities++;
            continue;
          }
          const result = await fetch("http://127.0.0.1:8765/v1/browser-materials", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Chronos-Bridge": "1" },
          body: JSON.stringify({status:snapshot.status, materials:snapshot.materials}),
        });
        if (!result.ok) throw new Error("metadata rejected");
          accepted.push(...snapshot.materials);
          acceptedActivities++;
        }
        offerDownloads(tab.id, accepted);
        show({ local_status: acceptedActivities ? "accepted_partial_catalog" : "unknown",
          accepted_activities: acceptedActivities, unavailable_activities: unavailableActivities,
          material_count: accepted.length, complete_course: false });
      } catch {
        show("PDF 清單未完整交付：先前項目可能已保存。請確認本機 Chronos receiver 後重試；尚未下載 PDF。");
      } finally {
        materialButton.disabled = false;
      }
    };
    chrome.tabs.sendMessage(tab.id, { type: "chronos.list_visible_materials" }, response => {
      if (!chrome.runtime.lastError && response?.status === "unsupported_page") {
        chrome.tabs.sendMessage(tab.id, { type: "chronos.list_course_materials" }, receive);
      } else receive(response);
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
