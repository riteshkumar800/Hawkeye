import time
from test_ax import get_active_window

print("Switch to Chrome (or any app) now... capturing in 5 seconds")
time.sleep(5)
app_name, title = get_active_window()
print(f"App:   {app_name}")
print(f"Title: {title}")
