// AW Instagram Watcher - tracks what type of content you're viewing

let lastUrl = "";
let lastEventStart = Date.now();
let lastEventData = null;

function classifyInstagramUrl(url) {
  const path = new URL(url).pathname;

  if (path.startsWith("/reel/") || path.startsWith("/reels/")) {
    return { type: "reel", target: path.split("/")[2] || "unknown" };
  }
  if (path.startsWith("/stories/")) {
    const parts = path.split("/");
    return { type: "story", target: parts[2] || "unknown" };
  }
  if (path.startsWith("/p/")) {
    return { type: "post", target: path.split("/")[2] || "unknown" };
  }
  if (path.startsWith("/explore")) {
    return { type: "explore", target: "explore" };
  }
  if (path.startsWith("/direct")) {
    return { type: "dm", target: "direct" };
  }
  if (path === "/" || path === "") {
    return { type: "feed", target: "home" };
  }
  const username = path.replace(/\//g, "");
  if (username) {
    return { type: "profile", target: username };
  }
  return { type: "unknown", target: "unknown" };
}

function getCaptionText() {
  const selectors = [
    "h1",
    "span[dir='auto']",
    "article span",
    "div[role='button'] + div span"
  ];
  for (const sel of selectors) {
    const elements = document.querySelectorAll(sel);
    for (const el of elements) {
      const text = el.innerText ? el.innerText.trim() : "";
      if (text && text.length > 5 && text.length < 300) {
        return text.slice(0, 200);
      }
    }
  }
  return "";
}

function sendEvent(eventData, durationSeconds) {
  const payload = {
    timestamp: new Date(lastEventStart).toISOString(),
    duration: durationSeconds,
    data: eventData
  };

  if (!chrome.runtime || !chrome.runtime.id) {
    console.log("AW Instagram Watcher: extension context stale (reload happened), skipping this event. Refresh this tab to fix.");
    return;
  }

  try {
    chrome.runtime.sendMessage({ action: "sendToAW", payload }, (response) => {
      if (chrome.runtime.lastError) {
        console.log("AW Instagram Watcher: message error (likely stale context)", chrome.runtime.lastError.message);
      } else if (response && !response.success) {
        console.log("AW Instagram Watcher: send failed", response.error);
      }
    });
  } catch (err) {
    console.log("AW Instagram Watcher: extension context invalidated, skipping event. Refresh this tab to fix.", err.message);
  }
}

function checkUrlChange() {
  const currentUrl = window.location.href;
  if (currentUrl !== lastUrl) {
    if (lastEventData) {
      const durationSeconds = (Date.now() - lastEventStart) / 1000;
      sendEvent(lastEventData, durationSeconds);
    }
    const classified = classifyInstagramUrl(currentUrl);
    lastEventData = {
      type: classified.type,
      target: classified.target,
      url: currentUrl,
      caption_snippet: getCaptionText()
    };
    lastEventStart = Date.now();
    lastUrl = currentUrl;
    console.log("AW Instagram Watcher tracking:", lastEventData);
  }
}

const observer = new MutationObserver(checkUrlChange);
observer.observe(document.body, { childList: true, subtree: true });

checkUrlChange();

setInterval(() => {
  if (lastEventData) {
    const durationSeconds = (Date.now() - lastEventStart) / 1000;
    if (durationSeconds > 15) {
      sendEvent(lastEventData, durationSeconds);
      lastEventStart = Date.now();
    }
  }
}, 15000);
