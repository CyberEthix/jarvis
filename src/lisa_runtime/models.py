"""Data contracts for the LISA cognitive runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RuntimeState(StrEnum):
    IDLE = "IDLE"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    RESPONDING = "RESPONDING"
    RESEARCH_QUEUED = "RESEARCH_QUEUED"
    RESEARCHING = "RESEARCHING"
    CONSOLIDATING = "CONSOLIDATING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    ERROR = "ERROR"


class JobStatus(StrEnum):
    NEW = "NEW"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class NodeCapability(StrEnum):
    CHAT = "chat"
    PLANNING = "planning"
    SUMMARIZATION = "summarization"
    EMBEDDING = "embedding"
    RESEARCH = "research"
    AUTOMATION = "automation"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ResearchJob:
    question: str
    rationale: str = ""
    source_conversation_id: str | None = None
    priority: int = 3
    max_iterations: int = 5
    max_runtime_seconds: int = 600
    max_retries: int = 2
    max_sources: int = 8
    status: JobStatus = JobStatus.NEW
    id: int | None = None
    attempts: int = 0
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    heartbeat_at: str | None = None
    error: str | None = None


@dataclass(slots=True)
class NodeDescriptor:
    node_id: str
    node_type: str
    capabilities: set[NodeCapability]
    endpoint: str
    models: list[str] = field(default_factory=list)
    healthy: bool = False
    current_load: float = 0.0
    last_seen: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
