"""Minimal PySide6 text-first desktop interface for the LISA runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LISA Local Cognitive Runtime")
        self.resize(1100, 720)

        db_path = Path(
            os.getenv(
                "LISA_RUNTIME_DB",
                "~/.local/share/lisa-runtime/lisa_runtime.db",
            )
        ).expanduser()
        endpoint = os.getenv("LISA_OLLAMA_ENDPOINT", "http://127.0.0.1:11434")
        model = os.getenv("LISA_OLLAMA_MODEL", "gemma4:e2b")

        self.repository = SQLiteRepository(db_path)
        self.orchestrator = CognitiveOrchestrator(
            repository=self.repository,
            node=OllamaNode(endpoint=endpoint, model=model),
            state_listener=self._on_state,
        )
        self.worker: TaskThread | None = None

        self.status_label = QLabel(f"IDLE — Ready | Model: {model}")
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
        splitter.setSizes([760, 340])

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.status_label)
        layout.addWidget(splitter)
        self.setCentralWidget(central)
        self._refresh_jobs()

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
        for job in self.repository.list_jobs():
            self.jobs_list.addItem(
                f"#{job['id']} [{job['status']}] {job['question']}"
            )

    def _on_state(self, state: RuntimeState, detail: str) -> None:
        self.status_label.setText(f"{state.value} — {detail}")

    def _show_error(self, message: str) -> None:
        self.status_label.setText(f"ERROR — {message}")
        QMessageBox.critical(self, "LISA Runtime Error", message)
        self._refresh_jobs()


def main() -> int:
    app = QApplication(sys.argv)
    window = LisaWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
