// Chronos read-only observation contract.
// This script never reads cookies, storage, passwords, headers, or form values.

const MAX_VISIBLE_TEXT = 10000;

function safeUrl() {
  const current = new URL(window.location.href);
  current.search = "";
  current.hash = "";
  return current.toString();
}

function observation() {
  return {
    url: safeUrl(),
    visible_text: (document.body?.innerText || "").slice(0, MAX_VISIBLE_TEXT),
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "chronos.observe_read_only") {
    return false;
  }
  sendResponse(observation());
  return false;
});
