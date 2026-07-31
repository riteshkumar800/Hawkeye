// IG DOM Watcher - learning demo
// Observing a single-page app (SPA) from a content script.
//
//   Alt+click a comment   save it
//   Alt+S on a profile    save handle / name / bio
//   Alt+N in a modal      save top 5 following/followers
//   Alt+A anywhere        add a custom note/observation about the user
//   Alt+R                 report: profiles + the comments saved from them
//   Alt+L                 raw tables
//   Alt+E                 export CSVs
//   Alt+K                 clear everything
//   Alt+D                 diagnose why comments aren't being found

console.log("[watcher] content script loaded on", location.href);

/* ------------------------------------------------------------------
   PART 1 - Route changes
------------------------------------------------------------------ */

let lastUrl = "";

function checkUrl() {
  if (location.href === lastUrl) return false;
  lastUrl = location.href;
  console.log("[watcher] route ->", location.pathname, "| type:", getPageType());
  return true;
}

window.addEventListener("popstate", checkUrl);
setInterval(checkUrl, 400);

/* ------------------------------------------------------------------
   PART 2 - Classify the page from its URL
------------------------------------------------------------------ */

const RESERVED = new Set([
  "explore", "reels", "reel", "p", "stories", "direct", "accounts",
  "challenge", "settings", "legal", "about", "developer", "tv", "s",
  "your_activity", "emails", "session", "oauth", "graphql", "api"
]);

function getPageType() {
  const seg = location.pathname.split("/").filter(Boolean);
  if (seg.length === 0) return "feed";

  const [first, second] = seg;
  if (first === "reels" || first === "reel") return "reel";
  if (first === "p") return "post";
  if (first === "stories") return "story";
  if (first === "explore") return "explore";
  if (first === "direct") return "direct";
  if (RESERVED.has(first)) return "other";

  return second ? "profile:" + second : "profile";
}

/* ------------------------------------------------------------------
   PART 3 - Watch the DOM
------------------------------------------------------------------ */

let debounceTimer;
let lastSignature = "";

new MutationObserver(() => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(scanPage, 400);
}).observe(document.body, { childList: true, subtree: true });

function scanRoot() {
  return document.querySelector('div[role="dialog"]') || document;
}

function scanPage() {
  checkUrl();
  buildPanel();          

  const type = getPageType();
  const timestamps = scanRoot().querySelectorAll("time[datetime]").length;

  const signature = `${type}|${timestamps}`;
  if (signature === lastSignature) return;
  lastSignature = signature;

  console.log(`[watcher] scan | page=${type} | <time> found=${timestamps}`);

  if (type === "post" || type === "reel") {
    try {
      const comments = extractComments();
      if (comments.length) console.table(comments);
    } catch (err) {
      console.error("[watcher] extractComments failed:", err);
    }
  }
}

/* ------------------------------------------------------------------
   PART 4 - Finding and parsing comments
------------------------------------------------------------------ */

const NOISE = /^(Reply|Like|Liked|Liked by|See Translation|Verified|Edited|Translate|Follow|Following|View all \d+ replies|View replies \(\d+\)|Hide replies|more|Show more|Show less|and [\d.,]+[kmb]? others?|[\d.,]+[kmb]? likes?|[\d.,]+[kmb]? repl(y|ies)|[•·—-]+)$/i;

const HAS_CONTENT = /[\p{L}\p{N}\p{Emoji_Presentation}]/u;

function usernameFromHref(href) {
  if (!href) return null;
  const seg = href.split("?")[0].split("/").filter(Boolean);
  if (seg.length !== 1) return null;
  const name = seg[0];
  if (RESERVED.has(name)) return null;
  if (!/^[a-zA-Z0-9._]{1,30}$/.test(name)) return null;
  return name;
}

function commentText(box, username) {
  const parts = [];
  const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);

  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    if (!parent) continue;

    const skip = parent.closest("time, svg");
    if (skip && box.contains(skip)) continue;

    const link = parent.closest('a[href^="/"]');
    if (link && box.contains(link) &&
        usernameFromHref(link.getAttribute("href")) === username) continue;

    const t = node.textContent.trim();
    if (t && !NOISE.test(t)) parts.push(t);
  }

  return parts
    .join(" ")
    .replace(/\s+/g, " ")
    .replace(/^([\d.,]+\s*[kmb]?\s*(likes?|comments?)\s*)+/i, "")
    .trim();
}

