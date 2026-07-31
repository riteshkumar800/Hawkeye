import requests
import json
from datetime import datetime, time

AW_SERVER = "http://localhost:5600"
OLLAMA_SERVER = "http://localhost:11434"
MODEL = "llama3.2:3b"

def load_schedule():
    with open("schedule.json", "r") as f:
        return json.load(f)["blocks"]

def get_bucket_events(bucket_id, limit=500):
    url = f"{AW_SERVER}/api/0/buckets/{bucket_id}/events"
    try:
        resp = requests.get(url, params={"limit": limit})
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"Could not fetch bucket {bucket_id}: {e}")
        return []

def filter_today_events(events):
    today_local = datetime.now().astimezone().date()
    filtered = []
    for e in events:
        ts_utc = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        ts_local = ts_utc.astimezone()
        if ts_local.date() == today_local:
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

def classify_content(platform, event_data):
    """Ask the local AI whether this specific piece of content is study-related or entertainment"""
    if platform == "youtube":
        title = event_data.get("title", "")
        channel = event_data.get("channel", "")
        content_type = event_data.get("type", "")
        context = f"Type: {content_type}\nTitle: {title}\nChannel: {channel}"
    else:
        content_type = event_data.get("type", "")
        target = event_data.get("target", "")
        caption = event_data.get("caption_snippet", "")
        context = f"Type: {content_type}\nProfile/target: {target}\nCaption snippet: {caption}"

    prompt = f"""Classify social media content as "study", "entertainment", or "ambiguous".

Rules:
- "study" = educational content, tutorials, news/current affairs analysis, documentaries, skill-building, productivity, academic subjects, coding, science, exam prep
- "entertainment" = comedy, memes, celebrity gossip, music videos, gaming for fun, pranks, drama/reality TV, sports highlights for fun, personal social content (DMs, browsing friends' profiles/feeds/stories with no informational content)
- "ambiguous" = ONLY use this if there is truly no title, caption, or channel information at all to make any judgment, or if the content genuinely spans both categories equally

Examples:
- Title: "Python Tutorial for Beginners", Channel: "freeCodeCamp" -> study
- Title: "Try not to laugh challenge", Channel: "Funny Videos" -> entertainment
- Type: profile, Profile: "friend_username", Caption: "" -> entertainment
- Type: dm, no title -> entertainment
- Title: "Modi's WAR Against Indians - Political Analysis", Channel: "Dhruv Rathee" -> study
- Type: short, Title: "We won #shorts", Channel: "Taarak Mehta Ka Ooltah Chashmah" -> entertainment

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
            return "study"
        elif "entertainment" in result:
            return "entertainment"
        else:
            return "ambiguous"
    except Exception as e:
        print(f"Classification failed: {e}")
        return "ambiguous"

def analyze_drift(events, schedule, platform):
    """
    For each event, find its schedule block AND classify its content.
    Returns a nested structure: block_label -> {study: seconds, entertainment: seconds, ambiguous: seconds}
    """
    block_drift = {}
    unscheduled_time = {"study": 0, "entertainment": 0, "ambiguous": 0}

    for e in events:
        ts_utc = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
        ts_local = ts_utc.astimezone()
        duration = e.get("duration", 0)

        # Skip very short events (likely accidental/passthrough navigation)
        if duration < 2:
            continue

        classification = classify_content(platform, e["data"])
        matched_block = find_matching_block(ts_local, schedule)

        if matched_block:
            key = f"{matched_block['start']}-{matched_block['end']} ({matched_block['activity']})"
            if key not in block_drift:
                block_drift[key] = {"study": 0, "entertainment": 0, "ambiguous": 0}
            block_drift[key][classification] += duration
        else:
            unscheduled_time[classification] += duration

    return block_drift, unscheduled_time

def merge_drift(drift1, drift2):
    """Combine drift dicts from two platforms into one"""
    merged = dict(drift1)
    for key, values in drift2.items():
        if key not in merged:
            merged[key] = {"study": 0, "entertainment": 0, "ambiguous": 0}
        for cat in ["study", "entertainment", "ambiguous"]:
            merged[key][cat] += values[cat]
    return merged

def ask_ollama(prompt):
    resp = requests.post(
        f"{OLLAMA_SERVER}/api/generate",
        json={"model": MODEL, "prompt": prompt, "stream": False}
    )
    resp.raise_for_status()
    return resp.json()["response"]

def generate_report(schedule, block_drift):
    if not block_drift:
        return "No significant social media activity was recorded during any scheduled block today. Nice and focused day!"

    drift_lines = []
    for block_label, cats in block_drift.items():
        drift_lines.append(
            f"Block \"{block_label}\" had exactly these totals: "
            f"{round(cats['entertainment'], 1)} seconds of entertainment content, "
            f"{round(cats['study'], 1)} seconds of study-related content, "
            f"{round(cats['ambiguous'], 1)} seconds of ambiguous content. "
            f"No other block had any activity."
        )
    drift_text = "\n".join(drift_lines)

    prompt = f"""You are analyzing schedule adherence. There is EXACTLY ONE data point below - do not split it into multiple blocks or invent additional blocks.

