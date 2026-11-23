from pathlib import Path
import subprocess

from conftest import create, wait_job


def test_conversation_answer_composition_and_product_language():
    script = Path(__file__).with_name('conversation-model.test.cjs')
    result = subprocess.run(['node', '--test', str(script)], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_submitted_chat_content_is_preserved_without_exposing_job_payload(env):
    client, app, root = env
    iv = create(client)
    content = '我希望首版先给自己使用，也保留后续邀请朋友的可能。'
    job = wait_job(client, client.post(f'/api/v1/interviews/{iv}/messages', json={'content': content}))
    assert job['status'] == 'succeeded' and job['user_content'] == content
    assert 'payload' not in job
    restored = client.get('/api/v1/jobs/' + job['id']).json()
    assert restored['user_content'] == content
    app.state.workflow.gateway.fail = True
    failed = wait_job(client, client.post(f'/api/v1/interviews/{iv}/messages', json={'content': '失败时也保留我的回答'}))
    assert failed['status'] == 'failed' and failed['user_content'] == '失败时也保留我的回答'
