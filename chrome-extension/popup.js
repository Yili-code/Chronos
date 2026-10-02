// Read-only user-facing trigger. No network calls and no form actions.

const output = document.getElementById("output");

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
    show(response || "No observation returned.");
  });
});
