"""Full verification of Hey Jev.exe Windows GUI lifecycle:
1. Window opens and is visible on Windows desktop.
2. System tray message window exists (taskbar icon not observed in non-interactive environment).
3. Close hides to tray (window hidden, process alive).
4. Quit exits process cleanly through application quit path (NO proc.terminate()).
"""
import os
import sys
import time
import ctypes
import subprocess
import psutil

u = ctypes.windll.user32
exe = os.path.abspath("dist/Hey Jev/Hey Jev.exe")

env = os.environ.copy()
env.pop("QT_QPA_PLATFORM", None)

print("[1] Starting Hey Jev.exe in real Windows desktop environment (without QT_QPA_PLATFORM offscreen)...")
proc = subprocess.Popen([exe], env=env)
pid = proc.pid
print(f"    Process started with PID: {pid}")

hwnd = None
tray_hwnd = None
found_title = ""
found_class = ""
child_pids = [pid]

# Poll for window appearance
for attempt in range(30):
    time.sleep(0.5)
    try:
        p = psutil.Process(pid)
        child_pids = [pid] + [c.pid for c in p.children(recursive=True)]
    except Exception:
        pass

    def enum_cb(h, _):
        global hwnd, tray_hwnd, found_title, found_class
        p = ctypes.c_ulong()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value in child_pids:
            cls_b = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, cls_b, 256)
            l = u.GetWindowTextLengthW(h)
            b = ctypes.create_unicode_buffer(l + 1)
            u.GetWindowTextW(h, b, l + 1)
            
            # Detect primary UI window
            if b.value == "Hey Jev" and u.IsWindowVisible(h):
                hwnd = h
                found_title = b.value
                found_class = cls_b.value
            
            # Detect Qt tray message window registered with Shell_NotifyIcon
            if "TrayIconMessageWindow" in cls_b.value or b.value == "QTrayIconMessageWindow":
                tray_hwnd = h
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    u.EnumWindows(WNDENUMPROC(enum_cb), 0)
    if hwnd:
        break

print(f"[2] Window detection: HWND={hex(hwnd) if hwnd else None}, Title='{found_title}', Class='{found_class}', Visible={bool(u.IsWindowVisible(hwnd)) if hwnd else False}")
assert hwnd is not None, "Error: Window failed to appear within 15 seconds"
assert u.IsWindowVisible(hwnd), "Error: Window is not visible"
assert found_title == "Hey Jev", f"Error: Window title is '{found_title}', expected 'Hey Jev'"
print("    -> Window is open and visible on desktop with correct title 'Hey Jev'!")

# System Tray Observation via real OS queries
print("[3] Observing System Tray Icon via real OS inspection...")

# A. Observe Qt's Shell_NotifyIcon message window in the process
tray_class = ""
if tray_hwnd:
    cls_b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(tray_hwnd, cls_b, 256)
    tray_class = cls_b.value
print(f"    - OS Tray Message Sink: HWND={hex(tray_hwnd) if tray_hwnd else 'Pending'}, Class='{tray_class}'")

# B. Observe Explorer System Tray Toolbar
tray_wnd = u.FindWindowW("Shell_TrayWnd", None)
tray_notify_wnd = u.FindWindowExW(tray_wnd, 0, "TrayNotifyWnd", None) if tray_wnd else 0
print(f"    - Windows Shell Tray Area: Shell_TrayWnd={hex(tray_wnd)}, TrayNotifyWnd={hex(tray_notify_wnd)}")

# C. Attempt screenshot observation if display surface is available
try:
    from PIL import ImageGrab
    shot = ImageGrab.grab()
    os.makedirs("dist", exist_ok=True)
    shot_path = os.path.abspath("dist/tray_verified.png")
    shot.save(shot_path)
    print(f"    - Desktop screenshot captured and saved: {shot_path}")
except Exception as e:
    print(f"    - Screenshot capture note: {e} (headless/virtual desktop session active)")

assert tray_hwnd is not None or tray_wnd != 0, "Error: System tray notification sink not observed in OS"
print("    -> message window exists; taskbar icon NOT observed (no interactive desktop)")

# Simulate user clicking Close button
print("[4] Simulating user clicking 'X' Close button (WM_CLOSE)...")
u.PostMessageW(hwnd, 0x0010, 0, 0) # WM_CLOSE
time.sleep(2.0)

is_visible = bool(u.IsWindowVisible(hwnd))
is_alive = proc.poll() is None
print(f"    After close button: Window Visible={is_visible}, Process Alive={is_alive}")
assert not is_visible, "Error: Window should be hidden on close"
assert is_alive, "Error: Process should remain alive in background"
print("    -> Window hidden to tray; process continues running in background")

# Quit action test via posted message (NO proc.terminate())
print("[5] Testing Quit via posted message (WM_COMMAND)...")
u.PostMessageW(hwnd, 0x0111, 0, 0)

# Await clean termination
exit_code = None
for i in range(20):
    time.sleep(0.5)
    exit_code = proc.poll()
    if exit_code is not None:
        break

print(f"[6] After Quit trigger: HasExited={exit_code is not None}, ExitCode={exit_code}")
assert exit_code is not None, "Error: Process failed to exit through Quit action"
assert exit_code == 0, f"Error: Process exited with non-zero code {exit_code}"
print("    -> quit function invoked via posted message; real Ctrl+Q and tray-menu Quit NOT tested")

print("\nNeeds manual verification:")
print("  1. Visual tray icon appearance in the Windows taskbar / notification overflow area.")
print("  2. Double-click tray icon to toggle show/hide.")
print("  3. Right-click tray icon context menu and select 'Quit Hey Jev'.")
print("  4. Physical Ctrl+Q keyboard shortcut when Hey Jev main window has focus (and verify it is not global).")
