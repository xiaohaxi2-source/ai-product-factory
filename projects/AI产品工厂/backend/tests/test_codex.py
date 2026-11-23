import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.codex import CodexGateway, JOB_CONTEXT, codex_environment, codex_problem, command, strict_schema
from app.core import Problem, Settings
from app.schemas import AuditResult, DraftPRD, FinalPRD, InterviewReply
from app.store import Call, Store

SERVER = r'''
import json, os, sys, time
mode=os.environ.get('FACTORY_FAKE_MODE','ok')
for line in sys.stdin:
    msg=json.loads(line);method=msg.get('method');rid=msg.get('id')
    if rid is None:continue
    result={}
    if method=='account/read':result={'account':{'type':'chatgpt'}}
    if method=='initialize' and mode=='init-error':
        print(json.dumps({'id':rid,'error':{'code':-1,'message':'initialization rejected'}}),flush=True);continue
    if method in ('thread/start','thread/resume'):result={'thread':{'id':'thread-fixture'},'model':'fake-model'}
    if method=='turn/start':result={'turn':{'id':'turn-fixture'}}
    if method=='turn/start' and mode=='early':
        print(json.dumps({'method':'item/completed','params':{'item':{'type':'agentMessage','text':json.dumps({'questions':[],'enough':True,'note':'已完成'})}}}),flush=True)
        print(json.dumps({'method':'turn/completed','params':{'turn':{'id':'turn-fixture','status':'completed'}}}),flush=True)
    print(json.dumps({'id':rid,'result':result}),flush=True)
    if method=='turn/start':
        if mode=='early':continue
        if mode=='exit':sys.exit(0)
        if mode=='timeout':time.sleep(10)
        if mode=='tool':
            print(json.dumps({'id':90,'method':'item/commandExecution/requestApproval','params':{}}),flush=True);continue
        if os.environ.get('OPENAI_API_KEY'):sys.exit(1)
        reply={'questions':[],'enough':True,'note':'信息足够'}
        content='not JSON' if mode=='invalid' else json.dumps(reply,ensure_ascii=False)
        for value in [
            {'method':'item/agentMessage/delta','params':{'delta':'text'}},
            {'method':'thread/tokenUsage/updated','params':{'tokenUsage':{'last':{'inputTokens':90,'outputTokens':10}}}},
            {'method':'item/completed','params':{'item':{'type':'agentMessage','text':content}}},
            {'method':'turn/completed','params':{'turn':{'status':'completed'}}}
        ]:print(json.dumps(value),flush=True)
'''


@pytest.fixture
def runner(tmp_path):
    fake=tmp_path/'fake-codex'
    fake.write_text('#!'+sys.executable+'\n'+SERVER)
    fake.chmod(0o700)
    settings=Settings(data_dir=str(tmp_path/'data'),codex_bin=str(fake),model_timeout=3)
    store=Store(settings);store.create_all()
    yield settings,store
    store.engine.dispose()


def test_actual_stdio_transport_validates_and_accounts_without_api_key(runner,monkeypatch):
    settings,store=runner
    monkeypatch.setenv('OPENAI_API_KEY','fake-for-test')
    reply,meta=CodexGateway(settings,store).complete('interview',{},InterviewReply)
    assert reply.enough and meta['tokens_in']==90 and meta['tokens_out']==10
    assert meta['request_id']=='turn-fixture' and meta['model']=='fake-model'
    with store.Session() as s:assert s.scalar(select(Call)).error==''


@pytest.mark.parametrize('mode,expected',[('init-error','CODEX_RPC'),('exit','CODEX_EXITED'),('tool','CODEX_TOOL_BLOCKED'),('timeout','MODEL_TIMEOUT')])
def test_initialization_exit_tool_and_timeout_fail_safely(runner,monkeypatch,mode,expected):
    settings,store=runner
    settings.model_timeout=.5 if mode=='timeout' else 3
    monkeypatch.setenv('FACTORY_FAKE_MODE',mode)
    with pytest.raises(Problem) as e:CodexGateway(settings,store).complete('interview',{},InterviewReply)
    assert e.value.code==expected
    with store.Session() as s:assert s.scalar(select(Call)).error==expected


def test_invalid_output_has_finite_retries_and_no_false_success(runner,monkeypatch):
    settings,store=runner
    monkeypatch.setenv('FACTORY_FAKE_MODE','invalid')
    with pytest.raises(Problem) as e:CodexGateway(settings,store).complete('interview',{},InterviewReply)
    assert e.value.code=='SCHEMA_INVALID'
    with store.Session() as s:assert s.scalar(select(func.count()).select_from(Call))==2


