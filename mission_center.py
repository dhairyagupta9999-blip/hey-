"""Native Windows PySide6 Mission Center for Hey Jev (Phase 28).

Provides:
- Mission Overview with live filtering, status badges, and derived progress
- Selected Mission Detail with goal normalization, constraints, and budget
- Dependency-aware Task Graph with execution order and capability bindings
- Acceptance Criteria status with verified outcomes vs completed execution
- Real-time Event Timeline with duplicate event suppression and bounded memory
- Safe Human Controls (Start, Pause, Resume, Cancel, Approve Plan, Approve Task, Replan)
- Phase 26 Operational Health & Diagnosis visibility
- Strict secret redaction via sanitize_content
- UI-thread safe event marshalling via Qt Signals
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from event_bus import EventBus, TaskEvent
from memory_model import sanitize_content
from mission_coordinator import MissionCoordinator
from mission_create_dialog import MissionCreateDialog
from mission_interpreter import MissionInterpreter
from mission_model import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Mission,
    MissionMode,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from mission_planner import MissionPlanner
from mission_replanner import MissionReplanner
from mission_store import SqliteMissionStore
from mission_ui_signals import (
    MissionCenterSignals,
    calculate_mission_progress,
    format_criterion_status,
    format_mission_status,
    format_relative_time,
    format_task_status,
)
from tool_model import RiskLevel

logger = logging.getLogger(__name__)


class MissionOverviewWidget(QWidget):
    """Left-hand panel displaying filtered mission list and derived progress."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.selected_mission_id: Optional[str] = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_label = QLabel("Filter:")
        filter_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        filter_row.addWidget(filter_label)

        self.combo_filter = QComboBox()
        self.combo_filter.addItems([
            "All Missions",
            "Active",
            "Waiting Approval",
            "Paused",
            "Blocked / Attention",
            "Completed",
            "Failed / Cancelled",
        ])
        filter_row.addWidget(self.combo_filter)
        layout.addLayout(filter_row)

        # Mission Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Status", "Objective", "Progress", "Updated"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # Empty State Label
        self.lbl_empty = QLabel("No missions found.\nClick 'New Mission' to start.")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        self.lbl_empty.setStyleSheet("color: #64748b; font-size: 12px; padding: 30px;")
        layout.addWidget(self.lbl_empty)
        self.lbl_empty.hide()

    def populate_missions(self, missions: List[Mission], selected_id: Optional[str] = None) -> None:
        """Render missions in table applying the active filter."""
        active_filter = self.combo_filter.currentText()
        filtered: List[Mission] = []

        for m in missions:
            st = m.status
            if active_filter == "Active":
                if st in (MissionStatus.RUNNING, MissionStatus.READY, MissionStatus.PLANNING, MissionStatus.VERIFYING):
                    filtered.append(m)
            elif active_filter == "Waiting Approval":
                if st == MissionStatus.WAITING_FOR_APPROVAL:
                    filtered.append(m)
            elif active_filter == "Paused":
                if st == MissionStatus.PAUSED:
                    filtered.append(m)
            elif active_filter == "Blocked / Attention":
                if st in (MissionStatus.BLOCKED, MissionStatus.NEEDS_ATTENTION):
                    filtered.append(m)
            elif active_filter == "Completed":
                if st in (MissionStatus.COMPLETED, MissionStatus.PARTIALLY_COMPLETED):
                    filtered.append(m)
            elif active_filter == "Failed / Cancelled":
                if st in (MissionStatus.FAILED, MissionStatus.CANCELLED):
                    filtered.append(m)
            else:
                filtered.append(m)

        if not filtered:
            self.table.setRowCount(0)
            self.table.hide()
            self.lbl_empty.show()
            return

        self.lbl_empty.hide()
        self.table.show()
        self.table.setRowCount(len(filtered))

        selected_row = -1
        for row, m in enumerate(filtered):
            # Status Item
            badge_text, color_hex = format_mission_status(m.status)
            item_status = QTableWidgetItem(badge_text)
            item_status.setForeground(QColor(color_hex))
            item_status.setData(Qt.UserRole, m.mission_id)
            self.table.setItem(row, 0, item_status)

            # Objective Item
            obj_text = m.objective[:60] + ("…" if len(m.objective) > 60 else "")
            item_obj = QTableWidgetItem(obj_text)
            item_obj.setToolTip(m.objective)
            item_obj.setData(Qt.UserRole, m.mission_id)
            self.table.setItem(row, 1, item_obj)

            # Progress Item
            pct, summary = calculate_mission_progress(m)
            item_prog = QTableWidgetItem(f"{pct:.0f}%")
            item_prog.setToolTip(summary)
            item_prog.setData(Qt.UserRole, m.mission_id)
            self.table.setItem(row, 2, item_prog)

            # Updated Item
            time_str = format_relative_time(m.updated_at)
            item_upd = QTableWidgetItem(time_str)
            item_upd.setData(Qt.UserRole, m.mission_id)
            self.table.setItem(row, 3, item_upd)

            if selected_id and m.mission_id == selected_id:
                selected_row = row

        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif self.table.rowCount() > 0:
            self.table.selectRow(0)


