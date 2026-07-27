#!/usr/bin/env python3
"""
Card dashboard for IG DOM Watcher data.

    python3 dashboard.py

Opens http://localhost:8765 - one card per saved account showing every
field from the CSV, with an Analyze button that classifies that
account's comments with Ollama.

Runs as a tiny local server (stdlib only, no pip install) because a
file:// page can't call Ollama directly - CORS blocks it.

SCOPE: labels COMMENTS. Cards show what someone engaged with and how
their comments were labelled. No personality inference, no scoring of
people from their bio.
"""

import csv
import json
import urllib.request
import webbrowser
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CSV_PATH = Path("instagram-data.csv")
CACHE_PATH = Path("analysis-cache.json")
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"
PORT = 8765

STANCES = {"supportive", "critical", "neutral", "question", "unclear"}
TONES = {"neutral", "positive", "angry", "abusive"}
INTENTS = {"question", "praise", "criticism", "request", "joke", "spam", "other"}

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
summary - max 10 words describing what this comment does,
          e.g. "asks about eligibility requirements"

Only emoji or too short to judge? stance "unclear", tone "neutral",
intent "other".  Do not guess.

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

asking_for     - what the comments request, max 25 words. Base this on the
                 comment text and what the post is about. If they ask for
                 nothing, write "nothing specific".
stated_context - ONLY study or work facts the person wrote in their own bio
                 that relate to the request: course, university, job title,
                 field of study. Paraphrase closely. If the bio says nothing
                 relevant, write "not stated".
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


# ----------------------------------------------------------------- data

FIELDS = ("fullName", "bio", "profileUrl")


def load_accounts():
    if not CSV_PATH.exists():
        return []

    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    grouped = defaultdict(lambda: {"comments": [], "savedAt": "N/A"})

    for r in rows:
        user = (r.get("username") or "").strip()
        if not user:
            continue

        acct = grouped[user]
        acct["username"] = user

        # profile fields repeat on every row; keep the first real value
        for field in FIELDS:
            value = r.get(field, "N/A")
            if acct.get(field) in (None, "", "N/A") and value not in ("", "N/A"):
                acct[field] = value
            acct.setdefault(field, "N/A")

        if acct["savedAt"] == "N/A":
            acct["savedAt"] = r.get("savedAt", "N/A")

        if r.get("commentText") not in ("", "N/A", None):
            acct["comments"].append({
                "text": r["commentText"],
                "posted": r.get("commentPosted", "N/A"),
                "postAuthor": r.get("postAuthor", "N/A"),
                "postCaption": r.get("postCaption", "N/A"),
                "postUrl": r.get("postUrl", "N/A"),
                "savedAt": r.get("savedAt", "N/A")
            })

    return sorted(grouped.values(), key=lambda a: -len(a["comments"]))


def load_cache():
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_cache(cache):
    CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


# -------------------------------------------------------------- ollama

def ask_ollama(prompt):
    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0}
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


def activity_summary(labelled):
    listing = "\n".join(
        f'- on a post about "{c["topic"]}": "{c["text"][:150]}" '
        f'[{c["stance"]}, {c["tone"]}]'
        for c in labelled
    )
    try:
        data = json.loads(ask_ollama(f"{ACTIVITY_RULES}\n\nCOMMENTS:\n{listing}\n\nJSON:"))
        return str(data.get("activity") or "").strip() or "N/A"
    except Exception:                                   # noqa: BLE001
        return "N/A"


def post_about(caption):
    if not caption or caption == "N/A":
        return "N/A"
    try:
        data = json.loads(ask_ollama(f"{POST_RULES}\n\nCAPTION: {caption[:600]}\n\nJSON:"))
        return str(data.get("about") or "").strip() or "N/A"
    except Exception:                                   # noqa: BLE001
        return "N/A"


