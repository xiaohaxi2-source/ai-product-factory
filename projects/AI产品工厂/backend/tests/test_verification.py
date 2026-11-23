import json
from pathlib import Path
import socket
import sys

import pytest

from app.check_sandbox import command_run, environment, sandbox_profile
from app.core import Problem, PRODUCT_ROOT
from app.execution import sha
from app.planning import digest
from app.store import Store
from conftest import wait_job
from test_planning import planning
from test_execution import executing, start


@pytest.fixture
def checkable(executing,monkeypatch):
    c,a,p,iv,body,g=executing
    def code(payload,schema):
        if payload['current_task']['id']=='T1':
            changes=[{'path':'requirements.txt','before_sha256':'new','content':'fastapi>=0.115,<1\npytest>=8,<9\n'},
                {'path':'backend/app/main.py','before_sha256':'new','content':
                 'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/api/v1/health")\ndef health(): return {"status":"ok"}\n@app.get("/")\ndef page(): return {"page":"local preview"}\n'},
                {'path':'backend/logic.py','before_sha256':'new','content':'VALUE = 2\n'}]
        else:
            changes=[{'path':'tests/test_basic.py','before_sha256':'new','content':
                'from backend.logic import VALUE\ndef test_value(): assert VALUE == 2\ndef test_boundary(): assert 2 + 2 == 4\n'}]
        return schema.model_validate({'summary':'预设隔离检查输入','changes':changes}),{}
    g.code_hook=code
    assert start(executing)['status']=='succeeded'
    service=a.state.jobs.verifications
    def existing_runtime(record,text):
        service.step(record,'依赖环境','passed','测试复用已安装运行时，不下载依赖。')
        return Path(sys.executable)
    monkeypatch.setattr(service,'ensure_environment',existing_runtime)
    run=a.state.store.latest_execution(iv,a.state.store.proposal(iv).id)
    request={'accepted':True,'execution_id':run.id,'revision':run.data['revision'],'manifest_digest':digest(run.data['manifest'])}
    return c,a,p,iv,g,request


def run_check(checkable):
    c,a,p,iv,g,body=checkable
    job=wait_job(c,c.post(f'/api/v1/interviews/{iv}/verify',json=body))
    return job,c.get(f'/api/v1/interviews/{iv}/verification').json()


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS隔离进程集成检查')
def test_real_pytest_preview_human_record_and_report_recovery(checkable):
    c,a,p,iv,g,body=checkable
    job,report=run_check(checkable)
    assert job['status']=='succeeded',report
    test=next(s for s in report['steps'] if s['name']=='自动化测试')
    assert test['passed']==2 and test['failed']==0
    assert report['status']=='passed' and report['blockers'] and not report['accepted']
    import httpx
    assert httpx.get(report['preview_url'],trust_env=False).status_code==200
    accepted=c.post(f'/api/v1/verifications/{report["id"]}/accept',json={'accepted':True,'revision':report['revision'],'comment':'预设工程检查样例通过，业务仍待验'})
    assert accepted.status_code==200 and accepted.json()['accepted'] and accepted.json()['blockers']
    second=Store(a.state.settings)
    assert second.verification(report['id']).data['accepted']
    assert Path(report['evidence_path']).is_file()
    assert json.loads(Path(report['evidence_path']).read_text())['status']=='passed'
    second.engine.dispose()
    plan=c.get(f'/api/v1/verifications/{report["id"]}/adoption').json()
    assert plan['files'] and not plan['conflicts']
    assert c.post(f'/api/v1/verifications/{report["id"]}/adopt',json={'accepted':True,'plan_digest':'0'*64}).status_code==409
    adopted=c.post(f'/api/v1/verifications/{report["id"]}/adopt',json={'accepted':True,'plan_digest':plan['plan_digest']})
    assert adopted.status_code==200 and adopted.json()['adopted']
    assert Path(plan['target'],'backend/logic.py').read_text()=='VALUE = 2\n'
    assert c.get(f'/api/v1/verifications/{report["id"]}').json()['adopted']
    assert c.post(f'/api/v1/verifications/{report["id"]}/preview-login').status_code==409
    c.post(f'/api/v1/verifications/{report["id"]}/stop-preview')
    assert c.get(f'/api/v1/verifications/{report["id"]}').json()['preview_url'] is None


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS隔离进程集成检查')
def test_failed_tests_are_real_failures_repair_keeps_tests_and_rechecks(checkable):
    c,a,p,iv,g,body=checkable
    execution=a.state.store.execution(body['execution_id'])
    service=a.state.jobs.executions
    files=service.current_files(execution);files['backend/logic.py']='VALUE = 1\n'
    revision,_=service.write_revision(execution.id,files)
    from app.execution import manifest
    execution.data.update(revision=revision,manifest=manifest(files))
    a.state.store.save_execution(execution.id,'generated',execution.data)
    body.update(revision=revision,manifest_digest=digest(manifest(files)))
    job,report=run_check(checkable)
    assert job['status']=='failed' and report['status']=='failed'
    step=next(s for s in report['steps'] if s['name']=='自动化测试')
    assert step['passed']==1 and step['failed']==1
    assert c.post(f'/api/v1/verifications/{report["id"]}/accept',json={'accepted':True,'revision':revision}).status_code==409
    original_test=files['tests/test_basic.py']
    def repair(payload,schema):
        assert payload['current_task']['id']=='T99' and payload['check_failures']
        return schema.model_validate({'summary':'修复实际失败的值','changes':[{'path':'backend/logic.py','before_sha256':sha('VALUE = 1\n'),'content':'VALUE = 2\n'}]}),{}
    g.code_hook=repair
    job=wait_job(c,c.post(f'/api/v1/interviews/{iv}/check-fix',json={'accepted':True,'verification_id':report['id']}))
    assert job['status']=='succeeded',job
    latest=c.get(f'/api/v1/interviews/{iv}/verification').json()
    assert latest['status']=='passed' and latest['id']!=report['id']
    assert c.get(f'/api/v1/verifications/{report["id"]}').json()['source_changed']
    assert service.current_files(a.state.store.execution(execution.id))['tests/test_basic.py']==original_test


