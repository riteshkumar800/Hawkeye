from flask import Flask, request, jsonify, render_template
import json
import sqlite3
import os
from datetime import datetime
import analyzer_core

TRACKER_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tracker", "activity.db")

def init_browser_table():
    conn = sqlite3.connect(TRACKER_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS browser_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            duration REAL NOT NULL,
            type TEXT,
            target TEXT,
            url TEXT,
            title TEXT,
            channel TEXT,
            caption TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_browser_ts ON browser_events(timestamp)")
    conn.commit()
    conn.close()


app = Flask(__name__)
SCHEDULE_FILE = "schedule.json"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/schedule", methods=["GET"])
def get_schedule():
    with open(SCHEDULE_FILE, "r") as f:
        return jsonify(json.load(f))

@app.route("/api/schedule", methods=["POST"])
def save_schedule():
    new_schedule = request.json
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(new_schedule, f, indent=2)
    return jsonify({"success": True})


@app.route("/api/trusted-channels", methods=["GET"])
def get_trusted_channels():
    try:
        with open("trusted_channels.json", "r") as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({"study_channels": [], "entertainment_channels": []})

@app.route("/api/trusted-channels", methods=["POST"])
def save_trusted_channels():
    new_data = request.json
    with open("trusted_channels.json", "w") as f:
        json.dump(new_data, f, indent=2)
    return jsonify({"success": True})


@app.route("/api/ingest/<platform>", methods=["POST"])
def ingest(platform):
    """Receives events from our Chrome extensions - replaces the ActivityWatch server"""
    if platform not in ("instagram", "youtube"):
        return jsonify({"success": False, "error": "unknown platform"}), 400
    try:
        payload = request.json or {}
        data = payload.get("data", {})
        conn = sqlite3.connect(TRACKER_DB)
        conn.execute(
            """INSERT INTO browser_events
               (platform, timestamp, duration, type, target, url, title, channel, caption)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                platform,
                payload.get("timestamp"),
                payload.get("duration", 0),
                data.get("type"),
                data.get("target"),
                data.get("url"),
                data.get("title"),
                data.get("channel"),
                data.get("caption_snippet"),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/analyze", methods=["POST"])
def analyze():
    try:
        data = request.json or {}
        target_date = data.get("date")
        end_date = data.get("end_date")
        result = analyzer_core.run_full_analysis(target_date, end_date)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

init_browser_table()

if __name__ == "__main__":
    app.run(port=5050, debug=True)
