const INGEST_URL = "http://localhost:5050/api/ingest/instagram";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === "sendToAW") {
    fetch(INGEST_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload)
    })
      .then(() => sendResponse({ success: true }))
      .catch(err => {
        console.log("Instagram Watcher (background): failed to send", err);
        sendResponse({ success: false, error: err.message });
      });
    return true;
  }
});
