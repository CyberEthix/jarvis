"""NiceGUI workspace extension with governed Self Curiosity controls."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from nicegui import ui

from . import nicegui_app as base
from .curiosity_manager import SelfCuriosityManager


class CuriosityWorkspace(base.Workspace):
    def __init__(self) -> None:
        super().__init__()
        self.curiosity = SelfCuriosityManager(base.store, base.repo, base.node)
        self.curiosity_running = False
        self.curiosity_button = None
        self.curiosity_badge = None
        self.curiosity_next_label = None
        self.curiosity_run_label = None
        self.curiosity_dialog = None
        self.curiosity_runs_table = None

    def _enabled(self) -> bool:
        return bool(self.curiosity.policy().get('enabled', False))

    def _control_text(self) -> str:
        return 'SELF CURIOSITY: ON' if self._enabled() else 'SELF CURIOSITY: OFF'

    def _control_color(self) -> str:
        return 'positive' if self._enabled() else 'grey-7'

    def refresh_curiosity_ui(self) -> None:
        enabled = self._enabled()
        if self.curiosity_button:
            self.curiosity_button.set_text(self._control_text())
            self.curiosity_button.props(f"color={self._control_color()} unelevated")
        if self.curiosity_badge:
            self.curiosity_badge.set_text('ACTIVE' if enabled else 'PAUSED')
            self.curiosity_badge.props(f"color={'positive' if enabled else 'grey'}")
        if self.curiosity_next_label:
            self.curiosity_next_label.set_text(f"Next cycle: {self.curiosity.next_run_text()}")
        if self.curiosity_run_label:
            runs = base.store.list_curiosity_runs(limit=1)
            if not runs:
                self.curiosity_run_label.set_text('No unattended cycles recorded yet.')
            else:
                latest = runs[0]
                question = str(latest.get('proposed_question', '')).strip()
                detail = f"Last cycle: {latest.get('status', 'unknown')}"
                if question:
                    detail += f" — {question[:100]}"
                self.curiosity_run_label.set_text(detail)
        self.refresh_curiosity_runs()

    def toggle_curiosity(self) -> None:
        enabled = not self._enabled()
        self.curiosity.set_enabled(enabled)
        self.refresh_curiosity_ui()
        if enabled:
            ui.notify(
                'Self Curiosity enabled. Local, bounded, non-destructive unattended discovery is active.',
                type='positive',
                timeout=6000,
            )
        else:
            ui.notify('Self Curiosity paused. No new unattended cycles will start.', type='warning')

    async def run_curiosity_now(self) -> None:
        if self.curiosity_running or self.busy:
            ui.notify('Another cognitive task is already running.', type='warning')
            return
        if not self._enabled():
            ui.notify('Turn Self Curiosity on before running a cycle.', type='warning')
            return
        self.curiosity_running = True
        self.busy = True
        try:
            result = await asyncio.to_thread(self.curiosity.run_once)
            if result.status == 'completed':
                ui.notify(f'Self Curiosity completed research job {result.research_job_id}.', type='positive')
            elif result.status == 'queued':
                ui.notify(f'Self Curiosity queued research job {result.research_job_id}.', type='positive')
            elif result.status in {'blocked', 'duplicate', 'daily_limit', 'insufficient_context', 'no_question'}:
                ui.notify(f'Self Curiosity: {result.reason}', type='info')
            else:
                ui.notify(f'Self Curiosity cycle ended: {result.status}', type='info')
        except Exception as exc:
            ui.notify(f'Self Curiosity failed safely: {exc}', type='negative', timeout=8000)
        finally:
            self.busy = False
            self.curiosity_running = False
            self.refresh_all()
            self.refresh_curiosity_ui()

    async def curiosity_tick(self) -> None:
        self.refresh_curiosity_ui()
        if self.curiosity_running or self.busy or not self.curiosity.is_due():
            return
        await self.run_curiosity_now()

    def refresh_curiosity_runs(self) -> None:
        if not self.curiosity_runs_table:
            return
        rows: list[dict[str, Any]] = []
        for run in base.store.list_curiosity_runs(limit=100):
            rows.append({
                'id': run.get('id'),
                'status': run.get('status'),
                'question': run.get('proposed_question') or '',
                'job': run.get('research_job_id') or '',
                'started': run.get('started_at'),
                'completed': run.get('completed_at') or '',
            })
        self.curiosity_runs_table.rows = rows
        self.curiosity_runs_table.update()

    def save_curiosity_policy(self, interval: Any, daily_limit: Any, auto_execute: bool) -> None:
        try:
            policy = self.curiosity.update_policy(
                interval_minutes=int(interval),
                max_runs_per_day=int(daily_limit),
                auto_execute_research=bool(auto_execute),
                local_only=True,
            )
            ui.notify('Self Curiosity policy saved.', type='positive')
            self.refresh_curiosity_ui()
        except Exception as exc:
            ui.notify(f'Could not save policy: {exc}', type='negative')

    def show_curiosity_console(self) -> None:
        if self.curiosity_dialog:
            self.refresh_curiosity_ui()
            self.curiosity_dialog.open()

    def build_curiosity_console(self) -> None:
        policy = self.curiosity.policy()
        with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as self.curiosity_dialog:
            with ui.card().classes('w-full h-full bg-[#0c1117] text-white p-0'):
                with ui.row().classes('w-full items-center bg-[#101821] p-4'):
                    ui.icon('psychology_alt', size='32px')
                    ui.label('Self Curiosity Control Center').classes('text-h5')
                    ui.space()
                    self.curiosity_badge = ui.badge('ACTIVE' if self._enabled() else 'PAUSED')
                    ui.button(icon='close', on_click=self.curiosity_dialog.close).props('flat round')

                with ui.row().classes('w-full gap-4 p-4 items-stretch'):
                    with ui.card().classes('lisa-card flex-1'):
                        ui.label('Operating Boundary').classes('text-h6')
                        ui.markdown(
                            '**Allowed:** inspect local persisted conversations and artifacts; propose one grounded question; '
                            'queue and optionally execute one bounded local research cycle; save the proposal, report, and audit record.\n\n'
                            '**Prohibited:** shell commands, file deletion, system changes, software installation, purchases, '
                            'communications, credential access, surveillance, external side effects, recursive job spawning, '
                            'or unbounded loops.'
                        )
                    with ui.card().classes('lisa-card w-[420px]'):
                        ui.label('Main Control').classes('text-h6')
                        ui.button(self._control_text(), icon='psychology', on_click=self.toggle_curiosity).props(
                            f"color={self._control_color()} unelevated size=lg"
                        ).classes('w-full')
                        self.curiosity_next_label = ui.label(f'Next cycle: {self.curiosity.next_run_text()}').classes('lisa-muted')
                        self.curiosity_run_label = ui.label('Loading cycle history...').classes('text-caption')
                        ui.button('Run One Safe Cycle Now', icon='play_arrow', on_click=self.run_curiosity_now).props('outline').classes('w-full')

                with ui.card().classes('lisa-card mx-4'):
                    ui.label('Governed Schedule').classes('text-h6')
                    with ui.row().classes('w-full items-end gap-4'):
                        interval = ui.number('Interval (minutes)', value=int(policy['interval_minutes']), min=5, max=1440, step=5)
                        daily = ui.number('Maximum cycles per day', value=int(policy['max_runs_per_day']), min=1, max=48, step=1)
                        auto = ui.switch('Automatically execute bounded local research', value=bool(policy['auto_execute_research']))
                        ui.button(
                            'Save Policy',
                            icon='save',
                            on_click=lambda: self.save_curiosity_policy(interval.value, daily.value, auto.value),
                        )

                with ui.card().classes('lisa-card m-4 flex-1'):
                    with ui.row().classes('w-full items-center'):
                        ui.label('Unattended Cycle Audit').classes('text-h6')
                        ui.space()
                        ui.button(icon='refresh', on_click=self.refresh_curiosity_runs).props('flat round')
                    self.curiosity_runs_table = ui.table(
                        columns=[
                            {'name':'id','label':'Run','field':'id','sortable':True},
                            {'name':'status','label':'Status','field':'status','sortable':True},
                            {'name':'question','label':'Proposed Question','field':'question','align':'left'},
                            {'name':'job','label':'Research Job','field':'job'},
                            {'name':'started','label':'Started','field':'started','sortable':True},
                            {'name':'completed','label':'Completed','field':'completed'},
                        ],
                        rows=[], row_key='id', pagination=20,
                    ).classes('w-full')
        self.refresh_curiosity_ui()

    def build(self) -> None:
        super().build()

        # A persistent, prominent control remains visible on every workspace page.
        with ui.page_sticky(position='top-right', x_offset=110, y_offset=76):
            with ui.row().classes('items-center gap-2 bg-[#18222d] border border-[#2a3b4c] rounded-lg p-2 shadow-lg'):
                self.curiosity_button = ui.button(
                    self._control_text(),
                    icon='psychology',
                    on_click=self.toggle_curiosity,
                ).props(f"color={self._control_color()} unelevated")
                ui.button(icon='tune', on_click=self.show_curiosity_console).props('flat round').tooltip('Self Curiosity controls and audit')

        self.build_curiosity_console()
        self.refresh_curiosity_ui()
        ui.timer(15.0, self.curiosity_tick)


# The page function registered by nicegui_app resolves this module global at request time.
base.workspace = CuriosityWorkspace()


def main() -> None:
    ui.run(
        title='LISA Cognitive Research Workspace',
        host='127.0.0.1',
        port=int(os.getenv('LISA_UI_PORT', '8090')),
        reload=False,
        show=True,
        dark=True,
        favicon='🜂',
    )


if __name__ in {'__main__', '__mp_main__'}:
    main()
