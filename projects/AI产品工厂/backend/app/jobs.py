"""Single local worker, durable jobs and confirmations, explicit recovery after restart."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text, update

from .codex import JOB_CONTEXT
from .core import LOCAL_OWNER, Problem
from .store import Confirmation, Execution, Job, Verification

ACTIVE = ('queued', 'running', 'cancelling')


class Jobs:
    def __init__(self, store, workflow, on_call=None):
        self.store, self.workflow = store, workflow
        self.on_call = on_call
        from .planning import Planning
        self.planning = Planning(workflow)
        from .execution import Executions
        self.executions = Executions(self.planning)
        from .verification import Verifications
        self.verifications = Verifications(self.executions)
        from .synthetic_demo import SyntheticDemo
        self.synthetic_demo = SyntheticDemo(workflow)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='factory')
        self.stopping = threading.Event()

    def recover(self):
        with self.store.Session.begin() as s:
            s.execute(update(Job).where(Job.status.in_(ACTIVE), Job.owner_id == LOCAL_OWNER)
                      .values(status='interrupted', error={'code': 'INTERRUPTED',
                         'message': '服务重启中断任务；已保留会话，请确认后重试。'}))
            for run in s.scalars(select(Execution).where(Execution.owner_id == LOCAL_OWNER,
                    Execution.status.in_(('pending','running')))):
                from copy import deepcopy
                data = deepcopy(run.data)
                for step in data['steps']:
                    if step['status'] == 'running':
                        step['status'] = 'interrupted'
                data['error'] = {'code':'INTERRUPTED','message':'服务重启，已保存代码和进度；请点击继续执行。'}
                run.data, run.status = data, 'interrupted'
            s.execute(update(Verification).where(Verification.owner_id==LOCAL_OWNER,
                Verification.status.in_(('pending','running'))).values(status='interrupted'))

    def close(self):
        self.stopping.set()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.verifications.close()

    def busy(self, interview_id):
        if any(j.status in ACTIVE for j in self.store.jobs(interview_id)):
            raise Problem('TASK_BUSY', '此访谈已有任务运行，请等待或取消。', 409)

    def submit(self, interview_id, action, payload=None):
        payload = payload or {}
        iv = self.store.get_interview(interview_id)
        expected = {'message': 'collecting', 'draft': 'collecting', 'revise': 'draft_pending',
                    'confirm': 'draft_pending', 'finalize': 'auditing',
                    'technical': 'closed', 'technical_revise': 'closed', 'technical_confirm': 'closed', 'execute':'closed',
                    'verify':'closed','check_fix':'closed','synthetic_demo':'closed'}
        if action not in expected or iv.status != expected[action]:
            raise Problem('INVALID_STATE', '当前访谈状态不能执行此操作。', 409)
        if action == 'confirm':
            draft = self.store.latest_draft(interview_id)
            if not payload.get('accepted') or not draft or payload.get('draft_version') != draft.version:
                raise Problem('CONFIRM_REQUIRED', '请确认当前版本的 PRD 草稿。', 409)
        if action == 'finalize':
            if not payload.get('accepted'):
                raise Problem('CONFIRM_REQUIRED', '请明确确认写回目标产品目录。', 409)
            if any(not it.closed for it in self.store.checklist(interview_id)):
                raise Problem('INVALID_STATE', '清单尚未全部回答。', 409)
        if action in ('technical', 'technical_revise', 'technical_confirm'):
            self.planning.source(interview_id)
        if action == 'technical_revise' and not self.store.proposal(interview_id):
            raise Problem('PROPOSAL_REQUIRED', '请先生成技术方案。', 409)
        if action == 'technical_confirm':
            self.planning.check_confirmation(interview_id, payload)
        if action == 'synthetic_demo':
            if not payload.get('accepted'):
                raise Problem('CONFIRM_REQUIRED', '请明确确认只发送固定虚构测试问题。', 409)
            self.synthetic_demo.context(interview_id)
        execution, new_execution = None, False
        if action == 'execute':
            execution, new_execution = self.executions.prepare(interview_id, payload)
            payload = dict(payload, execution_id=execution.id)
        verification=None
        if action=='verify':
            verification=self.verifications.prepare(interview_id,payload)
            payload=dict(payload,verification_id=verification.id)
        if action=='check_fix':
            record=self.store.verification(payload['verification_id'])
            if not payload.get('accepted') or record.interview_id!=interview_id or record.status!='failed':
                raise Problem('REPAIR_NOT_REQUIRED','请确认修复当前产品失败的检查。',409)
            self.verifications.assert_execution(record.execution_id,record.revision,record.manifest_digest)
        job = Job(id=str(uuid.uuid4()), interview_id=interview_id, action=action, payload=payload)
        with self.store.Session.begin() as s:
            s.execute(text('BEGIN IMMEDIATE'))
            if s.scalar(select(Job.id).where(Job.interview_id == interview_id, Job.status.in_(ACTIVE))):
                raise Problem('TASK_BUSY', '此访谈已有任务运行。', 409)
            # Recheck after acquiring the write lock.
            from .store import Interview
            if s.get(Interview, interview_id).status != expected[action]:
                raise Problem('INVALID_STATE', '访谈状态已更新，请刷新后重试。', 409)
            s.add(job)
            if new_execution:
                s.add(execution)
            if verification:s.add(verification)
            if action in ('confirm', 'finalize', 'technical_confirm', 'execute','verify','check_fix','synthetic_demo'):
                s.add(Confirmation(interview_id=interview_id, kind=action,
                                   data={'job_id': job.id, **payload}))
        self.store.event(job.id, 'queued', {})
        self.pool.submit(self.run, job.id)
        return job

    def run(self, job_id):
        job = self.store.get_job(job_id)
        if job.status != 'queued' or self.stopping.is_set():
            return
        with self.store.Session.begin() as s:
            changed = s.execute(update(Job).where(Job.id == job_id, Job.status == 'queued')
                                .values(status='running')).rowcount
        if not changed:
            return
        def cancelled():
            return self.stopping.is_set() or self.store.get_job(job_id).status in ('cancelled', 'cancelling')
        token = JOB_CONTEXT.set({'job_id': job_id, 'interview_id': job.interview_id,
                                 'cancelled': cancelled, 'on_call': self.on_call})
        try:
            if job.action == 'message':
                msg, _ = self.workflow.handle_message(job.interview_id, job.payload['content'])
                result = {'message_id': msg.id}
            elif job.action in ('draft', 'revise'):
                d = self.workflow.make_draft(job.interview_id, job.payload.get('content', ''))
                result = {'version': d.version}
            elif job.action == 'confirm':
                items = self.workflow.confirm_draft(job.interview_id)
                result = {'item_ids': [it.id for it in items]}
            elif job.action in ('technical', 'technical_revise'):
                result = self.planning.generate(job.interview_id, job.payload.get('content', ''))
            elif job.action == 'technical_confirm':
                result = self.planning.confirm_and_plan(job.interview_id, job.payload)
            elif job.action == 'execute':
                result = self.executions.execute(job.payload['execution_id'], job.payload.get('feedback', ''))
            elif job.action == 'verify':
                result = self.verifications.run(job.payload['verification_id'])
            elif job.action == 'check_fix':
                result = self.verifications.repair(job.payload['verification_id'],job.payload.get('feedback',''))
            elif job.action == 'synthetic_demo':
                result = self.synthetic_demo.run(job.interview_id)
            else:
                paths, iv = self.workflow.finalize(job.interview_id)
                result = {'written_paths': paths, 'status': iv.status}
            self.store.set_job(job_id, status='succeeded', result=result)
            self.store.event(job_id, 'done', result)
        except Exception as exc:
            err = {'code': exc.code, 'message': exc.message} if isinstance(exc, Problem) else {
                'code': 'INTERNAL', 'message': '任务执行失败，记录已保留，请重试。'}
            # Error details or uploaded user content never enter application logs.
            status = 'cancelled' if err['code'] == 'CANCELLED' else 'failed'
            self.store.set_job(job_id, status=status, error=err)
            self.store.event(job_id, 'error', {'error': err})
        finally:
            JOB_CONTEXT.reset(token)

    def cancel(self, job_id):
        job = self.store.get_job(job_id)
        if job.status not in ACTIVE:
            raise Problem('INVALID_STATE', '任务已结束。', 409)
        # File export is deterministic and short; don't interrupt a partial commit.
        if job.action == 'finalize' and job.status == 'running':
            raise Problem('INVALID_STATE', '写回任务已开始，请等待结果。', 409)
        self.store.set_job(job_id, status='cancelling' if job.status == 'running' else 'cancelled',
                           error={'code': 'CANCELLED', 'message': '用户取消任务。'})
        if job.action == 'execute' and job.status == 'queued':
            run = self.store.execution(job.payload['execution_id'])
            if run.status != 'generated':
                self.store.save_execution(run.id, 'cancelled', dict(run.data,
                    error={'code':'CANCELLED','message':'用户停止了排队中的开发，进度保留。'}))
        if job.action=='verify' and job.status=='queued':
            record=self.store.verification(job.payload['verification_id'])
            self.store.save_verification(record.id,'cancelled',record.data)
        return self.store.get_job(job_id)

    def retry(self, job_id):
        job = self.store.get_job(job_id)
        if job.status not in ('failed', 'interrupted', 'cancelled'):
            raise Problem('INVALID_STATE', '此任务不能重试。', 409)
        return self.submit(job.interview_id, job.action, job.payload)