def position_on_post(comments):
    """This account's overall position toward one post, from its own
    comments there. Derived by counting - not a second model call."""
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
    """What is this person asking for, and what would answer it?

    Deliberately narrow: the request, plus study/work context the person
    stated about themselves. Not a character sketch."""
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

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Saved accounts</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { background:#0f0f10; color:#e8e8ea;
         font:14px/1.55 system-ui,-apple-system,sans-serif; margin:0; padding:30px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#85858e; font-size:13px; margin-bottom:24px; }

  .grid { display:grid; gap:18px;
          grid-template-columns:repeat(auto-fill,minmax(520px,1fr)); }
  .card { background:#161618; border:1px solid #2b2b30; border-radius:14px;
          overflow:hidden; }

  .head { padding:18px 20px; border-bottom:1px solid #26262a; background:#1b1b1e; }
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
           border-radius:8px; padding:8px 15px; font:13px system-ui;
           cursor:pointer; }
  button:hover:not(:disabled) { background:#37373d; }
  button:disabled { opacity:.45; cursor:default; }

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

  .post { background:#1a1c1f; border:1px solid #2b3036; border-radius:10px;
          padding:14px 16px; margin-bottom:12px; }
  .post .about { font-size:13.5px; color:#dfe3e7; margin-bottom:6px; }
  .post .cap { color:#7b7b84; font-size:11.5px; margin:8px 0; }
  .post .line { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
                margin-bottom:8px; }
  .post .who { color:#8b8b93; font-size:12px; }

  .sect { border-top:1px solid #26262a; padding:16px 20px; }
  .sect h3 { font-size:11px; text-transform:uppercase; letter-spacing:.06em;
             color:#6f6f79; margin:0 0 10px; font-weight:600; }

  .cmt { background:#1b1b1e; border:1px solid #2a2a2f; border-radius:9px;
         padding:12px 14px; margin-bottom:10px; }
  .cmt .txt { font-size:13.5px; margin-bottom:8px; }
  .cmt .ctx { color:#75757e; font-size:11.5px; margin-bottom:8px; }
  .cmt .sum { color:#9c9ca6; font-size:12px; font-style:italic; margin-top:8px; }

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
  .note { color:#63636c; font-size:11.5px; margin-top:22px;
          border-top:1px solid #212124; padding-top:12px; max-width:760px; }
</style>

<h1>Saved accounts</h1>
<div class="sub" id="sub">loading…</div>
<div class="grid" id="grid"></div>
<div class="note">
  Labels describe individual comments. "Engaged with" comes from the posts an
  account commented under. The activity line summarises those comments only.
  Nothing here is an assessment of a person, and a small local model gets
  sarcasm and mixed-language text wrong often - verify before citing.
</div>

<script>
const esc = s => String(s ?? "").replace(/[&<>"]/g,
  c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));

let accounts = [], cache = {};

const field = (k, v, cls = "") =>
  `<div class="field"><div class="k">${esc(k)}</div>
   <div class="v ${cls}">${v}</div></div>`;

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
      <div class="ctx">
        posted ${when(c.posted)} · under @${esc(c.postAuthor)} ·
        ${link(c.postUrl)}
      </div>
      <div class="ctx">post caption: ${esc((c.postCaption || "N/A").slice(0, 220))}</div>
      ${labels}
    </div>`;
}

const POSITION_CLASS = {
  "against": "against", "in favour": "favour",
  "mixed": "mixed", "asking": "asking", "neutral": ""
};

function postsHtml(posts) {
  if (!posts || !posts.length) return "";
  return `
    <div class="sect">
      <h3>Posts commented on</h3>
      ${posts.map(p => `
        <div class="post">
          <div class="line">
            <span class="chip big ${POSITION_CLASS[p.position] ?? ""}">
              ${esc(p.position)}
            </span>
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
  const topics = (res.topics || []).length
    ? res.topics.map(t => `<span class="chip topic">${esc(t)}</span>`).join("")
    : '<span class="chip">N/A</span>';

  const n = res.needs;
  const needs = n ? `
    <div class="sect">
      <h3>What they're asking for</h3>
      <div class="needs">
        <div class="n"><div class="nk">asking for</div>
          <div class="nv">${esc(n.askingFor)}</div></div>
        <div class="n"><div class="nk">context they stated about themselves</div>
          <div class="nv">${esc(n.statedContext)}</div></div>
        <div class="n"><div class="nk">what would help</div>
          <div class="nv">${esc(n.wouldHelp)}</div></div>
      </div>
    </div>` : "";

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

function cardHtml(a, i) {
  const res = cache[a.username];
  const comments = a.comments.length
    ? (res ? res.comments : a.comments).map(c => commentBlock(c, !!res)).join("")
    : '<div class="v muted">No comments saved from this account.</div>';

  return `
    <div class="card" id="card${i}">
      <div class="head">
        <div class="handle">@${esc(a.username)}</div>
        <div class="name">${esc(a.fullName)}</div>
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
  document.getElementById("grid").innerHTML =
    accounts.map((a, i) => cardHtml(a, i)).join("");
}

async function boot() {
  const r = await fetch("/api/data").then(r => r.json());
  accounts = r.accounts; cache = r.cache;
  const total = accounts.reduce((n, a) => n + a.comments.length, 0);
  document.getElementById("sub").textContent =
    `${accounts.length} accounts · ${total} saved comments`;
  render();
}

async function run(i) {
  const card = document.getElementById("card" + i);
  const btn = card.querySelector("button");
  const out = document.getElementById("out" + i);
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
}

boot();
</script>
"""


# -------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/data":
            return self._send(200, json.dumps({
                "accounts": load_accounts(),
                "cache": load_cache()
            }))
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/api/analyze":
            return self._send(404, json.dumps({"error": "not found"}))

        length = int(self.headers.get("Content-Length", 0))
        username = json.loads(self.rfile.read(length) or "{}").get("username")

        account = next((a for a in load_accounts() if a["username"] == username), None)
        if not account:
            return self._send(404, json.dumps({"error": f"no account {username}"}))

        try:
            result = analyze(account)
        except Exception as err:                        # noqa: BLE001
            return self._send(500, json.dumps({
                "error": f"Ollama failed: {err} - is it running, and is MODEL '{MODEL}' pulled?"
            }))

        cache = load_cache()
        cache[username] = result
        save_cache(cache)
        self._send(200, json.dumps(result))

    def log_message(self, *args):
        pass


def main():
    if not CSV_PATH.exists():
        raise SystemExit(f"{CSV_PATH} not found - run this from the folder holding your CSV")

    url = f"http://localhost:{PORT}"
    print(f"Dashboard: {url}   (Ctrl+C to stop)")
    webbrowser.open(url)
    HTTPServer(("localhost", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()