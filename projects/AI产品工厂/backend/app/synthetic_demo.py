"""One fixed synthetic request. Never loads interviews, medical sources or user prompts."""
import fcntl
import json
import os
from datetime import datetime
from pathlib import Path
import time
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict

from .core import PRODUCT_ROOT as PRODUCT, Problem
ENDPOINT = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-flash'
FIXED_PROMPT = ('仅用于虚构数据连接测试，不包含任何真实用户信息。'
    '请仅输出这个JSON对象，不要添加字段或解释：'
    '{"status":"ok","sample":"DEMO_PRODUCT"}')


class SyntheticReply(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    status: Literal['ok']
    sample: Literal['DEMO_PRODUCT']


def save(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def run_probe(key, root, client):
    """Exactly one attempt per report directory; ambiguous failures cannot be resent."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = root / 'report.json'
    with (root / 'probe.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if report.exists():
            raise ValueError('此联调已有记录，禁止自动重发；请核对报告。')
        stamp = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
        result = {'status': 'sending', 'scope': 'fixed_synthetic_only', 'time': stamp,
                  'model': MODEL, 'attempts': 1, 'medical_enabled': False}
        save(report, result)
        started = time.monotonic()
        try:
            response = client.post(ENDPOINT, headers={'Authorization': 'Bearer ' + key}, json={
                'model': MODEL, 'messages': [{'role': 'user', 'content': FIXED_PROMPT}],
                'thinking': {'type': 'disabled'}, 'max_tokens': 128,
                'response_format': {'type': 'json_object'}, 'stream': False})
            if response.status_code != 200:
                result.update(status='failed', http_status=response.status_code)
            else:
                body = response.json()
                if body['choices'][0].get('finish_reason') != 'stop':
                    raise ValueError('incomplete response')
                reply = SyntheticReply.model_validate_json(body['choices'][0]['message']['content'])
                usage = body['usage']
                tin, tout = usage['prompt_tokens'], usage['completion_tokens']
                if type(tin) is not int or type(tout) is not int or not 0 <= tin <= 1048576 or not 0 <= tout <= 128:
                    raise ValueError('unverified usage')
                result.update(status='passed', reply=reply.model_dump(), tokens_in=tin, tokens_out=tout,
                    # Peak uncached rates verified 2026-11-23, CNY / million tokens.
                    cost_upper_micro_rmb=tin * 2 + tout * 8,
                    cost_basis='2026-11-23公开高峰未命中价上界；实际扣费以平台账单为准',
                    output_valid=True)
        except Exception:
            # Never print or persist exception/response bodies that could contain credentials.
            result.update(status='failed_or_uncertain', output_valid=False)
        result['duration_ms'] = round((time.monotonic() - started) * 1000)
        save(report, result)
        return result


class SyntheticDemo:
    """Host-owned transport; generated apps never receive the API key or network access."""
    def __init__(self, workflow):
        self.workflow = workflow
        self.store = workflow.store

    def context(self, interview_id):
        iv = self.store.get_interview(interview_id)
        if iv.product_name != '医美小助手':
            raise Problem('DEMO_SCOPE', '当前只为医美小助手开放已授权的固定虚构数据联调。', 409)
        target = self.workflow._safe_target(iv.product_name)
        secret = target/'backend/.env'
        if any(p.is_symlink() for p in (secret, *secret.parents)):
            raise Problem('CONFIG_UNSAFE', '本地配置路径不安全，未调用模型。', 409)
        # One durable report per authorized product, across all interviews and restarts.
        root = Path(self.store.settings.data_dir)/'synthetic-demo/medical'
        return secret, root

    def status(self, interview_id):
        from .schemas import SyntheticDemoOut
        secret, root = self.context(interview_id)
        record = self.read(root)
        jobs = [j for j in self.store.jobs(interview_id) if j.action == 'synthetic_demo']
        active = next((j for j in reversed(jobs) if j.status in ('queued', 'running', 'cancelling')), None)
        values = {k: record.get(k) for k in ('reply', 'time', 'tokens_in', 'tokens_out',
            'duration_ms', 'cost_upper_micro_rmb')}
        return SyntheticDemoOut(configured=secret.is_file(), question=FIXED_PROMPT,
            status=record.get('status', 'not_run'), model=MODEL, job_id=active.id if active else None, **values)

    @staticmethod
    def read(root):
        path = root/'report.json'
        if not path.exists():
            return {}
        try:
            if path.stat().st_size > 16000:
                raise ValueError()
            return json.loads(path.read_text())
        except (OSError, ValueError):
            raise Problem('DEMO_REPORT_INVALID', '联调记录不可读取，未重复发送；请检查已有记录。', 409) from None

    def client(self):
        return httpx.Client(timeout=httpx.Timeout(40, connect=10), follow_redirects=False,
            trust_env=False, transport=httpx.HTTPTransport(retries=0))

    def run(self, interview_id):
        from .codex import JOB_CONTEXT
        def cancelled():
            context = JOB_CONTEXT.get()
            if context and context['cancelled']():
                raise Problem('CANCELLED', '联调已停止；已发生的调用记录保留，不自动重发。', 409)
        cancelled()
        secret, root = self.context(interview_id)
        record = self.read(root)
        if not record:
            if not secret.is_file() or secret.stat().st_size > 16000:
                raise Problem('DEMO_KEY_MISSING', '请将医美小助手的密钥保存在其backend/.env，本页不接收密钥。', 409)
            key = next((line.split('=', 1)[1].strip() for line in secret.read_text().splitlines()
                if line.startswith('MODEL_API_KEY=')), '')
            if not key:
                raise Problem('DEMO_KEY_MISSING', '医美小助手本地配置尚未填入模型密钥。', 409)
            try:
                with self.client() as client:
                    record = run_probe(key, root, client)
            except ValueError:
                record = self.read(root)
        cancelled()
        if record.get('status') != 'passed':
            raise Problem('DEMO_NOT_PASSED', '联调尚未通过，已有记录保留；不确定的请求不会自动重发。', 409)
        # Only a strictly validated fixed reply can be returned to the browser.
        SyntheticReply.model_validate(record.get('reply'))
        return self.status(interview_id).model_dump()


