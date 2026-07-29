# Hawkeye — IG DOM Watcher

A Chrome extension and local dashboard for manually saving Instagram
comments while browsing, then classifying them with a locally-run
language model.

Everything runs on your machine. No cloud API, no credentials, no
automated collection — nothing is recorded unless you explicitly save it.

---

## Project structure

```
ig-dom/
├── manifest.json          # Chrome extension manifest (MV3)
├── content.js             # Content script — reads the Instagram DOM
├── dashboard.py           # HTTP server, dashboard UI, Ollama analysis
├── data.json              # Local data store (auto-generated)
└── analysis-cache.json    # Cached LLM analysis (auto-generated)
```

---

## Requirements

- Google Chrome
- Python 3.9+ (standard library only — no `pip install`)
- [Ollama](https://ollama.com) with a local model

---

## Setup

### 1. Pull the model

```bash
ollama pull llama3.2:3b
```

To use a different model, change `MODEL` near the top of `dashboard.py`
to match a name from `ollama list` — including the tag (`llama3.2:3b`,
not `llama3.2`).

### 2. Start the dashboard

```bash
cd ig-dom
python3 dashboard.py
```

The server starts on `http://localhost:8765` and opens your browser.
Leave the terminal running. `Ctrl+C` to stop.

### 3. Load the extension

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top-right)
3. Click **Load unpacked** (top-left)
4. Select the `ig-dom` folder — the folder, not a file
5. Confirm the toggle is on

Then reload any open Instagram tab. Extensions only inject into pages
loaded after installation.

---

## Usage

A floating control bar appears at the bottom-left of Instagram.

### Saving

| Action | How | What it saves |
|---|---|---|
| Save a comment | `Alt` + click the comment text | Author, text, timestamp, post URL, post author, post caption |
| Save a profile | `Alt+S`, or **Save profile** | Handle, display name, bio, verified status |

A toast confirms each save. `→ dashboard` means it reached the server;
`(dashboard offline)` means it's stored locally and the server isn't
running.

### Control bar

| Button | Action |
|---|---|
| Counter | Live totals from local storage |
| **Save profile** | Same as `Alt+S` — profile pages only |
| **Report** | Prints grouped tables to the browser console |
| **CSV** | Downloads `instagram-data.csv` |
| **Clear** | Wipes all local extension storage |

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Alt` + click | Save a comment |
| `Alt+S` | Save the current profile |
| `Alt+R` | Print report to console |
| `Alt+L` | Print raw tables to console |
| `Alt+E` | Export CSV |
| `Alt+K` | Clear storage |
| `Alt+D` | Diagnose DOM parsing on a post page |

> On macOS, some `Alt` combinations are claimed by Chrome or DevTools
> before the page sees them. The control-bar buttons always work.

---

## The dashboard

`http://localhost:8765` — one card per saved account, updating live as
you save. The dot beside the header is green when the page can reach the
server.

Each card shows:

- Handle, display name, verified badge, bio, profile link
- Every saved comment with its timestamp, post link and post caption
- **Also commented on the same posts** — other saved accounts that
  appeared under the same post, and whether their positions match
- **Delete** — removes the account from the store, the analysis cache,
  and the extension

### Analyze

Click **Analyze** on any card to run that account's comments through
Ollama. Results are cached in `analysis-cache.json`.

**Per comment:**

| Label | Values |
|---|---|
| `stance` | supportive, critical, neutral, question, unclear |
| `tone` | neutral, positive, angry, abusive |
| `intent` | question, praise, criticism, request, joke, spam, other |
| `topic` | 2–4 words describing the post |
| `language` | English, Hindi, Bengali, Mixed, Other |
| `targets a person` | flagged when aimed at an individual |
| `summary` | one line on what the comment does |

**Per account:**

- **What they're asking for** — the request, self-stated study or work
  context, and what resource would answer it
