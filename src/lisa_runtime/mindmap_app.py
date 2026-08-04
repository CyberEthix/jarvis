"""Mind-map extension for the LISA NiceGUI workspace.

Adds a Mind Map action to the E2E Thought Trail table. Maps are generated
from persisted relational records, can be reopened at any time, support
node drill-down, and can be saved as versioned knowledge artifacts.
"""
from __future__ import annotations

import json
import re
from typing import Any

from nicegui import ui

from . import nicegui_app as base


def _event_row(event: Any) -> dict[str, Any]:
    args = event.args if hasattr(event, 'args') else event
    if isinstance(args, dict) and isinstance(args.get('row'), dict):
        return args['row']
    return args if isinstance(args, dict) else {}


def _safe_label(value: str, limit: int = 72) -> str:
    compact = ' '.join(str(value or '').split())
    compact = compact[:limit] + ('…' if len(compact) > limit else '')
    return compact.replace('"', "'").replace('[', '(').replace(']', ')')


def _node_id(prefix: str, value: Any) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', f'{prefix}_{value}')


def _thought_by_id(thought_id: int) -> dict[str, Any] | None:
    return next((t for t in base.store.list_thoughts(limit=5000) if int(t['id']) == thought_id), None)


def _job_by_id(job_id: int) -> dict[str, Any] | None:
    return next((j for j in base.repo.list_jobs(5000) if int(j['id']) == job_id), None)


def _build_map(thought: dict[str, Any]) -> tuple[str, dict[str, dict[str, Any]], str]:
    job_id = thought.get('research_job_id')
    events = base.store.list_thoughts(int(job_id)) if job_id is not None else [thought]
    job = _job_by_id(int(job_id)) if job_id is not None else None
    report = base.repo.get_report(int(job_id)) if job_id is not None else None

    title = (
        f"Research #{job_id}: {job.get('question', '')}" if job_id is not None and job
        else f"Thought Trail: {thought.get('title', '')}"
    )
    nodes: dict[str, dict[str, Any]] = {}
    lines = ['flowchart LR']

    root_id = _node_id('root', job_id if job_id is not None else thought['id'])
    root_label = _safe_label(job.get('question', '') if job else thought.get('title', 'Thought Trail'))
    lines.append(f'    {root_id}["{root_label}"]')
    nodes[root_id] = {
        'title': title,
        'stage': 'research_goal' if job else thought.get('stage', 'thought'),
        'content': job.get('question', '') if job else thought.get('content', ''),
        'status': job.get('status', '') if job else thought.get('status', ''),
        'created_at': job.get('created_at', '') if job else thought.get('created_at', ''),
    }

    previous = root_id
    for event in events:
        event_id = _node_id('thought', event['id'])
        stage = str(event.get('stage', 'event')).replace('_', ' ').title()
        label = _safe_label(f"{stage}: {event.get('title', '')}")
        lines.append(f'    {event_id}["{label}"]')
        lines.append(f'    {previous} --> {event_id}')
        nodes[event_id] = event
        previous = event_id

    if report:
        report_id = _node_id('report', job_id)
        lines.append(f'    {report_id}[["Saved Research Report"]]')
        lines.append(f'    {previous} --> {report_id}')
        nodes[report_id] = {
            'title': 'Saved Research Report',
            'stage': 'research_report',
            'status': 'completed',
            'created_at': report.get('created_at', ''),
            'content': report.get('summary', ''),
            'limitations': report.get('limitations', ''),
            'findings_json': report.get('findings_json', '[]'),
        }

    lines += [
        f'    class {root_id} rootNode',
        '    classDef rootNode fill:#b8893c,color:#111827,stroke:#f2d6a2,stroke-width:2px;',
        '    classDef default fill:#18222d,color:#e8edf3,stroke:#58708a,stroke-width:1px;',
    ]
    return '\n'.join(lines), nodes, title


def _format_details(node: dict[str, Any]) -> str:
    metadata = node.get('metadata_json', '{}')
    try:
        metadata = json.loads(metadata) if isinstance(metadata, str) else metadata
    except Exception:
        metadata = {'raw': metadata}
    sections = [
        f"## {node.get('title', 'Mind-map node')}",
        f"**Stage:** {str(node.get('stage', '')).replace('_', ' ').title()}",
        f"**Status:** {node.get('status', '')}",
        f"**Created:** {node.get('created_at', '')}",
        '',
        str(node.get('content', '')),
    ]
    if node.get('limitations'):
        sections += ['', '### Limitations', str(node['limitations'])]
    if metadata:
        sections += ['', '### Metadata', f"```json\n{json.dumps(metadata, indent=2, ensure_ascii=False)}\n```"]
    return '\n\n'.join(sections)