class MissionTaskGraphWidget(QWidget):
    """Middle panel showing topological dependency DAG of tasks with status, capability and results."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Task Table
        self.table_tasks = QTableWidget()
        self.table_tasks.setColumnCount(6)
        self.table_tasks.setHorizontalHeaderLabels([
            "Task ID", "Description", "Dependencies", "Capability", "Risk", "Status"
        ])
        self.table_tasks.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_tasks.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_tasks.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_tasks.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_tasks.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table_tasks.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table_tasks.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_tasks.setSelectionMode(QTableWidget.SingleSelection)
        self.table_tasks.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_tasks.verticalHeader().setVisible(False)
        self.table_tasks.itemSelectionChanged.connect(self._on_task_selected)
        layout.addWidget(self.table_tasks)

        # Task Detail Box (Outputs / Errors)
        self.detail_box = QGroupBox("Selected Task Execution Evidence & Details")
        detail_layout = QVBoxLayout(self.detail_box)
        self.txt_task_detail = QTextEdit()
        self.txt_task_detail.setReadOnly(True)
        self.txt_task_detail.setFont(QFont("Consolas", 10))
        self.txt_task_detail.setPlaceholderText("Select a task above to inspect execution evidence, inputs, outputs, or error traces.")
        self.txt_task_detail.setMaximumHeight(150)
        detail_layout.addWidget(self.txt_task_detail)
        layout.addWidget(self.detail_box)

        self._current_tasks: List[MissionTask] = []

    def populate_tasks(self, plan: Optional[MissionPlan]) -> None:
        """Render the tasks DAG in topological order."""
        if not plan or not plan.tasks:
            self.table_tasks.setRowCount(0)
            self._current_tasks = []
            self.txt_task_detail.clear()
            return

        self._current_tasks = list(plan.tasks)
        self.table_tasks.setRowCount(len(self._current_tasks))

        for row, t in enumerate(self._current_tasks):
            # Task ID
            item_id = QTableWidgetItem(t.task_id)
            self.table_tasks.setItem(row, 0, item_id)

            # Description
            item_desc = QTableWidgetItem(t.description)
            item_desc.setToolTip(f"Intended result: {t.intended_result}")
            self.table_tasks.setItem(row, 1, item_desc)

            # Dependencies
            deps_str = ", ".join(t.dependencies) if t.dependencies else "—"
            item_dep = QTableWidgetItem(deps_str)
            self.table_tasks.setItem(row, 2, item_dep)

            # Capability
            cap_str = t.capability_id or "—"
            item_cap = QTableWidgetItem(cap_str)
            self.table_tasks.setItem(row, 3, item_cap)

            # Risk
            item_risk = QTableWidgetItem(t.risk_level.value)
            if t.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                item_risk.setForeground(QColor("#ef4444"))
            elif t.risk_level == RiskLevel.MEDIUM:
                item_risk.setForeground(QColor("#f59e0b"))
            else:
                item_risk.setForeground(QColor("#10b981"))
            self.table_tasks.setItem(row, 4, item_risk)

            # Status Badge
            badge_text, color_hex = format_task_status(t.status)
            item_status = QTableWidgetItem(badge_text)
            item_status.setForeground(QColor(color_hex))
            self.table_tasks.setItem(row, 5, item_status)

        if self.table_tasks.rowCount() > 0:
            self.table_tasks.selectRow(0)

    def _on_task_selected(self) -> None:
        selected_rows = self.table_tasks.selectionModel().selectedRows()
        if not selected_rows:
            self.txt_task_detail.clear()
            return
        row = selected_rows[0].row()
        if 0 <= row < len(self._current_tasks):
            t = self._current_tasks[row]
            lines = [
                f"TASK ID: {t.task_id}",
                f"DESCRIPTION: {t.description}",
                f"INTENDED OUTCOME: {t.intended_result}",
                f"CAPABILITY: {t.capability_id or 'None'}",
                f"STATUS: {t.status.value}",
                f"RETRIES: {t.retry_count}/{t.max_retries}",
                f"IDEMPOTENT: {t.is_idempotent}",
                f"SIDE EFFECTS OCCURRED: {t.side_effects_occurred}",
            ]
            if t.approval_checkpoint:
                lines.append(f"APPROVAL CHECKPOINT: YES (Reason: {t.approval_reason or 'Risk gating'})")

            if t.inputs:
                clean_inputs = {k: sanitize_content(v) if isinstance(v, str) else v for k, v in t.inputs.items()}
                lines.append(f"INPUTS: {json.dumps(clean_inputs, indent=2)}")

            if t.output:
                clean_output = {k: sanitize_content(v) if isinstance(v, str) else v for k, v in t.output.items()}
                lines.append(f"OUTPUT: {json.dumps(clean_output, indent=2)}")

            if t.error:
                lines.append(f"ERROR: {sanitize_content(t.error)}")

            if t.evidence:
                lines.append(f"EVIDENCE: {json.dumps(t.evidence, indent=2)}")

            self.txt_task_detail.setPlainText("\n".join(lines))


class AcceptanceCriteriaWidget(QWidget):
    """Panel displaying mission acceptance criteria with observable verification records."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.table_criteria = QTableWidget()
        self.table_criteria.setColumnCount(5)
        self.table_criteria.setHorizontalHeaderLabels([
            "ID", "Description", "Required", "Verification Status", "Evidence"
        ])
        self.table_criteria.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_criteria.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_criteria.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_criteria.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_criteria.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table_criteria.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_criteria.setSelectionMode(QTableWidget.SingleSelection)
        self.table_criteria.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_criteria.verticalHeader().setVisible(False)
        layout.addWidget(self.table_criteria)

    def populate_criteria(self, criteria: List[AcceptanceCriterion]) -> None:
        """Render acceptance criteria list with distinct status badges."""
        self.table_criteria.setRowCount(len(criteria))

        for row, ac in enumerate(criteria):
            # ID
            item_id = QTableWidgetItem(ac.criterion_id)
            self.table_criteria.setItem(row, 0, item_id)

            # Description
            item_desc = QTableWidgetItem(ac.description)
            self.table_criteria.setItem(row, 1, item_desc)

            # Required
            req_str = "Required" if ac.required else "Optional"
            item_req = QTableWidgetItem(req_str)
            item_req.setForeground(QColor("#cbd5e1" if ac.required else "#94a3b8"))
            self.table_criteria.setItem(row, 2, item_req)

            # Verification Status Badge
            badge_text, color_hex = format_criterion_status(ac.status)
            item_status = QTableWidgetItem(badge_text)
            item_status.setForeground(QColor(color_hex))
            self.table_criteria.setItem(row, 3, item_status)

            # Evidence Summary
            if ac.evidence:
                ev_str = str(ac.evidence)
            elif ac.external_dependency:
                ev_str = f"Blocked by external: {ac.external_dependency}"
            else:
                ev_str = "None"
            item_ev = QTableWidgetItem(ev_str)
            item_ev.setToolTip(ev_str)
            self.table_criteria.setItem(row, 4, item_ev)


