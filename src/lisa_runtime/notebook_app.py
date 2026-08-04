"""LISA Cognitive Notebook workspace.

This is the user-facing container for the end-to-end EA workflow. The LISA
SQLite RDBMS remains authoritative. An optional Open Notebook service acts as a
bounded research ingestion/search node through a governed REST adapter.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from nicegui import ui

from . import cognition_app as cognition
from . import nicegui_app as root
from .notebook_store import NotebookStore
from .open_notebook_adapter import OpenNotebookAdapter


ITEM_SECTIONS: list[tuple[str, str, str]] = [
    ('source', 'Sources', 'source'),
    ('cognition', 'Cognition', 'account_tree'),
    ('memo', 'Memos', 'description'),
    ('conversation', 'Conversations', 'forum'),
    ('evidence', 'Evidence', 'fact_check'),
    ('decision', 'Decisions', 'gavel'),
    ('commitment', 'Commitments', 'task_alt'),
    ('run', 'Runs', 'play_circle'),
    ('evaluation', 'Evaluations', 'analytics'),
]


def _event_row(event: Any) -> dict[str, Any]:
    args = getattr(event, 'args', event)
    if isinstance(args, dict) and isinstance(args.get('row'), dict):
        return args['row']
    return args if isinstance(args, dict) else {}


class NotebookWorkspace(cognition.CognitionWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.notebooks = NotebookStore(root.DB_PATH)
        self.open_notebook = OpenNotebookAdapter()
        self.selected_notebook_id = self.notebooks.ensure_default_notebook()
        self.notebooks.link_existing_records(self.selected_notebook_id)
        self.selected_notebook_item_id: int | None = None

        self.notebook_dialog = None
        self.notebook_list = None
        self.notebook_title = None
        self.notebook_subtitle = None
        self.notebook_node_badge = None
        self.notebook_tabs = None
        self.notebook_inspector = None
        self.notebook_inspector_title = None
        self.notebook_inspector_body = None
        self.notebook_tables: dict[str, Any] = {}
        self.notebook_count_labels: dict[str, Any] = {}
        self.notebook_activity_table = None
        self.notebook_overview = None
        self.notebook_create_dialog = None
        self.entry_dialog = None
        self.entry_type = None
        self.entry_title = None
        self.entry_body = None
        self.entry_status = None
        self.mapping_input = None

    def selected_notebook(self) -> dict[str, Any] | None:
        return self.notebooks.get_notebook(self.selected_notebook_id)

    def refresh_notebook_list(self) -> None:
        if not self.notebook_list:
            return
        self.notebook_list.clear()
        with self.notebook_list:
            for notebook in self.notebooks.list_notebooks():
                notebook_id = str(notebook['id'])
                selected = notebook_id == self.selected_notebook_id
                with ui.item(
                    on_click=lambda _, nid=notebook_id: self.select_notebook(nid),
                ).props('clickable v-ripple').classes(
                    'w-full rounded-lg ' + ('bg-[#26384a]' if selected else '')
                ):
                    with ui.item_section().props('avatar'):
                        ui.icon('menu_book', color='amber-7' if selected else 'blue-grey-4')
                    with ui.item_section():
                        ui.item_label(notebook.get('title', 'Notebook'))
                        ui.item_label(notebook.get('status', 'active')).props('caption')
                    with ui.item_section().props('side'):
                        count = len(self.notebooks.list_items(notebook_id))
                        ui.badge(str(count), color='amber-8' if selected else 'grey-7')

    def select_notebook(self, notebook_id: str) -> None:
        self.selected_notebook_id = notebook_id
        self.selected_notebook_item_id = None
        self.refresh_notebook_workspace()

    def _rows(self, item_type: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.notebooks.list_items(self.selected_notebook_id, item_type):
            rows.append({
                'id': item['id'],
                'type': item['item_type'],
                'title': item['title'],
                'status': item['status'],
                'source': item.get('external_table') or 'notebook',
                'external_id': item.get('external_id') or '',
                'updated_at': item['updated_at'],
            })
        return rows

    def refresh_notebook_workspace(self) -> None:
        notebook = self.selected_notebook()
        if not notebook:
            return
        if self.notebook_title:
            self.notebook_title.set_text(str(notebook['title']))
        if self.notebook_subtitle:
            mission = str(notebook.get('mission') or notebook.get('description') or 'Cognitive notebook')
            self.notebook_subtitle.set_text(mission)
        if self.mapping_input:
            self.mapping_input.value = notebook.get('open_notebook_id') or ''
            self.mapping_input.update()
        for item_type, _, _ in ITEM_SECTIONS:
            rows = self._rows(item_type)
            table = self.notebook_tables.get(item_type)
            if table:
                table.rows = rows
                table.update()
            label = self.notebook_count_labels.get(item_type)
            if label:
                label.set_text(str(len(rows)))
        if self.notebook_activity_table:
            self.notebook_activity_table.rows = [
                {
                    'id': row['id'], 'event': row['event_type'], 'actor': row['actor'],
                    'created_at': row['created_at'], 'detail': row['detail_json'],
                }
                for row in self.notebooks.activity(self.selected_notebook_id, 100)
            ]
            self.notebook_activity_table.update()
        self.refresh_notebook_list()
        self._show_notebook_overview()
        self._clear_inspector()

    def _show_notebook_overview(self) -> None:
        if not self.notebook_overview:
            return
        notebook = self.selected_notebook() or {}
        self.notebook_overview.clear()
        counts = {item_type: len(self.notebooks.list_items(self.selected_notebook_id, item_type)) for item_type, _, _ in ITEM_SECTIONS}
        with self.notebook_overview:
            with ui.row().classes('w-full gap-3 flex-wrap'):
                for item_type, label, icon in ITEM_SECTIONS:
                    with ui.card().classes('lisa-card min-w-[150px] p-3'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon(icon, color='amber-7')
                            ui.label(label).classes('text-caption lisa-muted')
                        ui.label(str(counts[item_type])).classes('text-h4')
            with ui.row().classes('w-full gap-4 mt-4 items-stretch'):
                with ui.card().classes('lisa-card flex-1'):
                    ui.label('Mission').classes('text-h6')
                    ui.markdown(str(notebook.get('mission') or 'No mission recorded.'))
                    ui.label('Success Criteria').classes('text-h6 mt-3')
                    ui.markdown(str(notebook.get('success_criteria') or 'No success criteria recorded.'))
                with ui.card().classes('lisa-card w-[390px]'):
                    ui.label('Research Node Mapping').classes('text-h6')
                    ui.label('Open Notebook remains a bounded research subsystem; the LISA RDBMS remains authoritative.').classes('text-caption lisa-muted')
                    self.mapping_input = ui.input(
                        'Open Notebook ID', value=notebook.get('open_notebook_id') or '',
                    ).classes('w-full')
                    with ui.row().classes('w-full'):
                        ui.button('Save Mapping', icon='link', on_click=self.save_open_notebook_mapping)
                        ui.button('Sync Metadata', icon='sync', on_click=self.sync_open_notebook).props('outline')

    def save_open_notebook_mapping(self) -> None:
        value = str(self.mapping_input.value or '').strip() if self.mapping_input else ''
        self.notebooks.update_notebook(
            self.selected_notebook_id,
            open_notebook_id=value or None,
        )
        ui.notify('Research-node mapping saved.', type='positive')
        self.refresh_notebook_workspace()

    async def sync_open_notebook(self) -> None:
        notebook = self.selected_notebook()
        if not notebook:
            return
        health = await asyncio.to_thread(self.open_notebook.health)
        if self.notebook_node_badge:
            self.notebook_node_badge.set_text('ONLINE' if health.online else 'OFFLINE')
            self.notebook_node_badge.props(f"color={'positive' if health.online else 'grey-7'}")
        if not health.online:
            ui.notify(f'Open Notebook is offline: {health.detail}', type='warning', timeout=8000)
            return
        try:
            remote_notebooks = await asyncio.to_thread(self.open_notebook.list_notebooks)
            remote_id = str(notebook.get('open_notebook_id') or '').strip()
            if not remote_id:
                match = next(
                    (record for record in remote_notebooks
                     if str(record.get('name') or record.get('title') or '').strip().lower()
                     == str(notebook.get('title') or '').strip().lower()),
                    None,
                )
                if match:
                    remote_id = self.open_notebook.record_id(match)
                    self.notebooks.update_notebook(self.selected_notebook_id, open_notebook_id=remote_id)
            if not remote_id:
                ui.notify('Node is online, but this notebook has no matching Open Notebook ID.', type='info')
                return
            sources, notes = await asyncio.gather(
                asyncio.to_thread(self.open_notebook.list_sources, remote_id),
                asyncio.to_thread(self.open_notebook.list_notes, remote_id),
            )
            for index, source in enumerate(sources, 1):
                source_id = self.open_notebook.record_id(source) or f'index-{index}'
                self.notebooks.add_item(
                    self.selected_notebook_id, 'source',
                    self.open_notebook.record_title(source, f'Open Notebook Source {index}'),
                    body=self.open_notebook.record_body(source),
                    external_table='open_notebook_source', external_id=source_id,
                    status=str(source.get('status') or 'indexed'),
                    provenance={'source': 'open_notebook', 'endpoint': self.open_notebook.endpoint},
                    metadata=source,
                )
            for index, note in enumerate(notes, 1):
                note_id = self.open_notebook.record_id(note) or f'index-{index}'
                self.notebooks.add_item(
                    self.selected_notebook_id, 'memo',
                    self.open_notebook.record_title(note, f'Open Notebook Note {index}'),
                    body=self.open_notebook.record_body(note),
                    external_table='open_notebook_note', external_id=note_id,
                    status='synced',
                    provenance={'source': 'open_notebook', 'endpoint': self.open_notebook.endpoint},
                    metadata=note,
                )
            ui.notify(f'Synced {len(sources)} sources and {len(notes)} notes.', type='positive')
            self.refresh_notebook_workspace()
        except Exception as exc:
            ui.notify(f'Open Notebook sync failed safely: {exc}', type='negative', timeout=9000)

    def _item_markdown(self, item: dict[str, Any]) -> tuple[str, str]:
        item_type = str(item.get('item_type') or 'record')
        external_table = str(item.get('external_table') or '')
        external_id = str(item.get('external_id') or '')
        if item_type == 'cognition' and external_table == 'research_jobs' and external_id.isdigit():
            return self._memo_for_kind('job', {'id': int(external_id)})
        if item_type == 'memo' and external_table == 'knowledge_artifacts' and external_id.isdigit():
            return self._memo_for_kind('artifact', {'id': int(external_id)})
        if item_type == 'run' and external_table == 'curiosity_runs' and external_id.isdigit():
            return self._memo_for_kind('curiosity', {'id': int(external_id)})
        if item_type == 'conversation' and external_table == 'conversations':
            conversation = next(
                (row for row in root.store.list_conversations(include_archived=True)
                 if str(row['id']) == external_id), None,
            )
            if conversation:
                body = [
                    f"# {conversation.get('title','Conversation Memo')}", '',
                    f"**Created:** {conversation.get('created_at','')}  ",
                    f"**Updated:** {conversation.get('updated_at','')}", '',
                    '## Conversation Log',
                ]
                for message in root.store.list_messages(external_id):
                    role = 'Charles' if message.get('role') == 'user' else 'LISA'
                    body += ['', f'### {role}', str(message.get('content') or '')]
                return str(conversation.get('title') or item.get('title')), '\n'.join(body)
        metadata = item.get('metadata_json') or '{}'
        provenance = item.get('provenance_json') or '{}'
        title = str(item.get('title') or 'Notebook Record')
        markdown = (
            f"# {title}\n\n"
            f"**Notebook section:** {item_type.title()}  \n"
            f"**Status:** {item.get('status','')}  \n"
            f"**Source:** {external_table or 'LISA notebook'}  \n"
            f"**Updated:** {item.get('updated_at','')}\n\n"
            f"## Content\n\n{item.get('body') or 'No body text was stored for this linked record.'}\n\n"
            f"## Provenance\n```json\n{provenance}\n```\n\n"
            f"## Metadata\n```json\n{metadata}\n```"
        )
        return title, markdown

    def inspect_item(self, item_id: int) -> None:
        item = self.notebooks.get_item(item_id)
        if not item or not self.notebook_inspector_body:
            return
        self.selected_notebook_item_id = item_id
        if self.notebook_inspector_title:
            self.notebook_inspector_title.set_text(str(item.get('title') or 'Selected Item'))
        title, markdown = self._item_markdown(item)
        self.notebook_inspector_body.clear()
        with self.notebook_inspector_body:
            ui.label(str(item.get('item_type') or '').upper()).classes('text-overline lisa-muted')
            ui.markdown(markdown.replace('# ', '## ', 1)).classes('w-full')
            with ui.row().classes('w-full gap-2 sticky bottom-0 bg-[#121a23] py-3'):
                ui.button('Open Memo', icon='open_in_full', on_click=lambda: self.show_memo(title, markdown)).props('outline')
                if item.get('item_type') == 'cognition' and str(item.get('external_id') or '').isdigit():
                    ui.button(
                        'Open Cognition', icon='account_tree',
                        on_click=lambda: self.open_cognition('job', int(item['external_id'])),
                    )
                if not item.get('external_table') or str(item.get('external_table')).startswith('open_notebook'):
                    ui.button('Edit', icon='edit', on_click=lambda: self.open_entry_editor(item)).props('flat')
                ui.button('Archive', icon='archive', on_click=lambda: self.archive_selected_item()).props('flat color=warning')

    def _clear_inspector(self) -> None:
        if not self.notebook_inspector_body:
            return
        self.notebook_inspector_body.clear()
        with self.notebook_inspector_body:
            ui.icon('touch_app', size='38px').classes('text-[#b8893c]')
            ui.label('Select any notebook record to inspect it here.').classes('lisa-muted')
            ui.label('Open Memo expands the complete record without overpowering the workspace.').classes('text-caption lisa-muted')

    def archive_selected_item(self) -> None:
        if not self.selected_notebook_item_id:
            return
        self.notebooks.archive_item(self.selected_notebook_item_id)
        ui.notify('Notebook item archived. The authoritative source record was not deleted.', type='warning')
        self.selected_notebook_item_id = None
        self.refresh_notebook_workspace()

    def _open_table_row(self, event: Any) -> None:
        row = _event_row(event)
        item_id = int(row.get('id', 0) or 0)
        if item_id:
            self.inspect_item(item_id)

    def _open_table_memo(self, event: Any) -> None:
        row = _event_row(event)
        item = self.notebooks.get_item(int(row.get('id', 0) or 0))
        if item:
            title, markdown = self._item_markdown(item)
            self.show_memo(title, markdown)

    def _open_table_cognition(self, event: Any) -> None:
        row = _event_row(event)
        item = self.notebooks.get_item(int(row.get('id', 0) or 0))
        if item and str(item.get('external_id') or '').isdigit():
            self.open_cognition('job', int(item['external_id']))

    def _build_item_table(self, item_type: str) -> Any:
        columns = [
            {'name': 'title', 'label': 'Title', 'field': 'title', 'align': 'left', 'sortable': True},
            {'name': 'status', 'label': 'Status', 'field': 'status', 'sortable': True},
            {'name': 'source', 'label': 'Source', 'field': 'source', 'sortable': True},
            {'name': 'updated_at', 'label': 'Updated', 'field': 'updated_at', 'sortable': True},
            {'name': 'memo', 'label': 'Memo', 'field': 'memo', 'align': 'center'},
        ]
        if item_type == 'cognition':
            columns.insert(-1, {'name': 'cognition', 'label': 'Cognition', 'field': 'cognition', 'align': 'center'})
        table = ui.table(columns=columns, rows=[], row_key='id', pagination=20).classes('w-full')
        table.add_slot(
            'body-cell-memo',
            '''<q-td :props="props"><q-btn flat dense round icon="description" color="amber-7" @click.stop="$parent.$emit('notebook_memo', props.row)"><q-tooltip>Open memo view</q-tooltip></q-btn></q-td>''',
        )
        table.on('notebook_memo', self._open_table_memo)
        if item_type == 'cognition':
            table.add_slot(
                'body-cell-cognition',
                '''<q-td :props="props"><q-btn flat dense round icon="account_tree" color="amber-7" @click.stop="$parent.$emit('notebook_cognition', props.row)"><q-tooltip>Open cognition mind map</q-tooltip></q-btn></q-td>''',
            )
            table.on('notebook_cognition', self._open_table_cognition)
        table.on('rowClick', self._open_table_row)
        return table

    def show_create_notebook(self) -> None:
        if self.notebook_create_dialog:
            self.notebook_create_dialog.open()

    def build_create_notebook_dialog(self) -> None:
        with ui.dialog() as self.notebook_create_dialog:
            with ui.card().classes('w-[720px] max-w-[95vw] lisa-card'):
                ui.label('Create Cognitive Notebook').classes('text-h5')
                title = ui.input('Title').classes('w-full')
                description = ui.textarea('Description').classes('w-full')
                mission = ui.textarea('Mission').classes('w-full')
                beneficiary = ui.input('Beneficiary').classes('w-full')
                success = ui.textarea('Success Criteria').classes('w-full')
                open_id = ui.input('Open Notebook ID (optional)').classes('w-full')

                def create() -> None:
                    if not str(title.value or '').strip():
                        ui.notify('A notebook title is required.', type='warning')
                        return
                    notebook_id = self.notebooks.create_notebook(
                        str(title.value), description=str(description.value or ''),
                        mission=str(mission.value or ''), beneficiary=str(beneficiary.value or ''),
                        success_criteria=str(success.value or ''),
                        open_notebook_id=str(open_id.value or '').strip() or None,
                    )
                    self.selected_notebook_id = notebook_id
                    self.notebook_create_dialog.close()
                    for control in (title, description, mission, beneficiary, success, open_id):
                        control.value = ''
                    self.refresh_notebook_workspace()
                    ui.notify('Cognitive notebook created.', type='positive')

                with ui.row().classes('w-full justify-end'):
                    ui.button('Cancel', on_click=self.notebook_create_dialog.close).props('flat')
                    ui.button('Create Notebook', icon='add', on_click=create)

    def show_new_entry(self) -> None:
        self.open_entry_editor(None)

    def open_entry_editor(self, item: dict[str, Any] | None) -> None:
        if not self.entry_dialog:
            return
        self.entry_dialog._editing_item_id = int(item['id']) if item else None
        self.entry_type.value = str(item.get('item_type') or 'memo') if item else 'memo'
        self.entry_type.disable() if item else self.entry_type.enable()
        self.entry_title.value = str(item.get('title') or '') if item else ''
        self.entry_body.value = str(item.get('body') or '') if item else ''
        self.entry_status.value = str(item.get('status') or 'active') if item else 'active'
        self.entry_dialog.open()

    def build_entry_dialog(self) -> None:
        with ui.dialog() as self.entry_dialog:
            self.entry_dialog._editing_item_id = None
            with ui.card().classes('w-[760px] max-w-[95vw] lisa-card'):
                ui.label('Notebook Entry').classes('text-h5')
                self.entry_type = ui.select(
                    ['source', 'memo', 'evidence', 'decision', 'commitment', 'evaluation'],
                    value='memo', label='Section',
                ).classes('w-full')
                self.entry_title = ui.input('Title').classes('w-full')
                self.entry_body = ui.textarea('Memo / record body').props('autogrow').classes('w-full')
                self.entry_status = ui.input('Status', value='active').classes('w-full')

                def save() -> None:
                    title = str(self.entry_title.value or '').strip()
                    if not title:
                        ui.notify('A title is required.', type='warning')
                        return
                    item_id = getattr(self.entry_dialog, '_editing_item_id', None)
                    if item_id:
                        self.notebooks.update_item(
                            int(item_id), title=title, body=str(self.entry_body.value or ''),
                            status=str(self.entry_status.value or 'active'),
                        )
                    else:
                        self.notebooks.add_item(
                            self.selected_notebook_id, str(self.entry_type.value or 'memo'), title,
                            body=str(self.entry_body.value or ''),
                            status=str(self.entry_status.value or 'active'),
                            provenance={'source': 'human_or_lisa_notebook_entry'},
                        )
                    self.entry_dialog.close()
                    self.refresh_notebook_workspace()
                    ui.notify('Notebook entry saved.', type='positive')

                with ui.row().classes('w-full justify-end'):
                    ui.button('Cancel', on_click=self.entry_dialog.close).props('flat')
                    ui.button('Save Entry', icon='save', on_click=save)

    def build_notebook_dialog(self) -> None:
        with ui.dialog().props('maximized transition-show=slide-up transition-hide=slide-down') as self.notebook_dialog:
            with ui.card().classes('w-full h-full bg-[#0c1117] text-white p-0'):
                with ui.row().classes('w-full items-center bg-[#101821] p-3'):
                    ui.icon('menu_book').classes('text-3xl text-[#b8893c]')
                    with ui.column().classes('gap-0'):
                        self.notebook_title = ui.label('LISA Cognitive Notebook').classes('text-h5')
                        self.notebook_subtitle = ui.label('Mission workspace').classes('text-caption lisa-muted')
                    ui.space()
                    self.notebook_node_badge = ui.badge('NODE UNKNOWN', color='grey-7')
                    ui.button('New Entry', icon='note_add', on_click=self.show_new_entry).props('outline')
                    ui.button(icon='sync', on_click=self.sync_open_notebook).props('flat round').tooltip('Sync bounded Open Notebook metadata')
                    ui.button(icon='close', on_click=self.notebook_dialog.close).props('flat round')

                with ui.splitter(value=19).classes('w-full h-[calc(100vh-72px)]') as outer:
                    with outer.before:
                        with ui.column().classes('w-full h-full bg-[#121a23] p-3'):
                            with ui.row().classes('w-full items-center'):
                                ui.label('NOTEBOOKS').classes('text-overline lisa-muted')
                                ui.space()
                                ui.button(icon='add', on_click=self.show_create_notebook).props('flat round dense')
                            self.notebook_list = ui.list().classes('w-full flex-1 overflow-auto')
                    with outer.after:
                        with ui.splitter(value=73).classes('w-full h-full') as inner:
                            with inner.before:
                                with ui.column().classes('w-full h-full'):
                                    with ui.tabs().props('dense align=left').classes('w-full bg-[#101821]') as self.notebook_tabs:
                                        overview_tab = ui.tab('Overview', icon='dashboard')
                                        section_tabs: dict[str, Any] = {
                                            item_type: ui.tab(label, icon=icon)
                                            for item_type, label, icon in ITEM_SECTIONS
                                        }
                                        activity_tab = ui.tab('Activity', icon='history')
                                    with ui.tab_panels(self.notebook_tabs, value=overview_tab).classes('w-full flex-1 bg-transparent'):
                                        with ui.tab_panel(overview_tab).classes('p-4'):
                                            self.notebook_overview = ui.column().classes('w-full')
                                        for item_type, label, icon in ITEM_SECTIONS:
                                            with ui.tab_panel(section_tabs[item_type]).classes('p-3'):
                                                with ui.row().classes('w-full items-center'):
                                                    ui.icon(icon, color='amber-7')
                                                    ui.label(label).classes('text-h5')
                                                    self.notebook_count_labels[item_type] = ui.badge('0', color='grey-7')
                                                    ui.space()
                                                    if item_type in {'source', 'memo', 'evidence', 'decision', 'commitment', 'evaluation'}:
                                                        ui.button('Add', icon='add', on_click=self.show_new_entry).props('outline dense')
                                                self.notebook_tables[item_type] = self._build_item_table(item_type)
                                        with ui.tab_panel(activity_tab).classes('p-3'):
                                            self.notebook_activity_table = ui.table(columns=[
                                                {'name':'event','label':'Event','field':'event','sortable':True},
                                                {'name':'actor','label':'Actor','field':'actor','sortable':True},
                                                {'name':'created_at','label':'Created','field':'created_at','sortable':True},
                                                {'name':'detail','label':'Detail','field':'detail','align':'left'},
                                            ], rows=[], row_key='id', pagination=25).classes('w-full')
                            with inner.after:
                                with ui.column().classes('w-full h-full bg-[#121a23] p-3'):
                                    ui.label('CONTEXT INSPECTOR').classes('text-overline lisa-muted')
                                    self.notebook_inspector_title = ui.label('No item selected').classes('text-h6')
                                    self.notebook_inspector_body = ui.scroll_area().classes('w-full flex-1')

    def show_notebook_workspace(self) -> None:
        self.refresh_notebook_workspace()
        self.notebook_dialog.open()
        asyncio.create_task(self._refresh_node_health())

    async def _refresh_node_health(self) -> None:
        health = await asyncio.to_thread(self.open_notebook.health)
        if self.notebook_node_badge:
            self.notebook_node_badge.set_text('NODE ONLINE' if health.online else 'NODE OFFLINE')
            self.notebook_node_badge.props(f"color={'positive' if health.online else 'grey-7'}")

    def build(self) -> None:
        super().build()
        with ui.teleport('.q-drawer .q-drawer__content'):
            ui.separator().classes('mt-3')
            with ui.column().classes('w-full p-3 gap-2'):
                ui.label('COGNITIVE NOTEBOOKS').classes('text-overline lisa-muted')
                ui.button(
                    'Open Notebook Workspace', icon='menu_book', on_click=self.show_notebook_workspace,
                ).props('unelevated color=amber-8').classes('w-full')
                ui.label('Notebook → Sources → Cognition → Memos → Decisions → Commitments').classes('text-caption lisa-muted')
        self.build_notebook_dialog()
        self.build_create_notebook_dialog()
        self.build_entry_dialog()
        self.refresh_notebook_workspace()


root.workspace = NotebookWorkspace()


def main() -> None:
    ui.run(
        title='LISA Cognitive Notebook Workspace',
        host='127.0.0.1',
        port=int(os.getenv('LISA_UI_PORT', '8090')),
        reload=False,
        show=True,
        dark=True,
        favicon='🜂',
    )


if __name__ in {'__main__', '__mp_main__'}:
    main()
