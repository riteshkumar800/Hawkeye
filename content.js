// IG DOM Watcher - learning demo
// Observing a single-page app (SPA) from a content script.
//
//   Alt+click a comment   save it
//   Alt+S on a profile    save handle / name / bio
//   Alt+R                 report: profiles + the comments saved from them
//   Alt+L                 raw tables
//   Alt+E                 export CSVs
//   Alt+K                 clear everything
//   Alt+D                 diagnose why comments aren't being found

console.log("[watcher] content script loaded on", location.href);

/* ------------------------------------------------------------------
   PART 1 - Route changes

   Content scripts run in an "isolated world": own JS globals, shared
   DOM. So patching history.pushState patches YOUR copy, not
   Instagram's, and never fires. We poll location.href instead - a
   string compare costs nothing and works however IG routes internally.
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

   Instagram uses virtualised lists - nodes churn on every scroll
   frame - so the observer is debounced and logs only on change.
------------------------------------------------------------------ */

let debounceTimer;
let lastSignature = "";

new MutationObserver(() => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(scanPage, 400);
}).observe(document.body, { childList: true, subtree: true });

// A post can be a full page OR a dialog layered over the feed. When
// it's a dialog, scan only inside it so the feed behind doesn't leak in.
function scanRoot() {
  return document.querySelector('div[role="dialog"]') || document;
}

function scanPage() {
  checkUrl();
  buildPanel();          // Instagram re-renders a lot; re-add if removed

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
      else console.log("[watcher] no comments parsed (timestamps seen:", timestamps, ") - press Alt+D to diagnose");
    } catch (err) {
      console.error("[watcher] extractComments failed:", err);
    }
  }
}

/* ------------------------------------------------------------------
   PART 4 - Finding and parsing comments

   Every comment holds exactly one <time datetime> and one profile
   link, both real semantic elements. Start at each <time> and climb.

   The climb is SELF-VALIDATING: rather than guessing the shape of the
   container, each level is tested by actually trying to parse it. The
   first level that yields both a username and non-empty text wins.
   That's what makes this work on the post-as-dialog layout and the
   standalone post page, which nest comments differently.
------------------------------------------------------------------ */

const NOISE = /^(Reply|Like|Liked|Liked by|See Translation|Verified|Edited|Translate|Follow|Following|View all \d+ replies|View replies \(\d+\)|Hide replies|more|Show more|Show less|and [\d.,]+[kmb]? others?|[\d.,]+[kmb]? likes?|[\d.,]+[kmb]? repl(y|ies)|[•·—-]+)$/i;

const HAS_CONTENT = /[\p{L}\p{N}\p{Emoji_Presentation}]/u;

// Instagram usernames: letters, digits, dot, underscore, max 30.
// This is what separates "/andrea_flatlays/" from "/p/DbQztW0jEju/".
function usernameFromHref(href) {
  if (!href) return null;
  const seg = href.split("?")[0].split("/").filter(Boolean);
  if (seg.length !== 1) return null;
  const name = seg[0];
  if (RESERVED.has(name)) return null;
  if (!/^[a-zA-Z0-9._]{1,30}$/.test(name)) return null;
  return name;
}

/* Read text by walking TEXT NODES, not innerText. innerText merges
   inline siblings onto one line, gluing the timestamp and Reply button
   to the comment as "8 mReply", which no line filter can separate. */
function commentText(box, username) {
  const parts = [];
  const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);

  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    if (!parent) continue;

    // Skip timestamps and icons only. NOT [role="button"] - Instagram
    // wraps the whole comment row in one, so excluding it wipes out the
    // body. "Reply"/"Like"/"4 likes" are handled by NOISE instead.
    //
    // The box.contains() guard matters: closest() walks to <html>, so
    // without it an ancestor outside the comment can match and silently
    // filter everything away.
    const skip = parent.closest("time, svg");
    if (skip && box.contains(skip)) continue;

    // the author's own name link - an @mention survives, since its
    // href points at a different username
    const link = parent.closest('a[href^="/"]');
    if (link && box.contains(link) &&
        usernameFromHref(link.getAttribute("href")) === username) continue;

    const t = node.textContent.trim();
    if (t && !NOISE.test(t)) parts.push(t);
  }

  // NOISE is tested per text node, but Instagram splits some labels
  // across nodes ("7,067" + "likes"), so they're only recognisable
  // after joining. Strip those leading counters here.
  return parts
    .join(" ")
    .replace(/\s+/g, " ")
    .replace(/^([\d.,]+\s*[kmb]?\s*(likes?|comments?)\s*)+/i, "")
    .trim();
}

