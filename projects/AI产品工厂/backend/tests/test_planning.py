from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import Problem, Settings
from app.schemas import DevelopmentContent, TechnicalContent
from app.store import Confirmation, Store, TechnicalProposal
from conftest import answer_all, create, to_auditing, wait_job

TECH = {'summary':'本机单人读书摘录整理工具，文本输入后生成可核对的Markdown草稿。',
        'default_stack':['Python3.12 + FastAPI','SQLite'],
        'modules':[{'name':'SQLite','choice':'采用','reason':'保存任务便于恢复','location':'backend/app/store.py'}],
        'cost_impact':'复用现有账号额度，不新增收费资源，实际额度由用户核对。',
        'deferred':['云部署'], 'verification':['测试重启后数据读回一致'], 'questions':[],
        'phases':[{'stage':'阶段2-1','goal':'实现核心整理链路','deliverables':['后端与测试'],'acceptance':'真实文本完整走通'},
                  {'stage':'阶段3-1','goal':'正式界面','deliverables':['前端页面'],'acceptance':'桌面与移动操作通过'}]}
TASKS = {'note':'待执行任务，尚未编写代码。', 'tasks':[
    {'id':'T1','phase':'阶段2-1','title':'任务持久化','goal':'重启可恢复','inputs':['已确认PRD'],
     'deliverables':['任务模型'],'checks':['重启按ID读回'],'confirmations':[],'depends_on':[]},
    {'id':'T2','phase':'阶段2-1','title':'整理API','goal':'返回可核对草稿','inputs':['T1任务记录'],
     'deliverables':['整理接口'],'checks':['真实文本结构校验'],'confirmations':['真实模型调用额度'],'depends_on':['T1']}]}


@pytest.fixture
def planning(env):
    c,a,p=env
    gateway=a.state.workflow.gateway
    original=gateway.complete
    gateway.technical=deepcopy(TECH)
    gateway.task_content=deepcopy(TASKS)
    def complete(name,payload,schema,**kwargs):
        if name in ('technical_proposal','development_tasks'):
            gateway.calls.append(name)
            if gateway.fail:raise Problem('MODEL_FAILED','模拟失败',502)
            raw=gateway.technical if name=='technical_proposal' else gateway.task_content
            return schema.model_validate(raw),{'model':'fake-for-test'}
        return original(name,payload,schema,**kwargs)
    gateway.complete=complete
    return c,a,p


def closed(c):
    iv=create(c);to_auditing(c,iv);answer_all(c,iv)
    result=wait_job(c,c.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
    assert result['status']=='succeeded'
    return iv


def generate(c,iv):
    job=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical'))
    assert job['status']=='succeeded',job
    return c.get(f'/api/v1/interviews/{iv}/workbench').json()


def confirmation(w):
    p=w['proposal']
    return {'accepted':True,'proposal_version':p['version'],'source_digest':p['source_digest'],
            'proposal_digest':p['proposal_digest']}


def test_full_planning_chain_versions_confirmations_files_and_restart(planning):
    c,a,p=planning;iv=closed(c);w=generate(c,iv)
    assert w['state']=='technical_review' and w['proposal']['version']==1
    assert '## 7. 子阶段规划' in w['proposal']['markdown']
    old=confirmation(w)
    assert wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/revise',json={'content':'暂缓云部署，保留本机'}))['status']=='succeeded'
    w=c.get(f'/api/v1/interviews/{iv}/workbench').json()
    assert w['proposal']['version']==2
    assert c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=old).status_code==409
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=confirmation(w)))
    assert j['status']=='succeeded',j
    w=c.get(f'/api/v1/interviews/{iv}/workbench').json()
    assert w['state']=='tasks_ready' and len(w['development_plan']['content']['tasks'])==2
    assert all(Path(file).is_file() for file in j['result']['written_paths'])
    assert not (p/'projects/测试产品/backend').exists()  # Planning must not pretend it executed code.
    second=Store(a.state.settings)
    assert second.proposal(iv).version==2
    assert second.technical_approved(second.proposal(iv))
    assert second.development_plan(second.proposal(iv).id).data['tasks'][1]['depends_on']==['T1']
    second.engine.dispose()


