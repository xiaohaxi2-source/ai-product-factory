from pathlib import Path

import pytest
from sqlalchemy import select
from app.core import Problem
from app.schemas import DraftPRD, InterviewReply
from app.store import Confirmation, Interview, Job, Store
from conftest import PRD, answer_all, create, to_auditing, wait_job


def test_complete_chain_persists_and_exports_decisions(env):
    c,a,p=env;iv=create(c)
    assert wait_job(c,c.post(f'/api/v1/interviews/{iv}/messages',json={'content':'按推荐'}))['status']=='succeeded'
    to_auditing(c,iv)
    rows=c.get(f'/api/v1/interviews/{iv}').json()['checklist']
    assert len(rows)==8  # All 6 must asks, a conflict, and draft assumption retained.
    assert c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}).status_code==409
    answer_all(c,iv)
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
    assert j['status']=='succeeded',j
    assert len(j['result']['written_paths'])==3
    assert c.get(f'/api/v1/interviews/{iv}').json()['written_paths']==j['result']['written_paths']
    root=p/'projects/测试产品'
    assert (root/'docs/PRD/PRD.md').read_text().strip()==PRD
    assert 'D9' in (root/'docs/项目状态.md').read_text()
    assert '用户裁决' in (root/'docs/阶段文档/阶段0-PRD补全清单.md').read_text()
    with a.state.store.Session() as s:
        assert len(list(s.scalars(select(Confirmation))))==10


@pytest.mark.parametrize('name',['..','.hidden','a/b','a\\b','bad name','a|b','a\nnext'])
def test_reject_unsafe_product_names(env,name):
    c,_,_=env
    r=c.post('/api/v1/interviews',data={'product_name':name,'idea':'测试'})
    assert r.status_code==422 and set(r.json())=={'error'}


def test_no_model_work_on_empty_input(env):
    c,a,_=env
    assert c.post('/api/v1/interviews',data={'product_name':'产品'}).status_code==422
    assert not a.state.workflow.gateway.calls


def test_failed_message_does_not_duplicate_user_history(env):
    c,a,_=env;iv=create(c);a.state.workflow.gateway.fail=True
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/messages',json={'content':'test'}))
    assert j['status']=='failed'
    d=c.get(f'/api/v1/interviews/{iv}').json()
    assert d['interview']['status']=='collecting' and len(d['messages'])==1
    a.state.workflow.gateway.fail=False
    assert wait_job(c,c.post('/api/v1/jobs/'+j['id']+'/retry'))['status']=='succeeded'
    assert len(c.get(f'/api/v1/interviews/{iv}').json()['messages'])==3


def test_confirm_requires_explicit_current_version(env):
    c,_,_=env;iv=create(c)
    assert c.post(f'/api/v1/interviews/{iv}/confirm',json={'accepted':True,'draft_version':1}).status_code==409
    wait_job(c,c.post(f'/api/v1/interviews/{iv}/draft'))
    for body in ({'accepted':False,'draft_version':1},{'accepted':True,'draft_version':2}):
        assert c.post(f'/api/v1/interviews/{iv}/confirm',json=body).status_code==409


def test_existing_product_documents_are_never_overwritten(env):
    c,a,p=env;root=p/'projects/已有产品/docs';(root/'PRD').mkdir(parents=True)
    (root/'PRD/PRD.md').write_text('原始人工PRD');(root/'项目状态.md').write_text('原始台账')
    iv=create(c,'已有产品');to_auditing(c,iv);answer_all(c,iv)
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
    assert j['status']=='succeeded',j
    assert all('工厂提案' in f for f in j['result']['written_paths'])
    assert (root/'PRD/PRD.md').read_text()=='原始人工PRD'
    assert (root/'项目状态.md').read_text()=='原始台账'


def test_symlink_cannot_escape_projects(env,tmp_path):
    c,_,p=env;projects=p/'projects';projects.mkdir();outside=p/'outside';outside.mkdir()
    (projects/'逃逸').symlink_to(outside,target_is_directory=True)
    assert c.post('/api/v1/interviews',data={'product_name':'逃逸','idea':'test'}).status_code==422
    assert not list(outside.iterdir())


