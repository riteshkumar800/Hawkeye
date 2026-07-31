import Quartz

def get_idle_seconds():
    """Seconds since the last keyboard or mouse input"""
    return Quartz.CGEventSourceSecondsSinceLastEventType(
        Quartz.kCGEventSourceStateHIDSystemState,
        Quartz.kCGAnyInputEventType
    )

if __name__ == "__main__":
    print(f"Idle for: {get_idle_seconds():.1f} seconds")