function findComment(timeEl) {
  let node = timeEl.parentElement;

  for (let depth = 0; depth < 10 && node && node !== document.body; depth++) {
    if (!node.querySelector('a[href*="/liked_by/"]')) {
      const username = [...node.querySelectorAll('a[href^="/"]')]
        .map(a => usernameFromHref(a.getAttribute("href")))
        .find(Boolean);

      if (username) {
        const text = commentText(node, username);
        if (text && HAS_CONTENT.test(text)) {
          return { box: node, username, text, posted: timeEl.getAttribute("datetime"), depth };
        }
      }
    }
    node = node.parentElement;
  }
  return null;
}

function extractComments() {
  const byKey = new Map();

  for (const timeEl of scanRoot().querySelectorAll("time[datetime]")) {
    const hit = findComment(timeEl);
    if (!hit) continue;

    const record = { username: hit.username, posted: hit.posted, text: hit.text };
    const key = `${record.username}|${record.posted}`;
    const prev = byKey.get(key);
    if (!prev || record.text.length > prev.text.length) byKey.set(key, record);
  }

  return [...byKey.values()];
}

function diagnose() {
  const times = [...scanRoot().querySelectorAll("time[datetime]")];
  console.group(`[watcher] diagnose: ${times.length} timestamps on a "${getPageType()}" page`);
  console.log("scan root:", scanRoot() === document ? "document" : 'div[role="dialog"]');

  times.slice(0, 5).forEach((timeEl, i) => {
    console.group(`timestamp ${i} (${timeEl.getAttribute("datetime")})`);
    let node = timeEl.parentElement;
    for (let d = 0; d < 6 && node && node !== document.body; d++) {
      const links = [...node.querySelectorAll('a[href^="/"]')].map(a => a.getAttribute("href"));
      const username = links.map(usernameFromHref).find(Boolean);
      console.log(`depth ${d}:`, {
        username: username || "(none)",
        hrefs: links.slice(0, 3),
        text: username ? commentText(node, username).slice(0, 60) : "",
        likedByLink: !!node.querySelector('a[href*="/liked_by/"]')
      });
      node = node.parentElement;
    }
    console.groupEnd();
  });

  console.groupEnd();
  toast(`Diagnosed ${times.length} timestamps - see console`);
}

/* ------------------------------------------------------------------
   PART 5 - Manual save (Comments)
------------------------------------------------------------------ */

const COLUMNS = ["username", "posted", "text", "url", "savedAt"];
const PROFILE_COLUMNS = ["username", "fullName", "bio", "url", "savedAt"];
const SERVER = "http://localhost:8765";
const commentKey = r => `${r.username}|${r.posted}|${r.text}`;

async function push(path, record) {
  try {
    const res = await fetch(SERVER + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record)
    });
    return res.ok;
  } catch {
    return false;                 
  }
}

async function syncFromServer() {
  if (!chrome.runtime?.id) return;

  let state;
  try {
    state = await fetch(SERVER + "/api/state").then(r => r.json());
  } catch {
    return;                       
  }

  const liveComments = new Set(state.comments || []);
  const liveProfiles = new Set(state.profiles || []);
  
  try {
    const { saved = [], profiles = [] } = await chrome.storage.local.get(["saved", "profiles"]);

    const keptComments = saved.filter(r => !r.synced || liveComments.has(commentKey(r)));
    const keptProfiles = profiles.filter(p => !p.synced || liveProfiles.has(p.username));

    if (keptComments.length !== saved.length || keptProfiles.length !== profiles.length) {
      await chrome.storage.local.set({ saved: keptComments, profiles: keptProfiles });
      refreshCount();
    }
  } catch (err) {
    if (!err.message?.includes("Extension context invalidated")) {
      console.error(err);
    }
  }
}

setInterval(syncFromServer, 4000);

function toast(message) {
  const el = document.createElement("div");
  el.textContent = message;
  el.style.cssText =
    "position:fixed;bottom:24px;left:50%;transform:translateX(-50%);" +
    "z-index:2147483647;background:#1a1a1a;color:#fff;border:1px solid #555;" +
    "padding:10px 16px;border-radius:8px;font:13px system-ui;" +
    "pointer-events:none;opacity:.96;max-width:70vw;text-align:center";
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2000);
}