def test_nested_docs_symlink_blocks_export(env):
    c,_,p=env;iv=create(c);to_auditing(c,iv);answer_all(c,iv)
    root=p/'projects/测试产品';root.mkdir(parents=True);outside=p/'outside';outside.mkdir()
    (root/'docs').symlink_to(outside,target_is_directory=True)
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
    assert j['error']['code']=='PATH_INVALID'
    assert not list(outside.iterdir())


def test_recovery_keeps_data_and_marks_inflight_interrupted(env):
    c,a,_=env;iv=create(c)
    a.state.store.add(Job(id='inflight',interview_id=iv,action='message',status='running',payload={'content':'继续'}))
    a.state.jobs.recover()
    assert a.state.store.get_job('inflight').status=='interrupted'
    second=Store(a.state.settings)
    assert second.get_interview(iv).idea_text=='整理读书摘录的小工具'
    second.engine.dispose()


def test_owned_records_not_visible_as_local(env):
    c,a,_=env
    other=Interview(owner_id='someone-else',product_name='其他用户',idea_text='private')
    a.state.store.add(other)
    assert c.get(f'/api/v1/interviews/{other.id}').status_code==404
    assert c.get('/api/v1/interviews').json()==[]


def test_sse_success_and_failure_have_terminal_events(env):
    c,a,_=env;iv=create(c)
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/messages',json={'content':'继续'}))
    stream=c.get('/api/v1/jobs/'+j['id']+'/events').text
    assert stream.count('event: done')==1 and 'event: error' not in stream
    a.state.workflow.gateway.fail=True
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/messages',json={'content':'继续'}))
    stream=c.get('/api/v1/jobs/'+j['id']+'/events').text
    assert stream.count('event: error')==1 and 'MODEL_FAILED' in stream


@pytest.mark.parametrize('path,headers',[('/api/v1/interviews',{'Origin':'https://evil.example'}),('/api/v1/interviews',{'Host':'evil.example'}),('/api/v1/interviews',{'Sec-Fetch-Site':'cross-site'})])
def test_local_api_rejects_cross_site_writes(env,path,headers):
    c,_,_=env
    assert c.post(path,headers=headers,data={'product_name':'x','idea':'test'}).status_code==403


def test_invalid_request_and_not_found_errors_are_uniform(env):
    c,_,_=env
    assert set(c.get('/api/v1/missing').json())=={'error'}
    assert set(c.post('/api/v1/interviews/1/messages',json={'content':''}).json())=={'error'}


def test_model_schema_rejects_missing_sections_and_inconsistent_reply():
    with pytest.raises(ValueError):DraftPRD(prd_markdown='wrong '+('x'*100))
    with pytest.raises(ValueError):InterviewReply(questions=[],enough=False)


def test_confirmed_answers_can_be_corrected_before_export(env):
    c,_,_=env;iv=create(c);to_auditing(c,iv)
    item=c.get(f'/api/v1/interviews/{iv}').json()['checklist'][0]
    endpoint=f'/api/v1/checklist-items/{item["id"]}/answer'
    assert c.post(endpoint,json={'content':'A'}).status_code==200
    assert c.post(endpoint,json={'content':'B，修改选择'}).json()['answer']=='B，修改选择'


def test_revision_retains_old_version_and_rejects_stale_confirmation(env):
    c,a,_=env;iv=create(c)
    wait_job(c,c.post(f'/api/v1/interviews/{iv}/draft'))
    job=wait_job(c,c.post(f'/api/v1/interviews/{iv}/revise',json={'content':'第一版仅保留文本输入'}))
    assert job['status']=='succeeded' and job['result']['version']==2
    assert c.post(f'/api/v1/interviews/{iv}/confirm',json={'accepted':True,'draft_version':1}).status_code==409
    from app.store import PrdDraft
    with a.state.store.Session() as s:
        assert len(list(s.scalars(select(PrdDraft).where(PrdDraft.interview_id==iv))))==2


