from AppKit import NSWorkspace
import subprocess

def get_frontmost_app():
    active_app = NSWorkspace.sharedWorkspace().activeApplication()
    return active_app['NSApplicationName']

def get_window_title(app_name):
    """Use AppleScript via System Events to get the frontmost window title"""
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            try
                return name of front window
            on error
                return ""
            end try
        end tell
    end tell
    '''
    try:
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=2)
        return result.stdout.strip()
    except Exception as e:
        return f"(error: {e})"

if __name__ == "__main__":
    app = get_frontmost_app()
    title = get_window_title(app)
    print(f"App: {app}")
    print(f"Window Title: {title}")
