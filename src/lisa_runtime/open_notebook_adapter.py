"""Governed REST adapter for an optional local Open Notebook research node.

The adapter is deliberately separate from the LISA authoritative store. Open
Notebook may ingest, index, search, and synthesize research content, while the
LISA RDBMS remains authoritative for identity, cognition, governance,
commitments, evaluations, and the executive-assistant workflow.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


@dataclass(frozen=True)
class OpenNotebookHealth:
    online: bool
    endpoint: str
    detail: str = ''


class OpenNotebookAdapter:
    def __init__(self, endpoint: str | None = None, password: str | None = None,
                 timeout: float = 8.0) -> None:
        self.endpoint = (endpoint or os.getenv('LISA_OPEN_NOTEBOOK_URL') or 'http://127.0.0.1:5055').rstrip('/')
        self.password = password if password is not None else os.getenv('LISA_OPEN_NOTEBOOK_PASSWORD', '')
        self.timeout = timeout
        self.session = requests.Session()

    @property
    def headers(self) -> dict[str, str]:
        headers = {'Accept': 'application/json'}
        if self.password:
            headers['Authorization'] = f'Bearer {self.password}'
        return headers

    def _get(self, paths: list[str], params: dict[str, Any] | None = None) -> Any:
        errors: list[str] = []
        for path in paths:
            try:
                response = self.session.get(
                    f'{self.endpoint}{path}', headers=self.headers, params=params, timeout=self.timeout,
                )
                if response.status_code == 404:
                    errors.append(f'{path}:404')
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                errors.append(f'{path}:{exc}')
        raise RuntimeError('; '.join(errors) or 'Open Notebook request failed')

    @staticmethod
    def _records(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ('items', 'results', 'data', 'notebooks', 'sources', 'notes'):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        return []

    def health(self) -> OpenNotebookHealth:
        try:
            payload = self._get(['/health', '/api/health'])
            detail = payload.get('status') if isinstance(payload, dict) else str(payload)
            return OpenNotebookHealth(True, self.endpoint, str(detail or 'online'))
        except Exception as exc:
            return OpenNotebookHealth(False, self.endpoint, str(exc))

    def list_notebooks(self) -> list[dict[str, Any]]:
        return self._records(self._get(['/api/notebooks', '/notebooks']))

    def list_sources(self, notebook_id: str) -> list[dict[str, Any]]:
        params = {'notebook_id': notebook_id, 'limit': 500, 'offset': 0}
        encoded = quote(str(notebook_id), safe=':_-')
        payload = self._get(
            ['/api/sources', '/sources', f'/api/notebooks/{encoded}/sources', f'/notebooks/{encoded}/sources'],
            params=params,
        )
        return self._records(payload)

    def list_notes(self, notebook_id: str) -> list[dict[str, Any]]:
        params = {'notebook_id': notebook_id, 'limit': 500, 'offset': 0}
        encoded = quote(str(notebook_id), safe=':_-')
        payload = self._get(
            ['/api/notes', '/notes', f'/api/notebooks/{encoded}/notes', f'/notebooks/{encoded}/notes'],
            params=params,
        )
        return self._records(payload)

    @staticmethod
    def record_id(record: dict[str, Any]) -> str:
        value = record.get('id') or record.get('source_id') or record.get('note_id')
        return str(value or '')

    @staticmethod
    def record_title(record: dict[str, Any], fallback: str) -> str:
        return str(record.get('title') or record.get('name') or record.get('filename') or fallback)

    @staticmethod
    def record_body(record: dict[str, Any]) -> str:
        for key in ('content', 'full_text', 'text', 'summary', 'description'):
            value = record.get(key)
            if value:
                return str(value)
        return ''
