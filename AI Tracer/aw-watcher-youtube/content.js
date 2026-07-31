// AW YouTube Watcher - tracks what type of content you're viewing

let lastUrl = "";
let lastEventStart = Date.now();
let lastEventData = null;
let navigationCounter = 0;

function classifyYouTubeUrl(url) {
  const u = new URL(url);
  const path = u.pathname;
  const params = u.searchParams;

  if (path === "/watch" && params.get("v")) {
    return { type: "video", target: params.get("v") };
  }
  if (path.startsWith("/shorts/")) {
    return { type: "short", target: path.split("/")[2] || "unknown" };
  }
  if (path.startsWith("/@") || path.startsWith("/channel/") || path.startsWith("/c/")) {
    return { type: "channel", target: path.replace(/^\//, "") };
  }
  if (path === "/results") {
    return { type: "search", target: params.get("search_query") || "unknown" };
  }
  if (path === "/" || path === "") {
    return { type: "home", target: "home" };
  }
  return { type: "other", target: path };
}

function getVideoTitle() {
  const title = document.title.replace(/ - YouTube$/, "").trim();
  return title.slice(0, 200);
}

function getChannelName() {
  const channelEl = document.querySelector("ytd-channel-name a, #channel-name a, #text.ytd-channel-name");
  return channelEl ? channelEl.innerText.trim().slice(0, 100) : "";
}

function sendEvent(eventData, durationSeconds) {
  console.log("[YT Watcher content] sendEvent called, duration:", durationSeconds);
  const payload = {
    timestamp: new Date(lastEventStart).toISOString(),
    duration: durationSeconds,
    data: eventData
  };

  if (!chrome.runtime || !chrome.runtime.id) {
    console.log("AW YouTube Watcher: extension context stale (reload happened), skipping this event. Refresh this tab to fix.");
    return;
  }

  try {
    chrome.runtime.sendMessage({ action: "sendToAW", payload }, (response) => {
      if (chrome.runtime.lastError) {
        console.log("AW YouTube Watcher: message error (likely stale context)", chrome.runtime.lastError.message);
      } else if (response && !response.success) {
        console.log("AW YouTube Watcher: send failed", response.error);
      }
    });
  } catch (err) {
    console.log("AW YouTube Watcher: extension context invalidated, skipping event. Refresh this tab to fix.", err.message);
  }
}

function checkUrlChange() {
  const currentUrl = window.location.href;
  if (currentUrl !== lastUrl) {
    if (lastEventData) {
      const durationSeconds = (Date.now() - lastEventStart) / 1000;
      sendEvent(lastEventData, durationSeconds);
    }

    const classified = classifyYouTubeUrl(currentUrl);
    const thisNavigationId = ++navigationCounter;
    const eventStartTime = Date.now();

    lastEventData = {
      type: classified.type,
      target: classified.target,
      url: currentUrl,
      title: "",
      channel: ""
    };
    lastEventStart = eventStartTime;
    lastUrl = currentUrl;

    setTimeout(() => {
      if (thisNavigationId === navigationCounter) {
        lastEventData.title = getVideoTitle();
        lastEventData.channel = getChannelName();
        console.log("AW YouTube Watcher tracking:", lastEventData);
      }
    }, 800);
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
