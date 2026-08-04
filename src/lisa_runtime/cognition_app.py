"""Universal cognition explorer for research, thoughts, and Self Curiosity runs.

Cognition is represented as a persisted, inspectable mind map backed by the
local relational database. Every node can be drilled into and the same records
are available in a standard table view.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from nicegui import ui

from . import nicegui_curiosity_app as base


def _row(event: Any) -> dict[str, Any]:
    args = getattr(event, 'args', event)
    if isinstance(args, dict) and isinstance(args.get('row'), dict):
        return args['row']
    return args if isinstance(args, dict) else {}


def _safe(value: Any, limit: int = 86) -> str:
    text = ' '.join(str(value or '').split())
    text = text[:limit] + ('…' if len(text) > limit else '')
    return text.replace('"', "'").replace('[', '(').replace(']', ')')


def _id(prefix: str, value: Any) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', f'{prefix}_{value}')


class CognitionWorkspace(base.CuriosityWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.cognition_dialog = None
        self.cognition_title = None
        self.cognition_map = None
        self.cognition_table = None
        self.cognition_detail = None
        self.cognition_nodes: dict[str, dict[str, Any]] = {}

    def _job(self, job_id: int) -> dict[str, Any] | None:
        return next((j for j in base.base.repo.list_jobs(5000) if int(j['id']) == int(job_id)), None)

    def _steps(self, job_id: int) -> list[dict[str, Any]]:
        with base.base.repo.connect() as db:
            rows = db.execute('SELECT * FROM research_steps WHERE job_id=? ORDER BY iteration,id', (job_id,)).fetchall()
            return [dict(r) for r in rows]

    def _curiosity_run(self, run_id: int) -> dict[str, Any] | None:
        return next((r for r in base.base.store.list_curiosity_runs(limit=5000) if int(r['id']) == int(run_id)), None)

    def _resolve_job_id(self, kind: str, record_id: int) -> int | None:
        if kind == 'job':
            return record_id
        if kind == 'thought':
            thought = next((t for t in base.base.store.list_thoughts(limit=5000) if int(t['id']) == record_id), None)
            return int(thought['research_job_id']) if thought and thought.get('research_job_id') is not None else None
        if kind == 'curiosity':
            run = self._curiosity_run(record_id)
            return int(run['research_job_id']) if run and run.get('research_job_id') else None
        return None

    def _build_cognition(self, kind: str, record_id: int) -> tuple[str, list[dict[str, Any]], str]:
        job_id = self._resolve_job_id(kind, record_id)
        job = self._job(job_id) if job_id else None
        thoughts = base.base.store.list_thoughts(job_id) if job_id else []
        steps = self._steps(job_id) if job_id else []
        report = base.base.repo.get_report(job_id) if job_id else None
        curiosity_runs = [r for r in base.base.store.list_curiosity_runs(limit=5000) if job_id and int(r.get('research_job_id') or 0) == job_id]

        title = f"Cognition #{job_id}: {job.get('question','')}" if job else f'Cognition Record {record_id}'
        nodes: list[dict[str, Any]] = []
        lines = ['flowchart LR']

        root_id = _id('goal', job_id or record_id)
        root = {
            'node_id': root_id, 'record_type': 'goal', 'record_id': job_id or record_id,
            'stage': 'Goal', 'title': job.get('question', title) if job else title,
            'status': job.get('status', '') if job else '', 'created_at': job.get('created_at', '') if job else '',
            'content': job.get('question', '') if job else '',
        }
        nodes.append(root)
        lines.append(f'    {root_id}(["{_safe(root["title"])}"])')
        previous = root_id

        for run in reversed(curiosity_runs):
            nid = _id('curiosity', run['id'])
            node = {'node_id': nid, 'record_type': 'self_curiosity', 'record_id': run['id'], 'stage': 'Self Curiosity', 'title': run.get('proposed_question') or f"Curiosity Run {run['id']}", 'status': run.get('status',''), 'created_at': run.get('started_at',''), 'content': run.get('reason',''), 'raw': run}
            nodes.append(node); lines += [f'    {nid}{{"{_safe(node["title"])}"}}', f'    {previous} --> {nid}']; previous = nid

        for thought in thoughts:
            nid = _id('thought', thought['id'])
            stage = str(thought.get('stage','thought')).replace('_',' ').title()
            node = {'node_id': nid, 'record_type': 'thought', 'record_id': thought['id'], 'stage': stage, 'title': thought.get('title',''), 'status': thought.get('status',''), 'created_at': thought.get('created_at',''), 'content': thought.get('content',''), 'raw': thought}
            nodes.append(node); lines += [f'    {nid}["{_safe(stage + ": " + node["title"])}"]', f'    {previous} --> {nid}']; previous = nid

        for step in steps:
            nid = _id('step', step['id'])
            try: action = json.loads(step.get('action_json') or '{}')
            except Exception: action = {'raw': step.get('action_json')}
            try: result = json.loads(step.get('result_json') or '{}')
            except Exception: result = {'raw': step.get('result_json')}
            title_text = action.get('query') or action.get('action') or f"Step {step.get('iteration')}"
            node = {'node_id': nid, 'record_type': 'research_step', 'record_id': step['id'], 'stage': f"Step {step.get('iteration')}", 'title': str(title_text), 'status': 'recorded', 'created_at': step.get('created_at',''), 'content': result.get('output') or json.dumps(result, indent=2), 'raw': {'action': action, 'result': result}}
            nodes.append(node); lines += [f'    {nid}["{_safe(node["stage"] + ": " + node["title"])}"]', f'    {previous} --> {nid}']; previous = nid

        if report:
            nid = _id('report', job_id)
            node = {'node_id': nid, 'record_type': 'report', 'record_id': report.get('id'), 'stage': 'Synthesis', 'title': 'Saved Research Report', 'status': 'completed', 'created_at': report.get('created_at',''), 'content': report.get('summary',''), 'raw': report}
            nodes.append(node); lines += [f'    {nid}[["Saved Research Report"]]', f'    {previous} --> {nid}']; previous = nid

        lines += [f'    class {root_id} rootNode', '    classDef rootNode fill:#b8893c,color:#111827,stroke:#f2d6a2,stroke-width:2px;', '    classDef default fill:#18222d,color:#e8edf3,stroke:#58708a,stroke-width:1px;']
        return '\n'.join(lines), nodes, title

    def _show_node(self, row: dict[str, Any]) -> None:
        node_id = str(row.get('node_id',''))
        node = self.cognition_nodes.get(node_id, row)
        self.cognition_detail.clear()
        with self.cognition_detail:
            ui.markdown(
                f"## {node.get('title','Cognition record')}\n\n"
                f"**Type:** {node.get('record_type','')}  \n"
                f"**Stage:** {node.get('stage','')}  \n"
                f"**Status:** {node.get('status','')}  \n"
                f"**Created:** {node.get('created_at','')}\n\n"
                f"{node.get('content','')}\n\n"
                f"### Stored Record\n```json\n{json.dumps(node.get('raw', node), indent=2, ensure_ascii=False, default=str)}\n```"
            ).classes('w-full')

    def open_cognition(self, kind: str, record_id: int) -> None:
        source, nodes, title = self._build_cognition(kind, record_id)
        self.cognition_nodes = {n['node_id']: n for n in nodes}
        self.cognition_title.set_text(title)
        self.cognition_map.clear(); self.cognition_detail.clear()
        with self.cognition_map:
            def drill(e: Any) -> None:
                node_id = getattr(e, 'node_id', '')
                if node_id in self.cognition_nodes:
                    self._show_node(self.cognition_nodes[node_id])
            ui.mermaid(source, config={'theme':'dark','flowchart':{'curve':'basis'},'securityLevel':'strict'}, on_node_click=drill).classes('w-full min-h-[520px]')
        self.cognition_table.rows = nodes
        self.cognition_table.update()
        if nodes: self._show_node(nodes[0])
        self.cognition_dialog.open()

    def _open_from_event(self, kind: str, event: Any) -> None:
        row = _row(event)
        record_id = int(row.get('id', 0) or 0)
        if record_id:
            self.open_cognition(kind, record_id)

    def _add_cognition_column(self, table: Any, kind: str) -> None:
        if not table: return
        columns = list(table.columns)
        if not any(c.get('name') == 'cognition' for c in columns):
            columns.append({'name':'cognition','label':'Cognition','field':'cognition','align':'center'})
            table.columns = columns
            table.add_slot('body-cell-cognition', '''<q-td :props="props"><q-btn flat dense round icon="account_tree" color="amber-7" @click.stop="$parent.$emit('cognition', props.row)"><q-tooltip>Open cognition mind map and records</q-tooltip></q-btn></q-td>''')
            table.on('cognition', lambda e, k=kind: self._open_from_event(k, e))
            table.update()

    def build_cognition_dialog(self) -> None:
        with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as self.cognition_dialog:
            with ui.card().classes('w-full h-full bg-[#0c1117] text-white p-0'):
                with ui.row().classes('w-full items-center bg-[#101821] p-3'):
                    ui.icon('account_tree').classes('text-2xl text-[#b8893c]')
                    self.cognition_title = ui.label('Cognition Explorer').classes('text-h5')
                    ui.space(); ui.button(icon='close', on_click=self.cognition_dialog.close).props('flat round')
                with ui.tabs().classes('w-full') as tabs:
                    map_tab = ui.tab('Mind Map', icon='account_tree')
                    records_tab = ui.tab('Records Table', icon='table_view')
                    detail_tab = ui.tab('Drill-down', icon='search')
                with ui.tab_panels(tabs, value=map_tab).classes('w-full h-[calc(100vh-120px)] bg-transparent'):
                    with ui.tab_panel(map_tab).classes('p-0'):
                        self.cognition_map = ui.scroll_area().classes('w-full h-full p-4')
                    with ui.tab_panel(records_tab).classes('p-3'):
                        self.cognition_table = ui.table(columns=[
                            {'name':'record_type','label':'Type','field':'record_type','sortable':True},
                            {'name':'stage','label':'Stage','field':'stage','sortable':True},
                            {'name':'title','label':'Title','field':'title','align':'left'},
                            {'name':'status','label':'Status','field':'status','sortable':True},
                            {'name':'created_at','label':'Created','field':'created_at','sortable':True},
                        ], rows=[], row_key='node_id', pagination=25).classes('w-full')
                        self.cognition_table.on('rowClick', lambda e: self._show_node(_row(e)))
                    with ui.tab_panel(detail_tab).classes('p-0'):
                        self.cognition_detail = ui.scroll_area().classes('w-full h-full p-5 bg-[#121a23]')

    def build(self) -> None:
        super().build()
        self.build_cognition_dialog()
        self._add_cognition_column(self.job_table, 'job')
        self._add_cognition_column(self.thought_table, 'thought')
        self._add_cognition_column(self.curiosity_runs_table, 'curiosity')


base.base.workspace = CognitionWorkspace()


def main() -> None:
    ui.run(title='LISA Cognitive Research Workspace', host='127.0.0.1', port=int(os.getenv('LISA_UI_PORT','8090')), reload=False, show=True, dark=True, favicon='🜂')


if __name__ in {'__main__','__mp_main__'}:
    main()
