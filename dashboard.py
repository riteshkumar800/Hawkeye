#!/usr/bin/env python3
"""
Live dashboard for IG DOM Watcher.

    python3 dashboard.py

Opens http://localhost:8765

The extension POSTs here every time you save, so cards appear without
exporting anything. Data lives in data.json; the CSV is generated on
demand from the Export button.

Deleting a card removes it here AND from the extension - the content
script reconciles against this server every few seconds.

SCOPE: labels COMMENTS. Cards show what someone engaged with, how their
comments were labelled, and what they asked for. No personality
inference, no scoring of people from their bio.
"""

import csv
import io
import json
import urllib.request
import webbrowser
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

STORE_PATH = Path("data.json")
CACHE_PATH = Path("analysis-cache.json")
LEGACY_CSV = Path("instagram-data.csv")
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"
PORT = 8765

STANCES = {"supportive", "critical", "neutral", "question", "unclear"}
TONES = {"neutral", "positive", "angry", "abusive"}
INTENTS = {"question", "praise", "criticism", "request", "joke", "spam", "other"}

CSV_COLUMNS = [
    "username", "fullName", "bio", "profileUrl",
    "commentText", "commentPosted",
    "postAuthor", "postCaption", "postUrl", "savedAt"
]

COMMENT_RULES = """You label social media comments. Return ONLY valid JSON.

Label the COMMENT TEXT. Never describe or judge the person who wrote it.

stance  - position toward the post's subject:
          supportive | critical | neutral | question | unclear
tone    - how it is expressed:
          neutral | positive | angry | abusive
          (abusive = slurs, insults at a person or group, harassment)
intent  - what the comment is doing:
          question | praise | criticism | request | joke | spam | other
topic   - 2-4 words naming what the POST is about, from its caption
language- language of the comment: English | Hindi | Bengali | Mixed | Other
targets_person - "yes" if it addresses or attacks a specific individual,
          otherwise "no"
summary - max 10 words describing what this comment does

Only emoji or too short to judge? stance "unclear", tone "neutral",
intent "other". Do not guess.

Respond exactly as:
{"stance":"...","tone":"...","intent":"...","topic":"...",
 "language":"...","targets_person":"...","summary":"..."}"""

POST_RULES = """Describe what an Instagram post is about, from its caption.

One sentence, max 25 words, plain and factual. Describe the POST only -
not the people who commented on it.

Return ONLY valid JSON: {"about": "..."}"""

NEEDS_RULES = """Someone left comments on Instagram posts. Work out what they
are ASKING FOR, so a person reading this could help them.

Return ONLY valid JSON:
{"asking_for":"...","stated_context":"...","would_help":"..."}

asking_for     - what the comments request, max 25 words. If they ask for
                 nothing, write "nothing specific".
stated_context - ONLY study or work facts the person wrote in their own bio
                 that relate to the request: course, university, job title,
                 field of study. If the bio says nothing relevant, write
                 "not stated".
would_help     - what information or resource would answer the request,
                 max 20 words. Describe the RESOURCE, not the person.

Hard rules:
- Use ONLY what is literally written in the comments and the bio.
- Never mention or infer religion, ethnicity, nationality, race, gender,
  age, income, politics, or personality - even if the bio states them.
- Never guess anything not literally written.
- Describe the request, never the character of the person."""

ACTIVITY_RULES = """Summarise a SET OF COMMENTS in 1-2 sentences.

Rules:
- Describe only the comments: what they ask, what topics they appear
  under, how they are phrased.
- Do NOT describe the person, their character, personality, beliefs,
  nationality, religion or intentions.
- Do NOT speculate beyond what the comments literally say.

Return ONLY valid JSON: {"activity": "..."}"""


# ---------------------------------------------------------------- store

def blank_store():
    return {"saved": [], "profiles": []}


