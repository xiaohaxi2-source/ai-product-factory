from copy import deepcopy
from pathlib import Path
import threading

import pytest

from app.core import Problem
from app.execution import relative_path, sha
from app.store import Confirmation, Execution, Store
from sqlalchemy import select
from conftest import wait_job
from test_planning import planning, closed, generate, confirmation


@pytest.fixture
def executing(planning):
    c,a,p = planning
    iv = closed(c)
    w = generate(c,iv)
    assert wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=confirmation(w)))['status']=='succeeded'
    w = c.get(f'/api/v1/interviews/{iv}/workbench').json()
    body = dict(confirmation(w), plan_digest=w['development_plan']['plan_digest'])
    gateway = a.state.workflow.gateway
    original = gateway.complete
    gateway.code_calls = []
    gateway.code_hook = None
    def code(name,payload,schema,**kwargs):
        if name!='development_code':return original(name,payload,schema,**kwargs)
        gateway.code_calls.append(deepcopy(payload))
        if gateway.code_hook:return gateway.code_hook(payload,schema)
        task=payload['current_task']['id']
        return schema.model_validate({'summary':task+'的真实代码结构已实现，功能待验', 'questions':[],
            'changes':[{'path':f'backend/app/{task.lower()}.py','before_sha256':'new','content':f'VALUE = {len(gateway.code_calls)}\n'}]}),{}
    gateway.complete = code
    return c,a,p,iv,body,gateway


def start(env,body=None):
    c,a,p,iv,default,g=env
    return wait_job(c,c.post(f'/api/v1/interviews/{iv}/execute',json=body or default))


def current(env):
    c,a,p,iv,body,g=env
    return c.get(f'/api/v1/interviews/{iv}/execution').json()


def test_real_files_changes_checks_diff_and_no_original_writes(executing):
    c,a,p,iv,body,g=executing
    root=p/'projects/测试产品';(root/'backend').mkdir()
    (root/'backend/original.py').write_text('ORIGINAL = True\n')
    (root/'backend/.env').write_text('PRIVATE_ONLY=not_for_model')
    before={str(f):f.read_bytes() for f in root.rglob('*') if f.is_file()}
    job=start(executing);assert job['status']=='succeeded',job
    run=current(executing);assert run['status']=='generated' and all(s['status']=='generated' for s in run['steps'])
    assert Path(run['workspace'],'backend/app/t1.py').read_text()=='VALUE = 1\n'
    assert Path(run['workspace'],'backend/app/t2.py').is_file()
    assert all(f['path']!='backend/.env' for call in g.code_calls for f in call['files'])
    assert all(Path(f).read_bytes()==text for f,text in before.items())
    assert not (root/'backend/app/t1.py').exists()
    assert run['steps'][0]['checks'][0]['status']=='passed'
    diff=c.get(f'/api/v1/executions/{run["id"]}/diff',params={'task_id':'T1','path':'backend/app/t1.py'})
    assert diff.status_code==200 and '+VALUE = 1' in diff.json()['diff']
    assert c.get(f'/api/v1/executions/{run["id"]}/diff',params={'task_id':'T1','path':'../../secret'}).status_code==422
    assert c.get(f'/api/v1/executions/{run["id"]}/diff',params={'task_id':'T1','path':'backend/unknown.py'}).status_code==404
    assert 'done' in c.get('/api/v1/jobs/'+job['id']+'/events').text
    with a.state.store.Session() as s:
        assert s.scalar(select(Confirmation).where(Confirmation.kind=='execute')).data['plan_digest']==body['plan_digest']
    assert start(executing)['status']=='succeeded'
    assert len(g.code_calls)==2 and current(executing)['id']==run['id']