def test_verification_requires_explicit_current_code_version_and_owner(checkable):
    c,a,p,iv,g,body=checkable
    for changed in ({'accepted':False},{'revision':'0'*32},{'manifest_digest':'0'*64}):
        assert c.post(f'/api/v1/interviews/{iv}/verify',json=dict(body,**changed)).status_code==409
    assert c.get('/api/v1/verifications/00000000-0000-0000-0000-000000000000').status_code==404


def test_adoption_conflicts_preserve_independent_target_code(checkable):
    c,a,p,iv,g,body=checkable
    service=a.state.jobs.verifications
    record=service.prepare(iv,body);record.status='passed';a.state.store.add(record)
    target=p/'projects/测试产品/backend/app/main.py';target.parent.mkdir(parents=True);target.write_text('INDEPENDENT_USER_CHANGE = True\n')
    plan=c.get(f'/api/v1/verifications/{record.id}/adoption').json()
    assert 'backend/app/main.py' in plan['conflicts']
    result=c.post(f'/api/v1/verifications/{record.id}/adopt',json={'accepted':True,'plan_digest':plan['plan_digest']})
    assert result.status_code==409
    assert target.read_text()=='INDEPENDENT_USER_CHANGE = True\n'
    assert not (target.parent.parent/'logic.py').exists()


@pytest.mark.parametrize('value',['-e .','https://example.com/file.whl','fastapi @ https://example.com/a.whl','--index-url https://example.com','unknown-secret-package==1.0','../requirements.txt',''])
def test_dependencies_cannot_be_commands_or_unreviewed_sources(value):
    from app.verification import requirements
    with pytest.raises(Problem):requirements(value)


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS文件和网络边界检查')
def test_generated_process_cannot_read_user_files_write_outside_or_contact_host(tmp_path):
    canary=PRODUCT_ROOT/'.runtime'/'sandbox-read-canary.txt'
    canary.parent.mkdir(exist_ok=True);canary.write_text('non-secret test canary')
    root=tmp_path/'allowed';root.mkdir();outside=tmp_path/'outside.txt'
    env=environment(root);profile=sandbox_profile(root,Path(sys.executable))
    with socket.socket() as server:
        server.bind(('127.0.0.1',0));server.listen();port=server.getsockname()[1]
        source=f'''import pathlib,socket
checks=[]
try: pathlib.Path({str(canary)!r}).read_text(); checks.append(False)
except PermissionError: checks.append(True)
try: pathlib.Path({str(outside)!r}).write_text('bad');checks.append(False)
except PermissionError: checks.append(True)
s=socket.socket();checks.append(s.connect_ex(('127.0.0.1',{port}))!=0);s.close()
print(checks)
assert all(checks)
'''
        code,out=command_run([sys.executable,'-c',source],root,env,profile=profile,timeout=15)
    assert code==0,out
    assert not outside.exists()


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS超时进程检查')
def test_check_process_timeout_is_bounded_and_sanitizes_even_on_error(tmp_path):
    env=environment(tmp_path);profile=sandbox_profile(tmp_path,Path(sys.executable))
    with pytest.raises(Problem) as error:
        command_run([sys.executable,'-u','-c','import time;print("api_key=fake-for-test");time.sleep(20)'],tmp_path,env,profile=profile,timeout=.5)
    assert error.value.code=='CHECK_TIMEOUT'
    logs=list(tmp_path.glob('process-*.txt'))
    assert logs and all('fake-for-test' not in p.read_text() for p in logs)
