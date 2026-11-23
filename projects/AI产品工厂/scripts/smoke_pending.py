"""Real regression: choosing A without its required budget stays pending; a complete answer closes."""
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PRODUCT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PRODUCT/'backend'))
from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app
from app.store import ChecklistItem, Interview, PrdDraft
from smoke import record, wait
from smoke_planning import PRD


def main():
    root=PRODUCT/'.runtime'/('pending-smoke-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    app=create_app(Settings(data_dir=str(root/'data'),projects_dir=str(root/'projects')),
        on_call=lambda name,meta,error:record(name,meta,error,stage='阶段2-2'))
    prd=re.sub(r'(## 6\.[^\n]*\n).*?(?=\n## 7\.)',
        r'\1允许在后续实现中调用模型；不指定厂商，按已定隐私边界推荐。月度预算金额尚未填写，达到上限暂停、不自动充值或提额。\n',PRD,flags=re.S)
    iv=Interview(product_name='预算缺失回归样例',status='auditing',idea_text='仅为隔离测试，不代表实际项目授权')
    app.state.store.add(iv)
    app.state.store.add(PrdDraft(interview_id=iv.id,content=prd,version=1))
    item=ChecklistItem(interview_id=iv.id,kind='must',question='模型厂商有哪些限制，每月预算上限是多少？',
        options=['A 不指定厂商，按隐私边界筛选，并补充预算金额','B 当前不启用付费，调用前再确认预算'],
        recommendation='推荐A，并填写月度预算金额；达到上限暂停，不自动加费',answer='a',closed=True)
    app.state.store.add(item)
    with TestClient(app) as client:
        job=client.post(f'/api/v1/interviews/{iv.id}/finalize',json={'accepted':True}).json()
        for _ in range(600):
            job=client.get('/api/v1/jobs/'+job['id']).json()
            if job['status'] in ('failed','succeeded'):break
            time.sleep(.3)
        assert job['status']=='failed' and job['error']['code']=='DECISIONS_PENDING',job
        assert 'D4' in job['error']['message'] and '预算' in job['error']['message']
        assert app.state.store.get_interview(iv.id).status=='auditing'
        assert app.state.store.asset(iv.id,'export_plan') is None
        print('真实缺失预算拦截通过，错误明确显示D4。',flush=True)
        # The number is a fabricated fixture value, not the user's budget or a payment authorization.
        review=client.get(f'/api/v1/interviews/{iv.id}').json()['pending_review']
        content='选择A，不指定厂商，月度模型费用上限30元；达到上限暂停，不自动充值、不提额。'
        client.post(f'/api/v1/interviews/{iv.id}/decision-answers',json={'review_id':review['review_id'],'answers':[{'number':'D4','content':content}]}).raise_for_status()
        assert client.get(f'/api/v1/interviews/{iv.id}').json()['pending_review']['decisions'][0]['answer']==content
        assert app.state.store.get_item(item.id).answer=='a'
        success=wait(client,client.post(f'/api/v1/interviews/{iv.id}/finalize',json={'accepted':True}))
        assert all(Path(p).is_file() and Path(p).is_relative_to(root) for p in success['result']['written_paths'])
        (PRODUCT/'docs/待定决策回归验证.md').write_text('# 待定决策回归验证\n\n预设隔离样例，非实际用户预算。\n\n'
            '仅选择A而未补金额时，真实Codex终稿保留D4待定，后端指出具体缺口、不写回；通过待确认决策专用接口保存月度预算和暂停规则，刷新读回一致，原清单答案a未改写；真实终稿采用补充答案并成功写回隔离目录。\n\n'
            '缺失时的提示\n\n'+job['error']['message']+'\n\n成功后的文件\n\n'+'\n'.join(success['result']['written_paths']))
        print('真实完整预算恢复通过，无需额外补写未被询问的单次费用上限。',flush=True)


if __name__=='__main__':main()
