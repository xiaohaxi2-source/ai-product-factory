"""DeepSeek 模型网关：超时/有限重试/主备降级/JSON 结构校验/计费台账。

所有模型调用只经过本模块；workflow 通过 complete() 拿到已校验的结构化结果。
"""
import json
import logging
import re
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

from .core import Problem, Settings
from .store import Store

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / 'services' / 'prompts'


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f'{name}.txt').read_text(encoding='utf-8')


def extract_json(text: str) -> dict:
    """宽容解析：直接 JSON 或 ```json 代码块。"""
    text = text.strip()
    m = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if m:
        text = m.group(1)
    return json.loads(text)


class ModelGateway:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self._prompts: dict[str, str] = {}

    def prompt(self, name: str) -> str:
        if name not in self._prompts:
            self._prompts[name] = load_prompt(name)
        return self._prompts[name]

    def complete(self, prompt_name: str, payload: dict, schema: type[BaseModel],
                 max_tokens: int = 4096) -> tuple[BaseModel, dict]:
        """调用模型并返回 (校验后的结构, 元信息)。失败抛 Problem，不泄露细节。"""
        if not self.settings.model_api_key:
            raise Problem('MODEL_NOT_CONFIGURED', '模型 Key 未配置，请在 backend/.env 填入 MODEL_API_KEY 后重启。', 503)
        used = self.store.budget_used()
        if used >= self.settings.total_budget_rmb:
            raise Problem('BUDGET_EXCEEDED',
                          f'本轮费用已达上限 {self.settings.total_budget_rmb:.2f} 元，暂停模型调用。', 429)
        system = self.prompt(prompt_name)
        user = json.dumps(payload, ensure_ascii=False)
        last_err = ''
        models = [self.settings.model_name or 'deepseek-chat', self.settings.model_fallback_name]
        deadline_retries = self.settings.model_max_retries
        start = time.monotonic()
        retries = 0
        for attempt in range(deadline_retries + 1):
            model = models[0] if attempt <= deadline_retries // 2 else models[-1]
            try:
                data = self._chat(model, system, user, max_tokens)
                meta = {'model': model, 'retry_count': retries,
                        'duration_ms': int((time.monotonic() - start) * 1000)}
                parsed = self._validate(data['content'], schema)
                self._bill(prompt_name, model, data, meta, '')
                return parsed, meta
            except Problem as e:
                last_err = e.code
                retries += 1
                log.info('model attempt failed prompt=%s err=%s', prompt_name, e.code)
                if e.code in ('SCHEMA_INVALID',):
                    continue  # 结构问题可重试
                if attempt < deadline_retries:
                    time.sleep(min(2 ** attempt, 5))
                    continue
                break
        self._bill(prompt_name, models[-1], {'usage': {}}, {'duration_ms': int((time.monotonic() - start) * 1000),
                                                             'retry_count': retries}, last_err or 'MODEL_FAILED')
        if last_err == 'SCHEMA_INVALID':
            raise Problem('SCHEMA_INVALID', '模型输出多次不符合结构要求，请重试或调整后再试。', 502)
        raise Problem('MODEL_FAILED', '模型服务暂时不可用，请稍后重试。', 502)

    def _chat(self, model: str, system: str, user: str, max_tokens: int) -> dict:
        url = self.settings.model_base_url.rstrip('/') + '/chat/completions'
        headers = {'Authorization': f'Bearer {self.settings.model_api_key}'}
        body = {'model': model, 'messages': [{'role': 'system', 'content': system},
                                             {'role': 'user', 'content': user}],
                'response_format': {'type': 'json_object'},
                'max_tokens': max_tokens}
        try:
            resp = httpx.post(url, headers=headers, json=body,
                              timeout=self.settings.model_timeout)
        except httpx.TimeoutException:
            raise Problem('MODEL_TIMEOUT', '模型请求超时。', 504)
        except httpx.HTTPError:
            raise Problem('MODEL_FAILED', '模型网络错误。', 502)
        if resp.status_code in (401, 403):
            raise Problem('MODEL_AUTH', '模型 Key 认证失败。', 502)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise Problem('MODEL_FAILED', f'模型服务状态异常（{resp.status_code}）。', 502)
        if resp.status_code != 200:
            raise Problem('MODEL_FAILED', f'模型请求失败（{resp.status_code}）。', 502)
        try:
            payload = resp.json()
            content = payload['choices'][0]['message']['content']
            usage = payload.get('usage', {})
        except (ValueError, KeyError, IndexError):
            raise Problem('MODEL_FAILED', '模型响应格式异常。', 502)
        return {'content': content, 'usage': usage, 'request_id': payload.get('id', '-')}

    def _validate(self, content: str, schema: type[BaseModel]) -> BaseModel:
        try:
            raw = extract_json(content)
        except (ValueError, re.error):
            raise Problem('SCHEMA_INVALID', '模型输出不是合法 JSON。', 502)
        try:
            return schema.model_validate(raw)
        except ValidationError as e:
            log.info('schema invalid fields: %s', [err['loc'] for err in e.errors()[:3]])
            raise Problem('SCHEMA_INVALID', '模型输出结构校验失败。', 502)

    def _bill(self, prompt: str, model: str, data: dict, meta: dict, error: str) -> None:
        usage = data.get('usage') or {}
        tin, tout = int(usage.get('prompt_tokens', 0)), int(usage.get('completion_tokens', 0))
        cost = (tin / 1e6 * self.settings.input_rmb_per_million +
                tout / 1e6 * self.settings.output_rmb_per_million)
        self.store.record_call(prompt=prompt, model=model, tokens_in=tin, tokens_out=tout,
                               cost_rmb=round(cost, 6), duration_ms=meta.get('duration_ms', 0),
                               retry_count=meta.get('retry_count', 0), error=error)