def test_unfinished_prd_cannot_generate_technical_plan(planning):
    c,a,_=planning;iv=create(c)
    assert c.get(f'/api/v1/interviews/{iv}/workbench').json()['state']=='prd_pending'
    assert c.post(f'/api/v1/interviews/{iv}/technical').status_code==409
    assert not a.state.workflow.gateway.calls


def test_source_edit_invalidates_confirmed_proposal_and_tasks(planning):
    c,a,_=planning;iv=closed(c);w=generate(c,iv);body=confirmation(w)
    assert wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=body))['status']=='succeeded'
    asset=a.state.store.asset(iv,'export_plan');prd=next(Path(p) for p in asset.data['files'] if p.endswith('PRD.md'))
    prd.write_text(prd.read_text()+'\n用户追加范围\n')
    w=c.get(f'/api/v1/interviews/{iv}/workbench').json()
    assert w['state']=='source_changed' and w['development_plan'] is None
    assert c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=body).status_code==409
    assert generate(c,iv)['proposal']['version']==2


def test_pending_questions_prevent_confirmation(planning):
    c,a,_=planning;iv=closed(c)
    a.state.workflow.gateway.technical['questions']=[{'question':'需要付费服务吗','why':'影响费用','options':['不用','使用'],
       'recommendation':'先不用','consequence':'选择使用需要确认费用'}]
    w=generate(c,iv)
    r=c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=confirmation(w))
    assert r.status_code==409 and r.json()['error']['code']=='QUESTIONS_PENDING'


def test_target_status_and_original_documents_are_read_only(planning):
    c,a,p=planning;iv=closed(c);docs=p/'projects/测试产品/docs'
    (docs/'流程状态.txt').write_text('阶段=阶段0\n状态=等验收\n')
    (docs/'本阶段任务.md').write_text('原管家任务')
    (docs/'阶段文档/技术适配声明.md').write_text('人工技术方案')
    before={f:f.read_bytes() for f in docs.rglob('*') if f.is_file()}
    w=generate(c,iv)
    assert w['target']['kit_stage']=='阶段0' and w['target']['current_task']=='原管家任务'
    wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=confirmation(w)))
    assert all(f.read_bytes()==text for f,text in before.items())


def test_repeated_confirm_reuses_saved_tasks_without_new_model_call(planning):
    c,a,_=planning;iv=closed(c);w=generate(c,iv);body=confirmation(w)
    first=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=body))
    count=a.state.workflow.gateway.calls.count('development_tasks')
    second=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=body))
    assert second['status']=='succeeded' and second['result']==first['result']
    assert a.state.workflow.gateway.calls.count('development_tasks')==count


def test_failed_model_does_not_create_partial_proposal(planning):
    c,a,_=planning;iv=closed(c);a.state.workflow.gateway.fail=True
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical'))
    assert j['status']=='failed' and a.state.store.proposal(iv) is None
    assert a.state.store.get_interview(iv).status=='closed'


def test_task_generation_failure_keeps_exact_version_approval(planning):
    c,a,_=planning;iv=closed(c);w=generate(c,iv);a.state.workflow.gateway.fail=True
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=confirmation(w)))
    assert j['status']=='failed'
    assert c.get(f'/api/v1/interviews/{iv}/workbench').json()['state']=='technical_approved'
    a.state.workflow.gateway.fail=False
    assert wait_job(c,c.post('/api/v1/jobs/'+j['id']+'/retry'))['status']=='succeeded'