{drift_text}

Write a short, honest, encouraging summary (3-5 sentences) about this single block only:
1. Name the block and state its three numbers clearly
2. If entertainment time is notable, gently flag it as a distraction from that specific block's purpose (unless the block name contains "Play" or "Leisure")
3. End with one practical suggestion tied specifically to that block

Do not mention any other time block. Do not invent a second block. There is only one block of data here."""

    return ask_ollama(prompt)

def main():
    print("Loading schedule...")
    schedule = load_schedule()

    print("Fetching Instagram activity...")
    ig_events_raw = get_bucket_events("aw-watcher-instagram")
    ig_events = filter_today_events(ig_events_raw)

    print("Fetching YouTube activity...")
    yt_events_raw = get_bucket_events("aw-watcher-youtube")
    yt_events = filter_today_events(yt_events_raw)

    # Plain raw summary - grouped by type, no AI involved, just addition
    print("\n--- Instagram Summary (today, raw) ---")
    ig_type_totals = {}
    for e in ig_events:
        t = e["data"].get("type", "unknown")
        ig_type_totals[t] = ig_type_totals.get(t, 0) + e.get("duration", 0)
    if ig_type_totals:
        for t, secs in sorted(ig_type_totals.items(), key=lambda x: -x[1]):
            print(f"  {t}: {round(secs, 1)}s")
    else:
        print("  No Instagram activity recorded today.")

    print("\n--- YouTube Summary (today, raw) ---")
    yt_type_totals = {}
    for e in yt_events:
        t = e["data"].get("type", "unknown")
        yt_type_totals[t] = yt_type_totals.get(t, 0) + e.get("duration", 0)
    if yt_type_totals:
        for t, secs in sorted(yt_type_totals.items(), key=lambda x: -x[1]):
            print(f"  {t}: {round(secs, 1)}s")
    else:
        print("  No YouTube activity recorded today.")

    print(f"\nClassifying {len(ig_events)} Instagram events and {len(yt_events)} YouTube events...")
    print("(This may take a minute since each event is classified individually by the local AI)\n")

    ig_drift, ig_unscheduled = analyze_drift(ig_events, schedule, "instagram")
    yt_drift, yt_unscheduled = analyze_drift(yt_events, schedule, "youtube")

    combined_drift = merge_drift(ig_drift, yt_drift)

    print("--- Combined Drift by Schedule Block (AI-classified) ---")
    if combined_drift:
        for block_label, cats in combined_drift.items():
            print(f"  {block_label}: entertainment={round(cats['entertainment'],1)}s, study={round(cats['study'],1)}s, ambiguous={round(cats['ambiguous'],1)}s")
    else:
        print("  No drift detected during any scheduled block.")

    print("\nAsking local AI to generate final report... (this may take 10-30 seconds)")
    report = generate_report(schedule, combined_drift)

    print("\n=== YOUR DAILY REPORT ===\n")
    print(report)

if __name__ == "__main__":
    main()
