"""Native Windows PySide6 interface for Hey Jev.

Implements all 7 tabs, system tray icon, floating dictation bubble,
status dot with color states, active timer rows, and settings.
"""
import os
import sys
import json
import time
import queue
import threading
from typing import Dict, Any, List

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from PySide6.QtGui import QIcon, QColor, QFont, QPalette, QAction, QPainter, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTabWidget, QLineEdit, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit, QScrollArea,
    QSystemTrayIcon, QMenu, QGroupBox, QFormLayout, QDoubleSpinBox,
    QRadioButton, QButtonGroup, QMessageBox
)

from config import (
    LOG_FILE, USER_APPS_FILE, DEFAULT_APPS_FILE, USER_VOCAB_FILE,
    DICTATION_LOG, APPDATA_DIR
)
from secrets_store import KEY_NAMES, get_secret, save_secret, missing_secrets
from bubble import DictationBubble

STATUS_COLORS = {
    "Starting": "#f59e0b",           # Amber
    "Ready": "#10b981",              # Emerald Green
    "Listening": "#ef4444",          # Red
    "Transcribing": "#3b82f6",       # Blue
    "Thinking": "#a855f7",           # Purple
    "Doing it": "#f97316",           # Orange
    "Speaking": "#06b6d4",           # Teal
    "Something went wrong": "#ef4444",# Red
    "Time's up": "#eab308",          # Yellow
}

HINTS = {
    "ptt": "Hold right Alt to talk",
    "wake": "Say \u201cHey Jev\u201d, then your command"
}

SETTINGS_FILE = os.path.join(APPDATA_DIR, "settings.json")

def load_settings() -> dict:
    defaults = {
        "mode": "ptt",
        "backend": os.getenv("HEYJEV_BACKEND", "jev"),
        "gate_jev": 0.65,
        "gate_laya": 0.45,
        "keep_on_top": False,
        "close_to_tray": True,
        "ptt_key": "right alt",
        "stt_engine": os.getenv("HEYJEV_STT", "whisper"),
        "whisper_model": os.getenv("HEYJEV_WHISPER_MODEL", "small.en"),
        "tier3_enabled": False,
        "tier3_provider": "openrouter",
        "tier3_daily_spend_cap": 0.10,
        "tier3_confirm_all_actions": True,
        "tier3_always_confirm": ["delete_file", "run_powershell", "close_all", "shutdown_computer", "open_dangerous_file"],
        "opendots_gateway_enabled": False,
        "opendots_gateway_port": 8765
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**defaults, **data}
        except Exception:
            pass
    return defaults

get_settings = load_settings

def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[settings save error]: {e}")

class WorkerSignals(QObject):
    status_updated = Signal(str, str)
    bubble_requested = Signal(bool)
    rms_updated = Signal(float)

