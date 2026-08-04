"""Local or remote Ollama adapter used as a cognitive capability node."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(slots=True)
class OllamaNode:
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    timeout_seconds: int = 180

    def health(self) -> dict[str, Any]:
        response = requests.get(f"{self.endpoint.rstrip('/')}/api/tags", timeout=10)
        response.raise_for_status()
        payload = response.json()
        return {
            "healthy": True,
            "endpoint": self.endpoint,
            "models": [item.get("name", "") for item in payload.get("models", [])],
        }

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        response = requests.post(
            f"{self.endpoint.rstrip('/')}/api/generate",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return str(response.json().get("response", "")).strip()
