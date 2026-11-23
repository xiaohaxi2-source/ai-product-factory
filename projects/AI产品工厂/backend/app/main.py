"""Local API. Model tasks are persisted, polled and streamed independently of HTTP requests."""
import asyncio
import json
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .codex import CodexGateway
from .core import KIT_ROOT, STAGE, Problem, Settings, get_settings, setup_logging
from .documents import MAX_BYTES, parse_upload
from .jobs import Jobs
from .model import ModelGateway
from .schemas import (ChecklistItemOut, ConfirmRequest, DraftOut, ExportRequest,
    HealthOut, InterviewCreate, InterviewDetail, InterviewOut, JobOut, MessageCreate, MessageOut, ProductOut,
    TechnicalConfirm, WorkbenchOut, DecisionAnswersRequest, DecisionReviewOut,
    ExecutionRequest, ExecutionOut, ExecutionDiffOut, VerificationRequest, VerificationOut,
    CheckRepairRequest, AcceptanceRequest, PreviewLoginOut, AdoptionPlanOut, AdoptionRequest,
    SyntheticDemoOut, SyntheticDemoRequest, WorkspaceProjectOut, ArtifactOut, ArtifactContentOut, LocalFilesOut)
from .store import Asset, Store, utcnow
from .workflow import Workflow


