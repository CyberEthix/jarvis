# LISA Cognitive Runtime

This branch introduces a text-first, local-first cognitive runtime that can later coordinate additional computers and services.

## Goals

- Text conversation is the primary interface.
- Research work is durable and survives restarts.
- The language model does not control process lifetime or scheduling.
- Every autonomous loop is bounded by iterations, runtime, retries, and source limits.
- Additional nodes can be registered later through stable capability contracts.
- No Replit or paid hosted runtime is required.

## Initial architecture

```text
PySide6 UI
    |
    v
CognitiveOrchestrator
    |-- Conversation service
    |-- Research queue
    |-- Reflection queue
    |-- Node registry
    |-- Health monitor
    `-- SQLite repository

Local node adapters
    |-- Ollama
    |-- Local files
    `-- Future NATS / n8n / remote Ollama nodes
```

## Runtime states

- IDLE
- LISTENING
- UNDERSTANDING
- PLANNING
- RESPONDING
- RESEARCH_QUEUED
- RESEARCHING
- CONSOLIDATING
- WAITING_FOR_APPROVAL
- ERROR

## Safety controls

Each job includes:

- `max_iterations`
- `max_runtime_seconds`
- `max_retries`
- `max_sources`
- explicit status transitions
- a durable heartbeat
- a final stop state

No job is allowed to recursively execute unlimited child jobs.

## First acceptance test

1. Start the desktop application.
2. Type a research question.
3. Confirm the job appears in SQLite.
4. Run one bounded worker cycle.
5. Confirm findings are stored.
6. Restart the application.
7. Retrieve the stored result.

## Planned phases

### 0.1

- text UI
- SQLite queue
- one local Ollama node
- bounded one-shot worker
- job and node indicators

### 0.2

- NATS message bus
- remote node heartbeats
- distributed capability routing

### 0.3

- reflection loop
- research proposals
- scheduled research
- memory consolidation

### 0.4

- n8n execution node
- additional model nodes
- approval workflows
- output voice service
