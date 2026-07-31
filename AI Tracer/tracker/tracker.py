"""
Native macOS activity tracker.
Polls the frontmost app + window title and idle time, writes to SQLite DB.
Features robust Quartz window title fallback for system apps (Finder, Settings, Notes, Claude).
"""

import sqlite3
import time
import os
from datetime import datetime, timezone

from AppKit import NSWorkspace
import Quartz
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    kAXFocusedWindowAttribute,
    kAXTitleAttribute,
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "activity.db")
POLL_INTERVAL = 1        # Poll every 1s for accurate quick switches
FLUSH_INTERVAL = 10      # Save current running duration to DB every 10s
AFK_THRESHOLD = 120      # seconds of no input before marking away


# ---------- database ----------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS window_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            duration REAL NOT NULL,
            app TEXT NOT NULL,
            title TEXT,
            afk INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON window_events(timestamp)")
    conn.commit()
    return conn


def save_event(conn, start_time, duration, app, title, afk):
    if duration < 0.5:
        return
    conn.execute(
        "INSERT INTO window_events (timestamp, duration, app, title, afk) VALUES (?, ?, ?, ?, ?)",
        (
            datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
            round(duration, 2),
            app,
            title or "",
            1 if afk else 0,
        ),
    )
    conn.commit()


# ---------- macOS probes ----------

def get_idle_seconds():
    try:
        return Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateHIDSystemState,
            Quartz.kCGAnyInputEventType,
        )
    except Exception:
        return 0


def get_window_title_quartz(pid):
    """Fallback window title lookup using Quartz Window List"""
    try:
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kNullWindowID
        )
        for window in window_list:
            if window.get(Quartz.kCGWindowOwnerPID) == pid:
                title = window.get(Quartz.kCGWindowName, "")
                if title:
                    return str(title)
    except Exception:
        pass
    return ""


def get_active_window():
    """Returns (app_name, window_title). Robust against AXUIElement accessibility failures."""
    try:
        app = NSWorkspace.sharedWorkspace().activeApplication()
        if not app:
            return None, ""

        app_name = app.get("NSApplicationName")
        pid = app.get("NSApplicationProcessIdentifier")
        if not app_name or pid is None:
            return None, ""

        # Normalize common app bundle names for standard displays
        app_name_map = {
            "com.apple.systemsettings": "System Settings",
            "System Settings": "Settings",
            "Activity Monitor": "Activity Monitor",
            "Finder": "Finder",
            "Notes": "Notes",
            "Terminal": "Terminal",
            "Claude": "Claude"
        }
        app_name = app_name_map.get(app_name, app_name)

        # Primary accessibility lookup
        title = ""
        ax_app = AXUIElementCreateApplication(pid)
        err, window = AXUIElementCopyAttributeValue(ax_app, kAXFocusedWindowAttribute, None)
        if err == 0 and window is not None:
            err, val = AXUIElementCopyAttributeValue(window, kAXTitleAttribute, None)
            if err == 0 and val is not None:
                title = str(val)

        # Quartz Fallback if title is still empty
        if not title:
            title = get_window_title_quartz(pid)

        return app_name, title
    except Exception as e:
        print(f"[tracker] probe error: {e}")
        return None, ""


# ---------- main loop ----------

def main():
    conn = init_db()
    print(f"[tracker] Running native watcher. Database: {DB_PATH}")
    print("[tracker] Press Ctrl+C to stop")

    current = None          # (app, title, afk)
    current_start = time.time()
    last_flush = time.time()

    try:
        while True:
            idle = get_idle_seconds()
            afk = idle > AFK_THRESHOLD
            app, title = get_active_window()

            if app is None:
                time.sleep(POLL_INTERVAL)
                continue

            state = (app, title, afk)
            now = time.time()

            if current is None:
                current = state
                current_start = now
                last_flush = now
            elif state != current:
                # App/Window switched: save accumulated span for the previous app
                duration = now - current_start
                save_event(conn, current_start, duration, current[0], current[1], current[2])
                status = "AFK" if current[2] else "active"
                print(f"[tracker] Saved: {current[0]!r} | {current[1][:50]!r} | {duration:.1f}s ({status})")
                
                current = state
                current_start = now
                last_flush = now
            else:
                # Same app running: periodically flush long active spans every FLUSH_INTERVAL
                if (now - last_flush) >= FLUSH_INTERVAL:
                    duration = now - current_start
                    save_event(conn, current_start, duration, current[0], current[1], current[2])
                    current_start = now
                    last_flush = now

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        if current is not None:
            duration = time.time() - current_start
            save_event(conn, current_start, duration, current[0], current[1], current[2])
        conn.close()
        print("\n[tracker] Stopped. Final event saved.")


if __name__ == "__main__":
    main()