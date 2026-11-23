"""Real regression for complementary answers, an explicit revision and deferred cloud checks."""
import re
import sys
from datetime import datetime
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / 'backend'))
from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app
from app.store import Asset, ChecklistItem, Interview, PrdDraft
from smoke import record, wait
from smoke_planning import PRD


def main():
    root = PRODUCT / '.runtime' / ('decision-history-smoke-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    app = create_app(Settings(data_dir=str(root / 'data'), projects_dir=str(root / 'projects')),
        on_call=lambda name, meta, error: record(name, meta, error, stage='阶段2-2'))
    prd = re.sub(r'(## 6\.[^\n]*\n).*?(?=\n## 7\.)',
        r'\1不指定厂商，按隐私边界选型。月度模型预算与超额策略由产品经理补充。\n', PRD, flags=re.S)
    prd = re.sub(r'(## 8\.[^\n]*\n).*',
        r'\1本机单人不登录。未来优先复用已有云环境，无可复用环境时采用免费方案；资源实际情况上线前核实，不授权付费。\n', prd, flags=re.S)
    reports = []
    with TestClient(app) as client:
        for amendment, expected in ((None, '50'), ('改成每月80元', '80')):
            iv = Interview(product_name='分次补充回归' + expected, status='auditing', idea_text='隔离验证预设输入，不代表开通资源或付费授权')
            app.state.store.add(iv)
            app.state.store.add(PrdDraft(interview_id=iv.id, content=prd, version=1))
            for question, options, answer in (
                ('模型服务商有哪些限制，每月模型费用上限是多少？', ['A 不指定厂商，并补充月预算'], 'a'),
                ('是否已有可复用的云环境，没有时拟选什么方案？', ['A 优先复用环境，核实共享额度与费用'], 'a')):
                app.state.store.add(ChecklistItem(interview_id=iv.id, kind='must', question=question,
                    options=options, recommendation='不自动加费，实际资源信息上线前核实', answer=answer, closed=True))
            app.state.store.add(Asset(interview_id=iv.id, kind='final_decision_review', data={'pending':[
                {'number':number, 'topic':topic, 'conclusion':'隔离样例等待用户补充', 'status':'待定'}
                for number, topic in (('D4','模型成本'), ('D9','上线套餐'))]}))
            def save(number, content):
                review = client.get(f'/api/v1/interviews/{iv.id}').json()['pending_review']
                client.post(f'/api/v1/interviews/{iv.id}/decision-answers', json={
                    'review_id':review['review_id'], 'answers':[{'number':number, 'content':content}]}).raise_for_status()
            save('D4', '上限50元')
            save('D4', '达到上限暂停新任务')
            if amendment:
                save('D4', amendment)
            save('D9', '没有的话选免费')
            success = wait(client, client.post(f'/api/v1/interviews/{iv.id}/finalize', json={'accepted':True}))
            final = app.state.store.asset(iv.id, 'prepared_final').data['result']
            decisions = {d['number']:d for d in final['decisions']}
            assert decisions['D4']['status'] == decisions['D9']['status'] == '已定', decisions
            assert expected in decisions['D4']['conclusion'] and '暂停' in decisions['D4']['conclusion'], decisions['D4']
            if amendment:
                assert '50' not in decisions['D4']['conclusion'], decisions['D4']
            assert '免费' in decisions['D9']['conclusion'], decisions['D9']
            assert all(Path(p).is_file() and Path(p).is_relative_to(root) for p in success['result']['written_paths'])
            reports.append(f'## 样例：月预算{expected}元\n\n' +
                '\n\n'.join(d['number'] + '：' + d['conclusion'] for d in (decisions['D4'],decisions['D9'])))
            print(f'真实回归通过：分次补充合并、预算{expected}元、超额暂停、D9免费选择不被上线技术核实阻断。', flush=True)
    (PRODUCT / 'docs/分次补充回归验证.md').write_text('# 分次补充回归验证\n\n预设隔离样例，不代表实际项目费用授权；文件仅写隔离目录。\n\n' + '\n\n'.join(reports))


if __name__ == '__main__':
    main()