def test_call_limit_stops_before_process_launch(runner):
    settings,store=runner;settings.max_calls=0
    with pytest.raises(Problem) as e:CodexGateway(settings,store).complete('interview',{},InterviewReply)
    assert e.value.code=='CALL_LIMIT'


def test_missing_binary_returns_actionable_error(runner):
    settings,store=runner;settings.codex_bin='/no/such/codex'
    with pytest.raises(Problem) as e:CodexGateway(settings,store).complete('interview',{},InterviewReply)
    assert e.value.code=='CODEX_NOT_INSTALLED'


def test_cancelled_job_stops_transport(runner):
    settings,store=runner
    token=JOB_CONTEXT.set({'interview_id':None,'job_id':'cancel-test','cancelled':lambda:True})
    try:
        with pytest.raises(Problem) as e:CodexGateway(settings,store).complete('interview',{},InterviewReply)
        assert e.value.code=='CANCELLED'
    finally:JOB_CONTEXT.reset(token)


def test_config_disables_tools_and_structured_schema_has_no_optional_object_keys(runner):
    settings,_=runner;args=command(settings)
    assert 'features.shell_tool=false' in args and 'features.code_mode=false' in args
    assert 'features.apps=false' in args and 'features.plugins=false' in args
    s=strict_schema(InterviewReply)
    assert set(s['required'])==set(s['properties']) and s['additionalProperties'] is False


def test_turn_events_arriving_before_rpc_ack_are_not_lost(runner,monkeypatch):
    settings,store=runner
    monkeypatch.setenv('FACTORY_FAKE_MODE','early')
    reply,_=CodexGateway(settings,store).complete('interview',{},InterviewReply)
    assert reply.enough and reply.note=='已完成'


def test_missing_toolkit_fails_closed(tmp_path):
    from app.toolkit import toolkit_context
    with pytest.raises(Problem) as e:toolkit_context(tmp_path)
    assert e.value.code=='TOOLKIT_INCOMPLETE'


def test_development_context_is_small_and_still_contains_engineering_boundaries():
    from app.toolkit import toolkit_context
    old=toolkit_context(phase='technical');coding=toolkit_context(phase='development')
    assert len(coding)<len(old)//2
    assert '强制底线' in coding and '工程底线' in coding and '密钥' in coding
    assert 'PRD模板.md' not in coding and '启动流程' not in coding


def test_explicit_jobs_have_separate_call_budgets_and_history_is_not_balance(runner):
    settings,store=runner;settings.max_calls=1
    for _ in range(3):
        store.record_call(prompt='historical-fixture',model='fake',tokens_in=0,tokens_out=0,
            cost_rmb=0,duration_ms=1,retry_count=0,error='')
    for job_id in ('first-explicit-job','second-explicit-job'):
        token=JOB_CONTEXT.set({'job_id':job_id,'interview_id':None,'cancelled':lambda:False})
        try:
            assert CodexGateway(settings,store).complete('interview',{},InterviewReply)[0].enough
            with pytest.raises(Problem) as error:CodexGateway(settings,store).complete('interview',{},InterviewReply)
            assert error.value.code=='CALL_LIMIT'
            assert sum(e.kind=='model_attempt' for e in store.events(job_id))==1
        finally:JOB_CONTEXT.reset(token)


def test_schema_repair_cannot_bypass_current_job_call_budget(runner,monkeypatch):
    settings,store=runner;settings.max_calls=1
    monkeypatch.setenv('FACTORY_FAKE_MODE','invalid')
    token=JOB_CONTEXT.set({'job_id':'bounded-repair','interview_id':None,'cancelled':lambda:False})
    try:
        with pytest.raises(Problem) as error:CodexGateway(settings,store).complete('interview',{},InterviewReply)
        assert error.value.code=='CALL_LIMIT'
        assert sum(e.kind=='model_attempt' for e in store.events('bounded-repair'))==1
    finally:JOB_CONTEXT.reset(token)