function findComment(timeEl) {
  let node = timeEl.parentElement;

  for (let depth = 0; depth < 10 && node && node !== document.body; depth++) {
    // "Liked by X and 65k others" is not a comment. It always links to
    // /p/SHORTCODE/liked_by/, which nothing else does.
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
  // Keyed by username+timestamp, NOT text: Instagram renders the
  // caption twice and the copies differ by a trailing "more" toggle.
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

/* Explains why nothing was found - run with Alt+D */
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
   PART 5 - Manual save

   A FLAT LOG: one row per saved comment, in save order. Nothing is
   captured automatically and nothing is grouped by account in storage.
------------------------------------------------------------------ */

const COLUMNS = ["username", "posted", "text", "url", "savedAt"];
const PROFILE_COLUMNS = ["username", "fullName", "bio", "url", "savedAt"];

/* ------------------------------------------------------------------
   Dashboard sync

   dashboard.py is the source of truth. We still write to
   chrome.storage first so saving works with the server down, then push.
   Records that made it to the server are marked synced, and anything
   synced that later disappears server-side was deleted from a card -
   so we drop it locally too.
------------------------------------------------------------------ */

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
    return false;                 // server not running - stays local
  }
}

async function syncFromServer() {
  let state;
  try {
    state = await fetch(SERVER + "/api/state").then(r => r.json());
  } catch {
    return;                       // offline: never prune on a failed fetch
  }

  const liveComments = new Set(state.comments || []);
  const liveProfiles = new Set(state.profiles || []);
  const { saved = [], profiles = [] } = await chrome.storage.local.get(["saved", "profiles"]);

  const keptComments = saved.filter(r => !r.synced || liveComments.has(commentKey(r)));
  const keptProfiles = profiles.filter(p => !p.synced || liveProfiles.has(p.username));

  if (keptComments.length !== saved.length || keptProfiles.length !== profiles.length) {
    await chrome.storage.local.set({ saved: keptComments, profiles: keptProfiles });
    refreshCount();
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

// From whatever was clicked, find the nearest enclosing <time>, then
// reuse the same self-validating climb the extractor uses.
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

/* What post is this comment on?

   The author is the first profile link in the post header. The caption
   is that author's EARLIEST entry in the comment list - captions are
   written when the post is created, so they always sort first. */
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
  const key = r => `${r.username}|${r.posted}|${r.text}`;
  if (saved.some(r => key(r) === key(record))) return toast("Already saved");

  record.synced = await push("/api/save/comment", record);

  saved.push(record);
  await chrome.storage.local.set({ saved });
  toast(record.synced
    ? `Saved #${saved.length} - @${record.username} → dashboard`
    : `Saved #${saved.length} - @${record.username} (dashboard offline)`);
  refreshCount();
}

// capture phase, so Instagram's own handlers don't swallow the click
document.addEventListener("click", e => {
  if (!e.altKey) return;
  const hit = commentFromClick(e.target);
  if (!hit) return toast("No comment found there - try clicking the comment text");
  e.preventDefault();
  e.stopPropagation();
  saveComment(hit);
}, true);

/* ------------------------------------------------------------------
   PART 6 - Profile snapshot (Alt+S)

   Handle, display name, bio. Manual trigger only. No stories, no
   follower/following lists, no scraping of the post grid.
------------------------------------------------------------------ */

const HEADER_NOISE =
  /^(Follow|Following|Follow Back|Message|Edit profile|View archive|Contact|Email|Call|Directions|Requested|[\d.,]+[kmb]? (posts?|followers?|following)|following|more|Verified)$/i;

function extractProfile() {
  const username = location.pathname.split("/").filter(Boolean)[0];
  if (!username || RESERVED.has(username)) return null;

  const header = document.querySelector("header");
  if (!header) return null;

  // The fragile part: there's no semantic marker separating display
  // name from bio - both are plain spans - so strip known chrome and
  // take what's left.
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

/* Join happens at report time from two independent flat lists -
   nothing accumulates into a per-person record in storage. */
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

/* ------------------------------------------------------------------
   PART 7 - Export / inspect
------------------------------------------------------------------ */

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

/* One flat sheet: a row per saved comment, with the author's profile
   fields joined on alongside. Profiles with no saved comments still get
   a row so their bio isn't lost. */
const EXPORT_COLUMNS = [
  "username", "fullName", "bio", "profileUrl",
  "commentText", "commentPosted",
  "postAuthor", "postCaption", "postUrl",
  "savedAt"
];

// empty cells read as mistakes in a spreadsheet; N/A reads as "known absent"
const na = v => (v && String(v).trim() ? String(v) : "N/A");

function buildRows(saved, profiles) {
  const byUser = new Map(profiles.map(p => [p.username, p]));
  const rows = [];

  for (const c of saved) {
    const p = byUser.get(c.username);
    rows.push({
      username: c.username,
      fullName: na(p?.fullName),
      bio: na(p?.bio),
      profileUrl: p?.url ?? `https://www.instagram.com/${c.username}/`,
      commentText: na(c.text),
      commentPosted: na(c.posted),
      postAuthor: na(c.postAuthor),
      postCaption: na(c.postCaption),
      postUrl: na(c.url),
      savedAt: c.savedAt
    });
  }

  // profiles you snapshotted but saved no comments from
  for (const p of profiles) {
    if (saved.some(c => c.username === p.username)) continue;
    rows.push({
      username: p.username,
      fullName: na(p.fullName),
      bio: na(p.bio),
      profileUrl: p.url,
      commentText: "N/A",
      commentPosted: "N/A",
      postAuthor: "N/A",
      postCaption: "N/A",
      postUrl: "N/A",
      savedAt: p.savedAt
    });
  }

  // group by post first, then by person - keeps one post's comments together
  return rows.sort((a, b) =>
    a.postUrl.localeCompare(b.postUrl) || a.username.localeCompare(b.username));
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
  console.log("--- saved comments ---");
  console.table(saved);
  console.log("--- saved profiles ---");
  console.table(profiles);
  toast(`${saved.length} comments, ${profiles.length} profiles`);
  return { saved, profiles };
}

async function clearSaved() {
  await chrome.storage.local.set({ saved: [], profiles: [] });
  toast("Cleared");
  refreshCount();
}

/* ------------------------------------------------------------------
   PART 8 - On-screen controls

   Keyboard shortcuts are unreliable here: Chrome and DevTools claim
   many Alt combos before the page sees them, and on macOS Option+E is
   a dead key for accents. Buttons always work.
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

// capture phase: Instagram stops propagation on some keydowns before
// they reach a bubble-phase listener on window
window.addEventListener("keydown", e => {
  if (!e.altKey) return;
  // e.key is unreliable with Alt on macOS (Option+S produces "ß"),
  // so match the physical key via e.code instead.
  const actions = {
    KeyS: saveProfile, KeyR: report, KeyL: listSaved,
    KeyE: exportCsv, KeyD: diagnose, KeyK: clearSaved
  };
  const action = actions[e.code];
  if (!action) return;
  e.preventDefault();
  e.stopPropagation();
  action();
}, true);

window.__watcher = {
  extractComments, extractProfile, findComment, diagnose,
  getPageType, saveProfile, listSaved, report, exportCsv, clearSaved
};

checkUrl();
scanPage();
