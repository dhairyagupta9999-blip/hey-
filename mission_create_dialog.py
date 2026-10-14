"""Mission Creation Dialog for Hey Jev (Phase 28).

Provides a focused natural-language input experience for creating persistent missions:
- Objective input with secret scrubbing
- Mode selection (MANUAL, COPILOT, AUTOPILOT) with policy boundaries explained
- Preview of interpreted goal, constraints, criteria, and proposed task DAG before commitment
- Transparent capability gap notification if required tools are unavailable
- Does not automatically execute missions upon creation
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from memory_model import sanitize_content
from mission_interpreter import MissionInterpreter
from mission_model import Mission, MissionMode, MissionPlan, MissionStatus
from mission_planner import MissionPlanner
from mission_store import SqliteMissionStore
from mission_ui_signals import format_task_status

logger = logging.getLogger(__name__)


class MissionCreateDialog(QDialog):
    """Dialog for creating a new goal-driven persistent mission."""

    def __init__(
        self,
        mission_interpreter: MissionInterpreter,
        mission_planner: MissionPlanner,
        mission_store: SqliteMissionStore,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.interpreter = mission_interpreter
        self.planner = mission_planner
        self.store = mission_store

        self.created_mission: Optional[Mission] = None
        self._preview_mission: Optional[Mission] = None

        self.setWindowTitle("Create New Mission — Hey Jev")
        self.setMinimumSize(600, 520)
        self.resize(650, 600)
        self._apply_theme()
        self._init_ui()

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QDialog, QWidget {
                background-color: #0f172a;
                color: #f1f5f9;
                font-family: 'Segoe UI Variable Text', 'Segoe UI', sans-serif;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                font-weight: bold;
                color: #cbd5e1;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QTextEdit {
                background-color: #1e293b;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 8px;
                color: #f1f5f9;
                font-size: 12px;
            }
            QTextEdit:focus {
                border: 1.5px solid #38bdf8;
            }
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 600;
                border: none;
            }
            QPushButton:hover {
                background-color: #3b82f6;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #334155;
                color: #64748b;
            }
            QRadioButton {
                color: #e2e8f0;
                font-size: 11px;
                spacing: 6px;
            }
            QRadioButton::indicator:checked {
                border: 2px solid #38bdf8;
                background-color: #38bdf8;
                border-radius: 6px;
            }
        """)

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(12)

        # Title / header
        lbl_title = QLabel("Define Autonomous Objective")
        lbl_title.setFont(QFont("Segoe UI Variable Display", 14, QFont.Bold))
        root_layout.addWidget(lbl_title)

        lbl_subtitle = QLabel(
            "State what you want Jev to accomplish. Jev will decompose your objective into verifiable steps."
        )
        lbl_subtitle.setStyleSheet("color: #94a3b8; font-size: 11px;")
        root_layout.addWidget(lbl_subtitle)

        # Objective Text Input
        self.txt_objective = QTextEdit()
        self.txt_objective.setPlaceholderText(
            "Example: Run health diagnostic on workspace, verify git status, and generate a test summary."
        )
        self.txt_objective.setMaximumHeight(90)
        root_layout.addWidget(self.txt_objective)

        # Operational Mode Selector
        mode_group = QGroupBox("Operational Autonomy Mode")
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(6)

        self.btn_group_mode = QButtonGroup(self)
        self.rb_copilot = QRadioButton("COPILOT (Recommended) — Executes safe read-only steps; pauses for human approval at risk checkpoints.")
        self.rb_manual = QRadioButton("MANUAL — Prepares verifiable plan; requires manual approval for each task before execution.")
        self.rb_autopilot = QRadioButton("AUTOPILOT — Autonomous policy execution within strict bounded resource & safety limits.")

        self.rb_copilot.setChecked(True)
        self.btn_group_mode.addButton(self.rb_copilot, 1)
        self.btn_group_mode.addButton(self.rb_manual, 2)
        self.btn_group_mode.addButton(self.rb_autopilot, 3)

        mode_layout.addWidget(self.rb_copilot)
        mode_layout.addWidget(self.rb_manual)
        mode_layout.addWidget(self.rb_autopilot)
        root_layout.addWidget(mode_group)

        # Preview Plan Section
        self.preview_group = QGroupBox("Plan Preview & Criteria")
        preview_layout = QVBoxLayout(self.preview_group)

        self.txt_preview = QTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setFont(QFont("Consolas", 10))
        self.txt_preview.setPlaceholderText("Click 'Preview Plan' to inspect decomposition before creating mission.")
        preview_layout.addWidget(self.txt_preview)
        root_layout.addWidget(self.preview_group)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_preview = QPushButton("Preview Plan")
        self.btn_preview.setStyleSheet("background-color: #334155; color: #f1f5f9;")
        self.btn_preview.clicked.connect(self._on_preview_clicked)
        btn_row.addWidget(self.btn_preview)

        btn_row.addStretch()

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setStyleSheet("background-color: #1e293b; color: #94a3b8;")
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_create = QPushButton("Create Mission")
        self.btn_create.clicked.connect(self._on_create_clicked)
        btn_row.addWidget(self.btn_create)

        root_layout.addLayout(btn_row)

    def _get_selected_mode(self) -> MissionMode:
        if self.rb_manual.isChecked():
            return MissionMode.MANUAL
        elif self.rb_autopilot.isChecked():
            return MissionMode.AUTOPILOT
        return MissionMode.COPILOT

    def _on_preview_clicked(self) -> None:
        """Interpret and generate a draft plan for user preview."""
        raw_obj = self.txt_objective.toPlainText().strip()
        if not raw_obj:
            QMessageBox.warning(self, "Objective Required", "Please enter a mission objective first.")
            return

        sanitized_obj = sanitize_content(raw_obj)
        mode = self._get_selected_mode()

        try:
            mission, meta = self.interpreter.interpret_objective(
                objective=sanitized_obj,
                mode=mode,
            )
            self.planner.create_plan(mission)
            self._preview_mission = mission

            # Format preview text
            lines = [
                f"OBJECTIVE: {mission.objective}",
                f"NORMALIZED GOAL: {mission.normalized_goal}",
                f"MODE: {mission.mode.value}",
                f"PLAN VERSION: {mission.plan.plan_version if mission.plan else 1}",
                "",
                "ACCEPTANCE CRITERIA:",
            ]
            for ac in mission.acceptance_criteria:
                req_str = "[Required]" if ac.required else "[Optional]"
                lines.append(f"  • {req_str} {ac.criterion_id}: {ac.description}")

            lines.append("")
            lines.append("PLANNED TASKS:")
            if mission.plan and mission.plan.tasks:
                for idx, t in enumerate(mission.plan.tasks, 1):
                    dep_str = f" (depends on: {', '.join(t.dependencies)})" if t.dependencies else ""
                    risk_str = f" [Risk: {t.risk_level.value}]"
                    chk_str = " [APPROVAL CHECKPOINT]" if t.approval_checkpoint else ""
                    lines.append(f"  {idx}. [{t.task_id}] {t.description}{dep_str}{risk_str}{chk_str}")
                    lines.append(f"     Capability: {t.capability_id or 'None / Unknown'}")
                    lines.append(f"     Intended: {t.intended_result}")
            else:
                lines.append("  (No tasks generated)")

            if mission.blockers:
                lines.append("")
                lines.append("BLOCKERS / WARNINGS:")
                for b in mission.blockers:
                    lines.append(f"  ⚠ {b}")

            self.txt_preview.setPlainText("\n".join(lines))
        except Exception as e:
            logger.error("Failed to preview plan: %s", e)
            self.txt_preview.setPlainText(f"Error preparing plan preview: {e}")

    def _on_create_clicked(self) -> None:
        """Save the mission to the persistent SQLite store and close dialog."""
        raw_obj = self.txt_objective.toPlainText().strip()
        if not raw_obj:
            QMessageBox.warning(self, "Objective Required", "Please enter a mission objective first.")
            return

        sanitized_obj = sanitize_content(raw_obj)
        mode = self._get_selected_mode()

        try:
            if self._preview_mission and self._preview_mission.objective == sanitized_obj:
                mission = self._preview_mission
            else:
                mission, _ = self.interpreter.interpret_objective(
                    objective=sanitized_obj,
                    mode=mode,
                )
                self.planner.create_plan(mission)

            # Persist to database
            self.store.save_mission(mission)
            self.created_mission = mission
            self.accept()
        except Exception as e:
            logger.error("Failed to create mission: %s", e)
            QMessageBox.critical(self, "Creation Failed", f"Failed to persist mission:\n{e}")
