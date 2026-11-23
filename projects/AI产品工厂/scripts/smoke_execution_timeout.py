"""Replay the user's stalled task against copied inputs; never mutate the live execution."""
import copy
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT/'backend'))
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.core import KIT_ROOT, Settings, get_settings
from app.execution import Executions
from app.main import create_app
from app.planning import Planning, digest
from app.store import Asset, Call, Confirmation, DevelopmentPlan, Interview, Store, TechnicalProposal
from app.workflow import Workflow
from smoke import record


def main():
    original = Store(get_settings())
    workflow = Workflow(None, original, KIT_ROOT/'projects')
    planning = Planning(workflow)
    stalled = original.latest_execution(2, original.proposal(2).id)
    task = next(copy.deepcopy(t) for t in original.development_plan(stalled.proposal_id).data['tasks'] if t['id']=='T3')
    files = Executions(planning).current_files(stalled)
    source = planning.source(stalled.interview_id)
    tech = copy.deepcopy(original.proposal(stalled.interview_id).data)
    original.engine.dispose()
    root = PRODUCT/'.runtime'/('timeout-replay-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    app = create_app(Settings(data_dir=str(root/'data'),projects_dir=str(root/'projects')),
        on_call=lambda name,meta,error: record(name,meta,error,stage='阶段2-3'))
    iv = Interview(product_name='超时任务隔离回放',status='closed',idea_text='复制用户已确认输入及T2代码，仅验证卡住的T3；不代表人工验收')
    app.state.store.add(iv)
    target=root/'projects'/iv.product_name
    for relative,text in files.items():
        path=target/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    inputs={str(target/'docs/PRD/PRD.md'):source['prd'],str(target/'docs/项目状态.md'):source['decision_ledger']}
    for name,text in inputs.items():
        path=Path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    app.state.store.add(Asset(interview_id=iv.id,kind='export_plan',data={'files':inputs}))
    proposal=TechnicalProposal(interview_id=iv.id,version=1,data=tech,proposal_digest=digest(tech),
        source_digest=app.state.jobs.planning.source(iv.id)['source_digest'])
    app.state.store.add(proposal)
    app.state.store.add(Confirmation(interview_id=iv.id,kind='technical_approved',data={
        'proposal_id':proposal.id,'proposal_digest':proposal.proposal_digest,'source_digest':proposal.source_digest}))
    # T1/T2 are already present in the copied source; only T3 is executed in this regression.
    task['depends_on']=[]
    plan={'note':'实际T3隔离回放；前置T1/T2代码已复制，非正式用户执行。','tasks':[task]}
    app.state.store.add(DevelopmentPlan(interview_id=iv.id,proposal_id=proposal.id,data=plan))
    with TestClient(app) as client:
        response=client.post(f'/api/v1/interviews/{iv.id}/execute',json={
            'accepted':True,'proposal_version':1,'source_digest':proposal.source_digest,
            'proposal_digest':proposal.proposal_digest,'plan_digest':digest(plan)})
        response.raise_for_status();job=response.json()
        print('真实T3回放开始：',root,flush=True)
        last=None
        for _ in range(6000):
            job=client.get('/api/v1/jobs/'+job['id']).json()
            run=client.get(f'/api/v1/interviews/{iv.id}/execution').json()
            saved=run['steps'][0]['chunks']
            if saved!=last:
                print('T3已保存批数：',saved,flush=True);last=saved
            if job['status'] in ('succeeded','failed','cancelled','interrupted'):
                break
            time.sleep(.3)
        (root/'execution.json').write_text(json.dumps(run,ensure_ascii=False,indent=2))
        with app.state.store.Session() as session:
            calls=list(session.scalars(select(Call).order_by(Call.id)))
        report={'status':job['status'],'error':job['error'],'chunks':run['steps'][0]['chunks'],
            'workspace':run['workspace'],'calls':[{'tokens_in':c.tokens_in,'tokens_out':c.tokens_out,
                'duration_ms':c.duration_ms,'error':c.error} for c in calls]}
        (PRODUCT/'docs/开发执行超时回归.md').write_text('# 实际超时任务回归\n\n实际T3与前置代码作为输入，只写隔离目录；功能测试和医学业务审核仍待验。\n\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n')
        print(json.dumps(report,ensure_ascii=False),flush=True)
        assert job['status']=='succeeded',report
        assert run['steps'][0]['status']=='generated' and run['steps'][0]['chunks']>=1


if __name__=='__main__':main()
