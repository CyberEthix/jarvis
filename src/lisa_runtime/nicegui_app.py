"""Full-screen NiceGUI workspace for end-to-end human + AI research."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from nicegui import ui

from .knowledge_store import KnowledgeStore
from .ollama_node import OllamaNode
from .orchestrator import CognitiveOrchestrator
from .repository import SQLiteRepository

DB_PATH = Path(os.getenv('LISA_RUNTIME_DB', '~/.local/share/lisa-runtime/lisa_runtime.db')).expanduser()
MODEL = os.getenv('LISA_OLLAMA_MODEL', 'gemma4:e2b')
ENDPOINT = os.getenv('LISA_OLLAMA_ENDPOINT', 'http://127.0.0.1:11434')
repo = SQLiteRepository(DB_PATH)
store = KnowledgeStore(DB_PATH)
node = OllamaNode(endpoint=ENDPOINT, model=MODEL)

CSS = '''
:root { --lisa-bg:#0c1117; --lisa-panel:#121a23; --lisa-panel2:#18222d; --lisa-border:#2a3b4c; --lisa-accent:#b8893c; --lisa-text:#e8edf3; --lisa-muted:#9aabba; }
body { background:var(--lisa-bg); color:var(--lisa-text); }
.q-page { min-height:100vh; }
.lisa-card { background:var(--lisa-panel); border:1px solid var(--lisa-border); border-radius:12px; }
.lisa-kpi { min-width:150px; padding:14px; background:var(--lisa-panel2); border:1px solid var(--lisa-border); border-radius:10px; }
.lisa-kpi-value { font-size:1.55rem; font-weight:700; }
.lisa-muted { color:var(--lisa-muted); }
.document-page { background:#f7f4ee; color:#1e2630; max-width:980px; min-height:1050px; margin:18px auto; padding:70px 82px; border-radius:3px; box-shadow:0 5px 24px rgba(0,0,0,.35); font-family:Georgia,'Times New Roman',serif; line-height:1.58; }
.document-page h1,.document-page h2,.document-page h3 { font-family:Arial,sans-serif; color:#24364a; }
.document-page pre { white-space:pre-wrap; font-family:Georgia,'Times New Roman',serif; }
.chat-scroll { height:calc(100vh - 260px); overflow:auto; }
.full-height-panel { height:calc(100vh - 120px); }
'''

class Workspace:
    def __init__(self) -> None:
        self.conversation_id: str | None = None
        self.selected_job_id: int | None = None
        self.selected_artifact_id: int | None = None
        self.busy = False
        self.progress = 0
        self.stage = 'Idle'
        self.current_step = '0 / 0'
        self.elapsed = '0.0 s'
        self.chat_container = None
        self.job_table = None
        self.artifact_table = None
        self.thought_table = None
        self.document_view = None
        self.document_title = None
        self.status_label = None
        self.progress_bar = None
        self.kpi_labels: dict[str, Any] = {}

    def ensure_conversation(self) -> str:
        if not self.conversation_id:
            self.conversation_id = store.create_conversation('Working session')
        return self.conversation_id

    def progress_event(self, payload: dict) -> None:
        self.stage = str(payload.get('stage', 'idle')).replace('_', ' ').title()
        self.progress = int(payload.get('percent', 0) or 0)
        self.current_step = f"{int(payload.get('current_step',0) or 0)} / {int(payload.get('total_steps',0) or 0)}"
        self.elapsed = f"{float(payload.get('elapsed',0.0) or 0.0):.1f} s"
        self.update_kpis()

    def state_event(self, state, detail: str) -> None:
        self.stage = str(getattr(state, 'value', state)).replace('_', ' ').title()
        if self.status_label:
            self.status_label.set_text(f'{self.stage} — {detail}')
        self.update_kpis()

    def orchestrator(self) -> CognitiveOrchestrator:
        return CognitiveOrchestrator(repository=repo, node=node, state_listener=self.state_event, progress_listener=self.progress_event)

    def update_kpis(self) -> None:
        jobs = repo.list_jobs()
        statuses = [str(j.get('status','')).upper() for j in jobs]
        values = {
            'stage': self.stage,
            'progress': f'{self.progress}%',
            'steps': self.current_step,
            'elapsed': self.elapsed,
            'queued': str(sum(s in {'NEW','READY','QUEUED'} for s in statuses)),
            'running': str(sum(s == 'RUNNING' for s in statuses)),
            'complete': str(sum(s == 'COMPLETED' for s in statuses)),
            'failed': str(sum(s == 'FAILED' for s in statuses)),
        }
        for key, value in values.items():
            if key in self.kpi_labels:
                self.kpi_labels[key].set_text(value)
        if self.progress_bar:
            self.progress_bar.set_value(max(0, min(1, self.progress / 100)))

    def kpi(self, title: str, key: str, value: str) -> None:
        with ui.column().classes('lisa-kpi gap-1'):
            ui.label(title).classes('text-caption lisa-muted')
            self.kpi_labels[key] = ui.label(value).classes('lisa-kpi-value')

    def render_chat(self) -> None:
        if not self.chat_container:
            return
        self.chat_container.clear()
        cid = self.ensure_conversation()
        with self.chat_container:
            for msg in store.list_messages(cid):
                sent = msg['role'] == 'user'
                with ui.chat_message(name='Charles' if sent else 'LISA', sent=sent).classes('w-full'):
                    ui.markdown(msg['content'])

    async def send_message(self, text: str) -> None:
        text = text.strip()
        if not text or self.busy:
            return
        cid = self.ensure_conversation()
        store.add_message(cid, 'user', text)
        store.add_thought('input', 'User input', text, conversation_id=cid)
        self.render_chat()
        self.busy = True
        try:
            response = await asyncio.to_thread(self.orchestrator().chat, text)
            store.add_message(cid, 'assistant', response, {'model': node.model})
            store.add_thought('response', 'Assistant response', response, conversation_id=cid)
            store.save_artifact('conversation_output', f'Response: {text[:60]}', response, conversation_id=cid)
        except Exception as exc:
            ui.notify(str(exc), type='negative')
        finally:
            self.busy = False
            self.render_chat()
            self.refresh_all()

    def queue_research(self, question: str) -> None:
        question = question.strip()
        if not question:
            ui.notify('Enter a research question first.', type='warning')
            return
        cid = self.ensure_conversation()
        job_id = self.orchestrator().queue_research(question)
        store.add_thought('research_queued', f'Research job {job_id}', question, conversation_id=cid, research_job_id=job_id)
        store.save_artifact('research_question', f'Research Question #{job_id}', question, research_job_id=job_id, conversation_id=cid)
        ui.notify(f'Research job {job_id} queued.', type='positive')
        self.refresh_all()

    async def run_one_job(self) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            job_id = await asyncio.to_thread(self.orchestrator().run_one_research_cycle)
            if job_id:
                report = repo.get_report(job_id) or {}
                body = report.get('summary', '')
                store.add_thought('research_complete', f'Research job {job_id} completed', body, research_job_id=job_id)
                store.save_artifact('research_report', f'Research Report #{job_id}', body, research_job_id=job_id)
                ui.notify(f'Research job {job_id} completed.', type='positive')
            else:
                ui.notify('No queued research jobs.', type='info')
        except Exception as exc:
            ui.notify(str(exc), type='negative')
        finally:
            self.busy = False
            self.refresh_all()

    def refresh_jobs(self) -> None:
        if not self.job_table: return
        rows = []
        for j in repo.list_jobs(500):
            rows.append({'id':j['id'],'status':j['status'],'priority':j['priority'],'question':j['question'],'created_at':j['created_at']})
        self.job_table.rows = rows
        self.job_table.update()

    def refresh_artifacts(self) -> None:
        if not self.artifact_table: return
        self.artifact_table.rows = [
            {'id':a['id'],'type':a['artifact_type'],'title':a['title'],'version':a['version'],'updated_at':a['updated_at']}
            for a in store.list_artifacts()
        ]
        self.artifact_table.update()

    def refresh_thoughts(self) -> None:
        if not self.thought_table: return
        self.thought_table.rows = [
            {'id':t['id'],'stage':t['stage'],'title':t['title'],'status':t['status'],'created_at':t['created_at']}
            for t in store.list_thoughts(limit=500)
        ]
        self.thought_table.update()

    def refresh_all(self) -> None:
        self.refresh_jobs(); self.refresh_artifacts(); self.refresh_thoughts(); self.update_kpis()

    def open_job(self, event) -> None:
        row = event.args if isinstance(event.args, dict) else getattr(event, 'args', {})
        job_id = int(row.get('id'))
        self.selected_job_id = job_id
        job = next((j for j in repo.list_jobs(500) if int(j['id']) == job_id), None)
        report = repo.get_report(job_id)
        thoughts = store.list_thoughts(job_id)
        title = f"Research #{job_id}: {job['question'] if job else ''}"
        sections = [f'# {title}']
        if job:
            sections += [f"**Status:** {job['status']}", f"**Created:** {job['created_at']}", '', '## Research Question', job['question'], '', '## Rationale', job.get('rationale','')]
        if report:
            sections += ['', '## Findings and Synthesis', report.get('summary',''), '', '## Limitations', report.get('limitations','')]
            try:
                findings = json.loads(report.get('findings_json','[]'))
                if findings:
                    sections += ['', '## Evidence and Step Outputs']
                    for i, f in enumerate(findings, 1):
                        sections += [f'### Step {i}', f.get('output', json.dumps(f, indent=2))]
            except Exception:
                pass
        if thoughts:
            sections += ['', '## E2E Thought Trail']
            for t in thoughts:
                sections += [f"### {t['stage'].replace('_',' ').title()} — {t['title']}", t['content']]
        self.show_document(title, '\n\n'.join(sections))

    def open_artifact(self, event) -> None:
        row = event.args if isinstance(event.args, dict) else getattr(event, 'args', {})
        artifact = store.get_artifact(int(row.get('id')))
        if artifact:
            self.selected_artifact_id = artifact['id']
            self.show_document(artifact['title'], f"# {artifact['title']}\n\n**Type:** {artifact['artifact_type']}  \n**Version:** {artifact['version']}  \n**Updated:** {artifact['updated_at']}\n\n{artifact['body']}")

    def show_document(self, title: str, markdown: str) -> None:
        if self.document_title: self.document_title.set_text(title)
        if self.document_view:
            self.document_view.clear()
            with self.document_view:
                with ui.column().classes('document-page w-full'):
                    ui.markdown(markdown).classes('w-full')

    def build(self) -> None:
        ui.add_css(CSS)
        ui.dark_mode().enable()
        with ui.header(elevated=True).classes('bg-[#101821] items-center'):
            ui.button(icon='menu', on_click=lambda: left.toggle()).props('flat round')
            ui.label('LISA Cognitive Research Workspace').classes('text-h6')
            ui.space()
            self.status_label = ui.label(f'Idle — Model: {MODEL}').classes('lisa-muted')
            ui.button(icon='refresh', on_click=self.refresh_all).props('flat round').tooltip('Refresh workspace')
            ui.button(icon='fullscreen', on_click=lambda: ui.run_javascript('document.documentElement.requestFullscreen()')).props('flat round').tooltip('Full screen')

        with ui.left_drawer(value=True, bordered=True).classes('bg-[#121a23]') as left:
            ui.label('WORKSPACE').classes('text-overline lisa-muted')
            with ui.list().props('dense').classes('w-full'):
                for icon, label, target in [
                    ('dashboard','Command Center','command'),('chat','Conversations','conversation'),
                    ('science','Research Queue','research'),('description','Research Library','library'),
                    ('psychology','Thought Trail','thoughts'),('hub','Nodes & Services','nodes'),
                    ('settings','Settings','settings')]:
                    ui.item(label, on_click=lambda _, t=target: tabs.set_value(t)).props(f'clickable v-ripple icon={icon}')
            ui.separator()
            ui.label('SYSTEM').classes('text-overline lisa-muted')
            ui.label(f'Model: {MODEL}').classes('text-caption')
            ui.label(f'Database: {DB_PATH}').classes('text-caption break-all')

        with ui.tabs().props('dense align=left').classes('hidden') as tabs:
            for name in ['command','conversation','research','library','thoughts','nodes','settings']:
                ui.tab(name)

        with ui.tab_panels(tabs, value='command').classes('w-full bg-transparent'):
            with ui.tab_panel('command').classes('p-3'):
                with ui.row().classes('w-full gap-3 flex-wrap'):
                    self.kpi('Stage','stage','Idle'); self.kpi('Progress','progress','0%'); self.kpi('Steps','steps','0 / 0'); self.kpi('Elapsed','elapsed','0.0 s')
                    self.kpi('Queued','queued','0'); self.kpi('Running','running','0'); self.kpi('Completed','complete','0'); self.kpi('Failed','failed','0')
                self.progress_bar = ui.linear_progress(value=0).classes('w-full mt-3')
                with ui.row().classes('w-full gap-3 mt-3'):
                    with ui.card().classes('lisa-card flex-1'):
                        ui.label('Active Collaboration').classes('text-h6')
                        quick_input = ui.textarea(placeholder='Ask Lisa, develop an idea, or frame a research question...').classes('w-full')
                        with ui.row():
                            ui.button('Send to Lisa', icon='send', on_click=lambda: self.send_message(quick_input.value or ''))
                            ui.button('Queue as Research', icon='science', on_click=lambda: self.queue_research(quick_input.value or '')).props('outline')
                            ui.button('Run Next Job', icon='play_arrow', on_click=self.run_one_job).props('outline')
                    with ui.card().classes('lisa-card w-[380px]'):
                        ui.label('Operating Principle').classes('text-h6')
                        ui.markdown('**Every input, output, research plan, thought event, result, and artifact is persisted in the local relational database.**')
                        ui.markdown('Human direction and autonomous curiosity remain visible, selectable, editable, and auditable.')

            with ui.tab_panel('conversation').classes('p-0'):
                with ui.splitter(value=23).classes('w-full full-height-panel') as split:
                    with split.before:
                        with ui.column().classes('w-full p-3'):
                            ui.label('Conversations').classes('text-h6')
                            ui.button('New Conversation', icon='add', on_click=lambda: self.new_conversation()).classes('w-full')
                            self.conversation_list = ui.list().classes('w-full')
                    with split.after:
                        with ui.column().classes('w-full h-full p-3'):
                            self.chat_container = ui.column().classes('w-full chat-scroll')
                            msg_input = ui.textarea(placeholder='Work with Lisa...').classes('w-full')
                            with ui.row().classes('w-full'):
                                ui.button('Send', icon='send', on_click=lambda: self.send_message(msg_input.value or ''))
                                ui.button('Research This', icon='science', on_click=lambda: self.queue_research(msg_input.value or '')).props('outline')

            with ui.tab_panel('research').classes('p-3'):
                with ui.row().classes('w-full items-center'):
                    ui.label('Research Queue and Execution').classes('text-h5')
                    ui.space(); ui.button('Run Next Job', icon='play_arrow', on_click=self.run_one_job)
                self.job_table = ui.table(columns=[
                    {'name':'id','label':'ID','field':'id','sortable':True}, {'name':'status','label':'Status','field':'status','sortable':True},
                    {'name':'priority','label':'Priority','field':'priority','sortable':True}, {'name':'question','label':'Question','field':'question','align':'left'},
                    {'name':'created_at','label':'Created','field':'created_at','sortable':True}], rows=[], row_key='id', pagination=20).classes('w-full')
                self.job_table.on('rowClick', self.open_job)

            with ui.tab_panel('library').classes('p-0'):
                with ui.splitter(value=32).classes('w-full full-height-panel') as library_split:
                    with library_split.before:
                        with ui.column().classes('w-full p-3'):
                            ui.label('Research Library').classes('text-h5')
                            self.artifact_table = ui.table(columns=[
                                {'name':'id','label':'ID','field':'id'}, {'name':'type','label':'Type','field':'type'},
                                {'name':'title','label':'Title','field':'title','align':'left'}, {'name':'version','label':'Version','field':'version'},
                                {'name':'updated_at','label':'Updated','field':'updated_at'}], rows=[], row_key='id', pagination=20).classes('w-full')
                            self.artifact_table.on('rowClick', self.open_artifact)
                    with library_split.after:
                        with ui.column().classes('w-full h-full bg-[#222831]'):
                            with ui.row().classes('w-full p-3 items-center'):
                                self.document_title = ui.label('Select research or an artifact').classes('text-h6')
                                ui.space(); ui.button(icon='print', on_click=lambda: ui.run_javascript('window.print()')).props('flat round').tooltip('Print / Save PDF')
                            self.document_view = ui.column().classes('w-full overflow-auto')
                            self.show_document('Research Document Viewer', '# Research Document Viewer\n\nSelect a research job or saved artifact to read the complete result, limitations, evidence, and thought trail in a Word-style document view.')

            with ui.tab_panel('thoughts').classes('p-3'):
                ui.label('E2E Thought Trail').classes('text-h5')
                ui.label('Visible internal workflow records: input → framing → planning → execution → synthesis → solution.').classes('lisa-muted')
                self.thought_table = ui.table(columns=[
                    {'name':'id','label':'ID','field':'id'}, {'name':'stage','label':'Stage','field':'stage'},
                    {'name':'title','label':'Title','field':'title','align':'left'}, {'name':'status','label':'Status','field':'status'},
                    {'name':'created_at','label':'Created','field':'created_at'}], rows=[], row_key='id', pagination=25).classes('w-full')

            with ui.tab_panel('nodes').classes('p-3'):
                ui.label('Nodes & Services').classes('text-h5')
                with ui.row().classes('w-full gap-3'):
                    with ui.card().classes('lisa-card'):
                        ui.label('LISA01 — Core Orchestrator').classes('text-h6'); ui.label('Local SQLite • NiceGUI • Python runtime'); ui.badge('ONLINE', color='positive')
                    with ui.card().classes('lisa-card'):
                        ui.label('Ollama Node').classes('text-h6'); ui.label(f'{ENDPOINT} • {MODEL}'); ui.badge('CONFIGURED', color='positive')
                    with ui.card().classes('lisa-card'):
                        ui.label('Expansion Bus').classes('text-h6'); ui.label('NATS / n8n / remote LLM nodes'); ui.badge('PLANNED', color='warning')

            with ui.tab_panel('settings').classes('p-3'):
                ui.label('Workspace Settings').classes('text-h5')
                ui.input('Ollama Endpoint', value=ENDPOINT).classes('w-[600px]')
                ui.input('Primary Model', value=MODEL).classes('w-[600px]')
                ui.input('Local Database', value=str(DB_PATH)).classes('w-[600px]')
                ui.switch('Autonomous curiosity', value=False)
                ui.switch('Require approval before web research', value=True)
                ui.switch('Save all E2E thought events', value=True)

        self.refresh_all()
        self.render_chat()

    def new_conversation(self) -> None:
        self.conversation_id = store.create_conversation('New working session')
        self.render_chat()
        ui.notify('New conversation created.', type='positive')

workspace = Workspace()

@ui.page('/')
def index() -> None:
    workspace.build()


def main() -> None:
    ui.run(title='LISA Cognitive Research Workspace', host='127.0.0.1', port=int(os.getenv('LISA_UI_PORT','8090')),
           reload=False, show=True, dark=True, favicon='🜂')

if __name__ in {'__main__', '__mp_main__'}:
    main()
