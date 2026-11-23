"""Read-only product deliverables from saved snapshots and validated source manifests."""
import io
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import zipfile

from .core import Problem
from .execution import manifest, relative_path, validate_text
from .planning import render_proposal, render_tasks
from .schemas import ArtifactOut, ArtifactContentOut, WorkspaceProjectOut, LocalFileOut, LocalFilesOut


class Deliverables:
    def __init__(self, jobs):
        self.jobs, self.store = jobs, jobs.store

    def overview(self):
        rows = []
        for iv in self.store.list_interviews():
            proposal = self.store.proposal(iv.id)
            run = self.store.latest_execution(iv.id, proposal.id) if proposal else None
            check = self.store.latest_verification(iv.id)
            stage = 'prd' if iv.status != 'closed' else 'plan'
            state = {'collecting': '梳理想法', 'draft_pending': '审阅PRD', 'auditing': '确认清单',
                     'closed': '准备技术方案'}.get(iv.status, iv.status)
            if proposal:
                state = '审阅技术方案'
                if self.store.development_plan(proposal.id):
                    stage, state = 'code', '准备开发'
            if run:
                stage = 'check' if run.status == 'generated' else 'code'
                state = {'generated': '代码已保存，待检查', 'running': '正在开发', 'pending': '等待开发',
                         'failed': '开发失败，可继续', 'blocked': '需要补充信息',
                         'interrupted': '已中断，可继续', 'cancelled': '已停止，进度保留'}.get(run.status, run.status)
            if check and run and check.revision == run.data.get('revision'):
                stage = 'check'
                state = {'passed': '工程检查通过', 'failed': '检查发现问题', 'running': '正在检查',
                         'pending': '等待检查', 'interrupted': '检查中断', 'cancelled': '检查已停止'}.get(check.status, check.status)
            running = any(j.status in ('queued', 'running', 'cancelling') for j in self.store.jobs(iv.id))
            rows.append(WorkspaceProjectOut(id=iv.id, name=iv.product_name or '未命名产品', stage=stage,
                state=state, updated_at=iv.updated_at, running=running,
                files_count=len(run.data.get('manifest', {})) if run else 0))
        return rows

    def documents(self, interview_id):
        iv = self.store.get_interview(interview_id)
        docs = {}
        exported = self.store.asset(iv.id, 'export_plan') if iv.status == 'closed' else None
        names = {'PRD.md': ('prd', 'PRD.md'), '阶段0-PRD补全清单.md': ('checklist', 'PRD补全清单.md'),
                 '项目状态.md': ('decisions', '项目状态与决策.md')}
        if exported:
            # Preview saved generated snapshots; never accept a disk path from the caller.
            for path, content in exported.data.get('files', {}).items():
                if Path(path).name in names and isinstance(content, str):
                    key, name = names[Path(path).name]
                    docs[key] = (name, content)
        if 'prd' not in docs:
            draft = self.store.latest_draft(iv.id)
            if draft:
                docs['prd'] = ('PRD草稿.md', draft.content)
        proposal = self.store.proposal(iv.id)
        if proposal:
            docs['technical'] = (f'技术方案-v{proposal.version}.md', render_proposal(proposal.data))
            plan = self.store.development_plan(proposal.id)
            if plan:
                docs['tasks'] = (f'开发任务-v{proposal.version}.md', render_tasks(plan.data))
        return docs

    def artifacts(self, interview_id):
        return [ArtifactOut(id=key, name=name, bytes=len(content.encode('utf-8')),
            local_path=str(self.save_document(interview_id, name, content)),
            download_url=f'/api/v1/interviews/{interview_id}/artifacts/{key}/download')
            for key, (name, content) in self.documents(interview_id).items()]

    def artifact(self, interview_id, key):
        docs = self.documents(interview_id)
        if key not in docs:
            raise Problem('NOT_FOUND', '此项目尚未生成该文件。', 404)
        name, content = docs[key]
        return ArtifactContentOut(id=key, name=name, bytes=len(content.encode('utf-8')), content=content,
            local_path=str(self.save_document(interview_id, name, content)),
            download_url=f'/api/v1/interviews/{interview_id}/artifacts/{key}/download')

    def document_directory(self, interview_id):
        self.store.get_interview(interview_id)
        return Path(self.store.settings.data_dir).absolute() / 'deliverables' / str(interview_id)

    def save_document(self, interview_id, name, content):
        """Materialize a DB snapshot without overwriting a user's independent edits."""
        raw = content.encode('utf-8')
        path = self.document_directory(interview_id) / hashlib.sha256(raw).hexdigest() / name
        for parent in [path, *path.parents]:
            if parent.is_symlink():
                raise Problem('LOCAL_FILE_UNSAFE', '保存目录包含链接，请恢复本机文件目录。', 409)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_bytes() != raw:
                    raise Problem('LOCAL_FILE_CHANGED', '本机文档已被独立修改，已保留，请核对保存目录。', 409)
                return path
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if path.is_symlink() or path.read_bytes() != raw:
                        raise Problem('LOCAL_FILE_CHANGED', '本机文档已变化，请核对保存目录。', 409)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return path
        except OSError:
            raise Problem('LOCAL_SAVE_FAILED', '本机文件保存失败，请检查磁盘空间和文件夹权限。', 409)

    def local_files(self, interview_id):
        iv = self.store.get_interview(interview_id)
        files = []
        def modified(path):
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        for item in self.artifacts(interview_id):
            path = Path(item.local_path)
            files.append(LocalFileOut(name=item.name, kind='document', local_path=str(path),
                bytes=item.bytes, updated_at=modified(path), artifact_id=item.id, download_url=item.download_url))
        proposal = self.store.proposal(iv.id)
        run = self.store.latest_execution(iv.id, proposal.id) if proposal else None
        code_directory = ''
        if run and run.data.get('revision'):
            sources = self.jobs.executions.current_files(run)
            root = self.jobs.executions.revision_path(run.id, run.data['revision'])
            code_directory = str(root)
            for name, content in sources.items():
                path = root / name
                files.append(LocalFileOut(name=name, kind='code', local_path=str(path),
                    bytes=len(content.encode('utf-8')), updated_at=modified(path)))
        check = self.store.latest_verification(iv.id)
        if check:
            report = self.jobs.verifications.root(check) / 'report.json'
            if report.is_file() and not report.is_symlink():
                files.append(LocalFileOut(name='report.json', kind='report', local_path=str(report),
                    bytes=report.stat().st_size, updated_at=modified(report)))
        return LocalFilesOut(document_directory=str(self.document_directory(iv.id)),
            code_directory=code_directory, files=sorted(files, key=lambda f: (f.updated_at, f.name), reverse=True))

    def code_zip(self, execution_id):
        run = self.store.execution(execution_id)
        self.store.get_interview(run.interview_id)
        if not run.data.get('revision'):
            raise Problem('CODE_NOT_READY', '尚未保存代码版本。', 409)
        files = self.jobs.executions.current_files(run)
        if not files or manifest(files) != run.data.get('manifest'):
            raise Problem('CODE_BASE_CHANGED', '代码与已保存版本不符，未打包。', 409)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                relative_path(name)
                validate_text(content)
                archive.writestr(name, content)
        return output.getvalue(), run.data['revision']
