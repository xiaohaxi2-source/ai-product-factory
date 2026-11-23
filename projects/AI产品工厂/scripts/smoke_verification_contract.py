"""Preset broken app -> real checks -> real Codex repair -> real tests and preview."""
import sys
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/'backend'))
from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app
from app.execution import manifest
from app.planning import digest
from app.store import Execution, TechnicalProposal
from smoke import record
from smoke_planning import seed


def main():
    root=PRODUCT/'.runtime'/('verification-contract-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    app=create_app(Settings(data_dir=str(root/'data'),projects_dir=str(root/'projects')),
        on_call=lambda name,meta,error:record(name,meta,error,stage='阶段2-4'))
    iv=seed(app)
    tech={'summary':'预设本机FastAPI校验应用，仅用于工程检查回归，不代表真实用户业务验收。',
        'default_stack':['Python 3.12','FastAPI'],'modules':[],'cost_impact':'复用已有Codex额度，不开通资源。',
        'deferred':['上线'],'verification':['保留测试并修复错误值'],'questions':[],
        'phases':[{'stage':'阶段2-1','goal':'提供健康接口和纯函数','deliverables':['源码及测试'],'acceptance':'工程检查及本地启动'}]}
    proposal=TechnicalProposal(interview_id=iv,version=1,data=tech,proposal_digest=digest(tech),
        source_digest=app.state.jobs.planning.source(iv)['source_digest']);app.state.store.add(proposal)
    files={'requirements.txt':'fastapi==0.115.6\nuvicorn==0.34.0\npytest==8.3.4\nhttpx==0.28.1\n',
        'backend/app/main.py':'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/api/v1/health")\ndef health():return {"status":"ok"}\n@app.get("/")\ndef page():return {"page":"preview"}\n',
        'backend/logic.py':'VALUE = 1\n',
        'tests/test_logic.py':'from backend.logic import VALUE\ndef test_value(): assert VALUE == 2\ndef test_base(): assert 2 + 2 == 4\n'}
    execution=Execution(id=str(uuid.uuid4()),interview_id=iv,proposal_id=proposal.id,plan_digest=digest(files),status='generated',
        data={'version':1,'steps':[]})
    revision,_=app.state.jobs.executions.write_revision(execution.id,files)
    execution.data.update(revision=revision,manifest=manifest(files));app.state.store.add(execution)
    def await_job(client,response):
        response.raise_for_status();job=response.json()
        for _ in range(3000):
            job=client.get('/api/v1/jobs/'+job['id']).json()
            if job['status'] in ('succeeded','failed','interrupted','cancelled'):return job
            time.sleep(.3)
        raise RuntimeError('检查回归超时')
    with TestClient(app) as client:
        first=await_job(client,client.post(f'/api/v1/interviews/{iv}/verify',json={'accepted':True,
            'execution_id':execution.id,'revision':revision,'manifest_digest':digest(manifest(files))}))
        failed=client.get(f'/api/v1/interviews/{iv}/verification').json()
        assert first['status']=='failed' and next(s for s in failed['steps'] if s['name']=='自动化测试')['failed']==1
        print('真实pytest失败已识别，开始真实Codex修复。',flush=True)
        repaired=await_job(client,client.post(f'/api/v1/interviews/{iv}/check-fix',json={'accepted':True,
            'verification_id':failed['id'],'feedback':'只将backend/logic.py中的错误值修正为测试要求的2，不改已有测试和任何其他功能。'}))
        passed=client.get(f'/api/v1/interviews/{iv}/verification').json()
        assert repaired['status']=='succeeded' and passed['status']=='passed',passed
        tests=next(s for s in passed['steps'] if s['name']=='自动化测试')
        assert tests['passed']>=2 and tests['failed']==0 and passed['preview_url']
        current=app.state.store.execution(execution.id)
        assert app.state.jobs.executions.current_files(current)['tests/test_logic.py']==files['tests/test_logic.py']
        (root/'result.json').write_text(json.dumps(passed,ensure_ascii=False,indent=2))
        (PRODUCT/'docs/检查验收真实回归.md').write_text('# 检查验收真实回归\n\n预设错误源码作为输入；真实pytest先检出1项失败，真实Codex修复源码，原测试未修改；再次运行完整测试并通过本地启动。此样例不代表实际产品人工验收。\n\n报告：'+passed['evidence_path']+'\n\n通过测试数：'+str(tests['passed'])+'\n')
        print('真实检查→失败报告→Codex修复→原测试重跑→本地预览通过。',flush=True)
        print(root,flush=True)


if __name__=='__main__':main()