- **Posts commented on** — each post with a one-line description and a
  position chip (`in favour` / `against` / `mixed` / `asking` / `neutral`)
- **Analysis** — activity summary plus stance, tone, intent and language
  distributions

Position chips are **counted from the per-comment stance labels**, not
generated separately, so they can never contradict the labels shown above
them.

---

## Architecture

```
   Instagram tab                 localhost:8765             localhost:11434
┌──────────────────┐          ┌──────────────────┐         ┌──────────────┐
│  content.js      │  POST    │  dashboard.py    │  POST   │  Ollama      │
│                  │─────────>│                  │────────>│  llama3.2:3b │
│  MutationObserver│  GET     │  data.json       │<────────│              │
│  DOM extraction  │<─────────│  serves the UI   │  labels └──────────────┘
└──────────────────┘  /state  └──────────────────┘
```

`data.json` is the source of truth. The extension writes to
`chrome.storage.local` first, then pushes — so saving still works with the
server down. It polls `/api/state` every 4s and prunes anything deleted
server-side, which is how card deletion propagates back.

The server exists partly because a `file://` page is CORS-blocked from
calling Ollama directly.

### How the DOM reading works

Instagram's classnames (`x1lliihq x1plvlek …`) are build-generated and
change on every deploy, so nothing here selects on them. Instead:

- **`time[datetime]`** anchors each comment — one per comment, semantic,
  can't be obfuscated without breaking accessibility
- The climb from that anchor is **self-validating**: it tests each
  ancestor by trying to parse it, rather than assuming a nesting depth.
  This is why the same code works on both the modal and standalone post
  layouts
- **`div[role="dialog"]`** scopes scanning so the feed behind an open post
  doesn't bleed in
- **`a[href*="/liked_by/"]`** identifies the likes footer
- **`svg[aria-label="Verified"]`** identifies the blue tick
- Text is read via a `TreeWalker` over text nodes, not `innerText`, which
  merges inline siblings and glues the timestamp to the comment

---

## API

| Route | Purpose |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /api/data` | Accounts, cached analyses, connections |
| `GET /api/state` | Key list for extension reconciliation |
| `GET /api/export.csv` | CSV generated on demand |
| `POST /api/save/comment` | Called by the extension |
| `POST /api/save/profile` | Called by the extension |
| `POST /api/analyze` | Runs Ollama for one account |
| `POST /api/delete` | Removes an account everywhere |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Extension won't load | Check `manifest.json` sits at the top level of the selected folder, and that filenames have no leading spaces |
| No `[watcher]` console output | Reload the tab — extensions only inject on page load |
| Code changes not applying | Hit ↻ on the extension card **then** reload the tab |
| Shortcuts do nothing | Use the control-bar buttons; Chrome claims some `Alt` combos |
| `no comments parsed` | Press `Alt+D` — it walks each timestamp and prints what it found at every depth |
| Ollama 404 | Model tag mismatch — check `ollama list` and match `MODEL` exactly |
| Card shows `not analysed yet` | Connections compare positions, so both accounts must be analysed first |
| Dot is red | Server isn't running |

---

## Limitations

- Only comments Instagram has rendered are visible; it lazy-loads, so
  counts grow as you scroll
- Replies are flattened in with top-level comments
- The bio parser is the most fragile piece — display name and bio are both
  plain spans with no semantic marker between them
- A 3B model misreads sarcasm and code-mixed Hindi/English regularly.
  Prompt-level constraints are best-effort, not enforcement; the validated
  label sets are the real guardrail
- Verify labels before citing them anywhere that matters

---

## Data handling

`data.json` and the exported CSVs contain other people's personal data —
handles, bios, and comments. Keep them local:

```
data.json
analysis-cache.json
*.csv
```

Add that to `.gitignore` before pushing. If any of it has already been
committed, `git rm --cached` it.

Automated bulk collection violates Instagram's Terms of Service. Keeping
capture manual-trigger and small is what keeps this on the right side of
that line.
