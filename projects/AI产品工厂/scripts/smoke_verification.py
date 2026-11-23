"""Run actual checks on a copy of the latest generated app; never edit original user code."""
import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/'backend'))
from fastapi.testclient import TestClient
from app.core import KIT_ROOT, Settings, get_settings
from app.execution import Executions, manifest
from app.main import create_app
from app.planning import Planning, digest
from app.store import Asset, Confirmation, Execution, Interview, Store, TechnicalProposal
from app.workflow import Workflow
from smoke import record, wait


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--repair',action='store_true')
    parser.add_argument('--feedback',default='')
    args=parser.parse_args()
    root=args.resume.resolve() if args.resume else PRODUCT/'.runtime'/('verification-smoke-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if not root.is_relative_to((PRODUCT/'.runtime').resolve()):parser.error('只能使用本产品.runtime目录')
    app=create_app(Settings(data_dir=str(root/'data'),projects_dir=str(root/'projects')),
        on_call=lambda name,meta,error:record(name,meta,error,stage='阶段2-4'))
    if not args.resume:
        original=Store(get_settings());workflow=Workflow(None,original,KIT_ROOT/'projects');planning=Planning(workflow)
        proposal=original.proposal(2);source=planning.source(2)
        old=original.latest_execution(2,proposal.id);files=Executions(planning).current_files(old)
        iv=Interview(product_name='真实生成代码检查样例',status='closed',idea_text='复制实际已生成代码作为检查输入，测试结果不预设')
        app.state.store.add(iv)
        target=root/'projects'/iv.product_name
        inputs={str(target/'docs/PRD/PRD.md'):source['prd'],str(target/'docs/项目状态.md'):source['decision_ledger']}
        for name,text in inputs.items():
            path=Path(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        app.state.store.add(Asset(interview_id=iv.id,kind='export_plan',data={'files':inputs}))
        tech=TechnicalProposal(interview_id=iv.id,version=1,data=proposal.data,
            source_digest=digest([source['prd'],source['decision_ledger']]),proposal_digest=proposal.proposal_digest)
        app.state.store.add(tech)
        run=Execution(id=__import__('uuid').uuid4().__str__(),interview_id=iv.id,proposal_id=tech.id,
            plan_digest=old.plan_digest,status='generated',data={'version':1,'steps':copy.deepcopy(old.data['steps'])})
        revision,_=app.state.jobs.executions.write_revision(run.id,files)
        run.data.update(revision=revision,manifest=manifest(files));app.state.store.add(run)
        original.engine.dispose()
    with TestClient(app) as client:
        iv=app.state.store.list_interviews()[0].id
        execution=app.state.store.latest_execution(iv,app.state.store.proposal(iv).id)
        previous=app.state.store.latest_verification(iv)
        if args.repair:
            response=client.post(f'/api/v1/interviews/{iv}/check-fix',json={'accepted':True,'verification_id':previous.id,'feedback':args.feedback})
        else:
            response=client.post(f'/api/v1/interviews/{iv}/verify',json={'accepted':True,'execution_id':execution.id,
                'revision':execution.data['revision'],'manifest_digest':digest(execution.data['manifest'])})
        response.raise_for_status();job=response.json();print('检查开始：',root,flush=True)
        last=None
        import time
        for _ in range(5000):
            current=client.get('/api/v1/jobs/'+job['id']).json()
            check=client.get(f'/api/v1/interviews/{iv}/verification').json()
            stage=[(s['name'],s['status']) for s in check['steps']] if check else []
            if stage!=last:print(stage,flush=True);last=stage
            if current['status'] in ('succeeded','failed','interrupted','cancelled'):break
            time.sleep(.3)
        print(json.dumps(check,ensure_ascii=False,indent=2),flush=True)
        (root/'check-result.json').write_text(json.dumps(check,ensure_ascii=False,indent=2))
        if current['status']!='succeeded':print('检查发现失败项；代码与报告已保留，不视为通过。',flush=True)
        else:print('工程检查通过，真实业务条件仍按报告待验。',flush=True)


if __name__=='__main__':main()
