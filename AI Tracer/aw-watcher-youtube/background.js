const INGEST_URL = "http://localhost:5050/api/ingest/youtube";

console.log("[YT Watcher] service worker started");

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log("[YT Watcher] received message:", message.action, message.payload);

  if (message.action === "sendToAW") {
    fetch(INGEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload)
    })
      .then(res => {
        console.log("[YT Watcher] server responded:", res.status);
        return res.json();
      })
      .then(json => {
        console.log("[YT Watcher] server body:", json);
        sendResponse({ success: true });
      })
      .catch(err => {
        console.error("[YT Watcher] fetch failed:", err);
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }
});
