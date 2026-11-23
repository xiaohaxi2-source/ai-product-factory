"""Bounded code generation, immutable workspace revisions and deterministic syntax checks.

No model-produced command is ever executed. Original product files are read-only inputs.
"""
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tomllib
import time
import uuid
from pathlib import Path, PurePosixPath

from .codex import JOB_CONTEXT
from .core import Problem
from .planning import digest
from .schemas import CodeBundle, ExecutionDiffOut, ExecutionOut
from .store import Execution

ROOTS = {'backend', 'frontend', 'scripts', 'tests', 'docs'}
ROOT_FILES = {'README.md', 'requirements.txt', 'pyproject.toml', 'package.json',
              'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'uv.lock', '.gitignore', '.env.example'}
IGNORED = {'node_modules', '__pycache__', 'data', 'uploads', 'dist', 'build', 'coverage', 'venv'}
EXTENSIONS = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.html', '.css',
              '.json', '.toml', '.md', '.txt', '.yaml', '.yml', '.sql', '.sh', '.command', '.lock', '.ini', '.cfg'}
MAX_FILES, MAX_BYTES = 200, 512 * 1024
SECRET = re.compile(r'(?:sk-[A-Za-z0-9_-]{20,}|AKID[A-Za-z0-9]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def relative_path(value):
    path = PurePosixPath(value)
    if (not value or '\\' in value or path.is_absolute() or str(path) != value or
            any(p in ('.', '..') or not re.fullmatch(r'[\w.\-]+', p) for p in path.parts)):
        raise Problem('CODE_PATH_INVALID', '代码文件路径不安全，未写入任何改动。', 422)
    if len(path.parts) == 1:
        if value not in ROOT_FILES:
            raise Problem('CODE_PATH_DENIED', '只能修改任务代码目录与允许的项目配置文件。', 422)
    elif path.parts[0] not in ROOTS:
        raise Problem('CODE_PATH_DENIED', '不能修改产品规则、业务数据或其他产品。', 422)
    if path.parts[0] == 'docs' and not document_path(path):
        raise Problem('CODE_PATH_DENIED', '只能编写开发文档和操作清单，不能编造冒烟、检查、验收记录或改管家状态。', 422)
    if any((p.startswith('.') and p not in ('.gitignore', '.env.example')) or p.lower() in IGNORED
           or p.lower() in ('agents.md', 'claude.md') for p in path.parts):
        raise Problem('CODE_PATH_DENIED', '拒绝读取或写入密钥、数据、缓存及规则文件。', 422)
    if path.name not in ('.gitignore', '.env.example') and path.suffix not in EXTENSIONS:
        raise Problem('CODE_TYPE_DENIED', '当前开发执行只支持文本源码和项目配置。', 422)
    return path


def document_path(path):
    parts = path.parts
    return (len(parts) == 3 and parts[1] in ('阶段文档', '开发记录') and path.suffix == '.md' or
            len(parts) == 4 and parts[1] == 'evidence' and re.fullmatch(r'阶段[23]-\d+', parts[2])
            and parts[3] == '照着做清单.md')


def validate_text(text):
    if '\x00' in text or SECRET.search(text):
        raise Problem('CODE_SECRET_DENIED', '文件含密钥样式内容或非文本数据，已停止处理。', 422)


def manifest(files):
    return {path: sha(text) for path, text in sorted(files.items())}


class Executions:
    def __init__(self, planning):
        self.planning = planning
        self.store, self.workflow, self.gateway = planning.store, planning.workflow, planning.gateway
        self.base = Path(self.store.settings.data_dir).resolve() / 'executions'

    def prepare(self, interview_id, payload):
        proposal, source = self.planning.check_confirmation(interview_id, payload)
        if not self.store.technical_approved(proposal):
            raise Problem('PLAN_REQUIRED', '请先确认技术方案并生成开发任务。', 409)
        plan = self.store.development_plan(proposal.id)
        if not plan or digest(plan.data) != payload.get('plan_digest'):
            raise Problem('PLAN_STALE', '开发任务已变化，请刷新后确认当前任务清单。', 409)
        existing = self.store.execution(payload['execution_id']) if payload.get('execution_id') else self.store.latest_execution(interview_id, proposal.id)
        if existing:
            if existing.interview_id != interview_id or existing.proposal_id != proposal.id or existing.plan_digest != digest(plan.data):
                raise Problem('EXECUTION_STALE', '开发记录不属于当前已确认方案。', 409)
            return existing, False
        return Execution(id=str(uuid.uuid4()), interview_id=interview_id, proposal_id=proposal.id,
            plan_digest=digest(plan.data), status='pending', data={
                'version': proposal.version, 'steps': [{'task_id':t['id'], 'title':t['title'], 'status':'pending'}
                    for t in plan.data['tasks']], 'revision':None, 'manifest':{}}), True

    def revision_path(self, execution_id, revision):
        if not re.fullmatch(r'[a-f0-9-]{36}', execution_id) or not re.fullmatch(r'[a-f0-9]{32}', revision or ''):
            raise Problem('WORKSPACE_INVALID', '隔离目录记录无效。', 409)
        path = self.base / execution_id / revision
        for p in [path, *path.parents]:
            if p.is_symlink():
                raise Problem('WORKSPACE_INVALID', '隔离目录含符号链接，已停止处理。', 409)
        return path

    def read_files(self, root):
        files = {}
        if root.is_symlink():
            raise Problem('CODE_PATH_INVALID', '源码目录不能是符号链接。', 422)
        if not root.exists():
            return files
        for directory, dirs, names in os.walk(root, followlinks=False):
            parent = Path(directory)
            dirs[:] = [d for d in dirs if not d.startswith('.') and d.lower() not in IGNORED
                       and (parent != root or d in ROOTS)]
            parts = parent.relative_to(root).parts
            if parts == ('docs',):
                dirs[:] = [d for d in dirs if d in ('阶段文档','开发记录','evidence')]
            elif parts and parts[0] == 'docs' and (len(parts) >= 3 or parts[1] != 'evidence'):
                dirs[:] = []
            if any((parent / d).is_symlink() for d in dirs):
                raise Problem('CODE_PATH_INVALID', '源码目录含符号链接，未复制。', 422)
            for name in names:
                if parent == root and name not in ROOT_FILES:
                    continue
                if name.startswith('.') and name not in ('.env.example', '.gitignore'):
                    continue
                path = parent / name
                rel = path.relative_to(root).as_posix()
                if parts and parts[0] == 'docs' and not document_path(PurePosixPath(rel)):
                    continue
                relative_path(rel)
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 100000:
                    raise Problem('CODE_SOURCE_INVALID', '源码包含链接、特殊文件或超大文件，未复制。', 422)
                try:
                    text = path.read_text(encoding='utf-8')
                except (OSError, UnicodeError):
                    raise Problem('CODE_SOURCE_INVALID', '当前执行只支持可读取的UTF-8源码。', 422)
                validate_text(text)
                files[rel] = text
        self.check_size(files)
        return files

    @staticmethod
    def check_size(files):
        if len(files) > MAX_FILES or sum(len(s.encode('utf-8')) for s in files.values()) > MAX_BYTES:
            raise Problem('CODE_CONTEXT_LIMIT', '当前任务源码超过200文件或512KB，请先缩小任务范围。', 422)
        if len({p.casefold() for p in files}) != len(files):
            raise Problem('CODE_PATH_INVALID', '文件名存在大小写冲突，已停止处理。', 422)

    def write_revision(self, execution_id, files):
        revision = uuid.uuid4().hex
        folder = self.revision_path(execution_id, revision)
        folder.mkdir(parents=True, exist_ok=False)
        for path, content in files.items():
            relative_path(path)
            dest = folder / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('x', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        return revision, folder

    def current_files(self, execution):
        root = self.revision_path(execution.id, execution.data['revision'])
        if not root.is_dir():
            raise Problem('WORKSPACE_MISSING', '隔离代码目录缺失，请先恢复目录；不会静默重新生成。', 409)
        files = self.read_files(root)
        if manifest(files) != execution.data['manifest']:
            raise Problem('WORKSPACE_CHANGED', '隔离代码被外部修改，已停止以免覆盖，请保留并核对改动。', 409)
        return files

    def checkpoint(self, run, status):
        self.store.save_execution(run.id, status, run.data)
        run.status = status

    @staticmethod
    def syntax_checks(folder, files, changes):
        checks = []
        for change in changes:
            suffix = Path(change.path).suffix
            status, kind, message = 'not_run', '功能检查', '未执行功能测试，留待检查验收阶段。'
            try:
                if suffix == '.py':
                    ast.parse(files[change.path], filename=change.path)
                    status, kind, message = 'passed', 'Python语法', '语法解析通过；未执行生成代码。'
                elif suffix == '.json':
                    json.loads(files[change.path])
                    status, kind, message = 'passed', 'JSON格式', 'JSON解析通过。'
                elif suffix == '.toml':
                    tomllib.loads(files[change.path])
                    status, kind, message = 'passed', 'TOML格式', 'TOML解析通过。'
                elif suffix in ('.js', '.mjs', '.cjs') and shutil.which('node'):
                    result = subprocess.run([shutil.which('node'), '--check', str(folder / change.path)],
                        cwd=folder, env={'PATH':os.environ.get('PATH','')}, capture_output=True, timeout=10)
                    status, kind, message = ('passed' if result.returncode == 0 else 'failed'), 'JavaScript语法', '语法检查通过。' if result.returncode == 0 else '语法检查失败，请查看代码变更。'
            except (SyntaxError, ValueError, TypeError, subprocess.SubprocessError, OSError):
                status, kind, message = 'failed', '语法/格式', '语法或格式检查失败，未采用此批改动。'
            checks.append({'path':change.path, 'kind':kind, 'status':status, 'message':message})
        return checks

    def execute(self, execution_id, feedback=''):
        run = self.store.execution(execution_id)
        proposal = self.store.proposal(run.interview_id)
        source = self.planning.assert_current(run.interview_id, proposal.source_digest, run.proposal_id)
        plan = self.store.development_plan(run.proposal_id)
        if not plan or digest(plan.data) != run.plan_digest:
            raise Problem('PLAN_STALE', '开发任务已变化，不能继续旧执行。', 409)
        if run.status == 'generated':
            self.current_files(run)
            return {'execution_id':run.id, 'status':run.status}
        ctx = JOB_CONTEXT.get()
        batch_attempts = 0
        def event(phase, **data):
            if ctx:
                self.store.event(ctx['job_id'], 'progress', {'phase':phase, 'execution_id':run.id, **data})
        def guard():
            if ctx and ctx['cancelled']():
                raise Problem('CANCELLED', '开发已停止，已完成的隔离代码与记录保留。', 409)
            self.planning.assert_current(run.interview_id, proposal.source_digest, run.proposal_id)
        try:
            guard()
            if not run.data['revision']:
                files = self.read_files(source['root'])
                revision, _ = self.write_revision(run.id, files)
                run.data.update(revision=revision, manifest=manifest(files))
            self.checkpoint(run, 'running')
            completed = {step['task_id'] for step in run.data['steps'] if step['status'] == 'generated'}
            for index, task in enumerate(plan.data['tasks']):
                if task['id'] in completed:
                    continue
                guard()
                if not set(task['depends_on']) <= completed:
                    raise Problem('DEPENDENCY_PENDING', '前置任务尚未完成，不能跳过执行。', 409)
                step = run.data['steps'][index]
                while step['status'] != 'generated':
                    guard()
                    if batch_attempts >= self.store.settings.max_calls:
                        raise Problem('CALL_LIMIT', '本次任务已到调用次数上限，代码与进度保留；核对账号额度后可明确继续。', 429)
                    files = self.current_files(run)
                    step.update(status='running', questions=[], active_batch=step.get('chunks',0)+1,
                                batch_started_at=time.time())
                    run.data.pop('error', None)
                    self.checkpoint(run, 'running')
                    event('正在实现 '+task['id']+' 第'+str(step['active_batch'])+'批 · '+task['title'])
                    if ctx:
                        ctx['execution_cwd'] = str(self.revision_path(run.id, run.data['revision']))
                    # Development needs code and the current contract, not every previous PRD interview/checklist.
                    contract = 'docs/阶段文档/'+task['phase']+'技术开发文档.md'
                    context_files = {path:text for path,text in files.items() if not path.startswith('docs/') or path == contract}
                    if any(str(value).startswith('docs/') for value in task['deliverables']):
                        context_files.update({path:text for path,text in files.items() if path.startswith('docs/阶段文档/') or path.startswith('docs/开发记录/')})
                    batch_attempts += 1
                    bundle, _ = self.gateway.complete('development_code', {
                        'prd':source['prd'], 'decision_ledger':source['decision_ledger'],
                        'technical_proposal':proposal.data, 'current_task':task,
                        'completed_tasks':sorted(completed), 'feedback':feedback,
                        'batch_number':step['active_batch'], 'saved_batches':step.get('chunks',0),
                        'previous_batch_summary':step.get('summary',''),
                        'previous_failed_attempt': {'checks':step.get('checks', []),
                            'files':self.read_files(self.revision_path(run.id, step['candidate_revision']))}
                            if step.get('candidate_revision') else None,
                        'file_inventory':[{'path':path,'sha256':sha(text)} for path,text in sorted(files.items())],
                        'files':[{'path':path,'sha256':sha(content),'content':content} for path,content in sorted(context_files.items())],
                    }, CodeBundle, max_tokens=12000)
                    guard()
                    self.current_files(run)
                    if bundle.questions:
                        step.update(status='blocked', summary=bundle.summary, questions=bundle.questions)
                        raise Problem('EXECUTION_INPUT_REQUIRED', '当前任务需要补充信息，请在执行区回答后继续。', 409)
                    updated, changed = dict(files), []
                    for change in bundle.changes:
                        relative_path(change.path)
                        validate_text(change.content)
                        before = files.get(change.path)
                        expected = sha(before) if before is not None else 'new'
                        if change.before_sha256 != expected:
                            raise Problem('CODE_BASE_CHANGED', '文件基准不一致，未采用改动，请重新执行。', 409)
                        if change.path not in context_files and before is not None:
                            raise Problem('CODE_CONTEXT_MISSING', '模型试图修改未提供正文的文件，已停止以免误覆盖。', 409)
                        updated[change.path] = change.content
                        if before != change.content:
                            changed.append(change)
                    if not changed:
                        raise Problem('CODE_NO_CHANGES', '模型没有产生实际文件改动，未标记任务完成。', 502)
                    self.check_size(updated)
                    revision, folder = self.write_revision(run.id, updated)
                    checks = self.syntax_checks(folder, updated, changed)
                    if any(check['status'] == 'failed' for check in checks):
                        step.update(status='failed', summary='候选代码未通过语法/格式检查，上一版隔离代码保留。', checks=checks,
                                    candidate_revision=revision)
                        raise Problem('CODE_CHECK_FAILED', '代码未通过语法检查，未采用此批改动；可补充修复意见后重试。', 409)
                    guard()
                    baseline = step.get('before_revision', run.data['revision'])
                    before_files = self.read_files(self.revision_path(run.id, baseline))
                    changes = []
                    for path, content in sorted(updated.items()):
                        before = before_files.get(path)
                        if before == content:
                            continue
                        diff = list(difflib.ndiff((before or '').splitlines(), content.splitlines()))
                        changes.append({'path':path,'before_sha256':sha(before) if before is not None else 'new',
                            'after_sha256':sha(content),'added':sum(line.startswith('+ ') for line in diff),
                            'removed':sum(line.startswith('- ') for line in diff)})
                    committed_checks = {c['path']:c for c in step.get('committed_checks',[])}
                    committed_checks.update({c['path']:c for c in checks})
                    step.update(status='generated' if bundle.task_complete else 'running', summary=bundle.summary,
                                changes=changes, checks=list(committed_checks.values()), committed_checks=list(committed_checks.values()),
                                before_revision=baseline, after_revision=revision, chunks=step.get('chunks',0)+1,
                                batch_started_at=None)
                    step.pop('candidate_revision', None)
                    step.setdefault('batches', []).append({'revision':revision, 'paths':[c.path for c in changed]})
                    run.data.update(revision=revision, manifest=manifest(updated))
                    self.checkpoint(run, 'running')
                    event(task['id']+' 第'+str(step['chunks'])+'批代码已保存', files=len(changed))
                completed.add(task['id'])
            run.data.pop('error', None)
            self.checkpoint(run, 'generated')
            return {'execution_id':run.id, 'status':'generated'}
        except Exception as exc:
            err = {'code':exc.code,'message':exc.message} if isinstance(exc, Problem) else {
                'code':'EXECUTION_FAILED','message':'开发执行失败，已保留最后一次有效代码与进度。'}
            for step in run.data['steps']:
                if step['status'] == 'running':
                    step['status'] = 'interrupted' if err['code'] == 'CANCELLED' else 'failed'
            run.data['error'] = err
            status = 'cancelled' if err['code'] == 'CANCELLED' else 'blocked' if err['code'] == 'EXECUTION_INPUT_REQUIRED' else 'failed'
            self.checkpoint(run, status)
            raise

    def output(self, run):
        workspace = str(self.revision_path(run.id, run.data['revision'])) if run.data.get('revision') else None
        return ExecutionOut(id=run.id, interview_id=run.interview_id, proposal_version=run.data['version'],
            status=run.status, workspace=workspace, steps=run.data['steps'], updated_at=run.updated_at,
            error=run.data.get('error'), timeout_per_batch=self.store.settings.development_timeout,
            manifest_digest=digest(run.data['manifest']),revision=run.data.get('revision') or '')

    def diff(self, execution_id, task_id, path):
        run = self.store.execution(execution_id)
        relative_path(path)
        step = next((s for s in run.data['steps'] if s['task_id'] == task_id and s.get('after_revision')), None)
        if not step or not any(change['path'] == path for change in step.get('changes', [])):
            raise Problem('NOT_FOUND', '该任务没有此文件的已保存改动。', 404)
        before = self.read_files(self.revision_path(run.id, step['before_revision'])).get(path, '')
        after = self.read_files(self.revision_path(run.id, step['after_revision'])).get(path, '')
        text = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile='之前/'+path, tofile='之后/'+path))
        return ExecutionDiffOut(path=path, diff=text[:30000], truncated=len(text)>30000)