def create_app(settings: Settings | None = None, projects_dir: Path | None = None,
               gateway_factory=None, on_call=None) -> FastAPI:
    setup_logging()
    settings = settings or get_settings()
    projects_dir = Path(projects_dir or settings.projects_dir or KIT_ROOT / 'projects').resolve()
    store = Store(settings)
    store.migrate()
    factory = gateway_factory or (CodexGateway if settings.model_backend == 'codex' else ModelGateway)
    workflow = Workflow(factory(settings, store), store, projects_dir)
    jobs = Jobs(store, workflow, on_call)
    from .deliverables import Deliverables
    deliverables = Deliverables(jobs)

    @asynccontextmanager
    async def lifespan(app):
        # One process per database, so a second instance cannot interrupt active jobs.
        import fcntl
        lock = (Path(settings.data_dir) / 'server.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('此数据目录已有服务运行，请勿重复启动。')
        jobs.recover()
        try:
            yield
        finally:
            await asyncio.to_thread(jobs.close)
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
            store.engine.dispose()

    app = FastAPI(title='AI产品工厂', docs_url='/api/docs', redoc_url=None, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.workflow, app.state.jobs = settings, store, workflow, jobs
    app.state.planning = jobs.planning

    def error(code, message, status):
        return JSONResponse(status_code=status, content={'error': {'code': code, 'message': message}})

    @app.middleware('http')
    async def local_boundary(request, call_next):
        host = request.url.hostname
        if host not in ('127.0.0.1', 'localhost', '::1', 'testserver'):
            return error('HOST_DENIED', '只允许本机访问。', 403)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and origin != f'{request.url.scheme}://{request.url.netloc}':
                return error('ORIGIN_DENIED', '请求来源不匹配。', 403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return error('ORIGIN_DENIED', '拒绝跨站请求。', 403)
        response = await call_next(request)
        if request.url.path == '/' or request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(Problem)
    async def problem_handler(_, exc):
        return error(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_, exc):
        return error('VALIDATION', '输入格式不正确，请检查产品名、内容和确认版本。', 422)

    @app.exception_handler(HTTPException)
    async def http_handler(_, exc):
        return error('HTTP_ERROR', '请求路径或方法不正确。', exc.status_code)

    @app.exception_handler(Exception)
    async def unknown_handler(_, exc):
        return error('INTERNAL', '服务内部错误，记录已保留。', 500)

    def iv_out(iv):
        return InterviewOut(id=iv.id, product_name=iv.product_name, status=iv.status,
                            idea_text=iv.idea_text, updated_at=iv.updated_at)

    def msg_out(m):
        return MessageOut(id=m.id, role=m.role, content=m.content,
                          questions=m.questions or [], enough=m.enough, created_at=m.created_at)

    def item_out(it):
        return ChecklistItemOut(id=it.id, kind=it.kind, question=it.question, why=it.why,
            options=it.options or [], recommendation=it.recommendation,
            consequence=it.consequence, answer=it.answer, closed=it.closed)

    def job_out(j):
        return JobOut(id=j.id, interview_id=j.interview_id, action=j.action, status=j.status,
                      user_content=(j.payload or {}).get('content') or (j.payload or {}).get('feedback') or '',
                      result=j.result, error=j.error, created_at=j.created_at, updated_at=j.updated_at)

    @app.get('/api/v1/health', response_model=HealthOut)
    def health():
        return HealthOut(status='ok', stage=STAGE, backend=settings.model_backend,
            model=settings.model_name or '账号默认模型', model_configured=bool(
                shutil.which(settings.codex_bin) if settings.model_backend == 'codex' else settings.model_api_key))

    @app.get('/api/v1/products', response_model=list[ProductOut])
    def products():
        return [ProductOut(name=n) for n in workflow.list_products()]

    @app.get('/api/v1/workspace', response_model=list[WorkspaceProjectOut])
    def workspace_overview():
        return deliverables.overview()

    @app.get('/api/v1/interviews/{interview_id}/artifacts', response_model=list[ArtifactOut])
    def artifact_list(interview_id: int):
        return deliverables.artifacts(interview_id)

    @app.get('/api/v1/interviews/{interview_id}/files', response_model=LocalFilesOut)
    def local_files(interview_id: int):
        return deliverables.local_files(interview_id)

    @app.get('/api/v1/interviews/{interview_id}/artifacts/{artifact_id}', response_model=ArtifactContentOut)
    def artifact_preview(interview_id: int, artifact_id: str):
        return deliverables.artifact(interview_id, artifact_id)

    @app.get('/api/v1/interviews/{interview_id}/artifacts/{artifact_id}/download')
    def artifact_download(interview_id: int, artifact_id: str):
        item = deliverables.artifact(interview_id, artifact_id)
        return Response(item.content, media_type='text/markdown', headers={
            'Content-Disposition': f'attachment; filename="{artifact_id}.md"', 'Cache-Control': 'no-store'})

    @app.get('/api/v1/executions/{execution_id}/download')
    def execution_download(execution_id: str):
        content, revision = deliverables.code_zip(execution_id)
        return Response(content, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="source-{revision[:12]}.zip"',
            'Cache-Control': 'no-store'})

    @app.post('/api/v1/interviews', response_model=InterviewOut, status_code=201)
    async def create_interview(product_name: str = Form(...), idea: str = Form(''),
                               prd_file: UploadFile | None = File(None)):
        try:
            body = InterviewCreate(product_name=product_name, idea=idea)
        except ValueError:
            raise Problem('VALIDATION', '产品名或想法格式不正确。', 422)
        prd_text, filename, content = '', '', b''
        if prd_file is not None and prd_file.filename:
            content = await prd_file.read(MAX_BYTES + 1)
            filename, prd_text = parse_upload(prd_file.filename, content)
            await prd_file.close()
        iv = workflow.create_interview(body.product_name, body.idea, prd_text)
        if filename:
            path = Path(settings.data_dir) / 'uploads' / str(iv.id) / f'{uuid.uuid4().hex}-{filename}'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            os.chmod(path, 0o600)
            store.add(Asset(interview_id=iv.id, kind='upload', data={
                'filename': filename, 'path': str(path), 'size': len(content),
                'uploaded_at': utcnow(), 'status': 'parsed'}))
        return iv_out(iv)

    @app.get('/api/v1/interviews', response_model=list[InterviewOut])
    def list_interviews():
        return [iv_out(iv) for iv in store.list_interviews()]

    @app.get('/api/v1/interviews/{interview_id}', response_model=InterviewDetail)
    def detail(interview_id: int):
        iv = store.get_interview(interview_id)
        d, meta = store.latest_draft(iv.id), store.asset(iv.id, 'draft_assumptions')
        plan = store.asset(iv.id, 'export_plan')
        return InterviewDetail(interview=iv_out(iv), messages=[msg_out(m) for m in store.messages(iv.id)],
            draft=DraftOut(content=d.content, version=d.version, assumptions=meta.data['items'] if meta else []) if d else None,
            checklist=[item_out(it) for it in store.checklist(iv.id)], jobs=[job_out(j) for j in store.jobs(iv.id)],
            written_paths=list(plan.data['files']) if plan and iv.status == 'closed' else [],
            pending_review=workflow.pending_review(iv.id))

    @app.post('/api/v1/interviews/{interview_id}/decision-answers', response_model=DecisionReviewOut)
    def decision_answers(interview_id: int, body: DecisionAnswersRequest):
        store.get_interview(interview_id)
        jobs.busy(interview_id)
        return workflow.save_decision_answers(interview_id, body)

    @app.post('/api/v1/interviews/{interview_id}/messages', response_model=JobOut, status_code=202)
    def message(interview_id: int, body: MessageCreate):
        if not body.content.strip():
            raise Problem('VALIDATION', '消息不能为空。', 422)
        return job_out(jobs.submit(interview_id, 'message', body.model_dump()))

    @app.post('/api/v1/interviews/{interview_id}/draft', response_model=JobOut, status_code=202)
    def draft(interview_id: int):
        return job_out(jobs.submit(interview_id, 'draft'))

    @app.post('/api/v1/interviews/{interview_id}/confirm', response_model=JobOut, status_code=202)
    def confirm(interview_id: int, body: ConfirmRequest):
        return job_out(jobs.submit(interview_id, 'confirm', body.model_dump()))

    @app.post('/api/v1/interviews/{interview_id}/revise', response_model=JobOut, status_code=202)
    def revise(interview_id: int, body: MessageCreate):
        if not body.content.strip():
            raise Problem('VALIDATION', '请填写修改意见。', 422)
        return job_out(jobs.submit(interview_id, 'revise', body.model_dump()))

    @app.post('/api/v1/checklist-items/{item_id}/answer', response_model=ChecklistItemOut)
    def answer(item_id: int, body: MessageCreate):
        item = store.get_item(item_id)
        jobs.busy(item.interview_id)
        return item_out(workflow.answer_item(item_id, body.content))

    @app.post('/api/v1/interviews/{interview_id}/finalize', response_model=JobOut, status_code=202)
    def finalize(interview_id: int, body: ExportRequest):
        return job_out(jobs.submit(interview_id, 'finalize', body.model_dump()))

    @app.get('/api/v1/interviews/{interview_id}/workbench', response_model=WorkbenchOut)
    def workbench(interview_id: int):
        return jobs.planning.workbench(interview_id)

    @app.get('/api/v1/interviews/{interview_id}/execution', response_model=ExecutionOut | None)
    def current_execution(interview_id: int):
        store.get_interview(interview_id)
        proposal = store.proposal(interview_id)
        run = store.latest_execution(interview_id, proposal.id) if proposal else None
        return jobs.executions.output(run) if run else None

    @app.post('/api/v1/interviews/{interview_id}/execute', response_model=JobOut, status_code=202)
    def execute(interview_id: int, body: ExecutionRequest):
        return job_out(jobs.submit(interview_id, 'execute', body.model_dump()))

    @app.get('/api/v1/executions/{execution_id}', response_model=ExecutionOut)
    def execution(execution_id: str):
        return jobs.executions.output(store.execution(execution_id))

    @app.get('/api/v1/interviews/{interview_id}/verification', response_model=VerificationOut | None)
    def current_verification(interview_id:int):
        store.get_interview(interview_id)
        row=store.latest_verification(interview_id)
        return jobs.verifications.output(row) if row else None

    @app.get('/synthetic-demo')
    def synthetic_demo_page():
        return FileResponse(Path(__file__).resolve().parents[1]/'static/synthetic-demo.html',
            headers={'Cache-Control': 'no-store', 'Content-Security-Policy':
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})

    @app.get('/api/v1/interviews/{interview_id}/synthetic-demo', response_model=SyntheticDemoOut)
    def synthetic_demo_status(interview_id: int):
        return jobs.synthetic_demo.status(interview_id)

    @app.post('/api/v1/interviews/{interview_id}/synthetic-demo', response_model=JobOut, status_code=202)
    def synthetic_demo_run(interview_id: int, body: SyntheticDemoRequest):
        return job_out(jobs.submit(interview_id, 'synthetic_demo', body.model_dump()))

    @app.post('/api/v1/interviews/{interview_id}/verify',response_model=JobOut,status_code=202)
    def verify(interview_id:int,body:VerificationRequest):
        return job_out(jobs.submit(interview_id,'verify',body.model_dump()))

    @app.post('/api/v1/interviews/{interview_id}/check-fix',response_model=JobOut,status_code=202)
    def check_fix(interview_id:int,body:CheckRepairRequest):
        return job_out(jobs.submit(interview_id,'check_fix',body.model_dump()))

    @app.get('/api/v1/verifications/{verification_id}',response_model=VerificationOut)
    def verification(verification_id:str):
        return jobs.verifications.output(store.verification(verification_id))

    @app.post('/api/v1/verifications/{verification_id}/accept',response_model=VerificationOut)
    def accept_verification(verification_id:str,body:AcceptanceRequest):
        record=store.verification(verification_id);jobs.busy(record.interview_id)
        return jobs.verifications.accept(verification_id,body)

    @app.post('/api/v1/verifications/{verification_id}/stop-preview',response_model=VerificationOut)
    def stop_preview(verification_id:str):
        record=store.verification(verification_id)
        jobs.verifications.stop_preview(record.id)
        return jobs.verifications.output(record)

    @app.post('/api/v1/verifications/{verification_id}/preview-login',response_model=PreviewLoginOut)
    def preview_login(verification_id:str):
        record=store.verification(verification_id);jobs.busy(record.interview_id)
        url,cookies=jobs.verifications.preview_login(record.id)
        response=JSONResponse({'url':url})
        for cookie in cookies:response.headers.append('set-cookie',cookie)
        return response

    @app.get('/api/v1/verifications/{verification_id}/adoption',response_model=AdoptionPlanOut)
    def adoption_plan(verification_id:str):
        return jobs.verifications.adoption_plan(verification_id)

    @app.post('/api/v1/verifications/{verification_id}/adopt',response_model=AdoptionPlanOut)
    def adopt_code(verification_id:str,body:AdoptionRequest):
        record=store.verification(verification_id);jobs.busy(record.interview_id)
        return jobs.verifications.adopt(verification_id,body)

    @app.get('/api/v1/executions/{execution_id}/diff', response_model=ExecutionDiffOut)
    def execution_diff(execution_id: str, task_id: str = Query(pattern=r'^T[1-9]\d*$'),
                       path: str = Query(min_length=1, max_length=240)):
        return jobs.executions.diff(execution_id, task_id, path)

    @app.post('/api/v1/interviews/{interview_id}/technical', response_model=JobOut, status_code=202)
    def technical(interview_id: int):
        return job_out(jobs.submit(interview_id, 'technical'))

    @app.post('/api/v1/interviews/{interview_id}/technical/revise', response_model=JobOut, status_code=202)
    def revise_technical(interview_id: int, body: MessageCreate):
        if not body.content.strip():
            raise Problem('VALIDATION', '请填写技术方案的修改意见。', 422)
        return job_out(jobs.submit(interview_id, 'technical_revise', body.model_dump()))

    @app.post('/api/v1/interviews/{interview_id}/technical/confirm', response_model=JobOut, status_code=202)
    def confirm_technical(interview_id: int, body: TechnicalConfirm):
        return job_out(jobs.submit(interview_id, 'technical_confirm', body.model_dump()))

    @app.get('/api/v1/jobs/{job_id}', response_model=JobOut)
    def job(job_id: str):
        return job_out(store.get_job(job_id))

    @app.post('/api/v1/jobs/{job_id}/cancel', response_model=JobOut)
    def cancel(job_id: str):
        return job_out(jobs.cancel(job_id))

    @app.post('/api/v1/jobs/{job_id}/retry', response_model=JobOut, status_code=202)
    def retry(job_id: str):
        return job_out(jobs.retry(job_id))

    @app.get('/api/v1/jobs/{job_id}/events')
    async def events(job_id: str, after: int = 0):
        store.get_job(job_id)
        async def stream():
            cursor = max(after, 0)
            while True:
                j = store.get_job(job_id)
                rows = store.events(job_id, cursor)
                for e in rows:
                    cursor = e.id
                    if e.kind not in ('done', 'error'):
                        yield f'id: {e.id}\nevent: chunk\ndata: {json.dumps(e.data, ensure_ascii=False)}\n\n'
                if j.status in ('succeeded', 'failed', 'interrupted', 'cancelled'):
                    kind = 'done' if j.status == 'succeeded' else 'error'
                    data = j.result if kind == 'done' else {'error': j.error}
                    yield f'event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
                    return
                yield ': heartbeat\n\n'
                await asyncio.sleep(.4)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})

    static_dir = Path(__file__).resolve().parents[1] / 'static'
    app.mount('/static', StaticFiles(directory=static_dir), name='static')

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(static_dir / 'index.html')

    return app