def test_single_active_job_per_interview(env):
    import threading
    c,a,_=env;iv=create(c)
    entered,release=threading.Event(),threading.Event()
    original=a.state.workflow.gateway.complete
    def slow(*args,**kwargs):
        entered.set();release.wait(3)
        return original(*args,**kwargs)
    a.state.workflow.gateway.complete=slow
    first=c.post(f'/api/v1/interviews/{iv}/messages',json={'content':'继续'})
    assert entered.wait(2)
    try:
        assert c.post(f'/api/v1/interviews/{iv}/draft').status_code==409
        assert c.post(f'/api/v1/interviews/{iv}/messages',json={'content':'重复点击'}).status_code==409
    finally:release.set()
    assert wait_job(c,first)['status']=='succeeded'


def test_recovery_retry_refuses_to_repeat_already_committed_business_transition(env):
    c,a,_=env;iv=create(c)
    wait_job(c,c.post(f'/api/v1/interviews/{iv}/draft'))
    a.state.store.add(Job(id='crashed-after-commit',interview_id=iv,action='draft',status='interrupted'))
    assert c.post('/api/v1/jobs/crashed-after-commit/retry').status_code==409
    assert a.state.store.latest_draft(iv).version==1


def test_pending_decision_identifies_missing_value_without_closing_or_exporting(env):
    from conftest import DECISIONS
    from copy import deepcopy
    c,a,p=env;iv=create(c);to_auditing(c,iv);answer_all(c,iv)
    original=a.state.workflow.gateway.complete
    def pending(name,payload,schema,**kw):
        if name!='finalize_prd':return original(name,payload,schema,**kw)
        decisions=deepcopy(DECISIONS)
        decisions[3].update(topic='模型厂商与成本上限',status='待定',conclusion='已选择不指定厂商，但未填写每月预算金额。')
        return schema.model_validate({'prd_markdown':PRD,'assumptions':[],'decisions':decisions}),{}
    a.state.workflow.gateway.complete=pending
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
    assert j['error']['code']=='DECISIONS_PENDING'
    assert 'D4' in j['error']['message'] and '每月预算金额' in j['error']['message']
    assert '修改答案' in j['error']['message']
    assert a.state.store.get_interview(iv).status=='auditing'
    assert a.state.store.asset(iv,'final_decision_review').data['pending'][0]['number']=='D4'
    assert a.state.store.asset(iv,'export_plan') is None


