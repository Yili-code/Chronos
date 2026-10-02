// Explicit user-facing handoff. It only targets the loopback Chronos process.

const LOCAL_ENDPOINT = "http://127.0.0.1:8765/v1/browser-observation";

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
