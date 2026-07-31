from AppKit import NSWorkspace
import subprocess

def get_frontmost_app():
    active_app = NSWorkspace.sharedWorkspace().activeApplication()
    return active_app['NSApplicationName']

def get_window_title_v2(app_name):
    script = f'''
    tell application "System Events"
        tell process "{app_name}"
            try
                set winList to windows
                if (count of winList) > 0 then
                    return name of item 1 of winList
                else
                    return "(no windows)"
                end if
            on error errMsg
                return "(error: " & errMsg & ")"
            end try
        end tell
    end tell
    '''
    try:
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=3)
        return result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return f"(exception: {e})", ""

if __name__ == "__main__":
    app = get_frontmost_app()
    title, err = get_window_title_v2(app)
    print(f"App: {app}")
    print(f"Window Title: {title}")
    if err:
        print(f"Stderr: {err}")
