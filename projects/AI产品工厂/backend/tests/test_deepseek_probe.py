import importlib.util
import json
from pathlib import Path

import httpx
import pytest

path = Path(__file__).resolve().parents[2] / 'scripts/probe_deepseek.py'
spec = importlib.util.spec_from_file_location('probe_deepseek', path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_probe_sends_only_fixed_fixture_and_validates_usage(tmp_path):
    def respond(request):
        body = json.loads(request.content)
        assert request.url == probe.ENDPOINT
        assert body['messages'] == [{'role': 'user', 'content': probe.FIXED_PROMPT}]
        assert body['max_tokens'] == 128 and not body['stream']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {
            'content': '{"status":"ok","sample":"DEMO_PRODUCT"}'}}],
            'usage': {'prompt_tokens': 40, 'completion_tokens': 12}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = probe.run_probe('fake-for-test', tmp_path, client)
        assert result['status'] == 'passed' and result['cost_upper_micro_rmb'] == 176
        assert result['medical_enabled'] is False
        with pytest.raises(ValueError):
            probe.run_probe('fake-for-test', tmp_path, client)
    assert 'fake-for-test' not in (tmp_path / 'report.json').read_text()


@pytest.mark.parametrize('response', [httpx.Response(401, text='api_key=fake-for-test'),
    httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'bad'}}]})])
def test_probe_keeps_failures_and_never_records_raw_error_or_key(tmp_path, response):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as client:
        result = probe.run_probe('fake-for-test', tmp_path, client)
        assert result['status'] in ('failed', 'failed_or_uncertain')
        with pytest.raises(ValueError):
            probe.run_probe('fake-for-test', tmp_path, client)
    assert 'fake-for-test' not in (tmp_path / 'report.json').read_text()