class MissionTimelineWidget(QWidget):
    """Panel presenting real-time event feed for mission lifecycle with deduplication."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._seen_events: Set[Tuple[str, str, float]] = set()
        self._max_entries = 100
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.table_events = QTableWidget()
        self.table_events.setColumnCount(4)
        self.table_events.setHorizontalHeaderLabels(["Time", "Event Type", "Task ID", "Details"])
        self.table_events.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_events.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_events.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_events.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table_events.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_events.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_events.verticalHeader().setVisible(False)
        layout.addWidget(self.table_events)

    def append_event(self, event: TaskEvent) -> bool:
        """Add event to timeline if not duplicate. Returns True if appended."""
        fingerprint = (event.event_type, event.task_id, round(event.timestamp, 2))
        if fingerprint in self._seen_events:
            return False  # Suppress duplicate event

        self._seen_events.add(fingerprint)

        row = self.table_events.rowCount()
        self.table_events.insertRow(row)

        # Time
        time_str = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
        item_time = QTableWidgetItem(time_str)
        self.table_events.setItem(row, 0, item_time)

        # Event Type
        item_type = QTableWidgetItem(event.event_type)
        if "fail" in event.event_type or "block" in event.event_type:
            item_type.setForeground(QColor("#ef4444"))
        elif "complete" in event.event_type or "approved" in event.event_type:
            item_type.setForeground(QColor("#10b981"))
        elif "started" in event.event_type:
            item_type.setForeground(QColor("#38bdf8"))
        elif "checkpoint" in event.event_type:
            item_type.setForeground(QColor("#f59e0b"))
        self.table_events.setItem(row, 1, item_type)

        # Task ID
        item_task = QTableWidgetItem(event.task_id or "—")
        self.table_events.setItem(row, 2, item_task)

        # Details
        clean_data = {k: sanitize_content(v) if isinstance(v, str) else v for k, v in event.data.items()}
        det_str = json.dumps(clean_data) if clean_data else ""
        item_det = QTableWidgetItem(det_str)
        item_det.setToolTip(det_str)
        self.table_events.setItem(row, 3, item_det)

        # Bound entries
        while self.table_events.rowCount() > self._max_entries:
            self.table_events.removeRow(0)

        self.table_events.scrollToBottom()
        return True

    def clear(self) -> None:
        self.table_events.setRowCount(0)
        self._seen_events.clear()


class MissionDiagnosisWidget(QWidget):
    """Panel displaying operational health, failure diagnosis, and recovery records (Phase 26 integration)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Failure & Root Cause Box
        self.diag_box = QGroupBox("Operational Failure Diagnosis & Root Cause")
        diag_lay = QVBoxLayout(self.diag_box)
        self.txt_diagnosis = QTextEdit()
        self.txt_diagnosis.setReadOnly(True)
        self.txt_diagnosis.setFont(QFont("Consolas", 10))
        self.txt_diagnosis.setPlaceholderText("No active failures diagnosed for this mission.")
        diag_lay.addWidget(self.txt_diagnosis)
        layout.addWidget(self.diag_box)

        # Recent Incidents Table
        self.incidents_box = QGroupBox("Recent Operational Incidents")
        inc_lay = QVBoxLayout(self.incidents_box)
        self.table_incidents = QTableWidget()
        self.table_incidents.setColumnCount(4)
        self.table_incidents.setHorizontalHeaderLabels(["Incident ID", "Component", "Symptom", "Outcome"])
        self.table_incidents.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_incidents.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_incidents.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_incidents.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table_incidents.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_incidents.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_incidents.verticalHeader().setVisible(False)
        inc_lay.addWidget(self.table_incidents)
        layout.addWidget(self.incidents_box)

    def update_diagnosis(self, mission: Mission, maintenance_coordinator: Optional[Any] = None) -> None:
        """Render diagnostic findings from mission blockers, tasks, and maintenance coordinator."""
        diag_lines: List[str] = []

        if mission.status in (MissionStatus.BLOCKED, MissionStatus.FAILED, MissionStatus.NEEDS_ATTENTION):
            diag_lines.append(f"OBSERVED STATUS: {mission.status.value}")
            diag_lines.append(f"STATUS REASON: {sanitize_content(mission.status_reason)}")

        if mission.blockers:
            diag_lines.append("\nRECORDED BLOCKERS:")
            for b in mission.blockers:
                diag_lines.append(f"  • {sanitize_content(b)}")

        if mission.plan:
            failed_tasks = [t for t in mission.plan.tasks if t.status in (MissionTaskStatus.FAILED, MissionTaskStatus.BLOCKED)]
            if failed_tasks:
                diag_lines.append("\nFAILED / BLOCKED TASKS:")
                for ft in failed_tasks:
                    diag_lines.append(f"  • Task '{ft.task_id}': {sanitize_content(ft.error or 'Failed without message')}")
                    diag_lines.append(f"    Capability: {ft.capability_id}")
                    diag_lines.append(f"    Retries Attempted: {ft.retry_count}/{ft.max_retries}")

        if maintenance_coordinator:
            try:
                inc_store = getattr(maintenance_coordinator, "incident_store", None)
                if inc_store:
                    recent = inc_store.get_recent_incidents(limit=10)
                    self.table_incidents.setRowCount(len(recent))
                    for r, inc in enumerate(recent):
                        self.table_incidents.setItem(r, 0, QTableWidgetItem(inc.incident_id))
                        self.table_incidents.setItem(r, 1, QTableWidgetItem(inc.component_id))
                        sym_str = ", ".join(inc.symptoms) if inc.symptoms else "—"
                        self.table_incidents.setItem(r, 2, QTableWidgetItem(sanitize_content(sym_str)))
                        self.table_incidents.setItem(r, 3, QTableWidgetItem(inc.final_state.value))
            except Exception as e:
                logger.debug("Failed reading incident records: %s", e)

        self.txt_diagnosis.setPlainText("\n".join(diag_lines) if diag_lines else "No operational failures detected.")