def test_retry_skips_completed_code_and_preserves_checkpoints(executing):
    c,a,p,iv,body,g=executing
    def hook(payload,schema):
        if payload['current_task']['id']=='T2':raise Problem('MODEL_FAILED','模拟模型失败',502)
        return schema.model_validate({'summary':'T1完成','changes':[{'path':'backend/first.py','before_sha256':'new','content':'FIRST = True\n'}]}),{}
    g.code_hook=hook
    job=start(executing);assert job['status']=='failed'
    run=current(executing);assert run['steps'][0]['status']=='generated' and run['steps'][1]['status']=='failed'
    second=Store(a.state.settings)
    assert second.execution(run['id']).data['manifest']['backend/first.py']==sha('FIRST = True\n')
    second.engine.dispose()
    g.code_hook=None
    retried=wait_job(c,c.post('/api/v1/jobs/'+job['id']+'/retry'))
    assert retried['status']=='succeeded',retried
    assert [call['current_task']['id'] for call in g.code_calls]==['T1','T2','T2']
    assert Path(current(executing)['workspace'],'backend/first.py').is_file()


@pytest.mark.parametrize('path',['../other.py','/tmp/p.py','backend/../p.py','backend/.env','backend/.git/config',
    'docs/流程状态.txt','AGENTS.md','projects/别的产品/backend/app.py','backend/data/records.json','backend/x\\y.py'])
def test_paths_rejected_without_partial_application(executing,path):
    c,a,p,iv,body,g=executing
    def hook(payload,schema):
        return schema.model_validate({'summary':'试图越界','changes':[
            {'path':'backend/valid.py','before_sha256':'new','content':'OK = True\n'},
            {'path':path,'before_sha256':'new','content':'BAD = True\n'}]}),{}
    g.code_hook=hook
    job=start(executing)
    assert job['status']=='failed' and job['error']['code'].startswith('CODE_PATH')
    assert not (Path(current(executing)['workspace'])/'backend/valid.py').exists()


@pytest.mark.parametrize('mode,expected',[('hash','CODE_BASE_CHANGED'),('syntax','CODE_CHECK_FAILED'),('secret','CODE_SECRET_DENIED'),('empty','CODE_NO_CHANGES')])
def test_invalid_code_is_not_adopted(executing,mode,expected):
    c,a,p,iv,body,g=executing
    def hook(payload,schema):
        content='VALUE = True\n'
        if mode=='syntax':content='def broken(:\n'
        if mode=='secret':content='TOKEN = "sk-'+'f'*30+'"\n'
        if mode=='empty':
            root=p/'projects/测试产品';content=''
        return schema.model_validate({'summary':'候选改动','changes':[{'path':'backend/test.py',
            'before_sha256':'0'*64 if mode=='hash' else 'new','content':content}]}),{}
    g.code_hook=hook
    if mode=='empty':
        root=p/'projects/测试产品/backend';root.mkdir();(root/'test.py').write_text('')
        def same(payload,schema):
            return schema.model_validate({'summary':'没有改动','changes':[{'path':'backend/test.py','before_sha256':sha(''),'content':''}]}),{}
        g.code_hook=same
    job=start(executing);assert job['status']=='failed' and job['error']['code']==expected,job
    assert not (Path(current(executing)['workspace'])/'backend/test.py').exists() or mode=='empty'


def test_questions_block_without_writes_feedback_continues(executing):
    c,a,p,iv,body,g=executing
    g.code_hook=lambda payload,schema:(schema.model_validate({'summary':'待业务范围确认','changes':[], 'questions':['数据需要保存哪些字段？']}),{})
    job=start(executing);assert job['error']['code']=='EXECUTION_INPUT_REQUIRED'
    run=current(executing);assert run['status']=='blocked' and run['steps'][0]['questions']
    g.code_hook=None
    body=dict(body,execution_id=run['id'],feedback='仅保存非敏感文本和归属ID')
    assert start(executing,body)['status']=='succeeded'
    assert g.code_calls[-1]['feedback']=='仅保存非敏感文本和归属ID'