def test_inline_decision_answers_persist_override_and_export_without_rewriting_originals(env):
    from app.store import Asset, Confirmation
    from sqlalchemy import select
    c,a,p=env;iv=create(c);to_auditing(c,iv);answer_all(c,iv)
    original_answers=[i.answer for i in a.state.store.checklist(iv)]
    a.state.store.add(Asset(interview_id=iv,kind='final_decision_review',data={'pending':[
        {'number':'D4','topic':'模型成本','status':'待定','conclusion':'缺月度预算'}]}))
    review=c.get(f'/api/v1/interviews/{iv}').json()['pending_review']
    body={'review_id':review['review_id'],'answers':[{'number':'D4','content':'当前不启用付费调用，付费前另行确认预算。'}]}
    assert c.post(f'/api/v1/interviews/{iv}/decision-answers',json=body).status_code==200
    fresh=c.get(f'/api/v1/interviews/{iv}').json()['pending_review']
    assert fresh['decisions'][0]['answer']==body['answers'][0]['content']
    assert [i.answer for i in a.state.store.checklist(iv)]==original_answers
    with a.state.store.Session() as session:
        assert session.scalar(select(Confirmation).where(Confirmation.kind=='decision_answer')).data['number']=='D4'
    original=a.state.workflow.gateway.complete
    def final(name,payload,schema,**kw):
        if name=='finalize_prd':
            assert payload['decision_answers'][0]['content']==body['answers'][0]['content']
        return original(name,payload,schema,**kw)
    a.state.workflow.gateway.complete=final
    job=wait_job(c,c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
    assert job['status']=='succeeded',job
    from pathlib import Path
    checklist=next(Path(f) for f in job['result']['written_paths'] if f.endswith('阶段0-PRD补全清单.md'))
    assert body['answers'][0]['content'] in checklist.read_text()
    assert c.get(f'/api/v1/interviews/{iv}').json()['pending_review'] is None
    assert c.post(f'/api/v1/interviews/{iv}/decision-answers',json=body).status_code==409


def test_decision_answer_rejects_stale_review_unknown_number_and_blank_atomically(env):
    from app.store import Asset
    c,a,p=env;iv=create(c);to_auditing(c,iv);answer_all(c,iv)
    a.state.store.add(Asset(interview_id=iv,kind='final_decision_review',data={'pending':[
        {'number':'D4','topic':'成本','status':'待定','conclusion':'缺预算'}]}))
    rid=c.get(f'/api/v1/interviews/{iv}').json()['pending_review']['review_id']
    for answers, review_id, status in [
        ([{'number':'D4','content':'预算由用户定'}],rid+1,409),
        ([{'number':'D4','content':'预算由用户定'},{'number':'D9','content':'未询问'}],rid,422),
        ([{'number':'D4','content':'   '}],rid,422),
        ([{'number':'D4','content':'a'},{'number':'D4','content':'b'}],rid,422),
    ]:
        assert c.post(f'/api/v1/interviews/{iv}/decision-answers',json={'review_id':review_id,'answers':answers}).status_code==status
    assert a.state.workflow.decision_answers(iv)==[]


def test_decision_history_survives_followup_review_and_restart_preserves_order_and_owner(env):
    from app.store import Asset
    c,a,p=env;iv=create(c);to_auditing(c,iv);answer_all(c,iv)
    decision={'number':'D4','topic':'模型成本','status':'待定','conclusion':'缺月度预算'}
    a.state.store.add(Asset(interview_id=iv,kind='final_decision_review',data={'pending':[decision]}))
    def save(content):
        review=c.get(f'/api/v1/interviews/{iv}').json()['pending_review']
        r=c.post(f'/api/v1/interviews/{iv}/decision-answers',json={'review_id':review['review_id'],
            'answers':[{'number':'D4','content':content}]})
        assert r.status_code==200,r.text
        return r.json()['decisions'][0]
    save('上限50元')
    # A later review asks only for the still-missing policy, not the entire decision again.
    a.state.store.add(Asset(interview_id=iv,kind='final_decision_review',data={'pending':[dict(decision,conclusion='缺超额处理方式')]}))
    save('达到上限暂停新任务')
    saved=save('达到上限暂停新任务')
    assert saved['history']==['上限50元','达到上限暂停新任务']
    assert saved['answer']=='达到上限暂停新任务'
    a.state.store.add(Asset(interview_id=iv,owner_id='another-owner',kind='decision_answer_D4',
        data={'number':'D4','topic':'模型成本','content':'不属于当前用户'}))
    restored=Store(a.state.settings)
    from app.workflow import Workflow
    reopened=Workflow(a.state.workflow.gateway,restored,p/'projects')
    assert reopened.decision_answers(iv)[0]['history']==['上限50元','达到上限暂停新任务']
    assert save('改成每月80元')['history']==['上限50元','达到上限暂停新任务','改成每月80元']
    assert save('改回每月50元')['history'][-2:]==['改成每月80元','改回每月50元']
    expected=['上限50元','达到上限暂停新任务','改成每月80元','改回每月50元']
    original=a.state.workflow.gateway.complete
    def final(name,payload,schema,**kw):
        if name=='finalize_prd':assert payload['decision_answers'][0]['history']==expected
        return original(name,payload,schema,**kw)
    a.state.workflow.gateway.complete=final
    job=wait_job(c,c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
    assert job['status']=='succeeded',job
    exported=next(Path(path) for path in job['result']['written_paths'] if path.endswith('阶段0-PRD补全清单.md')).read_text()
    for content in expected:assert content in exported
    restored.engine.dispose()
