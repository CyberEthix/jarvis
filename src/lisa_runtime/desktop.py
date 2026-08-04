"""PySide6 text-first desktop interface with thread-safe live task KPIs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .models import RuntimeState
from .ollama_node import OllamaNode
from .orchestrator import CognitiveOrchestrator
from .repository import SQLiteRepository


class TaskThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, task) -> None:
        super().__init__()
        self.task = task

    def run(self) -> None:
        try:
            self.completed.emit(self.task())
        except Exception as exc:  # pragma: no cover - UI boundary
            self.failed.emit(str(exc))


class LisaWindow(QMainWindow):
    state_event = Signal(object, str)
    progress_event = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LISA Local Cognitive Runtime")
        self.resize(1180, 780)

        db_path = Path(
            os.getenv("LISA_RUNTIME_DB", "~/.local/share/lisa-runtime/lisa_runtime.db")
        ).expanduser()
        endpoint = os.getenv("LISA_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")
        model = os.getenv("LISA_OLLAMA_MODEL", "gemma4:e2b")

        self.state_event.connect(self._apply_state)
        self.progress_event.connect(self._apply_progress)

        self.repository = SQLiteRepository(db_path)
        self.orchestrator = CognitiveOrchestrator(
            repository=self.repository,
            node=OllamaNode(endpoint=endpoint, model=model),
            state_listener=lambda state, detail: self.state_event.emit(state, detail),
            progress_listener=lambda payload: self.progress_event.emit(dict(payload)),
        )
        self.worker: TaskThread | None = None

        self.status_label = QLabel(f"IDLE — Ready | Model: {model}")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("No active task")

        self.kpi_stage, self.kpi_stage_value = self._make_kpi("Stage", "Idle")
        self.kpi_progress, self.kpi_progress_value = self._make_kpi("Progress", "0%")
        self.kpi_steps, self.kpi_steps_value = self._make_kpi("Steps", "0 / 0")
        self.kpi_elapsed, self.kpi_elapsed_value = self._make_kpi("Elapsed", "0.0 s")
        self.kpi_queue, self.kpi_queue_value = self._make_kpi("Queued", "0")
        self.kpi_complete, self.kpi_complete_value = self._make_kpi("Completed", "0")
        self.kpi_failed, self.kpi_failed_value = self._make_kpi("Failed", "0")
        self.kpi_model, self.kpi_model_value = self._make_kpi("Model", model)

        self.chat_view = QTextEdit()
        self.chat_view.setReadOnly(True)
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Type a message or research question...")
        self.send_button = QPushButton("Send")
        self.research_button = QPushButton("Queue Research")
        self.run_button = QPushButton("Run One Research Cycle")
        self.jobs_list = QListWidget()

        self.send_button.clicked.connect(self._send_chat)
        self.input_box.returnPressed.connect(self._send_chat)
        self.research_button.clicked.connect(self._queue_research)
        self.run_button.clicked.connect(self._run_research)

        kpi_panel = QWidget()
        kpi_layout = QGridLayout(kpi_panel)
        cards = [
            self.kpi_stage,
            self.kpi_progress,
            self.kpi_steps,
            self.kpi_elapsed,
            self.kpi_queue,
            self.kpi_complete,
            self.kpi_failed,
            self.kpi_model,
        ]
        for index, card in enumerate(cards):
            kpi_layout.addWidget(card, index // 4, index % 4)

        chat_panel = QWidget()
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.addWidget(self.chat_view)
        controls = QHBoxLayout()
        controls.addWidget(self.input_box)
        controls.addWidget(self.send_button)
        controls.addWidget(self.research_button)
        chat_layout.addLayout(controls)

        jobs_panel = QWidget()
        jobs_layout = QVBoxLayout(jobs_panel)
        jobs_layout.addWidget(QLabel("Research Jobs"))
        jobs_layout.addWidget(self.jobs_list)
        jobs_layout.addWidget(self.run_button)

        splitter = QSplitter()
        splitter.addWidget(chat_panel)
        splitter.addWidget(jobs_panel)
        splitter.setSizes([800, 380])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(kpi_panel)
        layout.addWidget(splitter)
        self.setCentralWidget(central)
        self._refresh_jobs()

    def _make_kpi(self, title: str, value: str) -> tuple[QWidget, QLabel]:
        card = QWidget()
        layout = QVBoxLayout(card)
        title_label = QLabel(title)
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        card.setStyleSheet(
            "QWidget { border: 1px solid #555; border-radius: 6px; padding: 4px; }"
        )
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return card, value_label

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.research_button.setEnabled(not busy)
        self.run_button.setEnabled(not busy)

    def _run_task(self, task, on_complete) -> None:
        if self.worker and self.worker.isRunning():
            return
        self._set_busy(True)
        self.worker = TaskThread(task)
        self.worker.completed.connect(on_complete)
        self.worker.completed.connect(lambda _: self._set_busy(False))
        self.worker.failed.connect(self._show_error)
        self.worker.failed.connect(lambda _: self._set_busy(False))
        self.worker.start()

    def _send_chat(self) -> None:
        message = self.input_box.text().strip()
        if not message:
            return
        self.input_box.clear()
        self.chat_view.append(f"<b>Charles:</b> {message}")
        self._run_task(
            lambda: self.orchestrator.chat(message),
            lambda response: self.chat_view.append(f"<b>LISA:</b> {response}"),
        )

    def _queue_research(self) -> None:
        question = self.input_box.text().strip()
        if not question:
            QMessageBox.information(self, "Research", "Enter a research question first.")
            return
        self.input_box.clear()
        job_id = self.orchestrator.queue_research(question)
        self.chat_view.append(f"<b>System:</b> Research job {job_id} queued: {question}")
        self._refresh_jobs()

    def _run_research(self) -> None:
        self._run_task(
            self.orchestrator.run_one_research_cycle,
            lambda job_id: self._research_finished(job_id),
        )

    def _research_finished(self, job_id: int | None) -> None:
        if job_id is None:
            self.chat_view.append("<b>System:</b> No queued research jobs.")
        else:
            report = self.repository.get_report(job_id)
            summary = report.get("summary", "") if report else ""
            self.chat_view.append(f"<b>Research {job_id}:</b> {summary}")
        self._refresh_jobs()

    def _refresh_jobs(self) -> None:
        self.jobs_list.clear()
        jobs = self.repository.list_jobs()
        for job in jobs:
            self.jobs_list.addItem(f"#{job['id']} [{job['status']}] {job['question']}")
        statuses = [str(job.get("status", "")).upper() for job in jobs]
        self.kpi_queue_value.setText(
            str(sum(s in {"NEW", "READY", "QUEUED"} for s in statuses))
        )
        self.kpi_complete_value.setText(str(sum(s == "COMPLETED" for s in statuses)))
        self.kpi_failed_value.setText(str(sum(s == "FAILED" for s in statuses)))

    def _apply_state(self, state: RuntimeState, detail: str) -> None:
        self.status_label.setText(f"{state.value} — {detail}")

    def _apply_progress(self, payload: dict) -> None:
        stage = str(payload.get("stage", "idle")).replace("_", " ").title()
        percent = int(payload.get("percent", 0) or 0)
        current_step = int(payload.get("current_step", 0) or 0)
        total_steps = int(payload.get("total_steps", 0) or 0)
        elapsed = float(payload.get("elapsed", 0.0) or 0.0)
        self.progress_bar.setValue(max(0, min(100, percent)))
        self.progress_bar.setFormat(f"{stage}: {percent}%")
        self.kpi_stage_value.setText(stage)
        self.kpi_progress_value.setText(f"{percent}%")
        self.kpi_steps_value.setText(f"{current_step} / {total_steps}")
        self.kpi_elapsed_value.setText(f"{elapsed:.1f} s")
        if stage in {"Complete", "Failed", "Queued", "Idle"}:
            self._refresh_jobs()

    def _show_error(self, message: str) -> None:
        self.status_label.setText(f"ERROR — {message}")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Task failed")
        QMessageBox.critical(self, "LISA Runtime Error", message)
        self._refresh_jobs()


def main() -> int:
    app = QApplication(sys.argv)
    window = LisaWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
