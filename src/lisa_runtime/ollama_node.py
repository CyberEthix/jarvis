"""Local or remote Ollama adapter used as a cognitive capability node."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(slots=True)
class OllamaNode:
    endpoint: str = "http://127.0.0.1:11434"
    model: str = "gemma4:e2b"
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

    def _resolve_model(self) -> str:
        """Return the configured model when installed, otherwise a safe chat fallback."""
        health = self.health()
        installed = [name for name in health.get("models", []) if name]
        if self.model in installed:
            return self.model

        chat_models = [
            name for name in installed
            if "embed" not in name.lower()
        ]
        if chat_models:
            self.model = chat_models[0]
            return self.model

        raise RuntimeError(
            "No usable Ollama chat model is installed. "
            f"Configured model: {self.model}. Installed models: {installed or 'none'}."
        )

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        model = self._resolve_model()
        payload: dict[str, Any] = {
            "model": model,
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
        if response.status_code == 404:
            raise RuntimeError(
                f"Ollama could not find model '{model}'. Run 'ollama list' and set "
                "LISA_OLLAMA_MODEL to an installed chat model."
            )
        response.raise_for_status()
        return str(response.json().get("response", "")).strip()