function commentFromClick(target) {
  let node = target;
  for (let i = 0; i < 12 && node && node !== document.body; i++) {
    const timeEl = node.querySelector?.("time[datetime]");
    if (timeEl) {
      const hit = findComment(timeEl);
      if (hit) return hit;
    }
    node = node.parentElement;
  }
  return null;
}

function extractPostContext() {
  const root = scanRoot();
  const header = root.querySelector("header") || root;

  const postAuthor = [...header.querySelectorAll('a[href^="/"]')]
    .map(a => usernameFromHref(a.getAttribute("href")))
    .find(Boolean) || "";

  let postCaption = "";
  if (postAuthor) {
    const theirs = extractComments()
      .filter(c => c.username === postAuthor)
      .sort((a, b) => new Date(a.posted) - new Date(b.posted));
    if (theirs.length) postCaption = theirs[0].text.slice(0, 1000);
  }

  return { postAuthor, postCaption, postUrl: location.href };
}

async function saveComment(hit) {
  const post = extractPostContext();

  const record = {
    username: hit.username,
    posted: hit.posted,
    text: hit.text,
    url: location.href,
    postAuthor: post.postAuthor,
    postCaption: post.postCaption,
    savedAt: new Date().toISOString()
  };

  const { saved = [] } = await chrome.storage.local.get("saved");
  if (saved.some(r => commentKey(r) === commentKey(record))) return toast("Already saved");

  record.synced = await push("/api/save/comment", record);

  saved.push(record);
  await chrome.storage.local.set({ saved });
  toast(record.synced
    ? `Saved #${saved.length} - @${record.username} → dashboard`
    : `Saved #${saved.length} - @${record.username} (dashboard offline)`);
  refreshCount();
}

document.addEventListener("click", e => {
  if (!e.altKey) return;
  const hit = commentFromClick(e.target);
  if (!hit) return toast("No comment found there - try clicking the comment text");
  e.preventDefault();
  e.stopPropagation();
  saveComment(hit);
}, true);

/* ------------------------------------------------------------------
   PART 6 - Profile Snapshot
------------------------------------------------------------------ */

const HEADER_NOISE =
  /^(Follow|Following|Follow Back|Message|Edit profile|View archive|Contact|Email|Call|Directions|Requested|[\d.,]+[kmb]? (posts?|followers?|following)|following|more|Verified)$/i;

function extractProfile() {
  const username = location.pathname.split("/").filter(Boolean)[0];
  if (!username || RESERVED.has(username)) return null;

  const header = document.querySelector("header");
  if (!header) return null;

  const lines = header.innerText
    .split("\n")
    .map(s => s.trim())
    .filter(s => s && s !== username && !HEADER_NOISE.test(s));

  return {
    username,
    fullName: lines[0] || "",
    bio: lines.slice(1).join(" ").trim(),
    url: `${location.origin}/${username}/`,
    savedAt: new Date().toISOString()
  };
}

async function saveProfile() {
  if (!getPageType().startsWith("profile")) {
    return toast(`Not a profile page (this is "${getPageType()}")`);
  }

  const profile = extractProfile();
  if (!profile) return toast("Couldn't read this profile");

  profile.synced = await push("/api/save/profile", profile);

  const { profiles = [] } = await chrome.storage.local.get("profiles");
  const i = profiles.findIndex(p => p.username === profile.username);
  if (i >= 0) profiles[i] = profile;
  else profiles.push(profile);

  await chrome.storage.local.set({ profiles });
  toast(profile.synced
    ? `Saved profile @${profile.username} → dashboard`
    : `Saved profile @${profile.username} (dashboard offline)`);
  refreshCount();
}

/* ------------------------------------------------------------------
   PART 6.1 - Save Following/Followers Modal (Alt+N)
------------------------------------------------------------------ */

function extractVisibleFollowers() {
  const dialog = document.querySelector('div[role="dialog"]');
  if (!dialog) return null;

  const headerText = dialog.querySelector('h1, h2, div[role="heading"]')?.innerText || "";
  const listType = headerText.toLowerCase().includes("following") ? "following" : "followers";

  const links = [...dialog.querySelectorAll('a[href^="/"]')];
  const items = [];
  const seen = new Set();

  for (const a of links) {
    const handle = usernameFromHref(a.getAttribute("href"));
    if (!handle || seen.has(handle)) continue;
    seen.add(handle);

    const row = a.closest('div[role="button"], li, div');
    const svgList = row?.querySelectorAll('svg') || [];
    const verified = Array.from(svgList).some(svg => 
      svg.getAttribute('aria-label')?.includes('Verified') || 
      svg.querySelector('title')?.textContent?.includes('Verified')
    );

    const fullName = row?.innerText?.split("\n").find(t => t && t.trim() !== handle && !/follow/i.test(t)) || "";

    items.push({ username: handle, fullName: fullName.trim(), verified });
    if (items.length >= 5) break; 
  }

  const profileHandle = location.pathname.split("/").filter(Boolean)[0];

  return {
    profileUsername: profileHandle,
    listType,
    items,
    savedAt: new Date().toISOString()
  };
}

