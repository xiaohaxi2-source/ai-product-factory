"""Real Codex -> code files -> checks -> diff, on an isolated approved-input fixture."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / 'backend'))
from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app
from app.planning import digest
from app.store import Call, Confirmation, DevelopmentPlan, TechnicalProposal
from sqlalchemy import func, select
from smoke import record, wait
from smoke_planning import seed, body


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--stage', choices=('阶段2-3', '阶段2-4'), default='阶段2-3')
    args = parser.parse_args()
    root = args.resume.resolve() if args.resume else PRODUCT / '.runtime' / ('execution-smoke-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    if args.resume and (not root.is_relative_to((PRODUCT / '.runtime').resolve()) or not (root / 'data/app.sqlite3').is_file()):
        parser.error('只能继续本产品.runtime内的已有冒烟目录')
    app = create_app(Settings(data_dir=str(root/'data'), projects_dir=str(root/'projects')),
        on_call=lambda name,meta,error: record(name,meta,error,stage=args.stage))
    with TestClient(app) as client:
        if args.resume:
            iv = app.state.store.list_interviews()[0].id
        else:
            iv = seed(app)
            # These are explicitly predetermined approved inputs, not model-generated results.
            tech = {'summary':'本机读书文本助手，先实现纯文本输入校验与测试，不部署、不调用业务付费服务。',
                'default_stack':['Python 3.12 标准库'], 'modules':[], 'cost_impact':'只用已有Codex额度生成代码，不付费开通资源。',
                'deferred':['云部署','外部模型调用'], 'verification':['合法文本原样返回，空白或超长文本拒绝'], 'questions':[],
                'phases':[{'stage':'阶段2-1','goal':'输入校验与单元测试代码','deliverables':['Python源码及测试'],'acceptance':'后续执行功能测试并人工核对'}]}
            proposal = TechnicalProposal(interview_id=iv,version=1,source_digest=app.state.jobs.planning.source(iv)['source_digest'],
                proposal_digest=digest(tech),data=tech)
            app.state.store.add(proposal)
            app.state.store.add(Confirmation(interview_id=iv,kind='technical_approved',data={
                'proposal_id':proposal.id,'proposal_digest':proposal.proposal_digest,'source_digest':proposal.source_digest,'version':1}))
            tasks = {'note':'隔离冒烟预设已确认任务；测试尚未执行，生成结果来自真实Codex。','tasks':[
                {'id':'T1','phase':'阶段2-1','title':'实现文本输入校验',
                 'goal':'在backend/validation.py实现validate_text(text: str) -> str，合法非空文本（至多10000字符）原样返回，纯空白或超长抛ValueError，非字符串抛TypeError。仅标准库。',
                 'inputs':['已确认文本范围'], 'deliverables':['backend/validation.py'],'checks':['Python语法与后续边界测试'],'confirmations':[],'depends_on':[]},
                {'id':'T2','phase':'阶段2-1','title':'编写输入边界单元测试',
                 'goal':'用unittest在tests/test_validation.py覆盖validate_text原文保留、空白、超长、非字符串四类边界，只编写测试，不声称测试已经运行。可增加必要包初始化文件。',
                 'inputs':['T1校验函数'], 'deliverables':['tests/test_validation.py'],'checks':['检查测试覆盖边界，功能执行待验'],'confirmations':[],'depends_on':['T1']}]}
            app.state.store.add(DevelopmentPlan(interview_id=iv,proposal_id=proposal.id,data=tasks))
        w = client.get(f'/api/v1/interviews/{iv}/workbench').json()
        request = dict(body(w),plan_digest=w['development_plan']['plan_digest'])
        success = wait(client,client.post(f'/api/v1/interviews/{iv}/execute',json=request))
        run = client.get('/api/v1/executions/'+success['result']['execution_id']).json()
        assert run['status']=='generated' and len(run['steps'])==2
        workspace = Path(run['workspace'])
        assert workspace.is_relative_to(root) and (workspace/'backend/validation.py').is_file()
        assert (workspace/'tests/test_validation.py').is_file()
        assert all(s['status']=='generated' for s in run['steps'])
        assert all(c['status']!='failed' for s in run['steps'] for c in s['checks'])
        diff = client.get(f'/api/v1/executions/{run["id"]}/diff',params={'task_id':'T1','path':'backend/validation.py'})
        diff.raise_for_status();assert 'validate_text' in diff.json()['diff']
        source = app.state.jobs.planning.source(iv)['root']
        assert not (source/'backend/validation.py').exists()
        # Exact repeat reuses committed files and produces no additional model turn.
        with app.state.store.Session() as session:
            call_count = session.scalar(select(func.count()).select_from(Call))
        wait(client,client.post(f'/api/v1/interviews/{iv}/execute',json=request))
        with app.state.store.Session() as session:
            assert session.scalar(select(func.count()).select_from(Call)) == call_count
        assert client.get(f'/api/v1/interviews/{iv}/execution').json()['workspace']==str(workspace)
        if args.stage == '阶段2-4':
            import re
            from app.check_sandbox import command_run, environment, sandbox_profile
            env = environment(root)
            profile = sandbox_profile(root, Path(sys.executable))
            code, output = command_run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                workspace, env, timeout=30, profile=profile)
            count = re.search(r'Ran (\d+) tests?', output)
            assert code == 0 and count and int(count[1]) >= 4, output
            download = client.get(f'/api/v1/executions/{run["id"]}/download')
            assert download.status_code == 200 and download.content.startswith(b'PK')
            assert client.get(f'/api/v1/interviews/{iv}/artifacts').json()
            (root/'validation.json').write_text(json.dumps({'tests_passed':int(count[1]),
                'code_download_bytes':len(download.content),'source_unchanged':True},ensure_ascii=False,indent=2))
            print('真实源码在沙箱中通过'+str(count[1])+'项功能测试；文档与源码ZIP接口通过。',flush=True)
        (root/'execution.json').write_text(json.dumps(run,ensure_ascii=False,indent=2))
        (PRODUCT/'docs/开发执行真实样例.md').write_text('# 开发执行真实样例\n\n预设已确认PRD、方案和两个任务为输入；源码、测试代码来自真实Codex。只在隔离目录写入，未改真实产品；仅语法检查通过，功能测试待阶段2-4。\n\n代码目录：'+str(workspace)+'\n\n'+
            '\n\n'.join(s['task_id']+'：'+s['summary']+'\n改动：'+', '.join(c['path'] for c in s['changes']) for s in run['steps']))
    print('真实执行链路通过：两项任务→真实源码和测试文件→语法检查→差异查看→重复启动复用。',flush=True)
    print('隔离目录：',root,flush=True)


if __name__ == '__main__':
    main()
