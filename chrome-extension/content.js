// Chronos read-only observation contract.
// This script never reads cookies, storage, passwords, headers, or form values.

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "chronos.observe_read_only") {
    return false;
  }
  sendResponse(globalThis.ChronosObservation.observation());
  return false;
});