async function saveFollowing() {
  const data = extractVisibleFollowers();
  if (!data || !data.items.length) {
    return toast("Open a Following or Followers modal first!");
  }

  const ok = await push("/api/save/following", data);
  toast(ok 
    ? `Saved ${data.items.length} ${data.listType} for @${data.profileUsername} → dashboard` 
    : `Saved local (${data.listType}) (offline)`);
  refreshCount();
}

/* ------------------------------------------------------------------
   PART 6.5 - Custom Notes / Observations (Alt+A)
------------------------------------------------------------------ */

function getActiveUsername() {
  const segs = location.pathname.split("/").filter(Boolean);
  const first = segs[0];

  // 1. If viewing a story or highlight viewer container
  if (first === "stories") {
    if (segs[1] && segs[1] !== "highlights") return segs[1];
    
    // Scan all anchor tags inside story headers or dialog sections to find the profile owner link
    const candidateLinks = [...document.querySelectorAll('section header a[href^="/"], div[role="dialog"] header a[href^="/"], header a[href^="/"], main a[href^="/"]')];
    for (const a of candidateLinks) {
      const href = a.getAttribute("href");
      const h = usernameFromHref(href);
      // Ensure we grab the profile link, avoiding audio tracks or asset links like /music/ or /explore/
      if (h && !href.includes("/music/") && !href.includes("/audio/")) {
        return h;
      }
    }
  }

  // 2. If on a standard profile page
  if (!RESERVED.has(first) && first) return first;

  // 3. Fallback: Post or general header author link
  const authorLink = document.querySelector('header a[href^="/"]');
  if (authorLink) return usernameFromHref(authorLink.getAttribute("href"));

  return null;
}

async function addNote() {
  const username = getActiveUsername();
  if (!username) return toast("Couldn't detect the user! Make sure you are on a profile or story.");

  const text = prompt(`Add a custom note/observation for @${username}:`);
  if (!text || !text.trim()) return;

  const record = {
    username: username,
    note: text.trim(),
    contextUrl: window.location.href, 
    savedAt: new Date().toISOString()
  };

  const ok = await push("/api/save/note", record);
  toast(ok ? `Note saved for @${username}` : `Dashboard offline - couldn't save note`);
}

/* ------------------------------------------------------------------
   PART 7 - Export / inspect
------------------------------------------------------------------ */

async function report() {
  const { saved = [], profiles = [] } = await chrome.storage.local.get(["saved", "profiles"]);

  if (!saved.length && !profiles.length) return toast("Nothing saved yet");

  for (const p of profiles) {
    const theirs = saved.filter(c => c.username === p.username);
    console.groupCollapsed(`@${p.username} - ${p.fullName} (${theirs.length} saved)`);
    console.log("bio:", p.bio);
    if (theirs.length) console.table(theirs.map(({ text, posted, url }) => ({ text, posted, url })));
    console.groupEnd();
  }

  const orphans = saved.filter(c => !profiles.some(p => p.username === c.username));
  if (orphans.length) console.log(`(${orphans.length} saved comments from profiles not snapshotted)`);

  toast(`Report: ${profiles.length} profiles, ${saved.length} comments`);
  return { profiles, saved };
}

function toCsv(rows, columns) {
  const esc = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return [columns.join(","), ...rows.map(r => columns.map(c => esc(r[c])).join(","))].join("\n");
}