def open_mindmap(self: base.Workspace, event: Any) -> None:
    row = _event_row(event)
    thought_id = int(row.get('id', 0) or 0)
    thought = _thought_by_id(thought_id)
    if not thought:
        ui.notify('The selected thought event could not be found.', type='negative')
        return

    source, nodes, title = _build_map(thought)
    self._mindmap_source = source
    self._mindmap_nodes = nodes
    self._mindmap_title = title
    self._mindmap_origin = thought

    self.mindmap_title.set_text(title)
    self.mindmap_canvas.clear()
    self.mindmap_detail.clear()

    def drill(event_args: Any) -> None:
        node_id = getattr(event_args, 'node_id', '')
        node = self._mindmap_nodes.get(node_id)
        if not node:
            return
        self.mindmap_detail.clear()
        with self.mindmap_detail:
            ui.markdown(_format_details(node)).classes('w-full')

    with self.mindmap_canvas:
        ui.mermaid(
            source,
            config={'theme': 'dark', 'flowchart': {'curve': 'basis'}, 'securityLevel': 'strict'},
            on_node_click=drill,
        ).classes('w-full min-h-[520px]')

    root = next(iter(nodes.values()))
    with self.mindmap_detail:
        ui.markdown(_format_details(root)).classes('w-full')
    self.mindmap_dialog.open()


def save_mindmap_snapshot(self: base.Workspace) -> None:
    source = getattr(self, '_mindmap_source', '')
    title = getattr(self, '_mindmap_title', 'Thought Trail Mind Map')
    origin = getattr(self, '_mindmap_origin', {})
    if not source:
        ui.notify('Open a mind map before saving a snapshot.', type='warning')
        return
    artifact_id = base.store.save_artifact(
        'thought_mindmap',
        f'Mind Map — {title}',
        f"# {title}\n\n```mermaid\n{source}\n```",
        research_job_id=origin.get('research_job_id'),
        conversation_id=origin.get('conversation_id'),
        metadata={'source_thought_id': origin.get('id'), 'format': 'mermaid'},
    )
    ui.notify(f'Mind-map snapshot saved as artifact {artifact_id}.', type='positive')
    self.refresh_all()


_original_build = base.Workspace.build


def build_with_mindmaps(self: base.Workspace) -> None:
    _original_build(self)

    # Dialog remains available for redisplay throughout the workspace session.
    with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as self.mindmap_dialog:
        with ui.card().classes('w-full h-full bg-[#0c1117] p-0'):
            with ui.row().classes('w-full items-center p-3 bg-[#101821]'):
                ui.icon('account_tree').classes('text-2xl text-[#b8893c]')
                self.mindmap_title = ui.label('Thought Trail Mind Map').classes('text-h5')
                ui.space()
                ui.button('Save Snapshot', icon='save', on_click=lambda: save_mindmap_snapshot(self)).props('outline')
                ui.button(icon='close', on_click=self.mindmap_dialog.close).props('flat round')
            with ui.splitter(value=72).classes('w-full h-[calc(100vh-76px)]') as mind_split:
                with mind_split.before:
                    self.mindmap_canvas = ui.scroll_area().classes('w-full h-full p-4 bg-[#0c1117]')
                with mind_split.after:
                    with ui.column().classes('w-full h-full p-4 bg-[#121a23]'):
                        ui.label('Node Drill-down').classes('text-h6')
                        ui.label('Select any mind-map node to inspect the saved record.').classes('lisa-muted')
                        self.mindmap_detail = ui.scroll_area().classes('w-full flex-1')

    if self.thought_table:
        columns = list(self.thought_table.columns)
        if not any(c.get('name') == 'mindmap' for c in columns):
            columns.append({'name': 'mindmap', 'label': 'Mind Map', 'field': 'mindmap', 'align': 'center'})
            self.thought_table.columns = columns
            self.thought_table.add_slot(
                'body-cell-mindmap',
                '''<q-td :props="props"><q-btn flat dense round icon="account_tree" color="amber-7" @click.stop="$parent.$emit('mindmap', props.row)"><q-tooltip>Open persistent mind map</q-tooltip></q-btn></q-td>''',
            )
            self.thought_table.on('mindmap', lambda e: open_mindmap(self, e))
            self.thought_table.update()
        self.thought_table.on('rowClick', lambda e: open_mindmap(self, e))


base.Workspace.open_mindmap = open_mindmap
base.Workspace.save_mindmap_snapshot = save_mindmap_snapshot
base.Workspace.build = build_with_mindmaps


def main() -> None:
    base.main()


if __name__ in {'__main__', '__mp_main__'}:
    main()
