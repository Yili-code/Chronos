// Pure, read-only page observation helpers.

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

globalThis.ChronosObservation = Object.freeze({ safeUrl, observation });