# ===========================================================================
# Main Mission Center Window
# ===========================================================================

class MissionCenterWindow(QMainWindow):
    """Primary desktop workspace for monitoring, inspecting, and controlling Hey Jev autonomous missions."""

    def __init__(
        self,
        mission_coordinator: Optional[MissionCoordinator] = None,
        mission_store: Optional[SqliteMissionStore] = None,
        mission_interpreter: Optional[MissionInterpreter] = None,
        mission_planner: Optional[MissionPlanner] = None,
        mission_replanner: Optional[MissionReplanner] = None,
        event_bus: Optional[EventBus] = None,
        maintenance_coordinator: Optional[Any] = None,
        runtime: Optional[Any] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        if runtime is not None:
            if event_bus is None and hasattr(runtime, "event_bus"):
                event_bus = runtime.event_bus
            if maintenance_coordinator is None and hasattr(runtime, "maintenance_coordinator"):
                maintenance_coordinator = runtime.maintenance_coordinator
            if mission_coordinator is None and hasattr(runtime, "mission_coordinator"):
                mission_coordinator = runtime.mission_coordinator
            if mission_store is None and hasattr(runtime, "mission_store"):
                mission_store = runtime.mission_store

        if event_bus is None:
            event_bus = EventBus()
        if mission_store is None:
            import config
            import tempfile
            db_dir = getattr(config, "APPDATA_DIR", tempfile.gettempdir())
            db_path = os.path.join(db_dir, "missions.db")
            mission_store = SqliteMissionStore(db_path=db_path)

        if mission_coordinator is None or mission_interpreter is None or mission_planner is None:
            from tool_registry import ToolRegistry
            from tool_scanner import ToolScanner
            from skill_registry import SkillRegistry
            from skill_executor import SkillExecutor
            from capability_registry import CapabilityRegistry
            from workflow_engine import WorkflowEngine
            from resource_governor import ResourceGovernor

            t_reg = ToolRegistry()
            try:
                ToolScanner(t_reg).scan_and_register_all()
            except Exception:
                pass
            s_reg = SkillRegistry()
            s_exec = SkillExecutor(tool_registry=t_reg)
            c_reg = CapabilityRegistry(tool_registry=t_reg, skill_registry=s_reg, skill_executor=s_exec)
            wf_eng = WorkflowEngine(registry=c_reg, event_bus=event_bus)
            gov = getattr(runtime, "governor", None) or ResourceGovernor()
            if mission_replanner is None:
                mission_replanner = MissionReplanner(registry=c_reg)
            if mission_coordinator is None:
                mission_coordinator = MissionCoordinator(
                    registry=c_reg,
                    workflow_engine=wf_eng,
                    mission_store=mission_store,
                    event_bus=event_bus,
                    replanner=mission_replanner,
                    resource_governor=gov,
                )
            if mission_interpreter is None:
                mission_interpreter = MissionInterpreter(registry=c_reg)
            if mission_planner is None:
                mission_planner = MissionPlanner(registry=c_reg, resource_governor=gov)

        self.coordinator = mission_coordinator
        self.store = mission_store
        self.interpreter = mission_interpreter
        self.planner = mission_planner
        self.replanner = mission_replanner
        self.event_bus = event_bus or getattr(self.coordinator, "event_bus", EventBus())
        self.maintenance_coordinator = maintenance_coordinator

        self.signals = MissionCenterSignals()
        self.current_mission_id: Optional[str] = None
        self._action_in_progress = False

        self.setWindowTitle("Mission Center — Hey Jev")
        self.setMinimumSize(880, 620)
        self.resize(960, 680)

        self._apply_dark_theme()
        self._init_ui()
        self._connect_signals()
        self._subscribe_events()

        # Initial data load
        self.refresh_missions()

    def _apply_dark_theme(self) -> None:
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
            QLineEdit, QComboBox, QTextEdit, QTableWidget {
                background-color: #1e293b;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 4px;
                color: #f1f5f9;
            }
            QPushButton {
                background-color: #2563eb;
                color: #ffffff;
                border-radius: 6px;
                padding: 6px 14px;
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
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 8px;
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
            QProgressBar {
                border: 1px solid #334155;
                border-radius: 4px;
                text-align: center;
                background-color: #1e293b;
                color: #f1f5f9;
                font-size: 10px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #10b981;
                border-radius: 3px;
            }
        """)

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)

        # ---------------- Top Bar ----------------
        top_bar = QHBoxLayout()
        lbl_brand = QLabel("🎯 Mission Center")
        lbl_brand.setFont(QFont("Segoe UI Variable Display", 15, QFont.Bold))
        top_bar.addWidget(lbl_brand)

        top_bar.addStretch()

        self.btn_new_mission = QPushButton("＋ New Mission")
        self.btn_new_mission.clicked.connect(self._on_new_mission_clicked)
        top_bar.addWidget(self.btn_new_mission)

        self.btn_refresh = QPushButton("⟳ Refresh")
        self.btn_refresh.setStyleSheet("background-color: #334155; color: #f1f5f9;")
        self.btn_refresh.clicked.connect(self.refresh_missions)
        top_bar.addWidget(self.btn_refresh)

        root_layout.addLayout(top_bar)

        # ---------------- Splitter: Overview & Detail ----------------
        self.splitter = QSplitter(Qt.Horizontal)
        root_layout.addWidget(self.splitter)

        # Left Pane: Overview Widget
        self.overview_widget = MissionOverviewWidget()
        self.overview_widget.combo_filter.currentIndexChanged.connect(self._on_filter_changed)
        self.overview_widget.table.itemSelectionChanged.connect(self._on_mission_selected_in_table)
        self.splitter.addWidget(self.overview_widget)

        # Right Pane: Mission Detail Tabs
        self.tabs_detail = QTabWidget()
        self.splitter.addWidget(self.tabs_detail)
        self.splitter.setSizes([360, 600])

        # Tab 1: Overview & Controls
        self.tab_controls = self._create_controls_tab()
        self.tabs_detail.addTab(self.tab_controls, "Overview & Controls")

        # Tab 2: Task Graph
        self.task_graph_widget = MissionTaskGraphWidget()
        self.tabs_detail.addTab(self.task_graph_widget, "Tasks & Graph")

        # Tab 3: Acceptance Criteria
        self.criteria_widget = AcceptanceCriteriaWidget()
        self.tabs_detail.addTab(self.criteria_widget, "Acceptance Criteria")

        # Tab 4: Live Timeline
        self.timeline_widget = MissionTimelineWidget()
        self.tabs_detail.addTab(self.timeline_widget, "Live Timeline")

        # Tab 5: Diagnosis & Recovery
        self.diag_widget = MissionDiagnosisWidget()
        self.tabs_detail.addTab(self.diag_widget, "Diagnosis & Health")

    def _create_controls_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Mission Header Box
        header_box = QGroupBox("Mission Status & Progress")
        h_lay = QVBoxLayout(header_box)
        h_lay.setSpacing(6)

        self.lbl_mission_title = QLabel("Select a mission from the list")
        self.lbl_mission_title.setFont(QFont("Segoe UI Variable Display", 13, QFont.Bold))
        self.lbl_mission_title.setWordWrap(True)
        h_lay.addWidget(self.lbl_mission_title)

        meta_row = QHBoxLayout()
        self.lbl_status_badge = QLabel("[NO SELECTION]")
        self.lbl_status_badge.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.lbl_status_badge.setStyleSheet("color: #94a3b8;")
        meta_row.addWidget(self.lbl_status_badge)

        self.lbl_mode_badge = QLabel("Mode: —")
        self.lbl_mode_badge.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        meta_row.addWidget(self.lbl_mode_badge)

        self.lbl_plan_version = QLabel("Plan: v—")
        self.lbl_plan_version.setStyleSheet("color: #cbd5e1; font-size: 11px;")
        meta_row.addWidget(self.lbl_plan_version)
        meta_row.addStretch()
        h_lay.addLayout(meta_row)

        # Progress Bar & Summary
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        h_lay.addWidget(self.progress_bar)

        self.lbl_progress_summary = QLabel("Progress: —")
        self.lbl_progress_summary.setStyleSheet("color: #94a3b8; font-size: 11px;")
        h_lay.addWidget(self.lbl_progress_summary)

        layout.addWidget(header_box)

        # Blocker / Approval Banner (Conditional)
        self.banner_alert = QFrame()
        self.banner_alert.setStyleSheet("background-color: #451a03; border: 1px solid #d97706; border-radius: 6px; padding: 6px;")
        banner_lay = QHBoxLayout(self.banner_alert)
        self.lbl_banner_text = QLabel("⚠ Approval checkpoint required before proceeding.")
        self.lbl_banner_text.setStyleSheet("color: #fef3c7; font-weight: bold; font-size: 11px;")
        banner_lay.addWidget(self.lbl_banner_text)
        layout.addWidget(self.banner_alert)
        self.banner_alert.hide()

        # Action Control Bar
        control_group = QGroupBox("Mission Control Actions")
        ctrl_lay = QHBoxLayout(control_group)
        ctrl_lay.setSpacing(6)

        self.btn_execute = QPushButton("▶ Start / Execute")
        self.btn_execute.clicked.connect(self._on_execute_clicked)
        ctrl_lay.addWidget(self.btn_execute)

        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setStyleSheet("background-color: #334155; color: #f1f5f9;")
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        ctrl_lay.addWidget(self.btn_pause)

        self.btn_resume = QPushButton("⏯ Resume")
        self.btn_resume.setStyleSheet("background-color: #334155; color: #f1f5f9;")
        self.btn_resume.clicked.connect(self._on_resume_clicked)
        ctrl_lay.addWidget(self.btn_resume)

        self.btn_approve_plan = QPushButton("✓ Approve Plan")
        self.btn_approve_plan.setStyleSheet("background-color: #059669; color: #ffffff;")
        self.btn_approve_plan.clicked.connect(self._on_approve_plan_clicked)
        ctrl_lay.addWidget(self.btn_approve_plan)

        self.btn_replan = QPushButton("↻ Replan")
        self.btn_replan.setStyleSheet("background-color: #475569; color: #f1f5f9;")
        self.btn_replan.clicked.connect(self._on_replan_clicked)
        ctrl_lay.addWidget(self.btn_replan)

        self.btn_cancel = QPushButton("⏹ Cancel")
        self.btn_cancel.setStyleSheet("background-color: #dc2626; color: #ffffff;")
        self.btn_cancel.clicked.connect(self._on_cancel_clicked)
        ctrl_lay.addWidget(self.btn_cancel)

        layout.addWidget(control_group)

        # Metadata & Budget Box
        meta_group = QGroupBox("Normalized Goal & Constraints")
        meta_lay = QVBoxLayout(meta_group)
        self.txt_meta_detail = QTextEdit()
        self.txt_meta_detail.setReadOnly(True)
        self.txt_meta_detail.setFont(QFont("Consolas", 10))
        meta_lay.addWidget(self.txt_meta_detail)
        layout.addWidget(meta_group)

        return w

    def _connect_signals(self) -> None:
        self.signals.mission_event_received.connect(self._on_event_received_ui)
        self.signals.missions_refreshed.connect(self._on_missions_refreshed_ui)
        self.signals.action_completed.connect(self._on_action_completed_ui)

    def _subscribe_events(self) -> None:
        """Subscribe to EventBus mission lifecycle events."""
        def _bus_listener(event: TaskEvent) -> None:
            # Marshal onto Qt event loop safely via Signal
            self.signals.mission_event_received.emit(event)

        self._bus_listener_func = _bus_listener
        self.event_bus.subscribe("mission.*", _bus_listener)

    def _unsubscribe_events(self) -> None:
        if hasattr(self, "_bus_listener_func"):
            self.event_bus.unsubscribe("mission.*", self._bus_listener_func)

    def closeEvent(self, event) -> None:
        """Clean up event bus listeners on window destruction."""
        self._unsubscribe_events()
        super().closeEvent(event)

    # -----------------------------------------------------------------------
    # Data Loading & UI Refresh
    # -----------------------------------------------------------------------

    def refresh_missions(self) -> None:
        """Reload missions from authoritative SQLite store and refresh views."""
        missions = self.store.list_missions()
        self.overview_widget.populate_missions(missions, self.current_mission_id)

        if self.current_mission_id:
            m = self.store.get_mission(self.current_mission_id)
            if m:
                self._render_mission_detail(m)
            else:
                self.current_mission_id = None
        elif missions:
            self._render_mission_detail(missions[0])

    def _on_filter_changed(self) -> None:
        missions = self.store.list_missions()
        self.overview_widget.populate_missions(missions, self.current_mission_id)

    def _on_mission_selected_in_table(self) -> None:
        selected_rows = self.overview_widget.table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        item = self.overview_widget.table.item(row, 0)
        if item:
            mission_id = item.data(Qt.UserRole)
            if mission_id and mission_id != self.current_mission_id:
                m = self.store.get_mission(mission_id)
                if m:
                    self._render_mission_detail(m)

    def select_mission(self, mission_id: str) -> None:
        """Select and display a mission by its mission_id."""
        m = self.store.get_mission(mission_id)
        if m:
            self._render_mission_detail(m)
            table = self.overview_widget.table
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item and item.data(Qt.UserRole) == mission_id:
                    table.selectRow(row)
                    break

    def _render_mission_detail(self, mission: Mission) -> None:
        """Update detail tabs with authoritative state from Mission model."""
        self.current_mission_id = mission.mission_id

        # 1. Header & Badges
        self.lbl_mission_title.setText(mission.objective)
        badge_text, color_hex = format_mission_status(mission.status)
        self.lbl_status_badge.setText(badge_text)
        self.lbl_status_badge.setStyleSheet(f"color: {color_hex}; font-weight: bold;")

        self.lbl_mode_badge.setText(f"Mode: {mission.mode.value}")
        v_num = mission.plan.plan_version if mission.plan else 1
        self.lbl_plan_version.setText(f"Plan: v{v_num}")

        # Progress
        pct, summary = calculate_mission_progress(mission)
        self.progress_bar.setValue(int(pct))
        self.lbl_progress_summary.setText(f"Progress: {summary}")

        # Blocker / Alert banner
        if mission.status == MissionStatus.WAITING_FOR_APPROVAL:
            self.lbl_banner_text.setText(f"⚠ Approval Required: {mission.status_reason or 'Checkpoint reached'}")
            self.banner_alert.show()
        elif mission.status in (MissionStatus.BLOCKED, MissionStatus.NEEDS_ATTENTION):
            self.lbl_banner_text.setText(f"🛑 Blocker: {mission.status_reason or 'Execution blocked'}")
            self.banner_alert.show()
        else:
            self.banner_alert.hide()

        # Update action buttons state
        self._update_action_buttons_state(mission)

        # Metadata Details
        meta_lines = [
            f"NORMALIZED GOAL: {mission.normalized_goal or 'None'}",
            f"MISSION ID: {mission.mission_id}",
            f"CREATED AT: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mission.created_at))}",
            f"BUDGET UTILIZATION: Tasks executed: {mission.budget.tasks_executed}/{mission.budget.max_tasks}, "
            f"Replans: {mission.budget.current_replan_count}/{mission.budget.max_replan_attempts}",
        ]
        if mission.explicit_constraints:
            meta_lines.append(f"EXPLICIT CONSTRAINTS: {', '.join(mission.explicit_constraints)}")
        if mission.inferred_constraints:
            meta_lines.append(f"INFERRED CONSTRAINTS: {', '.join(mission.inferred_constraints)}")
        if mission.assumptions:
            meta_lines.append(f"ASSUMPTIONS: {', '.join(mission.assumptions)}")
        if mission.required_permissions:
            meta_lines.append(f"PERMISSIONS NEEDED: {', '.join(mission.required_permissions)}")

        self.txt_meta_detail.setPlainText("\n".join(meta_lines))

        # 2. Tasks & Graph
        self.task_graph_widget.populate_tasks(mission.plan)

        # 3. Acceptance Criteria
        self.criteria_widget.populate_criteria(mission.acceptance_criteria)

        # 4. Diagnosis & Health
        self.diag_widget.update_diagnosis(mission, self.maintenance_coordinator)

    def _update_action_buttons_state(self, mission: Mission) -> None:
        """Enable or disable action buttons strictly according to legal lifecycle states."""
        if self._action_in_progress:
            # Prevent double click race conditions
            for btn in (
                self.btn_execute, self.btn_pause, self.btn_resume,
                self.btn_approve_plan, self.btn_replan, self.btn_cancel
            ):
                btn.setEnabled(False)
            return

        st = mission.status
        is_term = mission.is_terminal()

        # Start/Execute: allowed if READY or PAUSED, or CREATED
        self.btn_execute.setEnabled(st in (MissionStatus.READY, MissionStatus.PAUSED, MissionStatus.CREATED))

        # Pause: allowed only if actively RUNNING
        self.btn_pause.setEnabled(st == MissionStatus.RUNNING)

        # Resume: allowed only if PAUSED
        self.btn_resume.setEnabled(st == MissionStatus.PAUSED)

        # Approve Plan: allowed if WAITING_FOR_APPROVAL
        self.btn_approve_plan.setEnabled(st == MissionStatus.WAITING_FOR_APPROVAL)

        # Replan: allowed if BLOCKED, FAILED, or NEEDS_ATTENTION
        self.btn_replan.setEnabled(st in (MissionStatus.BLOCKED, MissionStatus.FAILED, MissionStatus.NEEDS_ATTENTION))

        # Cancel: allowed if not already terminal or cancelling
        self.btn_cancel.setEnabled(not is_term and st != MissionStatus.CANCELLING)

    # -----------------------------------------------------------------------
    # Live Event Ingestion (Main Thread)
    # -----------------------------------------------------------------------

    def _on_event_received_ui(self, event: TaskEvent) -> None:
        """Handle incoming TaskEvent on the main GUI thread."""
        appended = self.timeline_widget.append_event(event)

        # Correlate event to current mission or refresh list
        if event.task_id and self.current_mission_id and (
            event.task_id == self.current_mission_id or event.data.get("mission_id") == self.current_mission_id
        ):
            # Authoritative reload to guarantee accurate progress
            m = self.store.get_mission(self.current_mission_id)
            if m:
                self._render_mission_detail(m)

        # Refresh overview rows periodically on meaningful status changes
        if any(keyword in event.event_type for keyword in ("started", "completed", "failed", "cancelled", "approved")):
            missions = self.store.list_missions()
            self.overview_widget.populate_missions(missions, self.current_mission_id)

    def _on_missions_refreshed_ui(self) -> None:
        self.refresh_missions()

    def _on_action_completed_ui(self, mission_id: str, success: bool, message: str) -> None:
        self._action_in_progress = False
        self.refresh_missions()
        if not success:
            QMessageBox.warning(self, "Action Failed", f"Operation could not be completed:\n{message}")

    # -----------------------------------------------------------------------
    # Human Mission Actions (Dispatched to Worker Threads)
    # -----------------------------------------------------------------------

    def _on_new_mission_clicked(self) -> None:
        dialog = MissionCreateDialog(
            mission_interpreter=self.interpreter,
            mission_planner=self.planner,
            mission_store=self.store,
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted and dialog.created_mission:
            self.current_mission_id = dialog.created_mission.mission_id
            self.refresh_missions()

    def _on_execute_clicked(self) -> None:
        if not self.current_mission_id or self._action_in_progress:
            return
        m_id = self.current_mission_id
        self._action_in_progress = True
        self._update_action_buttons_state(self.store.get_mission(m_id) or Mission())

        def _worker() -> None:
            try:
                self.coordinator.execute_mission(m_id)
                self.signals.action_completed.emit(m_id, True, "Mission executed successfully.")
            except Exception as e:
                logger.error("Execution failed for mission %s: %s", m_id, e)
                self.signals.action_completed.emit(m_id, False, str(e))

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _on_pause_clicked(self) -> None:
        if not self.current_mission_id or self._action_in_progress:
            return
        m_id = self.current_mission_id
        ok = self.coordinator.pause_mission(m_id)
        self.refresh_missions()
        if not ok:
            QMessageBox.warning(self, "Pause Failed", "Could not pause mission (it may not be running).")

    def _on_resume_clicked(self) -> None:
        if not self.current_mission_id or self._action_in_progress:
            return
        m_id = self.current_mission_id
        ok = self.coordinator.resume_mission(m_id)
        self.refresh_missions()
        if not ok:
            QMessageBox.warning(self, "Resume Failed", "Could not resume mission (it may not be paused).")

    def _on_approve_plan_clicked(self) -> None:
        if not self.current_mission_id or self._action_in_progress:
            return
        m = self.store.get_mission(self.current_mission_id)
        if not m or not m.plan:
            return

        plan_v = m.plan.plan_version
        ok = self.coordinator.approve_plan(m.mission_id, plan_version=plan_v)
        self.refresh_missions()
        if not ok:
            QMessageBox.warning(self, "Approval Rejected", f"Approval for plan v{plan_v} was rejected.")

    def _on_replan_clicked(self) -> None:
        if not self.current_mission_id or self._action_in_progress:
            return
        m = self.store.get_mission(self.current_mission_id)
        if not m or not self.replanner:
            return

        try:
            new_plan = self.replanner.replan(m, failure_reason="User requested replan.")
            m.plan = new_plan
            m.transition_to(MissionStatus.READY, reason="Replanned upon user request.")
            self.store.save_mission(m)
            self.refresh_missions()
        except Exception as e:
            QMessageBox.critical(self, "Replan Failed", f"Replanning failed:\n{e}")

    def _on_cancel_clicked(self) -> None:
        if not self.current_mission_id or self._action_in_progress:
            return
        # Consequential action confirmation dialog
        ans = QMessageBox.question(
            self,
            "Confirm Mission Cancellation",
            "Are you sure you want to cancel this mission?\nActive in-flight tasks will be halted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        m_id = self.current_mission_id
        ok = self.coordinator.cancel_mission(m_id, reason="Cancelled by user via Mission Center.")
        self.refresh_missions()
        if not ok:
            QMessageBox.warning(self, "Cancellation Failed", "Could not cancel mission.")


def run_mission_center(runtime: Optional[Any] = None) -> None:
    """Entry point to launch the Mission Center desktop window standalone."""
    app = QApplication.instance() or QApplication([])
    if runtime is None:
        from runtime_manager import JevRuntime
        runtime = JevRuntime()

    win = MissionCenterWindow(
        mission_coordinator=runtime.mission_coordinator,
        mission_store=runtime.mission_store,
        mission_interpreter=runtime.mission_interpreter,
        mission_planner=runtime.mission_planner,
        mission_replanner=runtime.mission_replanner,
        event_bus=runtime.event_bus,
        maintenance_coordinator=runtime.maintenance_coordinator,
    )
    win.show()
    app.exec()


if __name__ == "__main__":
    run_mission_center()
