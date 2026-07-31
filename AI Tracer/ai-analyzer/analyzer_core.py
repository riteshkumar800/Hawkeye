import requests
import json
import sqlite3 as _sqlite3
import os as _os
from datetime import datetime, time, date

AW_SERVER = "http://localhost:5600"
OLLAMA_SERVER = "http://localhost:11434"
MODEL = "llama3.2:3b"

TRACKER_DB = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "tracker", "activity.db")
CACHE_FILE = "classification_cache.json"


def format_duration(seconds):
    """Convert raw seconds into a readable Xh Ym Zs format"""
    seconds = int(round(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def load_schedule():
    with open("schedule.json", "r") as f:
        return json.load(f)["blocks"]


def load_trusted_channels():
    try:
        with open("trusted_channels.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"study_channels": [], "entertainment_channels": []}


def load_trusted_apps():
    try:
        with open("trusted_apps.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"study_apps": [], "study_title_keywords": [], "entertainment_apps": [], "ignore_apps": []}


def load_classification_cache():
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_classification_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def cache_key(platform, event_data):
    """A stable identifier for a given piece of content, so we can cache its classification"""
    if platform == "youtube":
        return f"yt:{event_data.get('target', '')}"
    else:
        return f"ig:{event_data.get('type', '')}:{event_data.get('target', '')}"


def check_trusted_channel(channel_name, trusted):
    """Check if a channel name matches a trusted list entry (case-insensitive, partial match)"""
    if not channel_name:
        return None
    channel_lower = channel_name.lower().strip()
    for study_ch in trusted.get("study_channels", []):
        if study_ch.lower() in channel_lower or channel_lower in study_ch.lower():
            return "study"
    for ent_ch in trusted.get("entertainment_channels", []):
        if ent_ch.lower() in channel_lower or channel_lower in ent_ch.lower():
            return "entertainment"
    return None


def get_browser_events_db(platform):
    """Read Instagram/YouTube events from SQLite DB"""
    try:
        conn = _sqlite3.connect(TRACKER_DB)
        rows = conn.execute(
            """SELECT timestamp, duration, type, target, url, title, channel, caption
               FROM browser_events WHERE platform = ?""",
            (platform,),
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"Could not read browser_events for {platform}: {e}")
        return []

    events = []
    for ts, dur, typ, target, url, title, channel, caption in rows:
        events.append({
            "timestamp": ts,
            "duration": dur or 0,
            "data": {
                "type": typ,
                "target": target,
                "url": url,
                "title": title,
                "channel": channel,
                "caption_snippet": caption,
            },
        })
    return events


def get_window_events_db(exclude_afk=True):
    """Read desktop window events from SQLite DB, filtering out sleep/AFK periods"""
    try:
        conn = _sqlite3.connect(TRACKER_DB)
        query = "SELECT timestamp, duration, app, title FROM window_events WHERE duration < 1800"
        if exclude_afk:
            query += " AND afk = 0"
        rows = conn.execute(query).fetchall()
        conn.close()
    except Exception as e:
        print(f"Could not read window_events: {e}")
        return []

    events = []
    for ts, dur, app, title in rows:
        events.append({
            "timestamp": ts,
            "duration": dur or 0,
            "data": {"app": app or "Unknown", "title": title or ""},
        })
    return events


def filter_events_by_date_range(events, start_date, end_date):
    filtered = []
    for e in events:
        ts_utc = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        ts_local = ts_utc.astimezone().date()
        if start_date <= ts_local <= end_date:
            filtered.append(e)
    return filtered


def parse_time_str(t_str):
    hours, minutes = map(int, t_str.split(":"))
    return time(hour=hours, minute=minutes)


def find_matching_block(event_local_time, schedule):
    event_time_only = event_local_time.time()
    for block in schedule:
        start = parse_time_str(block["start"])
        end = parse_time_str(block["end"])
        if start <= event_time_only < end:
            return block
    return None


def classify_desktop_event(event_data, trusted_apps):
    """
    Classifies ANY active desktop window so all apps (Claude, Finder, Notes, Settings, etc.) appear.
    """
    app = event_data.get("app", "Unknown")
    title = event_data.get("title", "").lower()

    if app in trusted_apps.get("ignore_apps", []):
        return None, None

    # Check user explicitly declared study/entertainment apps
    if app in trusted_apps.get("study_apps", []):
        return "study", app
    if app in trusted_apps.get("entertainment_apps", []):
        return "entertainment", app

    for keyword in trusted_apps.get("study_title_keywords", []):
        if keyword in title:
            return "study", f"{app} ({keyword})"

    # Auto-classify software engineering, coding, and LLM workspace tools as study
    study_defaults = [
        "Visual Studio Code", "Code", "Terminal", "iTerm2", "Claude", "ChatGPT", 
        "Xcode", "Sublime Text", "PyCharm", "IntelliJ IDEA", "Notes", "Obsidian", "Notion"
    ]
    if any(s.lower() in app.lower() for s in study_defaults):
        return "study", app

    # Utility & system tools mapped as ambiguous
    system_defaults = ["Finder", "System Settings", "Settings", "Activity Monitor", "Calculator"]
    if any(sys_app.lower() in app.lower() for sys_app in system_defaults):
        return "ambiguous", app

    # Default fallback: return app name as ambiguous so ALL apps appear in usage list
    return "ambiguous", app


def classify_content(platform, event_data, trusted_channels=None, cache=None):
    if trusted_channels is None:
        trusted_channels = load_trusted_channels()

    if cache is not None:
        key = cache_key(platform, event_data)
        if key in cache:
            return cache[key]

    if platform == "youtube":
        title = event_data.get("title", "")
        channel = event_data.get("channel", "")
        content_type = event_data.get("type", "")

        trusted_result = check_trusted_channel(channel, trusted_channels)
        if trusted_result:
            if cache is not None:
                cache[cache_key(platform, event_data)] = trusted_result
            return trusted_result

        context = f"Type: {content_type}\nTitle: {title}\nChannel: {channel}"
    else:
        content_type = event_data.get("type", "")
        target = event_data.get("target", "")
        caption = event_data.get("caption_snippet", "")
        context = f"Type: {content_type}\nProfile/target: {target}\nCaption snippet: {caption}"

    prompt = f"""Classify social media content as "study", "entertainment", or "ambiguous".

Rules:
- "study" = educational content, tutorials, news/current affairs analysis, documentaries, skill-building, productivity, academic subjects, coding, science, exam prep
- "entertainment" = comedy, memes, celebrity gossip, music videos, gaming for fun, pranks, drama/reality TV, sports highlights for fun, personal social content
- "ambiguous" = ONLY use this if there is truly no title, caption, or channel information at all to make any judgment

Now classify this:
Platform: {platform}
{context}

Respond with ONLY one word: study, entertainment, or ambiguous."""

    try:
        resp = requests.post(
            f"{OLLAMA_SERVER}/api/generate",
            json={"model": MODEL, "prompt": prompt, "stream": False}
        )
        resp.raise_for_status()
        result = resp.json()["response"].strip().lower()
        if "study" in result:
            classification = "study"
        elif "entertainment" in result:
            classification = "entertainment"
        else:
            classification = "ambiguous"

        if cache is not None:
            cache[cache_key(platform, event_data)] = classification
        return classification
    except Exception as e:
        print(f"Classification failed: {e}")
        return "ambiguous"


def analyze_drift(events, schedule, platform, cache=None):
    trusted_channels = load_trusted_channels()
    if cache is None:
        cache = load_classification_cache()

    block_drift = {}
    unscheduled_time = {"study": 0, "entertainment": 0, "ambiguous": 0}

    for e in events:
        ts_utc = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        ts_local = ts_utc.astimezone()
        duration = e.get("duration", 0)

        if duration < 1:
            continue

        classification = classify_content(platform, e["data"], trusted_channels, cache)
        matched_block = find_matching_block(ts_local, schedule)

        if matched_block:
            key = f"{matched_block['start']}-{matched_block['end']} ({matched_block['activity']})"
            if key not in block_drift:
                block_drift[key] = {"study": 0, "entertainment": 0, "ambiguous": 0}
            block_drift[key][classification] += duration
        else:
            unscheduled_time[classification] += duration

    return block_drift, unscheduled_time


def analyze_desktop_activity(events, schedule, trusted_apps=None):
    if trusted_apps is None:
        trusted_apps = load_trusted_apps()

    app_totals = {}
    block_drift = {}

    for e in events:
        duration = e.get("duration", 0)
        if duration < 0.5:  # Count even short app switches
            continue

        app_name = e["data"].get("app", "Unknown App")
        classification, label = classify_desktop_event(e["data"], trusted_apps)
        
        if classification is None:
            continue

        display_name = label if label else app_name
        app_totals[display_name] = app_totals.get(display_name, 0) + duration

        ts_utc = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        ts_local = ts_utc.astimezone()
        matched_block = find_matching_block(ts_local, schedule)
        if matched_block:
            key = f"{matched_block['start']}-{matched_block['end']} ({matched_block['activity']})"
            if key not in block_drift:
                block_drift[key] = {"study": 0, "entertainment": 0, "ambiguous": 0}
            cat = classification if classification else "ambiguous"
            block_drift[key][cat] += duration

    return app_totals, block_drift


def get_block_max_seconds(block_label):
    """Parses '13:00-15:00 (Study)' and returns maximum possible seconds (e.g. 7200s for 2h)"""
    try:
        time_part = block_label.split(" (")[0]
        start_str, end_str = time_part.split("-")
        s_h, s_m = map(int, start_str.split(":"))
        e_h, e_m = map(int, end_str.split(":"))
        start_sec = s_h * 3600 + s_m * 60
        end_sec = e_h * 3600 + e_m * 60
        diff = end_sec - start_sec
        return diff if diff > 0 else 86400
    except Exception:
        return 86400


def merge_drift(drift1, drift2):
    """Combine drift dicts from two sources while capping maximum time to actual block duration"""
    merged = dict(drift1)
    for key, values in drift2.items():
        if key not in merged:
            merged[key] = {"study": 0, "entertainment": 0, "ambiguous": 0}
        for cat in ["study", "entertainment", "ambiguous"]:
            merged[key][cat] += values[cat]

    # CAPPING STEP: Normalize cumulative multi-source tracking so total seconds never exceed real block duration
    for key, cats in merged.items():
        max_allowed = get_block_max_seconds(key)
        total_time = cats["study"] + cats["entertainment"] + cats["ambiguous"]
        if total_time > max_allowed:
            scale = max_allowed / total_time
            cats["study"] = round(cats["study"] * scale, 2)
            cats["entertainment"] = round(cats["entertainment"] * scale, 2)
            cats["ambiguous"] = round(cats["ambiguous"] * scale, 2)

    return merged


def ask_ollama(prompt):
    resp = requests.post(
        f"{OLLAMA_SERVER}/api/generate",
        json={"model": MODEL, "prompt": prompt, "stream": False}
    )
    resp.raise_for_status()
    return resp.json()["response"]


def get_block_category_map(schedule):
    mapping = {}
    for b in schedule:
        label = f"{b['start']}-{b['end']} ({b['activity']})"
        mapping[label] = b.get("category", "other")
    return mapping


def generate_report(schedule, block_drift):
    if not block_drift:
        return "No significant activity recorded during any scheduled block today. Nice and focused day!"

    category_map = get_block_category_map(schedule)
    report_parts = []

    for block_label, cats in block_drift.items():
        category = category_map.get(block_label, "other")

        facts = (
            f"**{block_label}**\n"
            f"Entertainment: {format_duration(cats['entertainment'])} | "
            f"Study: {format_duration(cats['study'])} | "
            f"Ambiguous: {format_duration(cats['ambiguous'])}\n"
        )

        ent = cats["entertainment"]
        study = cats["study"]

        if category == "study":
            if ent > study:
                situation = "This is a STUDY block, but entertainment content dominated. This is a real distraction from the planned study goal."
            elif study > 0:
                situation = "This is a STUDY block, and study-related content was the main activity. This is on track with the plan."
            else:
                situation = "This is a STUDY block with minimal or ambiguous activity."
        elif category == "leisure":
            if ent > 0:
                situation = "This is a LEISURE/PLAY block, so entertainment content here is expected and healthy."
            elif study > 0:
                situation = "This is a LEISURE/PLAY block, but the user chose study-related content anyway."
            else:
                situation = "This is a LEISURE/PLAY block with minimal activity."
        else:
            if ent > study:
                situation = f"This block's purpose is '{category}' (an offline routine like breakfast/rest), but entertainment content pulled attention away."
            elif study > 0:
                situation = f"This block's purpose is '{category}' (an offline activity), but the user spent time studying instead."
            else:
                situation = f"This block's purpose is '{category}' with minimal device usage."

        prompt = f"""{situation}

Write ONLY 1-2 sentences of practical, encouraging advice or praise matching this exact situation. Do NOT restate the numbers."""

        advice = ask_ollama(prompt).strip()
        report_parts.append(facts + "\n" + advice)

    return "\n\n".join(report_parts)


def calculate_routine_adherence(combined_drift, schedule):
    """Calculates overall productivity percentage and routine schedule adherence percentage."""
    total_tracked_time = 0
    productive_time = 0
    on_routine_time = 0

    category_map = get_block_category_map(schedule)

    for block_label, cats in combined_drift.items():
        block_total = cats["study"] + cats["entertainment"] + cats["ambiguous"]
        total_tracked_time += block_total
        productive_time += cats["study"]

        category = category_map.get(block_label, "other")

        # Check if activity matched the block's intended category
        if category == "study":
            on_routine_time += cats["study"]
        elif category in ["leisure", "rest", "health", "personal"]:
            on_routine_time += (cats["entertainment"] + cats["ambiguous"])

    productivity_pct = round((productive_time / total_tracked_time * 100), 1) if total_tracked_time > 0 else 0
    routine_adherence_pct = round((on_routine_time / total_tracked_time * 100), 1) if total_tracked_time > 0 else 0

    return {
        "total_tracked_time": format_duration(total_tracked_time),
        "productive_time": format_duration(productive_time),
        "productivity_percentage": f"{productivity_pct}%",
        "routine_adherence_percentage": f"{routine_adherence_pct}%",
        "raw_productivity_pct": productivity_pct,
        "raw_routine_pct": routine_adherence_pct
    }


def run_full_analysis(target_date_str=None, end_date_str=None):
    """Runs complete analysis and returns app usage, drift metrics, and productivity scores"""
    if target_date_str:
        start_date = date.fromisoformat(target_date_str)
    else:
        start_date = datetime.now().astimezone().date()

    if end_date_str:
        end_date = date.fromisoformat(end_date_str)
    else:
        end_date = start_date

    schedule = load_schedule()
    cache = load_classification_cache()

    ig_events_raw = get_browser_events_db("instagram")
    ig_events = filter_events_by_date_range(ig_events_raw, start_date, end_date)

    yt_events_raw = get_browser_events_db("youtube")
    yt_events = filter_events_by_date_range(yt_events_raw, start_date, end_date)

    ig_type_totals = {}
    for e in ig_events:
        t = e["data"].get("type", "unknown")
        ig_type_totals[t] = ig_type_totals.get(t, 0) + e.get("duration", 0)

    yt_type_totals = {}
    for e in yt_events:
        t = e["data"].get("type", "unknown")
        yt_type_totals[t] = yt_type_totals.get(t, 0) + e.get("duration", 0)

    ig_drift, ig_unscheduled = analyze_drift(ig_events, schedule, "instagram", cache)
    yt_drift, yt_unscheduled = analyze_drift(yt_events, schedule, "youtube", cache)

    save_classification_cache(cache)

    # Desktop app tracking (Claude, VS Code, Notes, Finder, Terminal, etc.)
    desktop_events_raw = get_window_events_db(exclude_afk=True)
    desktop_events = filter_events_by_date_range(desktop_events_raw, start_date, end_date)
    desktop_app_totals, desktop_drift = analyze_desktop_activity(desktop_events, schedule)

    combined_drift = merge_drift(ig_drift, yt_drift)
    combined_drift = merge_drift(combined_drift, desktop_drift)

    combined_unscheduled = {
        "entertainment": ig_unscheduled["entertainment"] + yt_unscheduled["entertainment"],
        "study": ig_unscheduled["study"] + yt_unscheduled["study"],
        "ambiguous": ig_unscheduled["ambiguous"] + yt_unscheduled["ambiguous"]
    }

    metrics = calculate_routine_adherence(combined_drift, schedule)
    report = generate_report(schedule, combined_drift)

    ig_summary_formatted = {k: format_duration(v) for k, v in ig_type_totals.items()}
    yt_summary_formatted = {k: format_duration(v) for k, v in yt_type_totals.items()}

    block_drift_formatted = {}
    for block_label, cats in combined_drift.items():
        block_drift_formatted[block_label] = {
            "entertainment": format_duration(cats["entertainment"]),
            "study": format_duration(cats["study"]),
            "ambiguous": format_duration(cats["ambiguous"]),
            "entertainment_raw": cats["entertainment"],
            "study_raw": cats["study"],
            "ambiguous_raw": cats["ambiguous"]
        }

    unscheduled_formatted = {
        "entertainment": format_duration(combined_unscheduled["entertainment"]),
        "study": format_duration(combined_unscheduled["study"]),
        "ambiguous": format_duration(combined_unscheduled["ambiguous"])
    }

    desktop_app_totals_formatted = {k: format_duration(v) for k, v in desktop_app_totals.items()}

    return {
        "metrics": metrics,
        "others_summary": desktop_app_totals,
        "others_summary_formatted": desktop_app_totals_formatted,
        "target_date": str(start_date),
        "end_date": str(end_date),
        "instagram_summary": ig_type_totals,
        "instagram_summary_formatted": ig_summary_formatted,
        "youtube_summary": yt_type_totals,
        "youtube_summary_formatted": yt_summary_formatted,
        "block_drift": block_drift_formatted,
        "unscheduled": combined_unscheduled,
        "unscheduled_formatted": unscheduled_formatted,
        "report": report,
        "schedule": schedule
    }