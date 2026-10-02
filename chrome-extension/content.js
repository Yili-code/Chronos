// Chronos read-only observation contract.
// This script never reads cookies, storage, passwords, headers, or form values.

// Non-visual marker used by the smoke test to prove the content script loaded.
document.documentElement?.setAttribute("data-chronos-read-only-bridge", "active");

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "chronos.download_visible_pdf") {
    const catalog = globalThis.ChronosMaterials.extractMaterials(document, location.href);
    globalThis.ChronosPdfDownload.downloadVisiblePdf(message.source_id, catalog).then(sendResponse);
    return true;
  }
  if (message?.type === "chronos.list_visible_materials") {
    sendResponse(globalThis.ChronosMaterials.extractMaterials(document, location.href));
    return false;
  }
  if (message?.type !== "chronos.observe_read_only") {
    return false;
  }
  sendResponse(globalThis.ChronosObservation.observation());
  return false;
});

// Page-facing read-only probe for the local connector smoke test. It exposes
// only data already visible in the active page and never touches secrets.
window.addEventListener("message", (event) => {
  if (event.source !== window || event.data?.type !== "chronos.observe_read_only_request") {
    return;
  }

  window.postMessage(
    {
      type: "chronos.observe_read_only_response",
      request_id: event.data.request_id ?? null,
      observation: globalThis.ChronosObservation.observation(),
    },
    "*",
  );
});