function download(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const EXPORT_COLUMNS = [
  "username", "fullName", "bio", "profileUrl",
  "commentText", "commentPosted",
  "postAuthor", "postCaption", "postUrl",
  "savedAt"
];

const na = v => (v && String(v).trim() ? String(v) : "N/A");

function buildRows(saved, profiles) {
  const byUser = new Map(profiles.map(p => [p.username, p]));
  const rows = [];

  for (const c of saved) {
    const p = byUser.get(c.username);
    rows.push({
      username: c.username, fullName: na(p?.fullName), bio: na(p?.bio),
      profileUrl: p?.url ?? `https://www.instagram.com/${c.username}/`,
      commentText: na(c.text), commentPosted: na(c.posted),
      postAuthor: na(c.postAuthor), postCaption: na(c.postCaption),
      postUrl: na(c.url), savedAt: c.savedAt
    });
  }

  for (const p of profiles) {
    if (saved.some(c => c.username === p.username)) continue;
    rows.push({
      username: p.username, fullName: na(p.fullName), bio: na(p.bio),
      profileUrl: p.url, commentText: "N/A", commentPosted: "N/A",
      postAuthor: "N/A", postCaption: "N/A", postUrl: "N/A", savedAt: p.savedAt
    });
  }
  return rows.sort((a, b) => a.postUrl.localeCompare(b.postUrl) || a.username.localeCompare(b.username));
}

async function exportCsv() {
  const { saved = [], profiles = [] } = await chrome.storage.local.get(["saved", "profiles"]);
  if (!saved.length && !profiles.length) return toast("Nothing saved yet");
  const rows = buildRows(saved, profiles);
  download("instagram-data.csv", toCsv(rows, EXPORT_COLUMNS));
  toast(`Exported ${rows.length} rows`);
}

async function listSaved() {
  const { saved = [], profiles = [] } = await chrome.storage.local.get(["saved", "profiles"]);
  console.log("--- saved comments ---"); console.table(saved);
  console.log("--- saved profiles ---"); console.table(profiles);
  toast(`${saved.length} comments, ${profiles.length} profiles`);
  return { saved, profiles };
}

async function clearSaved() {
  await chrome.storage.local.set({ saved: [], profiles: [] });
  toast("Cleared"); refreshCount();
}

/* ------------------------------------------------------------------
   PART 8 - On-screen controls
------------------------------------------------------------------ */

const PANEL_ID = "__watcher_panel";

function button(label, handler) {
  const b = document.createElement("button");
  b.textContent = label;
  b.style.cssText =
    "background:#2d2d2d;color:#fff;border:1px solid #555;border-radius:6px;" +
    "padding:4px 9px;cursor:pointer;font:12px system-ui;white-space:nowrap";
  b.addEventListener("click", e => {
    e.preventDefault();
    e.stopPropagation();
    handler();
  }, true);
  return b;
}

function buildPanel() {
  if (document.getElementById(PANEL_ID)) return;

  const panel = document.createElement("div");
  panel.id = PANEL_ID;
  panel.style.cssText =
    "position:fixed;left:16px;bottom:16px;z-index:2147483646;" +
    "background:#1a1a1a;color:#fff;border:1px solid #555;border-radius:10px;" +
    "font:12px system-ui;padding:8px 10px;display:flex;gap:6px;align-items:center;" +
    "box-shadow:0 4px 16px rgba(0,0,0,.45)";

  const count = document.createElement("span");
  count.id = PANEL_ID + "_count";
  count.style.cssText = "opacity:.75;margin-right:2px";
  panel.appendChild(count);

  panel.appendChild(button("Save profile", saveProfile));
  panel.appendChild(button("Save network", saveFollowing));
  panel.appendChild(button("Add note", addNote)); 
  panel.appendChild(button("Report", report));
  panel.appendChild(button("CSV", exportCsv));
  panel.appendChild(button("Clear", clearSaved));

  document.body.appendChild(panel);
  refreshCount();
}

async function refreshCount() {
  const el = document.getElementById(PANEL_ID + "_count");
  if (!el) return;
  const { saved = [], profiles = [] } = await chrome.storage.local.get(["saved", "profiles"]);
  el.textContent = `${saved.length} comments · ${profiles.length} profiles`;
}

window.addEventListener("keydown", e => {
  if (!e.altKey) return;
  const actions = {
    KeyS: saveProfile, KeyN: saveFollowing, KeyA: addNote, 
    KeyR: report, KeyL: listSaved, KeyE: exportCsv, KeyD: diagnose, KeyK: clearSaved
  };
  const action = actions[e.code];
  if (!action) return;
  e.preventDefault();
  e.stopPropagation();
  action();
}, true);

window.__watcher = {
  extractComments, extractProfile, findComment, diagnose, getPageType, 
  saveProfile, saveFollowing, addNote, listSaved, report, exportCsv, clearSaved
};

checkUrl();
scanPage();