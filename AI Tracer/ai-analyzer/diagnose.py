from ApplicationServices import AXIsProcessTrusted
from AppKit import NSWorkspace
import Quartz

print("=== 1. Accessibility trust ===")
print("AXIsProcessTrusted:", AXIsProcessTrusted())

print("\n=== 2. Frontmost app (NSWorkspace) ===")
app = NSWorkspace.sharedWorkspace().activeApplication()
print("Name:", app.get('NSApplicationName'))
print("PID:", app.get('NSApplicationProcessIdentifier'))

print("\n=== 3. Quartz window list (first 15 on-screen windows) ===")
opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
windows = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
count = 0
for w in windows:
    owner = w.get('kCGWindowOwnerName', '')
    name = w.get('kCGWindowName', None)
    layer = w.get('kCGWindowLayer', 0)
    if layer == 0:
        print(f"  owner={owner!r}  title={name!r}")
        count += 1
        if count >= 15:
            break
if count == 0:
    print("  (no layer-0 windows found)")
