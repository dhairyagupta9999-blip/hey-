"""Global low-level push-to-talk hook using keyboard (Right Alt default, non-admin)."""
import keyboard
import threading

class PTTListener:
    def __init__(self, key="right alt", on_press=None, on_release=None):
        self.key = key
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self.is_down = False
        self._hook = None

    def _event_filter(self, event: keyboard.KeyboardEvent):
        # Match right alt or configured key name
        if event.name == self.key or (self.key == "right alt" and event.scan_code in (541, 0x138, 56)):
            if event.event_type == keyboard.KEY_DOWN and not self.is_down:
                self.is_down = True
                if self.on_press_cb:
                    self.on_press_cb()
            elif event.event_type == keyboard.KEY_UP and self.is_down:
                self.is_down = False
                if self.on_release_cb:
                    self.on_release_cb()

    def start(self):
        self._hook = keyboard.hook(self._event_filter)

    def stop(self):
        if self._hook:
            keyboard.unhook(self._hook)
            self._hook = None
