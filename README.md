# Hawkeye & AI Activity Tracer

A dual-purpose local productivity and activity intelligence suite consisting of two privacy-focused tools: **Hawkeye** (IG DOM Watcher) and **AI Activity Tracer**.

> Everything runs locally on your machine. No cloud API, no credentials, no automated collection — your personal data and activity logs never leave your device.

---

## Table of Contents

- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Setup](#setup)
  - [1. Pull the Model](#1-pull-the-model)
  - [2. Setting up Hawkeye (IG DOM Watcher)](#2-setting-up-hawkeye-ig-dom-watcher)
  - [3. Setting up AI Activity Tracer](#3-setting-up-ai-activity-tracer)
- [Usage](#usage)
  - [1. Hawkeye (IG DOM Watcher)](#1-hawkeye-ig-dom-watcher-1)
  - [2. AI Activity Tracer](#2-ai-activity-tracer-1)
- [The Dashboards](#the-dashboards)
- [Architecture](#architecture)
- [API](#api)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Data Handling](#data-handling)

---

## Project Structure

```
Hawkeye/
├── Hawkeye/                     # IG DOM Watcher project
│   ├── manifest.json            # Chrome extension manifest (MV3)
│   ├── content.js                # Content script — reads the Instagram DOM
│   ├── dashboard.py              # HTTP server, dashboard UI, Ollama analysis
│   ├── data.json                 # Local data store (auto-generated)
│   └── analysis-cache.json       # Cached LLM analysis (auto-generated)
│
└── AI Tracer/                   # AI Activity Tracer project
    ├── ai-analyzer/              # Core analysis engine & Web UI
    │   ├── app.py                 # Flask server and API endpoints
    │   ├── analyzer.py            # Main analysis routines
    │   ├── analyzer_core.py       # LLM classification & schedule drift engine
    │   ├── schedule.json          # User schedule configuration
    │   ├── trusted_channels.json  # Bypassed/whitelisted YouTube channels
    │   ├── trusted_apps.json      # Whitelisted/classified desktop applications
    │   └── templates/
    │       └── index.html         # UI dashboard for daily routine adherence
    │
    ├── tracker/                  # Native macOS window activity watcher
    │   ├── tracker.py             # Quartz/AppKit background window logger
    │   └── activity.db            # Local SQLite database storing application usage
    │
    ├── aw-watcher-instagram/     # Dedicated Instagram browser activity extension
    └── aw-watcher-youtube/       # Dedicated YouTube browser activity extension
```

---

## Requirements

- Google Chrome
- Python 3.9+
- [Ollama](https://ollama.com/) with a local model
- macOS (required for `tracker.py` native desktop window tracking)

---

## Setup

### 1. Pull the Model

```bash
ollama pull llama3.2:3b
```

To use a different model, update the `MODEL` string near the top of:
- `Hawkeye/dashboard.py`
- `AI Tracer/ai-analyzer/analyzer_core.py`

to match a name from `ollama list`.

### 2. Setting up Hawkeye (IG DOM Watcher)

**Start the Hawkeye dashboard**

```bash
cd Hawkeye
python3 dashboard.py
```

The server starts on `http://localhost:8765` and opens your browser.

**Load the Hawkeye extension**

1. Open `chrome://extensions/`
2. Enable **Developer mode** (top-right)
3. Click **Load unpacked** (top-left)
4. Select the `Hawkeye` folder
5. Reload any open Instagram tab

### 3. Setting up AI Activity Tracer

**Install Python macOS dependencies**

```bash
pip install pyobjc requests flask
```

**Grant macOS Accessibility Permissions**

To allow `tracker.py` to capture active application window titles:

1. Open **System Settings → Privacy & Security → Accessibility**
2. Enable **Terminal** (or your Python environment)

**Start the background desktop tracker**

```bash
cd "AI Tracer"
python3 tracker/tracker.py
```

**Start the AI Tracer web backend**

In a separate terminal tab:

```bash
cd "AI Tracer/ai-analyzer"
python3 app.py
```

The dashboard will be available at `http://localhost:5050`.

**Load the Chrome extensions**

1. Open `chrome://extensions/`
2. Click **Load unpacked**
3. Select `AI Tracer/aw-watcher-instagram` and `AI Tracer/aw-watcher-youtube` individually

---

## Usage

### 1. Hawkeye (IG DOM Watcher)

A floating control bar appears at the bottom-left of Instagram.

**Saving**

| Action | How |
|---|---|
| Save a comment | Alt + click the comment text (saves author, text, timestamp, post URL, post author, post caption) |
| Save a profile | Alt+S, or **Save profile** button (saves handle, display name, bio, verified status) |

**Control bar**

- **Counter** — Live totals from local storage
- **Save profile** — Same as Alt+S (profile pages only)
- **Report** — Prints grouped tables to the browser console
- **CSV** — Downloads `instagram-data.csv`
- **Clear** — Wipes all local extension storage

**Keyboard shortcuts**

| Shortcut | Action |
|---|---|
| Alt + click | Save a comment |
| Alt+S | Save the current profile |
| Alt+R | Print report to console |
| Alt+L | Print raw tables to console |
| Alt+E | Export CSV |
| Alt+K | Clear storage |
| Alt+D | Diagnose DOM parsing on a post page |

### 2. AI Activity Tracer

Track daily screen time across desktop apps and browsers, comparing actual usage against your defined routine schedule.

**Dashboard features** (`http://localhost:5050`)

- **Routine Adherence Score (%)** — Quantifies how faithfully you stuck to your scheduled routine blocks throughout the day
- **Productivity Score (%)** — Tracks total productive work time across tools (VS Code, Claude, Terminal, Notes, etc.)
- **Desktop Apps Breakdown (Others)** — Displays time spent across all active applications (Claude, Finder, Terminal, System Settings, Notes, VS Code)
- **Instagram & YouTube Granular Trackers** — Monitors specific content types (Reels, Feed, Shorts, Channels)
- **Schedule Drift Analysis** — Compares activity inside time blocks (e.g., 10:00–12:00 Study) and flags distractions
- **AI Coaching Report** — Local LLM-generated feedback offering actionable advice tailored to your day

---

## The Dashboards

### Hawkeye Dashboard (`http://localhost:8765`)

One card per saved account, updating live as you save. Each card shows:

- Handle, display name, verified badge, bio, profile link
- Every saved comment with its timestamp, post link, and post caption
- **Also commented on the same posts** — other saved accounts that appeared under the same post
- **Delete** — removes the account from the store, the analysis cache, and the extension

**Analyze**

Click **Analyze** on any card to run that account's comments through Ollama. Results are cached in `analysis-cache.json`.

### AI Tracer Dashboard (`http://localhost:5050`)

Allows setting time blocks (e.g., Study, Leisure, Health, Rest) and triggers AI-driven daily evaluations by clicking **Analyze My Day**.

---

## Architecture

### Hawkeye Data Flow

```
   Instagram tab               localhost:8765             localhost:11434
┌──────────────────┐          ┌──────────────────┐         ┌──────────────┐
│  content.js      │   POST   │  dashboard.py    │   POST  │  Ollama      │
│                  │─────────>│                  │────────>│  llama3.2:3b │
│  MutationObserver│   GET    │  data.json       │<────────│              │
│  DOM extraction  │<─────────│  serves the UI   │  labels └──────────────┘
└──────────────────┘  /state  └──────────────────┘
```

### AI Activity Tracer Data Flow

```
 Desktop Apps & Chrome        SQLite Database            localhost:5050            localhost:11434
┌─────────────────────┐      ┌─────────────────┐       ┌─────────────────┐        ┌──────────────┐
│ tracker.py (macOS)  │─────>│                 │       │ ai-analyzer/    │  POST  │ Ollama       │
│ aw-watcher-instagram│─────>│   activity.db   │──────>│ app.py          │───────>│ llama3.2:3b  │
│ aw-watcher-youtube  │─────>│                 │       │ analyzer_core.py│<───────│              │
└─────────────────────┘      └─────────────────┘       └─────────────────┘ labels └──────────────┘
```

---

## API

### Hawkeye API (`http://localhost:8765`)

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard HTML |
| `GET /api/data` | Accounts, cached analyses, connections |
| `GET /api/state` | Key list for extension reconciliation |
| `GET /api/export.csv` | CSV generated on demand |
| `POST /api/save/comment` | Called by the extension |
| `POST /api/save/profile` | Called by the extension |
| `POST /api/analyze` | Runs Ollama for one account |
| `POST /api/delete` | Removes an account everywhere |

### AI Activity Tracer API (`http://localhost:5050`)

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard HTML UI |
| `GET /api/schedule` | Fetches configured schedule blocks |
| `POST /api/schedule` | Saves schedule block updates |
| `GET /api/trusted-channels` | Reads whitelisted YouTube channels |
| `POST /api/trusted-channels` | Saves channel whitelist updates |
| `POST /api/ingest/<platform>` | Receives event streams from browser extensions |
| `POST /api/analyze` | Triggers full-day productivity & adherence analysis |

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Extension won't load | Check `manifest.json` sits at the top level of the selected folder |
| No `[watcher]` console output | Reload the tab — extensions only inject on page load |
| Shortcuts do nothing | Use the control-bar buttons; Chrome claims some Alt combos |
| No comments parsed | Press Alt+D — it walks each timestamp and prints what it found at every depth |
| Ollama 404 | Model tag mismatch — check `ollama list` and match `MODEL` exactly |
| Desktop apps not showing | Ensure `tracker.py` is running and Terminal/Python has Accessibility permission in macOS System Settings |
| Double counted time | Ensure latest `analyzer_core.py` is running (caps max duration to schedule block limits) |

---

## Limitations

- Hawkeye only parses comments Instagram has rendered (lazy-loaded)
- Desktop tracking depends on native macOS Quartz/AppKit APIs (`tracker.py`)
- A 3B model misreads sarcasm and code-mixed Hindi/English regularly — prompt-level constraints are best-effort

---

## Data Handling

Local databases, data stores, and generated export files contain personal usage metrics and data. Keep them local.