def load_store():
    if STORE_PATH.exists():
        try:
            data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            data.setdefault("saved", [])
            data.setdefault("profiles", [])
            return data
        except json.JSONDecodeError:
            pass

    # first run: pull in an existing CSV export so old data isn't lost
    store = blank_store()
    if LEGACY_CSV.exists():
        for r in csv.DictReader(LEGACY_CSV.open(encoding="utf-8")):
            user = (r.get("username") or "").strip()
            if not user:
                continue
            if r.get("commentText") not in ("", "N/A", None):
                store["saved"].append({
                    "username": user,
                    "posted": r.get("commentPosted", "N/A"),
                    "text": r["commentText"],
                    "url": r.get("postUrl", "N/A"),
                    "postAuthor": r.get("postAuthor", "N/A"),
                    "postCaption": r.get("postCaption", "N/A"),
                    "savedAt": r.get("savedAt", "N/A")
                })
            if r.get("bio") not in ("", None) or r.get("fullName") not in ("", None):
                if not any(p["username"] == user for p in store["profiles"]):
                    store["profiles"].append({
                        "username": user,
                        "fullName": r.get("fullName", "N/A"),
                        "bio": r.get("bio", "N/A"),
                        "url": r.get("profileUrl", f"https://www.instagram.com/{user}/"),
                        "savedAt": r.get("savedAt", "N/A")
                    })
        save_store(store)
    return store


def save_store(store):
    STORE_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


def comment_key(c):
    return f"{c.get('username')}|{c.get('posted')}|{c.get('text')}"


def load_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def build_accounts(store):
    grouped = defaultdict(lambda: {
        "comments": [], "savedAt": "N/A",
        "fullName": "N/A", "bio": "N/A", "profileUrl": "N/A"
    })

    for p in store["profiles"]:
        acct = grouped[p["username"]]
        acct.update(
            username=p["username"],
            fullName=p.get("fullName") or "N/A",
            bio=p.get("bio") or "N/A",
            profileUrl=p.get("url") or f"https://www.instagram.com/{p['username']}/",
            savedAt=p.get("savedAt", "N/A")
        )

    for c in store["saved"]:
        acct = grouped[c["username"]]
        acct["username"] = c["username"]
        if acct["profileUrl"] == "N/A":
            acct["profileUrl"] = f"https://www.instagram.com/{c['username']}/"
        if acct["savedAt"] == "N/A":
            acct["savedAt"] = c.get("savedAt", "N/A")
        acct["comments"].append({
            "text": c.get("text", "N/A"),
            "posted": c.get("posted", "N/A"),
            "postAuthor": c.get("postAuthor", "N/A"),
            "postCaption": c.get("postCaption", "N/A"),
            "postUrl": c.get("url", "N/A"),
            "savedAt": c.get("savedAt", "N/A")
        })

    return sorted(grouped.values(), key=lambda a: (-len(a["comments"]), a["username"]))


def store_to_csv(store):
    by_user = {p["username"]: p for p in store["profiles"]}
    rows = []

    for c in store["saved"]:
        p = by_user.get(c["username"], {})
        rows.append({
            "username": c["username"],
            "fullName": p.get("fullName") or "N/A",
            "bio": p.get("bio") or "N/A",
            "profileUrl": p.get("url") or f"https://www.instagram.com/{c['username']}/",
            "commentText": c.get("text", "N/A"),
            "commentPosted": c.get("posted", "N/A"),
            "postAuthor": c.get("postAuthor", "N/A"),
            "postCaption": c.get("postCaption", "N/A"),
            "postUrl": c.get("url", "N/A"),
            "savedAt": c.get("savedAt", "N/A")
        })

    for p in store["profiles"]:
        if any(c["username"] == p["username"] for c in store["saved"]):
            continue
        rows.append({
            "username": p["username"],
            "fullName": p.get("fullName") or "N/A",
            "bio": p.get("bio") or "N/A",
            "profileUrl": p.get("url") or "N/A",
            "commentText": "N/A", "commentPosted": "N/A",
            "postAuthor": "N/A", "postCaption": "N/A", "postUrl": "N/A",
            "savedAt": p.get("savedAt", "N/A")
        })

    rows.sort(key=lambda r: (r["postUrl"], r["username"]))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


# -------------------------------------------------------------- ollama