class StatusDot(QWidget):
    """14px status dot with crisp anti-aliased rendering."""
    def __init__(self, color_hex="#f59e0b", parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.color = QColor(color_hex)

    def set_color(self, color_hex: str):
        self.color = QColor(color_hex)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(self.color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(1, 1, 14, 14)
        painter.end()


def is_torch_available() -> bool:
    """Check if PyTorch is available in current runtime environment."""
    try:
        import torch  # noqa: F401
        return True
    except (ImportError, Exception):
        return False


class MainWindow(QMainWindow):
    """Primary 7-tab Hey Jev desktop window."""

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.signals = WorkerSignals()
        self.controls_queue = queue.Queue()
        self.worker_thread = None

        self.setWindowTitle("Hey Jev")
        self.setMinimumSize(480, 520)
        self.resize(500, 560)

        if self.settings.get("keep_on_top", False):
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        self.bubble = DictationBubble()
        self._mission_center_window = None

        # Connect signals
        self.signals.status_updated.connect(self._on_status_updated)
        self.signals.bubble_requested.connect(self._on_bubble_requested)
        self.signals.rms_updated.connect(self.bubble.set_rms)

        self._apply_dark_theme()
        self._init_ui()
        self._init_tray()

        # Window-scoped Ctrl+Q shortcut (active only when Hey Jev window has focus)
        self.quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self.quit_shortcut.setContext(Qt.WindowShortcut)
        self.quit_shortcut.activated.connect(self._quit_application)

        # Esc shortcut to cancel current action / confirmation
        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.esc_shortcut.setContext(Qt.ApplicationShortcut)
        self.esc_shortcut.activated.connect(self._on_esc_pressed)

        # Timer countdown tick
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._on_timer_tick)
        self.tick_timer.start(500)

        # Check keys on launch
        if missing_secrets():
            self.tabs.setCurrentWidget(self.tab_keys)
            self._on_status_updated("Starting", "Add your API keys to begin")
        else:
            self._start_worker()

    def _apply_dark_theme(self):
        """Native dark theme matching modern Windows 11 aesthetics."""
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(15, 23, 42))        # #0f172a
        palette.setColor(QPalette.WindowText, QColor(241, 245, 249)) # #f1f5f9
        palette.setColor(QPalette.Base, QColor(30, 41, 59))          # #1e293b
        palette.setColor(QPalette.AlternateBase, QColor(51, 65, 85))  # #334155
        palette.setColor(QPalette.Text, QColor(241, 245, 249))
        palette.setColor(QPalette.Button, QColor(30, 41, 59))
        palette.setColor(QPalette.ButtonText, QColor(241, 245, 249))
        palette.setColor(QPalette.Highlight, QColor(56, 189, 248))    # #38bdf8
        palette.setColor(QPalette.HighlightedText, QColor(15, 23, 42))
        self.setPalette(palette)

        self.setStyleSheet("""
            QMainWindow, QWidget {
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
                background-color: #0f172a;
                color: #f1f5f9;
            }
            QTabWidget::pane {
                border: 1px solid #334155;
                background-color: #0f172a;
                border-radius: 8px;
            }
            QTabBar::tab {
                background: #1e293b;
                color: #94a3b8;
                padding: 8px 14px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-size: 11px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #334155;
                color: #38bdf8;
                font-weight: bold;
            }
            QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit {
                background-color: #1e293b;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 6px 10px;
                color: #f1f5f9;
                selection-background-color: #38bdf8;
                selection-color: #0f172a;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus {
                border: 1.5px solid #38bdf8;
            }
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
                border: none;
            }
            QPushButton:hover {
                background-color: #3b82f6;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 14px;
                font-weight: bold;
                color: #cbd5e1;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QTableWidget {
                gridline-color: #334155;
                border: 1px solid #334155;
                border-radius: 6px;
                background-color: #1e293b;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                padding: 4px;
                border: none;
                font-weight: bold;
            }
        """)

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(12)

        # ---------------- Top Status Area ----------------
        top_box = QWidget()
        top_layout = QHBoxLayout(top_box)
        top_layout.setContentsMargins(0, 0, 0, 0)

        self.dot = StatusDot(STATUS_COLORS["Starting"])
        top_layout.addWidget(self.dot)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        self.status_label = QLabel("Starting")
        self.status_label.setFont(QFont("Segoe UI Variable Display", 18, QFont.Bold))
        text_layout.addWidget(self.status_label)

        self.detail_label = QLabel("Loading Whisper…")
        self.detail_label.setFont(QFont("Segoe UI", 11))
        self.detail_label.setStyleSheet("color: #94a3b8;")
        text_layout.addWidget(self.detail_label)

        top_layout.addLayout(text_layout)
        top_layout.addStretch()

        # Mode Selector Buttons
        mode_box = QWidget()
        mode_layout = QHBoxLayout(mode_box)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(4)

        self.btn_ptt = QPushButton("Hold Alt")
        self.btn_ptt.setCheckable(True)
        self.btn_ptt.setChecked(self.settings.get("mode") == "ptt")
        self.btn_ptt.clicked.connect(lambda: self._set_mode("ptt"))

        self.btn_wake = QPushButton("Hey Jev")
        self.btn_wake.setCheckable(True)
        self.btn_wake.setChecked(self.settings.get("mode") == "wake")
        self.btn_wake.clicked.connect(lambda: self._set_mode("wake"))

        self._update_mode_buttons()

        mode_layout.addWidget(self.btn_ptt)
        mode_layout.addWidget(self.btn_wake)
        top_layout.addWidget(mode_box)

        root_layout.addWidget(top_box)

        # ---------------- Active Timers Row ----------------
        self.timers_widget = QWidget()
        self.timers_layout = QVBoxLayout(self.timers_widget)
        self.timers_layout.setContentsMargins(8, 4, 8, 4)
        self.timers_widget.setStyleSheet("background-color: #1e293b; border-radius: 6px;")
        self.timers_widget.hide()
        root_layout.addWidget(self.timers_widget)

        # ---------------- The 7 Tabs ----------------
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)

        self.tab_home = self._create_home_tab()
        self.tab_vocab = self._create_vocab_tab()
        self.tab_apps = self._create_apps_tab()
        self.tab_history = self._create_history_tab()
        self.tab_privacy = self._create_privacy_tab()
        self.tab_settings = self._create_settings_tab()
        self.tab_keys = self._create_keys_tab()

        self.tabs.addTab(self.tab_home, "Home")
        self.tabs.addTab(self.tab_vocab, "Dictionary")
        self.tabs.addTab(self.tab_apps, "Apps")
        self.tabs.addTab(self.tab_history, "Dictation")
        self.tabs.addTab(self.tab_privacy, "Privacy")
        self.tabs.addTab(self.tab_settings, "Settings")
        self.tabs.addTab(self.tab_keys, "Keys")

    def _init_tray(self):
        """Configure system tray icon with context menu."""
        self.tray = QSystemTrayIcon(self)
        # Create a simple tray icon pixmap with the status dot
        pix = QPixmap(16, 16)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(QColor("#10b981"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(1, 1, 14, 14)
        p.end()
        self.tray.setIcon(QIcon(pix))
        self.tray.setToolTip("Hey Jev Voice Assistant")

        tray_menu = QMenu()
        show_action = QAction("Show Hey Jev", self)
        show_action.triggered.connect(self.show_normal)
        tray_menu.addAction(show_action)

        top_action = QAction("Keep on Top", self)
        top_action.setCheckable(True)
        top_action.setChecked(self.settings.get("keep_on_top", False))
        top_action.triggered.connect(self._toggle_keep_on_top)
        tray_menu.addAction(top_action)

        about_action = QAction("About Hey Jev\u2026", self)
        about_action.triggered.connect(self._show_about_dialog)
        tray_menu.addAction(about_action)

        missions_action = QAction("Mission Center\u2026", self)
        missions_action.triggered.connect(self.open_mission_center)
        tray_menu.addAction(missions_action)

        tray_menu.addSeparator()

        quit_action = QAction("Quit Hey Jev", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self._quit_application)
        tray_menu.addAction(quit_action)
        self.addAction(quit_action)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def show_normal(self):
        self.show()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_normal()

    def _toggle_keep_on_top(self):
        cur = not self.settings.get("keep_on_top", False)
        self.settings["keep_on_top"] = cur
        save_settings(self.settings)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, cur)
        self.show()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            from safety_engine import get_confirmation_manager
            cm = get_confirmation_manager()
            cancelled, msg = cm.cancel_pending("Esc key pressed")
            if cancelled:
                self.tray.showMessage("Hey Jev", msg, QSystemTrayIcon.Information, 2000)
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_about_dialog(self):
        msg = (
            "<h3>Hey Jev (Windows 10/11 x64 Port)</h3>"
            "<p><b>Version:</b> 1.0 (Windows Port)</p>"
            "<p>Windows port of <a href='https://github.com/henryklunaris/hey-jev'>henryklunaris/hey-jev</a> by Dhairya Gupta. Original by Henryk Lunaris (MIT).</p>"
            "<p><b>Decision Backends:</b><br>"
            "• TypeSafe Jev (Speculative fan-out System 1, default)<br>"
            "• Laya (Local open-weight System 1)</p>"
            "<p><b>Referenced Projects & Credits:</b><br>"
            "• <a href='https://github.com/touhidsiddiqueeraj-bit/hey-laya'>hey-laya</a> — MIT License (© 2026 Touhid Siddique Eraj)<br>"
            "• <a href='https://github.com/allenporter/home-assistant-laya'>home-assistant-laya</a> — Apache 2.0 (© 2026 Allen Porter)<br>"
            "• <a href='https://github.com/dscripka/openWakeWord'>openWakeWord</a> — Apache 2.0 (© David Scripka)</p>"
        )
        QMessageBox.about(self, "About Hey Jev", msg)

    def _set_mode(self, mode: str):
        self.settings["mode"] = mode
        save_settings(self.settings)
        self._update_mode_buttons()
        self.detail_label.setText(HINTS[mode])
        self.controls_queue.put(("mode", mode))

    def _update_mode_buttons(self):
        is_ptt = self.settings.get("mode") == "ptt"
        self.btn_ptt.setChecked(is_ptt)
        self.btn_wake.setChecked(not is_ptt)
        active_style = "background-color: #38bdf8; color: #0f172a; font-weight: bold;"
        inactive_style = "background-color: #1e293b; color: #94a3b8;"
        self.btn_ptt.setStyleSheet(active_style if is_ptt else inactive_style)
        self.btn_wake.setStyleSheet(inactive_style if is_ptt else active_style)

    # ---------------- Tab 1: Home ----------------
    def _create_home_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        card = QWidget()
        card_lay = QVBoxLayout(card)
        card.setStyleSheet("background-color: #1e293b; border-radius: 8px; padding: 12px;")

        card_title = QLabel("Example Commands")
        card_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        card_lay.addWidget(card_title)

        examples = [
            "• \u201cOpen Spotify and play music\u201d",
            "• \u201cTurn volume to 40%\u201d or \u201cMute the computer\u201d",
            "• \u201cSet a timer for 10 minutes\u201d or \u201cRemind me in 1 hour\u201d",
            "• \u201cTurn on dark mode\u201d or \u201cLock the screen\u201d",
            "• \u201cOpen youtube.com in Edge\u201d",
            "• \u201cPause Spotify and open Slack\u201d (Compound)",
            "• \u201cHey Jev, transcribe\u201d (Waveform dictation bubble)",
            "• \u201cWho wrote Hamlet?\u201d (Knowledge query via Haiku)"
        ]
        for ex in examples:
            lbl = QLabel(ex)
            lbl.setStyleSheet("color: #cbd5e1; font-size: 11px;")
            card_lay.addWidget(lbl)

        lay.addWidget(card)

        # Autonomous Missions Card
        mission_card = QWidget()
        mission_card_lay = QVBoxLayout(mission_card)
        mission_card.setStyleSheet("background-color: #1e293b; border-radius: 8px; padding: 12px;")

        mission_title = QLabel("Autonomous Missions")
        mission_title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        mission_card_lay.addWidget(mission_title)

        mission_desc = QLabel("Track, control, and inspect long-running autonomous goals, task graphs, live progress, and verification outcomes.")
        mission_desc.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        mission_desc.setWordWrap(True)
        mission_card_lay.addWidget(mission_desc)

        self.btn_mission_center = QPushButton("Open Mission Center")
        self.btn_mission_center.setStyleSheet("background-color: #0284c7; color: #ffffff; font-weight: 600; padding: 6px 12px;")
        self.btn_mission_center.clicked.connect(self.open_mission_center)
        mission_card_lay.addWidget(self.btn_mission_center)

        lay.addWidget(mission_card)
        lay.addStretch()
        return w

    # ---------------- Tab 2: Dictionary (Vocabulary) ----------------
    def _create_vocab_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)

        info = QLabel("Personal phonetic vocabulary fixes (saved to vocabulary.json).")
        info.setStyleSheet("color: #94a3b8; font-size: 11px;")
        lay.addWidget(info)

        self.vocab_edit = QTextEdit()
        self.vocab_edit.setFont(QFont("Consolas", 10))
        # Load existing vocabulary.json
        if os.path.exists(USER_VOCAB_FILE):
            with open(USER_VOCAB_FILE, "r", encoding="utf-8") as f:
                self.vocab_edit.setPlainText(f.read())
        else:
            sample = '[\n  ["visual studio code", "VS Code"],\n  ["mind stream", "Mimestream"]\n]'
            self.vocab_edit.setPlainText(sample)

        lay.addWidget(self.vocab_edit)

        save_btn = QPushButton("Save Dictionary")
        save_btn.clicked.connect(self._save_vocabulary)
        lay.addWidget(save_btn)
        return w

    def _save_vocabulary(self):
        try:
            data = json.loads(self.vocab_edit.toPlainText())
            with open(USER_VOCAB_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            QMessageBox.information(self, "Dictionary Saved", "Vocabulary updated successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Invalid JSON", f"Could not parse dictionary JSON:\n{e}")

    # ---------------- Tab 3: Apps ----------------
    def _create_apps_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)

        info = QLabel("Configured applications from apps.json:")
        info.setStyleSheet("color: #94a3b8; font-size: 11px;")
        lay.addWidget(info)

        self.apps_table = QTableWidget()
        self.apps_table.setColumnCount(3)
        self.apps_table.setHorizontalHeaderLabels(["Voice Trigger", "Say Name", "Target Windows App"])
        self.apps_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.apps_table.setAlternatingRowColors(True)

        apps_path = USER_APPS_FILE if os.path.exists(USER_APPS_FILE) else DEFAULT_APPS_FILE
        if os.path.exists(apps_path):
            with open(apps_path, "r", encoding="utf-8") as f:
                apps_data = json.load(f)
            self.apps_table.setRowCount(len(apps_data))
            for row, (k, v) in enumerate(apps_data.items()):
                app_name = v if isinstance(v, str) else v.get("app", k)
                say_name = k if isinstance(v, str) else v.get("say", k)
                self.apps_table.setItem(row, 0, QTableWidgetItem(k))
                self.apps_table.setItem(row, 1, QTableWidgetItem(say_name))
                self.apps_table.setItem(row, 2, QTableWidgetItem(app_name))

        lay.addWidget(self.apps_table)
        return w

    # ---------------- Tab 4: Dictation History ----------------
    def _create_history_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)

        info = QLabel("Recent dictations (from Hey Jev dictation.jsonl):")
        info.setStyleSheet("color: #94a3b8; font-size: 11px;")
        lay.addWidget(info)

        self.history_list = QTextEdit()
        self.history_list.setReadOnly(True)
        self.history_list.setFont(QFont("Segoe UI", 10))
        lay.addWidget(self.history_list)

        btn_refresh = QPushButton("Refresh History")
        btn_refresh.clicked.connect(self._refresh_history)
        lay.addWidget(btn_refresh)

        self._refresh_history()
        return w

    def _refresh_history(self):
        if os.path.exists(DICTATION_LOG):
            lines = []
            try:
                with open(DICTATION_LOG, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            item = json.loads(line)
                            ts = item.get("time", "")
                            text = item.get("text", "")
                            lines.append(f"[{ts}] {text}\n")
                self.history_list.setPlainText("".join(reversed(lines[-50:])))
            except Exception as e:
                self.history_list.setPlainText(f"Error loading dictation history: {e}")
        else:
            self.history_list.setPlainText("No dictations recorded yet.\nSay \u201cHey Jev, transcribe\u201d to start dictating.")

    # ---------------- Tab 5: Privacy ----------------
    def _create_privacy_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)

        title = QLabel("Privacy & Architecture Contract")
        title.setFont(QFont("Segoe UI", 13, QFont.Bold))
        lay.addWidget(title)

        self.privacy_text = QLabel()
        self.privacy_text.setWordWrap(True)
        self.privacy_text.setFont(QFont("Segoe UI", 10))
        self.privacy_text.setStyleSheet("color: #cbd5e1; line-height: 1.4;")
        lay.addWidget(self.privacy_text)

        self._update_privacy_text()
        lay.addStretch()
        return w

    def _update_privacy_text(self):
        backend = self.settings.get("backend", "jev")
        torch_ok = is_torch_available()
        laya_actually_active = (backend == "laya" and torch_ok)

        stt_eng = self.settings.get("stt_engine", "whisper")
        if stt_eng == "whistle":
            stt_label = "local Whistle (Cactus Compute, fast on-device)"
        else:
            wm = self.settings.get("whisper_model", "small.en")
            stt_label = f"local faster-whisper ({wm}, CPU int8)"

        if laya_actually_active:
            msg = (
                "<b>Active Backend: Local Laya (100% On-Device)</b><br><br>"
                f"• <b>Audio Capture & Speech Recognition:</b> All audio stays 100% on your device, "
                f"transcribed in-process via {stt_label}.<br>"
                "• <b>Decision Engine:</b> Decisions are evaluated entirely on-device by the open-weight "
                "Laya decision model running on CPU. <b>Zero command text or audio leaves your computer.</b><br>"
                "• <b>Dictation:</b> Dictation is the ONLY feature that contacts a cloud endpoint "
                "(OpenRouter/OpenAI), only when you explicitly say 'Hey Jev, transcribe'.<br>"
                "• <b>Custom Vocabulary:</b> Your personal dictionary and timers remain gitignored on your machine."
            )
        elif backend == "laya" and not torch_ok:
            msg = (
                "<b>Active Backend: Jev (Fallback - Local Laya Unavailable)</b><br><br>"
                "• <b>Notice:</b> Laya requires PyTorch, which is not bundled in this build (source mode only).<br>"
                "• <b>Decision Routing:</b> Command decisions route to TypeSafe Jev System 1 (~$0.00004 per turn). "
                "<b>Decisions are NOT evaluated on-device because Laya is inactive.</b><br>"
                f"• <b>Local Audio Privacy:</b> Raw microphone audio NEVER leaves your machine. "
                f"{stt_label} converts speech to text locally on your CPU.<br>"
                "• <b>Dictation:</b> Dictation audio is sent only when explicitly requested."
            )
        else:
            msg = (
                "<b>Active Backend: Jev (TypeSafe Hosted System 1)</b><br><br>"
                f"• <b>Local Audio Privacy:</b> Raw microphone audio NEVER leaves your machine. "
                f"{stt_label} converts speech to text locally on your CPU.<br>"
                "• <b>Decision Economics:</b> Only the transcribed text command is sent to the TypeSafe Jev API "
                "using speculative fan-out (~$0.00004 per turn). No audio is transmitted.<br>"
                "• <b>Factual Queries:</b> General knowledge queries route to Claude Haiku via OpenRouter.<br>"
                "• <b>Dictation:</b> Dictation audio is sent only when explicitly requested.<br>"
                "• <b>Local Credentials:</b> API keys are stored securely in Windows Credential Manager."
            )

        ans_provider = self.settings.get("answer_provider", "openrouter")
        if ans_provider == "opencode_zen":
            ans_text = (
                "<br><br><b>Answer Provider: OpenCode Zen (Free Models)</b><br>"
                "• <b>Official Zen Privacy Policy:</b> According to OpenCode's official privacy documentation "
                "(https://opencode.ai/docs/zen/), during the free period for models such as Big Pickle, Fledge, "
                "MiMo, and Ling, collected data and prompt inputs <b>may be used to improve and train the models</b>.<br>"
                "• <b>Strict Data Isolation:</b> In Hey Jev, OpenCode Zen is used <b>ONLY</b> for plain Q&A "
                "(factual questions and chit-chat). Only the spoken transcript is transmitted. "
                "<b>Never sent to Zen:</b> file names, paths, open window text, clipboard data, or PC automation tool outputs.<br>"
                "• <b>Phase 6 PC Control:</b> Stays strictly on OpenRouter / on-device safety engine."
            )
        else:
            ans_text = (
                "<br><br><b>Answer Provider: OpenRouter (Haiku)</b><br>"
                "• Factual Q&A routes to Claude 3 Haiku via OpenRouter under zero-retention / API terms. "
                "OpenCode Zen is not contacted."
            )
        msg += ans_text

        t3_on = self.settings.get("tier3_enabled", False)
        t3_prov = self.settings.get("tier3_provider", "openrouter")
        t3_cap = self.settings.get("tier3_daily_spend_cap", 0.10)
        t3_status = "ENABLED" if t3_on else "OFF (Default)"
        t3_prov_name = "OpenCode Zen (free)" if t3_prov == "opencode_zen" else "OpenRouter (Claude Haiku)"
        t3_text = (
            f"<br><br><b>Tier 3 PC Control Agent ('Let Jev do anything'): {t3_status}</b><br>"
            f"• <b>Status:</b> {t3_status} (Provider: {t3_prov_name}, Daily Cap: ${t3_cap:.2f}).<br>"
            "• <b>What Tier 3 Sends:</b> When enabled, Tier 3 sends the spoken user prompt and only the specific "
            "tool execution context required to fulfill the request (e.g., active window title or specific file search query) "
            "to the configured agent model endpoint. Zero audio is transmitted.<br>"
            "• <b>Prompt Injection Quarantine:</b> External data (files, window text, clipboard, web results) is strictly "
            "quarantined inside &lt;DATA&gt; tags with prompt-injection tokens redacted, preventing untrusted text from executing as instructions.<br>"
            "• <b>Preview Mode & Confirmation Safety:</b> When 'Confirm every AI-agent UI action' is enabled (default ON), Tier 3 speaks a preview sentence before every click, typing, or keypress and waits for 'yes'. Administrative tools and shells are blocked outright, and high-risk operations default to NO after 8 seconds. Pressing Esc or saying 'stop' immediately halts the action."
        )
        msg += t3_text
        self.privacy_text.setText(msg)

    # ---------------- Tab 6: Settings ----------------
    def _create_settings_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        # Decision Backend Group
        backend_group = QGroupBox("Decision Engine")
        bg_lay = QFormLayout(backend_group)

        self.backend_combo = QComboBox()
        self.backend_combo.addItem("Jev (TypeSafe System 1 - Default)", "jev")
        torch_ok = is_torch_available()

        if not torch_ok:
            self.backend_combo.addItem("Laya (experimental, needs install)", "laya_unavailable")
            model = self.backend_combo.model()
            item = model.item(1)
            if item:
                item.setEnabled(False)
            idx = 0
            if self.settings.get("backend") == "laya":
                self.settings["backend"] = "jev"
                save_settings(self.settings)
        else:
            self.backend_combo.addItem("Laya (experimental, slow on CPU)", "laya")
            idx = 1 if self.settings.get("backend") == "laya" else 0

        self.backend_combo.setCurrentIndex(idx)
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)
        bg_lay.addRow("Backend:", self.backend_combo)

        self.gate_spin = QDoubleSpinBox()
        self.gate_spin.setRange(0.1, 1.0)
        self.gate_spin.setSingleStep(0.05)
        cur_gate = self.settings.get("gate_laya" if idx == 1 else "gate_jev", 0.65)
        self.gate_spin.setValue(cur_gate)
        self.gate_spin.valueChanged.connect(self._on_gate_changed)
        bg_lay.addRow("Confidence Gate:", self.gate_spin)

        lay.addWidget(backend_group)

        # Answer Provider Group (Non-command Q&A and Chit-chat)
        ans_group = QGroupBox("Answer Provider (Q&A & Chit-Chat)")
        ans_lay = QFormLayout(ans_group)

        self.ans_combo = QComboBox()
        self.ans_combo.addItem("OpenRouter (Haiku)", "openrouter")
        self.ans_combo.addItem("OpenCode Zen (free)", "opencode_zen")
        cur_ans = self.settings.get("answer_provider", "openrouter")
        self.ans_combo.setCurrentIndex(1 if cur_ans == "opencode_zen" else 0)
        self.ans_combo.currentIndexChanged.connect(self._on_answer_provider_changed)
        ans_lay.addRow("Answer Provider:", self.ans_combo)

        lay.addWidget(ans_group)

        # Tier 3 Agent Group ("Do Anything" Layer)
        t3_group = QGroupBox("Open-Vocabulary Agent ('Do Anything' Mode)")
        t3_lay = QFormLayout(t3_group)

        self.chk_tier3 = QCheckBox("Let Jev do anything (AI agent)")
        self.chk_tier3.setChecked(self.settings.get("tier3_enabled", False))
        self.chk_tier3.toggled.connect(self._on_tier3_toggled)
        t3_lay.addRow(self.chk_tier3)

        self.t3_provider_combo = QComboBox()
        self.t3_provider_combo.addItem("OpenRouter (Claude Haiku - Default)", "openrouter")
        self.t3_provider_combo.addItem("OpenCode Zen (free)", "opencode_zen")
        cur_t3 = self.settings.get("tier3_provider", "openrouter")
        self.t3_provider_combo.setCurrentIndex(1 if cur_t3 == "opencode_zen" else 0)
        self.t3_provider_combo.currentIndexChanged.connect(self._on_tier3_provider_changed)
        t3_lay.addRow("Agent Model:", self.t3_provider_combo)

        self.t3_spend_cap_spin = QDoubleSpinBox()
        self.t3_spend_cap_spin.setRange(0.01, 10.00)
        self.t3_spend_cap_spin.setSingleStep(0.05)
        self.t3_spend_cap_spin.setPrefix("$")
        self.t3_spend_cap_spin.setValue(self.settings.get("tier3_daily_spend_cap", 0.10))
        self.t3_spend_cap_spin.valueChanged.connect(self._on_spend_cap_changed)
        t3_lay.addRow("Daily Spend Cap:", self.t3_spend_cap_spin)

        self.chk_confirm_all = QCheckBox("Confirm every AI-agent UI action")
        self.chk_confirm_all.setChecked(self.settings.get("tier3_confirm_all_actions", True))
        self.chk_confirm_all.toggled.connect(self._on_confirm_all_toggled)
        t3_lay.addRow(self.chk_confirm_all)

        confirm_box = QGroupBox("Actions That Always Need Confirmation")
        confirm_box_lay = QVBoxLayout(confirm_box)
        confirm_items = [
            "• Running PowerShell commands / scripts (run_powershell)",
            "• Deleting files (moved to Recycle Bin with undo)",
            "• Closing all windows / 'close everything' (close_all)",
            "• System power operations (restart, shutdown, sleep)",
            "• Launching executable files / installers (.exe, .bat, .ps1, .vbs)"
        ]
        for it in confirm_items:
            lbl = QLabel(it)
            lbl.setStyleSheet("color: #cbd5e1; font-size: 11px;")
            confirm_box_lay.addWidget(lbl)

        confirm_note = QLabel("<i>Confirmations default to NO after 8s. Press Esc key or say 'stop' to cancel.</i>")
        confirm_note.setStyleSheet("color: #94a3b8; font-size: 10px; margin-top: 4px;")
        confirm_box_lay.addWidget(confirm_note)

        t3_lay.addRow(confirm_box)
        lay.addWidget(t3_group)

        stt_group = QGroupBox("Speech-to-Text (STT)")
        stt_lay = QFormLayout(stt_group)

        self.stt_combo = QComboBox()
        self.stt_combo.addItem("faster-whisper (Default)", "whisper")
        self.stt_combo.addItem("Whistle (Cactus Compute, fast on-device)", "whistle")
        cur_stt = self.settings.get("stt_engine", "whisper")
        self.stt_combo.setCurrentIndex(1 if cur_stt == "whistle" else 0)
        self.stt_combo.currentIndexChanged.connect(self._on_stt_changed)
        stt_lay.addRow("STT Engine:", self.stt_combo)

        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.addItem("small.en (Default, high accuracy)", "small.en")
        self.whisper_model_combo.addItem("base.en (Balanced)", "base.en")
        self.whisper_model_combo.addItem("tiny.en (Fastest)", "tiny.en")
        cur_wm = self.settings.get("whisper_model", "small.en")
        wm_idx = 0
        if cur_wm == "base.en":
            wm_idx = 1
        elif cur_wm == "tiny.en":
            wm_idx = 2
        self.whisper_model_combo.setCurrentIndex(wm_idx)
        self.whisper_model_combo.currentIndexChanged.connect(self._on_whisper_model_changed)
        stt_lay.addRow("Whisper Model:", self.whisper_model_combo)

        lay.addWidget(stt_group)

        # Hotkey Group
        hotkey_group = QGroupBox("Push-to-Talk & Hotkeys")
        hk_lay = QFormLayout(hotkey_group)

        self.ptt_combo = QComboBox()
        self.ptt_combo.addItems(["Right Alt (Default)", "Caps Lock", "Right Ctrl"])
        self.ptt_combo.setCurrentText(self.settings.get("ptt_key", "Right Alt (Default)"))
        self.ptt_combo.currentTextChanged.connect(self._on_ptt_changed)
        hk_lay.addRow("PTT Key:", self.ptt_combo)

        lay.addWidget(hotkey_group)

        # Window Behavior Group
        win_group = QGroupBox("Window & Tray Behavior")
        win_lay = QVBoxLayout(win_group)

        self.chk_ontop = QCheckBox("Keep window always on top")
        self.chk_ontop.setChecked(self.settings.get("keep_on_top", False))
        self.chk_ontop.toggled.connect(self._on_ontop_toggled)
        win_lay.addWidget(self.chk_ontop)

        self.chk_tray = QCheckBox("Minimize to system tray on close (keeps listening)")
        self.chk_tray.setChecked(self.settings.get("close_to_tray", True))
        self.chk_tray.toggled.connect(self._on_tray_toggled)
        win_lay.addWidget(self.chk_tray)

        from autostart import is_autostart_enabled
        self.chk_autostart = QCheckBox("Launch Hey Jev at Windows startup (at logon)")
        self.chk_autostart.setChecked(is_autostart_enabled())
        self.chk_autostart.toggled.connect(self._on_autostart_toggled)
        win_lay.addWidget(self.chk_autostart)

        lay.addWidget(win_group)

        # System Diagnostics Group (Booleans & Counters Only)
        diag_group = QGroupBox("System Diagnostics (Booleans & Counters Only)")
        diag_lay = QVBoxLayout(diag_group)
        self.diag_view = QTextEdit()
        self.diag_view.setReadOnly(True)
        self.diag_view.setMaximumHeight(160)
        self.diag_view.setFont(QFont("Consolas", 9))
        diag_lay.addWidget(self.diag_view)

        btn_refresh_diag = QPushButton("Refresh Diagnostics")
        btn_refresh_diag.clicked.connect(self._refresh_diagnostics)
        diag_lay.addWidget(btn_refresh_diag)
        lay.addWidget(diag_group)

        self._refresh_diagnostics()

        lay.addStretch()
        scroll.setWidget(w)
        return scroll

    def _refresh_diagnostics(self):
        try:
            import diagnostics
            self.diag_view.setPlainText(diagnostics.format_diagnostics_text())
        except Exception as e:
            self.diag_view.setPlainText(f"Error loading diagnostics: {e}")

    def _on_backend_changed(self, idx: int):
        val = self.backend_combo.itemData(idx)
        if val == "laya_unavailable":
            # Revert selection to Jev if unavailable
            self.backend_combo.setCurrentIndex(0)
            return
        self.settings["backend"] = val
        os.environ["HEYJEV_BACKEND"] = val
        self.gate_spin.setValue(self.settings.get("gate_laya" if val == "laya" else "gate_jev", 0.65 if val == "jev" else 0.45))
        save_settings(self.settings)
        self._update_privacy_text()

    def _on_answer_provider_changed(self, idx: int):
        val = self.ans_combo.itemData(idx)
        self.settings["answer_provider"] = val
        os.environ["HEYJEV_ANSWER_PROVIDER"] = val
        save_settings(self.settings)
        self._update_privacy_text()

    def _on_tier3_toggled(self, checked: bool):
        self.settings["tier3_enabled"] = checked
        os.environ["HEYJEV_TIER3_ENABLED"] = "1" if checked else "0"
        save_settings(self.settings)
        self._update_privacy_text()

    def _on_tier3_provider_changed(self, idx: int):
        val = self.t3_provider_combo.itemData(idx)
        self.settings["tier3_provider"] = val
        os.environ["HEYJEV_TIER3_PROVIDER"] = val
        save_settings(self.settings)
        self._update_privacy_text()

    def _on_spend_cap_changed(self, val: float):
        self.settings["tier3_daily_spend_cap"] = val
        os.environ["HEYJEV_TIER3_DAILY_SPEND_CAP"] = str(val)
        save_settings(self.settings)
        self._update_privacy_text()

    def _on_confirm_all_toggled(self, checked: bool):
        self.settings["tier3_confirm_all_actions"] = checked
        os.environ["HEYJEV_TIER3_CONFIRM_ALL"] = "1" if checked else "0"
        save_settings(self.settings)
        self._update_privacy_text()

    def _on_stt_changed(self, idx: int):
        val = self.stt_combo.itemData(idx)
        self.settings["stt_engine"] = val
        os.environ["HEYJEV_STT"] = val
        save_settings(self.settings)
        import stt
        stt.get_stt_backend(val, self.settings.get("whisper_model", "small.en"))
        self._update_privacy_text()

    def _on_whisper_model_changed(self, idx: int):
        val = self.whisper_model_combo.itemData(idx)
        self.settings["whisper_model"] = val
        os.environ["HEYJEV_WHISPER_MODEL"] = val
        save_settings(self.settings)
        import stt
        stt.get_stt_backend(self.settings.get("stt_engine", "whisper"), val)
        self._update_privacy_text()

    def _on_gate_changed(self, val: float):
        b = self.settings.get("backend", "jev")
        if b == "laya":
            self.settings["gate_laya"] = val
        else:
            self.settings["gate_jev"] = val
        save_settings(self.settings)

    def _on_ptt_changed(self, text: str):
        self.settings["ptt_key"] = text
        save_settings(self.settings)

    def _on_ontop_toggled(self, checked: bool):
        self.settings["keep_on_top"] = checked
        save_settings(self.settings)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()

    def _on_tray_toggled(self, checked: bool):
        self.settings["close_to_tray"] = checked
        save_settings(self.settings)

    def _on_autostart_toggled(self, checked: bool):
        from autostart import enable_autostart, disable_autostart
        if checked:
            ok, msg = enable_autostart()
        else:
            ok, msg = disable_autostart()
        self.settings["autostart"] = checked
        save_settings(self.settings)

    # ---------------- Tab 7: Keys ----------------
    def _create_keys_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        desc = QLabel("API keys are saved in Windows Credential Manager.")
        desc.setStyleSheet("color: #94a3b8; font-size: 11px;")
        lay.addWidget(desc)

        form = QFormLayout()
        self.key_inputs = {}

        fields = [
            ("TypeSafe (Jev)", "TYPESAFE_API_KEY"),
            ("Fish Audio (TTS)", "FISH_AUDIO_API_KEY"),
            ("OpenRouter (Chat)", "OPENROUTER_API_KEY"),
            ("OpenAI (Transcribe)", "OPENAI_API_KEY"),
            ("OpenCode Zen (free)", "OPENCODE_ZEN_API_KEY")
        ]

        for title, key_name in fields:
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.Password)
            existing = get_secret(key_name)
            if existing:
                edit.setPlaceholderText("Configured in Windows Credential Manager")
            else:
                edit.setPlaceholderText("Paste key here…")
            form.addRow(f"{title}:", edit)
            self.key_inputs[key_name] = edit

        lay.addLayout(form)

        save_btn = QPushButton("Save Keys to Credential Manager")
        save_btn.clicked.connect(self._save_keys)
        lay.addWidget(save_btn)

        lay.addStretch()
        return w

    def _save_keys(self):
        saved = 0
        for key_name, edit in self.key_inputs.items():
            val = edit.text().strip()
            if val:
                save_secret(key_name, val)
                edit.clear()
                edit.setPlaceholderText("Configured in Windows Credential Manager")
                saved += 1

        from siri import reload_all_keys
        reload_all_keys()

        still_missing = missing_secrets()
        if still_missing:
            names = ", ".join(k.replace("_API_KEY", "") for k in still_missing)
            QMessageBox.warning(self, "Keys Needed", f"Keys saved. Still needed for full operation: {names}")
        else:
            QMessageBox.information(self, "Keys Saved", "All keys securely stored. Voice assistant ready.")
            self._start_worker()

    # ---------------- Application Lifecycle & Worker ----------------
    def _start_worker(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return
        self.worker_thread = threading.Thread(target=self._run_assistant, daemon=True, name="HeyJevVoiceWorker")
        self.worker_thread.start()

    def _run_assistant(self):
        from siri import run_voice_assistant
        try:
            run_voice_assistant(
                notify=lambda s, d="": self.signals.status_updated.emit(s, d),
                controls=self.controls_queue,
                mode=self.settings.get("mode", "ptt")
            )
        except Exception as e:
            self.signals.status_updated.emit("Something went wrong", str(e))

    def _on_status_updated(self, state: str, detail: str = ""):
        self.status_label.setText(state)
        self.detail_label.setText(detail)
        color = STATUS_COLORS.get(state, "#94a3b8")
        self.dot.set_color(color)

        if state == "Dictating":
            self.signals.bubble_requested.emit(True)
        elif state in ("Ready", "Thinking", "Doing it"):
            self.signals.bubble_requested.emit(False)

    def _on_bubble_requested(self, visible: bool):
        if visible:
            self.bubble.show_bubble()
        else:
            self.bubble.hide_bubble()

    def _on_timer_tick(self):
        """Update live countdowns for active timers."""
        try:
            from timers import timer_snapshot
            timers = timer_snapshot()[:3]
            if not timers:
                self.timers_widget.hide()
                return

            # Clear old widgets
            while self.timers_layout.count():
                child = self.timers_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            for name, left in timers:
                m, sec = divmod(int(left + 0.999), 60)
                h, m = divmod(m, 60)
                time_str = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

                row = QWidget()
                row_lay = QHBoxLayout(row)
                row_lay.setContentsMargins(4, 2, 4, 2)

                lbl_name = QLabel(name or "Timer")
                lbl_name.setStyleSheet("color: #cbd5e1; font-size: 11px;")
                row_lay.addWidget(lbl_name)

                lbl_time = QLabel(time_str)
                lbl_time.setFont(QFont("Consolas", 12, QFont.Bold))
                lbl_time.setStyleSheet("color: #38bdf8;")
                row_lay.addWidget(lbl_time)

                self.timers_layout.addWidget(row)

            self.timers_widget.show()
        except Exception:
            pass

    def keyPressEvent(self, event):
        """Esc key cancels pending safety confirmations and current actions."""
        if event.key() == Qt.Key_Escape:
            self._on_esc_pressed()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_esc_pressed(self):
        from safety_engine import request_global_cancel
        request_global_cancel("Esc key pressed")
        self.signals.status_updated.emit("Cancelled", "Action cancelled by Esc key")

    def closeEvent(self, event):
        """Dock-hide parity: Close hides window to system tray; quit is explicit."""
        if self.settings.get("close_to_tray", True):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "Hey Jev",
                "Listening in background. Double-click tray icon to open.",
                QSystemTrayIcon.Information,
                1500
            )
        else:
            self._quit_application()

    def nativeEvent(self, event_type, message):
        """Handle native Windows events."""
        if sys.platform == "win32" and event_type == b"windows_generic_MSG":
            try:
                import ctypes.wintypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                # 0x0111 is WM_COMMAND (standard Win32 command / test quit path)
                if msg.message == 0x0111:
                    self._quit_application()
                    return True, 0
            except Exception:
                pass
        return False, 0

    def open_mission_center(self):
        """Open or bring to front the Hey Jev Mission Center window."""
        if getattr(self, "_mission_center_window", None) is not None:
            self._mission_center_window.show()
            self._mission_center_window.raise_()
            self._mission_center_window.activateWindow()
            return self._mission_center_window

        try:
            from mission_center import MissionCenterWindow
            from runtime_manager import get_runtime
            runtime = get_runtime()
            self._mission_center_window = MissionCenterWindow(runtime=runtime, parent=None)
            self._mission_center_window.show()
            self._mission_center_window.raise_()
            self._mission_center_window.activateWindow()
            return self._mission_center_window
        except Exception as e:
            QMessageBox.warning(self, "Mission Center Error", f"Could not open Mission Center: {e}")
            return None

    def _quit_application(self):
        if getattr(self, "_mission_center_window", None) is not None:
            try:
                self._mission_center_window.close()
            except Exception:
                pass
        try:
            from runtime_manager import stop_runtime
            stop_runtime()
        except Exception:
            pass
        self.bubble.close()
        QApplication.quit()


def run_app():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Hey Jev")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    run_app()