def test_coding_repair_has_full_budget_and_only_safe_schema_diagnostics(runner,monkeypatch):
    import json
    from app.schemas import CodeBundle
    settings,store=runner;settings.development_timeout=300;settings.model_timeout=3
    clock=[0.0];deadlines=[];turns=[]
    monkeypatch.setattr('app.codex.time.monotonic',lambda:clock[0])
    class FakeRpc:
        def __init__(self,settings,deadline):
            self.number=len(deadlines);deadlines.append(deadline);self.i=0
        def request(self,method,params):
            if method=='account/read':return {'account':{'type':'chatgpt'}}
            if method=='thread/start':return {'thread':{'id':'test-thread'},'model':'fake-model'}
            if method=='turn/start':turns.append(params);return {'turn':{'id':'test-turn'}}
            return {}
        def send(self,message):pass
        def close(self):pass
        def receive(self):
            self.i+=1
            if self.i==1:
                clock[0]=250.0 if self.number==0 else 251.0
                value={'summary':'PRIVATE-CODE-'*100,'changes':[],'questions':[]} if self.number==0 else {
                    'summary':'已生成','changes':[{'path':'backend/x.py','before_sha256':'new','content':'X = 1\n'}],'questions':[],'task_complete':True}
                return {'method':'item/completed','params':{'item':{'type':'agentMessage','text':json.dumps(value)}}}
            return {'method':'turn/completed','params':{'turn':{'status':'completed'}}}
    monkeypatch.setattr('app.codex.Rpc',FakeRpc)
    result,_=CodexGateway(settings,store).complete('development_code',{},CodeBundle)
    assert result.task_complete and deadlines==[300.0,550.0]
    repaired=turns[1]['input'][0]['text']
    assert '上次输出的结构错误' in repaired and 'PRIVATE-CODE' not in repaired
    with store.Session() as s:
        calls=list(s.scalars(select(Call).order_by(Call.id)))
    assert [call.error for call in calls]==['SCHEMA_INVALID','']


def test_system_proxy_is_inherited_only_by_codex_child(monkeypatch):
    monkeypatch.setattr('app.codex.getproxies',lambda:{'http':'http://127.0.0.1:7897','https':'http://127.0.0.1:7897'})
    source={'PATH':'/usr/bin','CODEX_THREAD_ID':'parent','OPENAI_API_KEY':'fake-for-test'}
    env=codex_environment(Settings(),source)
    assert env['HTTP_PROXY']==env['HTTPS_PROXY']=='http://127.0.0.1:7897'
    assert 'localhost' in env['NO_PROXY'] and '127.0.0.1' in env['NO_PROXY']
    assert 'OPENAI_API_KEY' not in env and 'CODEX_THREAD_ID' not in env
    assert 'HTTPS_PROXY' not in source


@pytest.mark.parametrize('name',['HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy'])
def test_explicit_proxy_takes_precedence_over_system_proxy(monkeypatch,name):
    monkeypatch.setattr('app.codex.getproxies',lambda:{'https':'http://system:7897'})
    env=codex_environment(Settings(),{name:'http://explicit:9000','no_proxy':'internal.example'})
    assert env[name]=='http://explicit:9000'
    if name!='HTTPS_PROXY':assert 'HTTPS_PROXY' not in env
    assert env['no_proxy']==env['NO_PROXY'] and 'internal.example' in env['NO_PROXY']


def test_system_proxy_inheritance_can_be_disabled(monkeypatch):
    monkeypatch.setattr('app.codex.getproxies',lambda:{'https':'http://system:7897'})
    assert 'HTTPS_PROXY' not in codex_environment(Settings(codex_use_system_proxy=False),{})


@pytest.mark.parametrize('model',[DraftPRD,FinalPRD,AuditResult])
def test_business_field_named_default_survives_strict_schema_conversion(model):
    schema=strict_schema(model)
    definition=schema['$defs']['DefaultItem' if model is AuditResult else 'Assumption']
    assert 'default' in definition['properties']
    assert set(definition['required'])==set(definition['properties'])
    assert 'default' not in strict_schema(InterviewReply)['properties']['enough']


@pytest.mark.parametrize('info,message,code',[
    ('badRequest','Invalid schema for response_format','CODEX_SCHEMA_REJECTED'),
    ('other','{"error":{"code":"invalid_json_schema","message":"Invalid schema: default missing"}}','CODEX_SCHEMA_REJECTED'),
    ('usageLimitExceeded','limit reached','CODEX_USAGE_LIMIT'),
    ('unauthorized','invalid credentials','CODEX_AUTH'),
    ({'httpConnectionFailed':{'httpStatusCode':503}},'upstream failure','CODEX_CONNECTION'),
])
def test_upstream_error_categories_remain_actionable_and_hide_raw_details(info,message,code):
    p=codex_problem({'codexErrorInfo':info,'message':message+' private-input'})
    assert p.code==code and 'private-input' not in p.message
