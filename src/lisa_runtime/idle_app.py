"""LISA notebook UI with idle research supervision and restart controls."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from nicegui import app, ui

from . import nicegui_app as root
from . import notebook_app as base
from .idle_loop_manager import IdleResearchLoopManager
from .runtime_control import RuntimeControlStore


GLOBAL_CONTROL = RuntimeControlStore(root.DB_PATH)


@app.post('/api/lisa/activity')
async def record_lisa_activity() -> dict[str, str]:
    stamp = GLOBAL_CONTROL.mark_user_activity('browser_interaction')
    return {'status': 'recorded', 'at': stamp}


def _row(event: Any) -> dict[str, Any]:
    args = getattr(event, 'args', event)
    if isinstance(args, dict) and isinstance(args.get('row'), dict):
        return args['row']
    return args if isinstance(args, dict) else {}


class AutonomousNotebookWorkspace(base.NotebookWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.idle_loop = IdleResearchLoopManager(root.store, root.repo, root.node)
        self.idle_dialog = None
        self.idle_state_label = None
        self.idle_time_label = None
        self.idle_service_label = None
        self.idle_next_label = None
        self.idle_event_table = None
        self.incomplete_job_table = None

    def mark_activity(self, source: str) -> None:
        self.idle_loop.mark_user_activity(source)

    async def send_message(self, text: str) -> None:
        self.mark_activity('send_message')
        await super().send_message(text)

    def queue_research(self, question: str) -> None:
        self.mark_activity('queue_research')
        super().queue_research(question)

    async def run_one_job(self) -> None:
        self.mark_activity('manual_run_job')
        await super().run_one_job()

    def new_conversation(self) -> None:
        self.mark_activity('new_conversation')
        super().new_conversation()

    def toggle_curiosity(self) -> None:
        self.mark_activity('toggle_curiosity')
        super().toggle_curiosity()
        self.refresh_idle_ui()

    async def run_curiosity_now(self) -> None:
        self.mark_activity('manual_curiosity_cycle')
        await super().run_curiosity_now()
        self.refresh_idle_ui()

    def select_notebook(self, notebook_id: str) -> None:
        self.mark_activity('select_notebook')
        super().select_notebook(notebook_id)

    def show_notebook_workspace(self) -> None:
        self.mark_activity('open_notebook_workspace')
        super().show_notebook_workspace()

    def show_new_entry(self) -> None:
        self.mark_activity('new_notebook_entry')
        super().show_new_entry()

    async def curiosity_tick(self) -> None:
        """UI timer is status-only; the detached idle service owns auto execution."""
        self.refresh_curiosity_ui()
        self.refresh_idle_ui()

    def _rows(self, item_type: str) -> list[dict[str, Any]]:
        rows = super()._rows(item_type)
        if item_type != 'cognition':
            return rows
        jobs = {int(job['id']): job for job in root.repo.list_jobs(5000)}
        for row in rows:
            external_id = str(row.get('external_id') or '')
            if external_id.isdigit() and int(external_id) in jobs:
                job = jobs[int(external_id)]
                row['status'] = job.get('status', row.get('status'))
                row['run_generation'] = job.get('run_generation', 1)
                row['restart_count'] = job.get('restart_count', 0)
                row['job_id'] = int(external_id)
        return rows

    def refresh_jobs(self) -> None:
        if not self.job_table:
            return
        self.job_table.rows = [
            {
                'id': job['id'],
                'status': job['status'],
                'priority': job['priority'],
                'question': job['question'],
                'generation': job.get('run_generation', 1),
                'restarts': job.get('restart_count', 0),
                'heartbeat': job.get('heartbeat_at') or '',
                'created_at': job['created_at'],
            }
            for job in root.repo.list_jobs(500)
        ]
        self.job_table.update()

    def refresh_thoughts(self) -> None:
        if not self.thought_table:
            return
        jobs = {int(job['id']): job for job in root.repo.list_jobs(5000)}
        rows: list[dict[str, Any]] = []
        for thought in root.store.list_thoughts(limit=500):
            job_id = thought.get('research_job_id')
            job = jobs.get(int(job_id)) if job_id is not None else None
            rows.append(
                {
                    'id': thought['id'],
                    'stage': thought['stage'],
                    'title': thought['title'],
                    'status': thought['status'],
                    'created_at': thought['created_at'],
                    'research_job_id': job_id or '',
                    'job_status': job.get('status', '') if job else '',
                }
            )
        self.thought_table.rows = rows
        self.thought_table.update()

    def refresh_curiosity_runs(self) -> None:
        if not self.curiosity_runs_table:
            return
        jobs = {int(job['id']): job for job in root.repo.list_jobs(5000)}
        rows: list[dict[str, Any]] = []
        for run in root.store.list_curiosity_runs(limit=100):
            job_id = run.get('research_job_id')
            job = jobs.get(int(job_id)) if job_id else None
            rows.append(
                {
                    'id': run.get('id'),
                    'status': run.get('status'),
                    'question': run.get('proposed_question') or '',
                    'job': job_id or '',
                    'job_status': job.get('status', '') if job else '',
                    'started': run.get('started_at'),
                    'completed': run.get('completed_at') or '',
                }
            )
        self.curiosity_runs_table.rows = rows
        self.curiosity_runs_table.update()

    def refresh_notebook_workspace(self) -> None:
        try:
            self.notebooks.link_existing_records(self.selected_notebook_id)
        except Exception:
            pass
        super().refresh_notebook_workspace()

    def restart_job(self, job_id: int, source: str = 'workspace') -> None:
        if not job_id:
            ui.notify('This record is not linked to a research job.', type='warning')
            return
        self.mark_activity(f'restart_job:{source}')
        try:
            restarted = root.repo.restart_job(
                int(job_id), reason=f'User requested restart from {source}'
            )
        except Exception as exc:
            ui.notify(str(exc), type='negative', timeout=7000)
            return
        root.store.add_thought(
            'research_restart',
            f'Research job {job_id} restarted',
            (
                f"Job moved to READY as run generation {restarted.get('run_generation')}. "
                'Prior steps remain available as audit history.'
            ),
            research_job_id=int(job_id),
            metadata={
                'source': source,
                'run_generation': restarted.get('run_generation'),
                'restart_count': restarted.get('restart_count'),
            },
        )
        root.store.save_artifact(
            'research_restart_record',
            f'Research Restart #{job_id} Generation {restarted.get("run_generation")}',
            (
                f"# Research Restart\n\n**Job:** {job_id}  \n"
                f"**Generation:** {restarted.get('run_generation')}  \n"
                f"**Restart count:** {restarted.get('restart_count')}  \n"
                f"**Source:** {source}\n\n"
                'The job was returned to READY. Previous cognition records were preserved.'
            ),
            research_job_id=int(job_id),
            metadata={'source': source, 'job': restarted},
        )
        self.idle_loop.control.add_event(
            'job_restart',
            'ready',
            {
                'source': source,
                'run_generation': restarted.get('run_generation'),
                'restart_count': restarted.get('restart_count'),
            },
            research_job_id=int(job_id),
        )
        try:
            self.notebooks.link_existing_records(self.selected_notebook_id)
        except Exception:
            pass
        self.refresh_all()
        self.refresh_curiosity_runs()
        self.refresh_notebook_workspace()
        self.refresh_idle_ui()
        ui.notify(
            f'Research job {job_id} restarted and returned to READY.',
            type='positive',
        )

    def _restart_from_event(self, event: Any, id_field: str, source: str) -> None:
        row = _row(event)
        value = row.get(id_field)
        try:
            job_id = int(value)
        except Exception:
            job_id = 0
        self.restart_job(job_id, source)

    def _add_restart_column(
        self,
        table: Any,
        *,
        id_field: str,
        source: str,
        status_field: str = 'status',
    ) -> None:
        if not table:
            return
        columns = list(table.columns)
        if not any(column.get('name') == 'restart' for column in columns):
            columns.append(
                {
                    'name': 'restart', 'label': 'Restart',
                    'field': 'restart', 'align': 'center',
                }
            )
            table.columns = columns
            table.add_slot(
                'body-cell-restart',
                f'''<q-td :props="props"><q-btn v-if="props.row['{id_field}'] && String(props.row['{status_field}'] || '').toUpperCase() !== 'COMPLETED'" flat dense round icon="restart_alt" color="orange-6" @click.stop="$parent.$emit('restart_job', props.row)"><q-tooltip>Restart incomplete or hung research job</q-tooltip></q-btn></q-td>''',
            )
            table.on(
                'restart_job',
                lambda event, field=id_field, origin=source: self._restart_from_event(
                    event, field, origin
                ),
            )
            table.update()

    def refresh_idle_ui(self) -> None:
        status = self.idle_loop.status()
        service = status.get('service') or {}
        if self.idle_state_label:
            state = service.get('state') or ('active' if status['enabled'] else 'paused')
            self.idle_state_label.set_text(
                f"Loop: {str(state).replace('_', ' ').title()}"
            )
        if self.idle_time_label:
            self.idle_time_label.set_text(
                f"User idle: {status['idle_minutes']:.1f} min / "
                f"{status['idle_threshold_minutes']} min required"
            )
        if self.idle_service_label:
            heartbeat = service.get('heartbeat_at') or 'No heartbeat recorded'
            pid = service.get('pid') or 'unknown'
            self.idle_service_label.set_text(
                f'Service PID: {pid} | Heartbeat: {heartbeat}'
            )
        if self.idle_next_label:
            self.idle_next_label.set_text(
                f"Next self-led project: {status['next_curiosity']}"
            )
        if self.idle_event_table:
            self.idle_event_table.rows = [
                {
                    'id': row['id'],
                    'event': row['event_type'],
                    'status': row['status'],
                    'job': row.get('research_job_id') or '',
                    'created': row['created_at'],
                    'detail': row['detail_json'],
                }
                for row in self.idle_loop.control.list_events(200)
            ]
            self.idle_event_table.update()
        if self.incomplete_job_table:
            self.incomplete_job_table.rows = [
                {
                    'id': job['id'],
                    'status': job['status'],
                    'question': job['question'],
                    'generation': job.get('run_generation', 1),
                    'restarts': job.get('restart_count', 0),
                    'heartbeat': job.get('heartbeat_at') or '',
                }
                for job in root.repo.list_incomplete_jobs(500)
            ]
            self.incomplete_job_table.update()

    def save_idle_policy(
        self,
        idle_minutes: Any,
        stale_minutes: Any,
        interval_minutes: Any,
        daily_limit: Any,
        process_queue: bool,
        auto_execute: bool,
    ) -> None:
        try:
            self.curiosity.update_policy(
                idle_minutes=int(idle_minutes),
                stale_job_minutes=int(stale_minutes),
                interval_minutes=int(interval_minutes),
                max_runs_per_day=int(daily_limit),
                process_existing_queue_on_idle=bool(process_queue),
                auto_execute_research=bool(auto_execute),
                local_only=True,
            )
            ui.notify('Idle research policy saved.', type='positive')
            self.refresh_curiosity_ui()
            self.refresh_idle_ui()
        except Exception as exc:
            ui.notify(f'Could not save idle policy: {exc}', type='negative')

    def show_idle_console(self) -> None:
        self.mark_activity('open_idle_console')
        self.refresh_idle_ui()
        self.idle_dialog.open()

    def build_idle_console(self) -> None:
        policy = self.curiosity.policy()
        with ui.dialog().props(
            'maximized transition-show=slide-up transition-hide=slide-down'
        ) as self.idle_dialog:
            with ui.card().classes('w-full h-full bg-[#0c1117] text-white p-0'):
                with ui.row().classes('w-full items-center bg-[#101821] p-4'):
                    ui.icon('hourglass_top', size='32px').classes('text-[#b8893c]')
                    ui.label('Idle Research Loop').classes('text-h5')
                    ui.space()
                    ui.button(
                        icon='close', on_click=self.idle_dialog.close
                    ).props('flat round')
                with ui.row().classes('w-full gap-4 p-4 flex-wrap'):
                    with ui.card().classes('lisa-card min-w-[300px] flex-1'):
                        self.idle_state_label = ui.label('Loop status').classes('text-h6')
                        self.idle_time_label = ui.label('User idle').classes('lisa-muted')
                        self.idle_service_label = ui.label('Service heartbeat').classes('text-caption')
                        self.idle_next_label = ui.label('Next project').classes('text-caption')
                        ui.markdown(
                            '**Operating order:** monitor activity → quarantine stale jobs → '
                            'wait for idle → process READY work → initiate one bounded self-led project.'
                        )
                    with ui.card().classes('lisa-card min-w-[430px]'):
                        ui.label('Idle and Curiosity Policy').classes('text-h6')
                        idle = ui.number(
                            'Idle threshold (minutes)',
                            value=int(policy.get('idle_minutes', 5)), min=1, max=1440,
                        )
                        stale = ui.number(
                            'Stale heartbeat threshold (minutes)',
                            value=int(policy.get('stale_job_minutes', 20)), min=5, max=1440,
                        )
                        interval = ui.number(
                            'Minimum time between self-led projects',
                            value=int(policy.get('interval_minutes', 30)), min=5, max=1440,
                        )
                        daily = ui.number(
                            'Maximum self-led projects per day',
                            value=int(policy.get('max_runs_per_day', 8)), min=1, max=48,
                        )
                        process_queue = ui.switch(
                            'Process queued and restarted jobs while idle',
                            value=bool(policy.get('process_existing_queue_on_idle', True)),
                        )
                        auto_execute = ui.switch(
                            'Execute bounded research automatically',
                            value=bool(policy.get('auto_execute_research', True)),
                        )
                        ui.button(
                            'Save Policy',
                            icon='save',
                            on_click=lambda: self.save_idle_policy(
                                idle.value, stale.value, interval.value, daily.value,
                                process_queue.value, auto_execute.value,
                            ),
                        ).classes('w-full')
                with ui.tabs().classes('w-full') as tabs:
                    incomplete_tab = ui.tab('Incomplete Jobs', icon='restart_alt')
                    audit_tab = ui.tab('Idle Loop Audit', icon='history')
                with ui.tab_panels(tabs, value=incomplete_tab).classes(
                    'w-full flex-1 bg-transparent'
                ):
                    with ui.tab_panel(incomplete_tab).classes('p-4'):
                        self.incomplete_job_table = ui.table(
                            columns=[
                                {'name':'id','label':'Job','field':'id','sortable':True},
                                {'name':'status','label':'Status','field':'status','sortable':True},
                                {'name':'question','label':'Question','field':'question','align':'left'},
                                {'name':'generation','label':'Generation','field':'generation'},
                                {'name':'restarts','label':'Restarts','field':'restarts'},
                                {'name':'heartbeat','label':'Heartbeat','field':'heartbeat'},
                                {'name':'restart','label':'Restart','field':'restart','align':'center'},
                            ],
                            rows=[], row_key='id', pagination=25,
                        ).classes('w-full')
                        self.incomplete_job_table.add_slot(
                            'body-cell-restart',
                            '''<q-td :props="props"><q-btn flat dense round icon="restart_alt" color="orange-6" @click.stop="$parent.$emit('restart_job', props.row)"><q-tooltip>Restart job as a new preserved generation</q-tooltip></q-btn></q-td>''',
                        )
                        self.incomplete_job_table.on(
                            'restart_job',
                            lambda event: self._restart_from_event(
                                event, 'id', 'idle_console'
                            ),
                        )
                        self.incomplete_job_table.on(
                            'rowClick', lambda event: self.open_job(event)
                        )
                    with ui.tab_panel(audit_tab).classes('p-4'):
                        self.idle_event_table = ui.table(
                            columns=[
                                {'name':'event','label':'Event','field':'event','sortable':True},
                                {'name':'status','label':'Status','field':'status','sortable':True},
                                {'name':'job','label':'Job','field':'job'},
                                {'name':'created','label':'Created','field':'created','sortable':True},
                                {'name':'detail','label':'Detail','field':'detail','align':'left'},
                            ],
                            rows=[], row_key='id', pagination=30,
                        ).classes('w-full')
        self.refresh_idle_ui()

    def build(self) -> None:
        super().build()
        ui.add_body_html(
            '''<script>
            (() => {
              let last = 0;
              const mark = () => {
                const now = Date.now();
                if (now - last < 15000) return;
                last = now;
                fetch('/api/lisa/activity', {method:'POST', keepalive:true}).catch(() => {});
              };
              ['pointerdown','keydown','wheel','touchstart'].forEach(
                eventName => window.addEventListener(eventName, mark, {passive:true})
              );
              mark();
            })();
            </script>'''
        )
        with ui.teleport('.q-drawer .q-drawer__content'):
            with ui.column().classes('w-full px-3 pb-3 gap-2'):
                ui.button(
                    'Idle Research Loop',
                    icon='hourglass_top',
                    on_click=self.show_idle_console,
                ).props('flat').classes('w-full')
        self.build_idle_console()

        self._add_restart_column(
            self.job_table, id_field='id', source='research_queue'
        )
        self._add_restart_column(
            self.thought_table,
            id_field='research_job_id',
            status_field='job_status',
            source='thought_trail',
        )
        self._add_restart_column(
            self.curiosity_runs_table,
            id_field='job',
            status_field='job_status',
            source='curiosity_audit',
        )
        cognition_table = self.notebook_tables.get('cognition')
        self._add_restart_column(
            cognition_table,
            id_field='external_id',
            status_field='status',
            source='notebook_cognition',
        )
        self.mark_activity('workspace_loaded')
        self.refresh_all()
        self.refresh_curiosity_runs()
        self.refresh_notebook_workspace()
        self.refresh_idle_ui()
        ui.timer(10.0, self.refresh_idle_ui)


root.workspace = AutonomousNotebookWorkspace()


def main() -> None:
    ui.run(
        title='LISA Autonomous Cognitive Notebook',
        host='127.0.0.1',
        port=int(os.getenv('LISA_UI_PORT', '8090')),
        reload=False,
        show=True,
        dark=True,
        favicon='🜂',
    )


if __name__ in {'__main__', '__mp_main__'}:
    main()
