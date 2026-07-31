from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    kAXFocusedWindowAttribute,
    kAXTitleAttribute,
)

def get_active_window():
    app = NSWorkspace.sharedWorkspace().activeApplication()
    app_name = app.get('NSApplicationName')
    pid = app.get('NSApplicationProcessIdentifier')

    ax_app = AXUIElementCreateApplication(pid)

    err, window = AXUIElementCopyAttributeValue(ax_app, kAXFocusedWindowAttribute, None)
    if err != 0 or window is None:
        return app_name, f"(no focused window, err={err})"

    err, title = AXUIElementCopyAttributeValue(window, kAXTitleAttribute, None)
    if err != 0 or title is None:
        return app_name, f"(no title, err={err})"

    return app_name, str(title)

if __name__ == "__main__":
    app_name, title = get_active_window()
    print(f"App:   {app_name}")
    print(f"Title: {title}")
