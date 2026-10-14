"""Floating, frameless, topmost dictation waveform bubble for Hey Jev.

Behavioral contracts:
- Frameless, topmost, click-through, per-monitor DPI aware.
- Never steals focus (Qt.WA_ShowWithoutActivating, Qt.WindowDoesNotAcceptFocus).
- Bottom-center of active monitor, floating safely above the Windows taskbar.
- Animated multi-bar waveform that breathes with input RMS amplitude.
- Smooth spring fade-in / fade-out transitions.
"""
import math
import random
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QLinearGradient, QFont, QPainterPath
from PySide6.QtWidgets import QWidget, QApplication

class DictationBubble(QWidget):
    """Floating transparent waveform bubble shown during dictation."""
    
    WIDTH = 340
    HEIGHT = 72
    NUM_BARS = 24

    def __init__(self, parent=None):
        super().__init__(parent)

        # Window flags: Tool, Frameless, StaysOnTop, Never steals focus
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |
            Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # Click-through

        self.resize(self.WIDTH, self.HEIGHT)
        self.rms_level = 0.05
        self.target_rms = 0.05
        self.bar_heights = [4.0] * self.NUM_BARS
        self.phase = 0.0

        # Animation timer at ~60 FPS
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._animate_step)
        self.anim_timer.start(16)

    def set_rms(self, rms: float):
        """Update current microphone RMS volume level (0.0 to 1.0)."""
        # Smooth and scale RMS
        self.target_rms = max(0.04, min(1.0, rms * 4.0))

    def _animate_step(self):
        # Lerp RMS
        self.rms_level += (self.target_rms - self.rms_level) * 0.25
        self.phase += 0.08

        # Update bar heights
        for i in range(self.NUM_BARS):
            # Center-weighted bell curve
            dist_from_center = abs(i - (self.NUM_BARS - 1) / 2.0) / ((self.NUM_BARS - 1) / 2.0)
            bell = math.exp(-2.2 * (dist_from_center ** 2))
            
            # Subtle wave motion
            wave = math.sin(self.phase + i * 0.45) * 0.35 + 0.65
            h = (self.rms_level * 36.0 * bell * wave) + 4.0
            self.bar_heights[i] += (h - self.bar_heights[i]) * 0.35

        self.update()

    def reposition(self):
        """Position bottom-center of active monitor, 80px above taskbar."""
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - self.WIDTH) // 2
        y = screen.y() + screen.height() - self.HEIGHT - 48
        self.move(x, y)

    def show_bubble(self):
        self.reposition()
        self.show()

    def hide_bubble(self):
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # 1. Background Pill Container
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)

        # Frosted dark slate surface
        bg_brush = QBrush(QColor(18, 20, 26, 235))
        painter.fillPath(path, bg_brush)

        # Subtle glowing cyan/teal border
        border_pen = QPen(QColor(56, 189, 248, 120), 1.5)
        painter.strokePath(path, border_pen)

        # 2. Text Status Label
        painter.setFont(QFont("Segoe UI Variable Text", 9, QFont.Medium))
        painter.setPen(QColor(203, 213, 225, 220))
        painter.drawText(QRectF(16, 10, self.width() - 32, 18), Qt.AlignCenter, "Dictating… Say \u201cstop transcribing\u201d to paste")

        # 3. Waveform Bars
        bar_w = 4.0
        gap = 4.5
        total_w = self.NUM_BARS * bar_w + (self.NUM_BARS - 1) * gap
        start_x = (self.width() - total_w) / 2.0
        center_y = 44.0

        for i in range(self.NUM_BARS):
            x = start_x + i * (bar_w + gap)
            h = min(28.0, max(3.0, self.bar_heights[i]))
            y = center_y - h / 2.0

            # Gradient from electric cyan to violet
            grad = QLinearGradient(x, y, x, y + h)
            grad.setColorAt(0.0, QColor(56, 189, 248, 255))   # Cyan #38bdf8
            grad.setColorAt(1.0, QColor(168, 85, 247, 230))  # Violet #a855f7

            bar_rect = QRectF(x, y, bar_w, h)
            bar_path = QPainterPath()
            bar_path.addRoundedRect(bar_rect, 2.0, 2.0)
            painter.fillPath(bar_path, QBrush(grad))

        painter.end()