def test_unapproved_or_wrong_phase_tasks_cannot_be_saved(planning):
    c,a,_=planning;iv=closed(c);w=generate(c,iv)
    a.state.workflow.gateway.task_content['tasks'][0]['phase']='阶段3-1'
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=confirmation(w)))
    assert j['error']['code']=='PLAN_INVALID'
    assert a.state.store.development_plan(a.state.store.proposal(iv).id) is None


@pytest.mark.parametrize('mutation',['missing','symlink','outside'])
def test_invalid_source_is_rejected_without_model_calls(planning,mutation):
    c,a,p=planning;iv=closed(c)
    asset=a.state.store.asset(iv,'export_plan');prd=next(Path(v) for v in asset.data['files'] if v.endswith('PRD.md'))
    if mutation=='missing':prd.rename(prd.with_suffix('.backup'))
    elif mutation=='symlink':
        prd.rename(prd.with_suffix('.backup'));prd.symlink_to(p/'outside.md')
    else:
        from app.store import Asset
        a.state.store.add(Asset(interview_id=iv,kind='export_plan',data={'files':{str(p/'PRD/PRD.md'):'x',str(p/'项目状态.md'):'y'}}))
    before=len(a.state.workflow.gateway.calls)
    assert c.post(f'/api/v1/interviews/{iv}/technical').status_code in (409,422)
    assert len(a.state.workflow.gateway.calls)==before


@pytest.mark.parametrize('dependency',[['T2'],['missing'],['T1']])
def test_task_dependencies_cannot_reference_self_future_or_unknown(dependency):
    data=deepcopy(TASKS);data['tasks'][0]['depends_on']=dependency
    with pytest.raises(ValueError):DevelopmentContent.model_validate(data)


def test_same_stage_name_cannot_repeat():
    data=deepcopy(TECH);data['phases'][1]['stage']='阶段2-1'
    with pytest.raises(ValueError):TechnicalContent.model_validate(data)


def test_migration_preserves_existing_interview_and_adds_planning_tables(tmp_path):
    import sqlite3
    p=tmp_path/'data';p.mkdir()
    settings=Settings(data_dir=str(p));db=Store(settings);db.create_all()
    from app.store import Interview
    iv=Interview(product_name='保留原数据',idea_text='原内容');db.add(iv)
    from sqlalchemy import text
    from app.store import DevelopmentPlan
    # Simulate the already-installed 0001 database, with no planning tables.
    DevelopmentPlan.__table__.drop(db.engine)
    TechnicalProposal.__table__.drop(db.engine)
    with db.engine.begin() as connection:
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0001_factory')"))
    db.migrate()
    assert db.get_interview(iv.id).idea_text=='原内容'
    assert db.proposal(iv.id) is None
    db.engine.dispose()


def test_source_changed_during_model_generation_cannot_save_proposal(planning):
    c,a,_=planning;iv=closed(c);original=a.state.workflow.gateway.complete
    def changing(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[0]=='technical_proposal':
            files=a.state.store.asset(iv,'export_plan').data['files']
            prd=next(Path(p) for p in files if p.endswith('PRD.md'))
            prd.write_text(prd.read_text()+'\n处理中修改\n')
        return result
    a.state.workflow.gateway.complete=changing
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical'))
    assert j['error']['code']=='SOURCE_CHANGED' and a.state.store.proposal(iv) is None


def test_conflicting_export_never_overwrites_and_retry_reuses_model_result(planning):
    c,a,_=planning;iv=closed(c);w=generate(c,iv)
    path=Path(w['proposal']['paths'][0]);original=path.read_text();path.write_text('人工变更')
    j=wait_job(c,c.post(f'/api/v1/interviews/{iv}/technical/confirm',json=confirmation(w)))
    assert j['error']['code']=='FILE_CONFLICT' and path.read_text()=='人工变更'
    count=a.state.workflow.gateway.calls.count('development_tasks')
    path.write_text(original)
    assert wait_job(c,c.post('/api/v1/jobs/'+j['id']+'/retry'))['status']=='succeeded'
    assert a.state.workflow.gateway.calls.count('development_tasks')==count