def ask_ollama(prompt):
    payload = json.dumps({
        "model": MODEL, "prompt": prompt, "stream": False,
        "format": "json", "options": {"temperature": 0}
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())["response"]


def pick(value, allowed, fallback):
    v = str(value or "").lower().strip()
    return v if v in allowed else fallback


def label_comment(comment):
    prompt = (
        f"{COMMENT_RULES}\n\n"
        f"POST CAPTION: {comment['postCaption'][:400]}\n"
        f"COMMENT: {comment['text'][:400]}\n\nJSON:"
    )
    try:
        data = json.loads(ask_ollama(prompt))
    except Exception:                                   # noqa: BLE001
        data = {}

    return {
        "stance": pick(data.get("stance"), STANCES, "unclear"),
        "tone": pick(data.get("tone"), TONES, "neutral"),
        "intent": pick(data.get("intent"), INTENTS, "other"),
        "topic": str(data.get("topic") or "").strip() or "N/A",
        "language": str(data.get("language") or "").strip() or "N/A",
        "targetsPerson": pick(data.get("targets_person"), {"yes", "no"}, "no"),
        "summary": str(data.get("summary") or "").strip() or "N/A"
    }


def post_about(caption):
    if not caption or caption == "N/A":
        return "N/A"
    try:
        data = json.loads(ask_ollama(f"{POST_RULES}\n\nCAPTION: {caption[:600]}\n\nJSON:"))
        return str(data.get("about") or "").strip() or "N/A"
    except Exception:                                   # noqa: BLE001
        return "N/A"


def position_on_post(comments):
    """Counted, not generated - so it can never contradict the labels."""
    stances = Counter(c["stance"] for c in comments)
    supportive, critical = stances["supportive"], stances["critical"]
    if supportive and critical:
        return "mixed"
    if critical:
        return "against"
    if supportive:
        return "in favour"
    if stances["question"]:
        return "asking"
    return "neutral"


def build_posts(labelled):
    by_url = defaultdict(list)
    for c in labelled:
        by_url[c["postUrl"]].append(c)

    posts = []
    for url, group in by_url.items():
        first = group[0]
        posts.append({
            "postUrl": url,
            "postAuthor": first["postAuthor"],
            "postCaption": first["postCaption"],
            "topic": first["topic"],
            "about": post_about(first["postCaption"]),
            "position": position_on_post(group),
            "commentCount": len(group),
            "comments": group
        })
    return sorted(posts, key=lambda p: -p["commentCount"])


def needs_summary(account, labelled):
    if not labelled:
        return {"askingFor": "N/A", "statedContext": "N/A", "wouldHelp": "N/A"}

    listing = "\n".join(
        f'- on a post about "{c["topic"]}" ({c["postCaption"][:120]}): "{c["text"][:200]}"'
        for c in labelled
    )
    prompt = (
        f"{NEEDS_RULES}\n\n"
        f"THEIR BIO: {account.get('bio', 'N/A')[:400]}\n\n"
        f"THEIR COMMENTS:\n{listing}\n\nJSON:"
    )
    try:
        data = json.loads(ask_ollama(prompt))
    except Exception:                                   # noqa: BLE001
        data = {}

    return {
        "askingFor": str(data.get("asking_for") or "").strip() or "N/A",
        "statedContext": str(data.get("stated_context") or "").strip() or "N/A",
        "wouldHelp": str(data.get("would_help") or "").strip() or "N/A"
    }


def activity_summary(labelled):
    listing = "\n".join(
        f'- on a post about "{c["topic"]}": "{c["text"][:150]}" [{c["stance"]}, {c["tone"]}]'
        for c in labelled
    )
    try:
        data = json.loads(ask_ollama(f"{ACTIVITY_RULES}\n\nCOMMENTS:\n{listing}\n\nJSON:"))
        return str(data.get("activity") or "").strip() or "N/A"
    except Exception:                                   # noqa: BLE001
        return "N/A"


def analyze(account):
    labelled = [{**c, **label_comment(c)} for c in account["comments"]]
    return {
        "comments": labelled,
        "posts": build_posts(labelled),
        "needs": needs_summary(account, labelled),
        "topics": sorted({c["topic"] for c in labelled if c["topic"] != "N/A"}),
        "stances": Counter(c["stance"] for c in labelled),
        "tones": Counter(c["tone"] for c in labelled),
        "intents": Counter(c["intent"] for c in labelled),
        "languages": Counter(c["language"] for c in labelled),
        "flagged": sum(1 for c in labelled
                       if c["tone"] in ("angry", "abusive") or c["targetsPerson"] == "yes"),
        "activity": activity_summary(labelled) if labelled else "N/A"
    }


# ---------------------------------------------------------------- page

PAGE = r"""<!doctype html>
<meta charset="utf-8">
<title>Saved accounts</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { background:#0f0f10; color:#e8e8ea;
         font:14px/1.55 system-ui,-apple-system,sans-serif; margin:0; padding:30px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#85858e; font-size:13px; }
  .bar { display:flex; align-items:center; gap:12px; margin-bottom:24px; }
  .dot { width:8px; height:8px; border-radius:50%; background:#3f7052; }
  .dot.off { background:#7d3a2f; }

  .grid { display:grid; gap:18px;
          grid-template-columns:repeat(auto-fill,minmax(520px,1fr)); }
  .card { background:#161618; border:1px solid #2b2b30; border-radius:14px;
          overflow:hidden; }
  .card.new { animation: pop .5s ease; }
  @keyframes pop { from { transform:scale(.98); opacity:0 } to { transform:none; opacity:1 } }

  .head { padding:18px 20px; border-bottom:1px solid #26262a; background:#1b1b1e;
          display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
  .handle { font-weight:650; font-size:17px; }
  .name { color:#9c9ca6; font-size:13px; margin-top:1px; }
  .body { padding:18px 20px; }

  .field { margin-bottom:14px; }
  .k { color:#6f6f79; font-size:11px; text-transform:uppercase;
       letter-spacing:.06em; margin-bottom:3px; }
  .v { font-size:13.5px; white-space:pre-wrap; word-break:break-word; }
  .v.muted { color:#75757e; }
  a { color:#6fa8e0; text-decoration:none; word-break:break-all; }
  a:hover { text-decoration:underline; }
  .row { display:flex; gap:22px; flex-wrap:wrap; }
  .row .field { flex:1; min-width:150px; }

  button { background:#2c2c31; color:#fff; border:1px solid #45454c;
           border-radius:8px; padding:8px 15px; font:13px system-ui; cursor:pointer; }
  button:hover:not(:disabled) { background:#37373d; }
  button:disabled { opacity:.45; cursor:default; }
  button.ghost { background:transparent; border-color:#3a3a40; color:#9c9ca6;
                 padding:5px 11px; font-size:12px; }
  button.ghost:hover { background:#2a1e1e; border-color:#8b3a3a; color:#ffb0b0; }

  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip { font-size:11.5px; padding:3px 9px; border-radius:20px;
          background:#2a2a2e; border:1px solid #3d3d43; }
  .chip.angry     { background:#3f201c; border-color:#7d3a2f; color:#ffb9a8; }
  .chip.abusive   { background:#4d1a1a; border-color:#912f2f; color:#ffb0b0; }
  .chip.positive,
  .chip.supportive{ background:#1b3324; border-color:#2f6b41; color:#a8e6bd; }
  .chip.critical  { background:#3a2f16; border-color:#7d6222; color:#f0d391; }
  .chip.topic     { background:#1c2a3c; border-color:#2f4f70; color:#a6cdf0; }
  .chip.big       { font-size:12.5px; padding:4px 12px; }
  .chip.against   { background:#4d1a1a; border-color:#912f2f; color:#ffb0b0; }
  .chip.favour    { background:#1b3324; border-color:#2f6b41; color:#a8e6bd; }
  .chip.mixed     { background:#3a2f16; border-color:#7d6222; color:#f0d391; }
  .chip.asking    { background:#1c2a3c; border-color:#2f4f70; color:#a6cdf0; }

  .sect { border-top:1px solid #26262a; padding:16px 20px; }
  .sect h3 { font-size:11px; text-transform:uppercase; letter-spacing:.06em;
             color:#6f6f79; margin:0 0 10px; font-weight:600; }

  .cmt { background:#1b1b1e; border:1px solid #2a2a2f; border-radius:9px;
         padding:12px 14px; margin-bottom:10px; }
  .cmt .txt { font-size:13.5px; margin-bottom:8px; }
  .cmt .ctx { color:#75757e; font-size:11.5px; margin-bottom:8px; }
  .cmt .sum { color:#9c9ca6; font-size:12px; font-style:italic; margin-top:8px; }

  .post { background:#1a1c1f; border:1px solid #2b3036; border-radius:10px;
          padding:14px 16px; margin-bottom:12px; }
  .post .about { font-size:13.5px; color:#dfe3e7; margin-bottom:6px; }
  .post .cap { color:#7b7b84; font-size:11.5px; margin:8px 0; }
  .post .line { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
                margin-bottom:8px; }
  .post .who { color:#8b8b93; font-size:12px; }

  .activity { background:#181a1d; border:1px solid #2b3138; border-left:3px solid #3d5a75;
              border-radius:8px; padding:12px 14px; font-size:13px; color:#c8cdd3; }
  .needs { background:#171b19; border:1px solid #29332c; border-left:3px solid #3f7052;
           border-radius:9px; padding:14px 16px; }
  .needs .n { margin-bottom:11px; }
  .needs .n:last-child { margin-bottom:0; }
  .needs .nk { color:#6f8578; font-size:11px; text-transform:uppercase;
               letter-spacing:.06em; margin-bottom:3px; }
  .needs .nv { font-size:13.5px; color:#dbe4dd; }

  .flag { color:#e0906a; font-size:12px; margin-top:8px; }
  .err { color:#e07a6a; font-size:13px; }
  .empty { color:#6e6e77; padding:40px 0; }
  .note { color:#63636c; font-size:11.5px; margin-top:22px;
          border-top:1px solid #212124; padding-top:12px; max-width:760px; }
</style>

<h1>Saved accounts</h1>
<div class="bar">
  <span class="dot" id="dot"></span>
  <span class="sub" id="sub">loading…</span>
  <button class="ghost" onclick="exportCsv()">Export CSV</button>
</div>
<div class="grid" id="grid"></div>
<div id="empty"></div>
<div class="note">
  Labels describe individual comments. "Engaged with" comes from the posts an
  account commented under. Nothing here is an assessment of a person, and a
  small local model gets sarcasm and mixed-language text wrong often - verify
  before citing.
</div>

<script>
const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));

let accounts = [], cache = {}, known = new Set();

const field = (k, v, cls = "") =>
  `<div class="field"><div class="k">${esc(k)}</div><div class="v ${cls}">${v}</div></div>`;

const link = u => (u && u !== "N/A")
  ? `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`
  : '<span class="muted">N/A</span>';

const when = t => {
  if (!t || t === "N/A") return "N/A";
  const d = new Date(t);
  return isNaN(d) ? esc(t) : d.toLocaleString();
};

function chips(obj, colour) {
  const e = Object.entries(obj || {});
  if (!e.length) return '<span class="chip">N/A</span>';
  return e.map(([k, v]) =>
    `<span class="chip ${colour ? esc(k) : ''}">${esc(k)} ×${v}</span>`).join("");
}

function commentBlock(c, labelled) {
  const labels = labelled ? `
    <div class="chips">
      <span class="chip ${esc(c.stance)}">${esc(c.stance)}</span>
      <span class="chip ${esc(c.tone)}">${esc(c.tone)}</span>
      <span class="chip">${esc(c.intent)}</span>
      <span class="chip">${esc(c.language)}</span>
      ${c.targetsPerson === "yes" ? '<span class="chip abusive">targets a person</span>' : ""}
    </div>
    <div class="sum">${esc(c.summary)}</div>` : "";

  return `
    <div class="cmt">
      <div class="txt">${esc(c.text)}</div>
      <div class="ctx">posted ${when(c.posted)} · under @${esc(c.postAuthor)} · ${link(c.postUrl)}</div>
      <div class="ctx">post caption: ${esc((c.postCaption || "N/A").slice(0, 220))}</div>
      ${labels}
    </div>`;
}

const POSITION_CLASS = {
  "against":"against", "in favour":"favour",
  "mixed":"mixed", "asking":"asking", "neutral":""
};

function postsHtml(posts) {
  if (!posts || !posts.length) return "";
  return `
    <div class="sect">
      <h3>Posts commented on</h3>
      ${posts.map(p => `
        <div class="post">
          <div class="line">
            <span class="chip big ${POSITION_CLASS[p.position] ?? ""}">${esc(p.position)}</span>
            <span class="who">${p.commentCount} comment(s) · by @${esc(p.postAuthor)}</span>
          </div>
          <div class="about">${esc(p.about)}</div>
          <div class="chips"><span class="chip topic">${esc(p.topic)}</span></div>
          <div class="cap">caption: ${esc((p.postCaption || "N/A").slice(0, 260))}</div>
          <div>${link(p.postUrl)}</div>
        </div>`).join("")}
    </div>`;
}

function analysisHtml(res) {
  if (!res) return "";
  const n = res.needs;
  const needs = n ? `
    <div class="sect">
      <h3>What they're asking for</h3>
      <div class="needs">
        <div class="n"><div class="nk">asking for</div><div class="nv">${esc(n.askingFor)}</div></div>
        <div class="n"><div class="nk">context they stated about themselves</div>
          <div class="nv">${esc(n.statedContext)}</div></div>
        <div class="n"><div class="nk">what would help</div><div class="nv">${esc(n.wouldHelp)}</div></div>
      </div>
    </div>` : "";

  const topics = (res.topics || []).length
    ? res.topics.map(t => `<span class="chip topic">${esc(t)}</span>`).join("")
    : '<span class="chip">N/A</span>';

  return `
    ${needs}
    ${postsHtml(res.posts)}
    <div class="sect">
      <h3>Analysis</h3>
      <div class="activity">${esc(res.activity)}</div>
      ${res.flagged ? `<div class="flag">${res.flagged} comment(s) flagged angry, abusive, or aimed at an individual</div>` : ""}
      <div class="field" style="margin-top:14px">
        <div class="k">engaged with</div><div class="chips">${topics}</div>
      </div>
      <div class="row">
        ${field("stance", `<div class="chips">${chips(res.stances, true)}</div>`)}
        ${field("tone", `<div class="chips">${chips(res.tones, true)}</div>`)}
      </div>
      <div class="row">
        ${field("intent", `<div class="chips">${chips(res.intents)}</div>`)}
        ${field("language", `<div class="chips">${chips(res.languages)}</div>`)}
      </div>
    </div>`;
}

function cardHtml(a, i, isNew) {
  const res = cache[a.username];
  const comments = a.comments.length
    ? (res ? res.comments : a.comments).map(c => commentBlock(c, !!res)).join("")
    : '<div class="v muted">No comments saved from this account.</div>';

  return `
    <div class="card ${isNew ? "new" : ""}" id="card${i}">
      <div class="head">
        <div>
          <div class="handle">@${esc(a.username)}</div>
          <div class="name">${esc(a.fullName)}</div>
        </div>
        <button class="ghost" onclick="del('${esc(a.username)}')">Delete</button>
      </div>
      <div class="body">
        ${field("bio", esc(a.bio), a.bio === "N/A" ? "muted" : "")}
        ${field("profile", link(a.profileUrl))}
        <div class="row">
          ${field("saved comments", a.comments.length)}
          ${field("first saved", when(a.savedAt))}
        </div>
        <button onclick="run(${i})" ${a.comments.length ? "" : "disabled"}>
          ${res ? "Re-analyze" : "Analyze"}
        </button>
      </div>
      <div class="sect">
        <h3>Saved comments</h3>
        <div id="cmts${i}">${comments}</div>
      </div>
      <div id="out${i}">${analysisHtml(res)}</div>
    </div>`;
}

function render() {
  const grid = document.getElementById("grid");
  const fresh = new Set(accounts.map(a => a.username));
  grid.innerHTML = accounts
    .map((a, i) => cardHtml(a, i, !known.has(a.username)))
    .join("");
  known = fresh;

  document.getElementById("empty").innerHTML = accounts.length ? "" :
    '<div class="empty">Nothing saved yet. Alt+click a comment or press Alt+S on a profile in Instagram - cards appear here automatically.</div>';
}

let busy = false;   // don't re-render underneath a running analysis

async function poll() {
  try {
    const r = await fetch("/api/data").then(r => r.json());
    document.getElementById("dot").classList.remove("off");

    const changed = JSON.stringify(r.accounts) !== JSON.stringify(accounts);
    accounts = r.accounts; cache = r.cache;

    const total = accounts.reduce((n, a) => n + a.comments.length, 0);
    document.getElementById("sub").textContent =
      `${accounts.length} accounts · ${total} saved comments · live`;

    if (changed && !busy) render();
  } catch {
    document.getElementById("dot").classList.add("off");
    document.getElementById("sub").textContent = "server unreachable";
  }
}

async function del(username) {
  if (!confirm(`Delete @${username} and all saved comments from them?\n\nThis also removes them from the extension.`)) return;
  await fetch("/api/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username })
  });
  known.delete(username);
  poll();
}

function exportCsv() { window.location = "/api/export.csv"; }

async function run(i) {
  const card = document.getElementById("card" + i);
  const btn = card.querySelectorAll("button")[1];
  const out = document.getElementById("out" + i);
  busy = true;
  btn.disabled = true; btn.textContent = "Analyzing…";
  out.innerHTML = '<div class="sect"><div class="v muted">Running Ollama…</div></div>';

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: accounts[i].username })
    }).then(r => r.json());

    if (res.error) throw new Error(res.error);
    cache[accounts[i].username] = res;
    document.getElementById("cmts" + i).innerHTML =
      res.comments.map(c => commentBlock(c, true)).join("");
    out.innerHTML = analysisHtml(res);
    btn.textContent = "Re-analyze";
  } catch (err) {
    out.innerHTML = `<div class="sect"><div class="err">${esc(err.message)}</div></div>`;
    btn.textContent = "Analyze";
  }
  btn.disabled = false;
  busy = false;
}

poll();
setInterval(poll, 2500);
</script>
"""


# -------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # the extension calls this from the instagram.com origin
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or "{}")

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")

        if self.path == "/api/data":
            store = load_store()
            return self._send(200, json.dumps({
                "accounts": build_accounts(store), "cache": load_cache()
            }))

        # extension polls this to mirror deletions
        if self.path == "/api/state":
            store = load_store()
            return self._send(200, json.dumps({
                "comments": [comment_key(c) for c in store["saved"]],
                "profiles": [p["username"] for p in store["profiles"]]
            }))

        if self.path == "/api/export.csv":
            return self._send(
                200, store_to_csv(load_store()), "text/csv; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="instagram-data.csv"'}
            )

        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        store = load_store()

        if self.path == "/api/save/comment":
            record = self._body()
            if not record.get("username"):
                return self._send(400, json.dumps({"error": "no username"}))
            if not any(comment_key(c) == comment_key(record) for c in store["saved"]):
                store["saved"].append(record)
                save_store(store)
            return self._send(200, json.dumps({"ok": True, "count": len(store["saved"])}))

        if self.path == "/api/save/profile":
            record = self._body()
            if not record.get("username"):
                return self._send(400, json.dumps({"error": "no username"}))
            i = next((n for n, p in enumerate(store["profiles"])
                      if p["username"] == record["username"]), None)
            if i is None:
                store["profiles"].append(record)
            else:
                store["profiles"][i] = record
            save_store(store)
            return self._send(200, json.dumps({"ok": True, "count": len(store["profiles"])}))

        if self.path == "/api/delete":
            username = self._body().get("username")
            store["saved"] = [c for c in store["saved"] if c["username"] != username]
            store["profiles"] = [p for p in store["profiles"] if p["username"] != username]
            save_store(store)

            cache = load_cache()
            cache.pop(username, None)
            save_cache(cache)
            return self._send(200, json.dumps({"ok": True}))

        if self.path == "/api/analyze":
            username = self._body().get("username")
            account = next((a for a in build_accounts(store)
                            if a["username"] == username), None)
            if not account:
                return self._send(404, json.dumps({"error": f"no account {username}"}))
            try:
                result = analyze(account)
            except Exception as err:                    # noqa: BLE001
                return self._send(500, json.dumps({
                    "error": f"Ollama failed: {err} - is it running, and is MODEL '{MODEL}' pulled?"
                }))
            cache = load_cache()
            cache[username] = result
            save_cache(cache)
            return self._send(200, json.dumps(result))

        self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, *args):
        pass


def main():
    url = f"http://localhost:{PORT}"
    store = load_store()
    print(f"Dashboard: {url}   (Ctrl+C to stop)")
    print(f"Store: {STORE_PATH}  -  {len(store['saved'])} comments, "
          f"{len(store['profiles'])} profiles")
    webbrowser.open(url)
    HTTPServer(("localhost", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
