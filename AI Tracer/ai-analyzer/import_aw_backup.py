import json, sqlite3, os

TRACKER_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker", "activity.db")
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "aw-backup")

def import_platform(platform, filename):
    path = os.path.join(BACKUP_DIR, filename)
    with open(path) as f:
        events = json.load(f)

    conn = sqlite3.connect(TRACKER_DB)
    inserted = 0
    for e in events:
        d = e.get("data", {})
        conn.execute(
            """INSERT INTO browser_events
               (platform, timestamp, duration, type, target, url, title, channel, caption)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                platform,
                e.get("timestamp"),
                e.get("duration", 0),
                d.get("type"),
                d.get("target"),
                d.get("url"),
                d.get("title"),
                d.get("channel"),
                d.get("caption_snippet"),
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    print(f"Imported {inserted} {platform} events")

if __name__ == "__main__":
    import_platform("instagram", "instagram_events.json")
    import_platform("youtube", "youtube_events.json")