def test_manual_workspace_changes_stop_resume(executing):
    c,a,p,iv,body,g=executing
    g.code_hook=lambda payload,schema: (_ for _ in ()).throw(Problem('MODEL_FAILED','暂停',502))
    assert start(executing)['status']=='failed'
    run=current(executing);folder=Path(run['workspace']);(folder/'README.md').write_text('人工修改')
    g.code_hook=None
    job=start(executing,dict(body,execution_id=run['id']))
    assert job['error']['code']=='WORKSPACE_CHANGED'
    assert (folder/'README.md').read_text()=='人工修改'


def test_source_symlink_and_new_source_version_stop_execution(executing):
    c,a,p,iv,body,g=executing
    root=p/'projects/测试产品/backend';root.mkdir();outside=p/'outside';outside.mkdir();(outside/'secret.py').write_text('SECRET = 1')
    (root/'linked').symlink_to(outside,target_is_directory=True)
    job=start(executing);assert job['error']['code']=='CODE_PATH_INVALID' and not g.code_calls
    (root/'linked').unlink()
    asset=a.state.store.asset(iv,'export_plan');prd=next(Path(v) for v in asset.data['files'] if v.endswith('PRD.md'))
    prd.write_text(prd.read_text()+'\n改变范围')
    assert c.post(f'/api/v1/interviews/{iv}/execute',json=body).status_code==409


def test_explicit_version_plan_consent_and_owner_are_required(executing):
    c,a,p,iv,body,g=executing
    for update in ({'accepted':False},{'proposal_version':99},{'plan_digest':'0'*64}):
        assert c.post(f'/api/v1/interviews/{iv}/execute',json=dict(body,**update)).status_code==409
    assert start(executing)['status']=='succeeded'
    run=current(executing)
    with a.state.store.Session.begin() as s:
        row=s.get(Execution,run['id']);row.owner_id='other-owner'
    assert c.get('/api/v1/executions/'+run['id']).status_code==404
    assert c.post(f'/api/v1/interviews/{iv}/execute',json=dict(body,execution_id=run['id'])).status_code==404


def test_cancel_and_busy_keep_workspace_and_recovery_does_not_replay(executing):
    from app.codex import JOB_CONTEXT
    c,a,p,iv,body,g=executing
    entered,release=threading.Event(),threading.Event()
    def hook(payload,schema):
        entered.set();release.wait(4)
        if JOB_CONTEXT.get()['cancelled']():raise Problem('CANCELLED','用户取消',409)
        return schema.model_validate({'summary':'生成','changes':[{'path':'backend/value.py','before_sha256':'new','content':'X=1'}]}),{}
    g.code_hook=hook
    response=c.post(f'/api/v1/interviews/{iv}/execute',json=body);assert entered.wait(3)
    try:
        assert c.post(f'/api/v1/interviews/{iv}/execute',json=body).status_code==409
        assert c.post('/api/v1/jobs/'+response.json()['id']+'/cancel').status_code==200
    finally:release.set()
    job=wait_job(c,response);assert job['status']=='cancelled'
    run=current(executing);assert run['status']=='cancelled' and Path(run['workspace']).is_dir()
    row=a.state.store.execution(run['id']);data=deepcopy(row.data);data['steps'][0]['status']='running'
    a.state.store.save_execution(row.id,'running',data)
    count=len(g.code_calls);a.state.jobs.recover()
    assert current(executing)['status']=='interrupted' and len(g.code_calls)==count
    g.code_hook=None
    assert start(executing,dict(body,execution_id=run['id']))['status']=='succeeded'


