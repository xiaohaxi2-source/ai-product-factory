import json

import httpx
import pytest

from app.core import LOCAL_OWNER
from app.store import Interview
from conftest import create, wait_job


def setup_demo(env, monkeypatch):
    client, app, root = env
    iv = create(client, '医美小助手')
    with app.state.store.Session.begin() as session:
        session.get(Interview, iv).status = 'closed'
    secret = root/'projects/医美小助手/backend/.env'
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text('MODEL_API_KEY=fake-for-test\n')
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': '{"status":"ok","sample":"DEMO_PRODUCT"}'}}],
            'usage': {'prompt_tokens': 68, 'completion_tokens': 13}})
    monkeypatch.setattr(app.state.jobs.synthetic_demo, 'client',
        lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    return client, app, root, iv, secret, calls


def test_web_demo_real_job_fixed_payload_cached_result_and_refresh(env, monkeypatch):
    c, app, root, iv, secret, calls = setup_demo(env, monkeypatch)
    base = f'/api/v1/interviews/{iv}/synthetic-demo'
    assert c.get('/synthetic-demo').status_code == 200
    assert c.get(base).json()['status'] == 'not_run'
    for extra in ('prompt', 'question', 'content', 'endpoint', 'model'):
        assert c.post(base, json={'accepted': True, extra: 'must never be sent'}).status_code == 422
    assert c.post(base, json={'accepted': False}).status_code == 409
    first = wait_job(c, c.post(base, json={'accepted': True}))
    assert first['status'] == 'succeeded'
    record = c.get(base).json()
    assert record['status'] == 'passed' and record['reply']['sample'] == 'DEMO_PRODUCT'
    assert record['medical_enabled'] is False and record['cost_upper_micro_rmb'] == 240
    assert len(calls) == 1 and len(calls[0]['messages']) == 1
    assert 'must never be sent' not in json.dumps(calls)
    assert 'fake-for-test' not in json.dumps(first) + json.dumps(record)
    second = wait_job(c, c.post(base, json={'accepted': True}))
    assert second['status'] == 'succeeded' and len(calls) == 1
    from app.synthetic_demo import SyntheticDemo
    assert SyntheticDemo(app.state.workflow).status(iv).reply == record['reply']
    stream = c.get('/api/v1/jobs/' + first['id'] + '/events').text
    assert 'event: done' in stream


def test_web_demo_missing_key_and_scope_do_not_call_provider(env, monkeypatch):
    c, app, root, iv, secret, calls = setup_demo(env, monkeypatch)
    secret.unlink()
    base = f'/api/v1/interviews/{iv}/synthetic-demo'
    assert not c.get(base).json()['configured']
    job = wait_job(c, c.post(base, json={'accepted': True}))
    assert job['error']['code'] == 'DEMO_KEY_MISSING' and not calls
    other = create(c, '其他产品')
    assert c.get(f'/api/v1/interviews/{other}/synthetic-demo').status_code == 409
    assert c.get('/api/v1/interviews/99999/synthetic-demo').status_code == 404
    assert c.post(base, json={'accepted': True}, headers={'Origin': 'https://external.invalid'}).status_code == 403


@pytest.mark.parametrize('status', ['sending', 'failed_or_uncertain'])
def test_restart_or_uncertain_result_never_automatically_resends(env, monkeypatch, status):
    c, app, root, iv, secret, calls = setup_demo(env, monkeypatch)
    report = root/'data/synthetic-demo/medical/report.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'status': status, 'medical_enabled': False}))
    job = wait_job(c, c.post(f'/api/v1/interviews/{iv}/synthetic-demo', json={'accepted': True}))
    assert job['status'] == 'failed' and job['error']['code'] == 'DEMO_NOT_PASSED'
    assert not calls


def test_web_demo_keeps_owner_and_symlink_boundaries(env, monkeypatch):
    c, app, root, iv, secret, calls = setup_demo(env, monkeypatch)
    secret.unlink()
    private = root/'private.env'
    private.write_text('MODEL_API_KEY=fake-for-test\n')
    secret.symlink_to(private)
    base = f'/api/v1/interviews/{iv}/synthetic-demo'
    assert c.get(base).status_code == 409
    with app.state.store.Session.begin() as session:
        session.get(Interview, iv).owner_id = 'another-owner'
    assert c.get(base).status_code == 404 and not calls