def test_batch_timeout_restart_resume_preserves_code_and_aggregates_diff(executing):
    c,a,p,iv,body,g=executing
    def partial(payload,schema):
        if payload['batch_number']==2:
            raise Problem('MODEL_TIMEOUT','模拟当前批超时',504)
        return schema.model_validate({'summary':'第一部分已实现，下一批补齐', 'task_complete':False,
            'changes':[{'path':'backend/partial.py','before_sha256':'new','content':'FIRST = 1\n'}]}),{}
    g.code_hook=partial
    job=start(executing);assert job['error']['code']=='MODEL_TIMEOUT'
    run=current(executing);step=run['steps'][0]
    assert run['status']=='failed' and step['chunks']==1 and step['status']!='generated'
    assert Path(run['workspace'],'backend/partial.py').read_text()=='FIRST = 1\n'
    assert c.get(f'/api/v1/executions/{run["id"]}/diff',params={'task_id':'T1','path':'backend/partial.py'}).status_code==200
    row=a.state.store.execution(run['id']);data=deepcopy(row.data);data['steps'][0]['status']='running'
    a.state.store.save_execution(row.id,'running',data);a.state.jobs.recover()
    assert current(executing)['steps'][0]['chunks']==1
    def resume(payload,schema):
        if payload['current_task']['id']=='T1':
            assert payload['saved_batches']==1 and payload['batch_number']==2
            file=next(f for f in payload['files'] if f['path']=='backend/partial.py')
            assert file['content']=='FIRST = 1\n'
            return schema.model_validate({'summary':'补齐当前任务', 'task_complete':True,
                'changes':[{'path':file['path'],'before_sha256':file['sha256'],'content':'FIRST = 1\nSECOND = 2\n'}]}),{}
        return schema.model_validate({'summary':'后续任务', 'changes':[{'path':'tests/test_value.py','before_sha256':'new','content':'def test_value():\n    assert 1 + 1 == 2\n'}]}),{}
    g.code_hook=resume
    assert start(executing,dict(body,execution_id=run['id']))['status']=='succeeded'
    final=current(executing);assert final['steps'][0]['chunks']==2
    change=final['steps'][0]['changes'][0]
    assert change['before_sha256']=='new' and change['added']==2
    assert [call['batch_number'] for call in g.code_calls]==[1,2,2,1]


def test_automatic_generation_has_a_call_budget_not_a_code_batch_cap(executing):
    c,a,p,iv,body,g=executing
    def endless(payload,schema):
        return schema.model_validate({'summary':'尚有代码未完成','task_complete':False,
            'changes':[{'path':f'backend/part{payload["batch_number"]}.py','before_sha256':'new','content':'VALUE = 1\n'}]}),{}
    g.code_hook=endless
    a.state.settings.max_calls=3
    job=start(executing);assert job['error']['code']=='CALL_LIMIT'
    run=current(executing)
    assert len(g.code_calls)==3 and run['steps'][0]['chunks']==3 and run['status']=='failed'
    assert run['steps'][0]['status']!='generated' and len(run['steps'][0]['changes'])==3
    a.state.settings.max_calls=60
    g.code_hook=None
    assert start(executing,dict(body,execution_id=run['id']))['status']=='succeeded'
    assert current(executing)['steps'][0]['chunks']==4


def test_coding_context_excludes_old_checklists_without_losing_manifest(executing):
    c,a,p,iv,body,g=executing
    path=p/'projects/测试产品/docs/阶段文档/阶段0-PRD补全清单.md'
    path.write_text('历史访谈内容，不需要每一批代码都重新发送')
    assert start(executing)['status']=='succeeded'
    assert any(f['path'].endswith('阶段0-PRD补全清单.md') for f in g.code_calls[0]['file_inventory'])
    assert not any(f['path'].endswith('阶段0-PRD补全清单.md') for f in g.code_calls[0]['files'])


def test_large_task_can_continue_past_eight_saved_batches(executing):
    c,a,p,iv,body,g=executing
    def many(payload,schema):
        task=payload['current_task']['id'];batch=payload['batch_number']
        return schema.model_validate({'summary':'持续实现当前任务','task_complete':task=='T2' or batch>=9,
            'changes':[{'path':f'backend/{task}_{batch}.py','before_sha256':'new','content':'VALUE = 1\n'}]}),{}
    g.code_hook=many
    assert start(executing)['status']=='succeeded'
    assert current(executing)['steps'][0]['chunks']==9
