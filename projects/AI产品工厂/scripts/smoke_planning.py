"""Real stage2-2 model contracts on an explicitly fabricated, approved PRD fixture."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT/'backend'))

from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app
from app.schemas import Decision
from app.store import Asset, Interview
from smoke import record, wait

PRD = '# PRD：本机读书摘录助手\n\n' + '\n\n'.join(f'## {i}. {title}\n{body}' for i,title,body in [
    (1,'目标用户与核心任务','仅本人使用，将普通读书摘录按主题分组，原文逐字保留。'),
    (2,'输入、过程和产物','输入可复制的非敏感文本；输出Markdown草稿供核对和保存。'),
    (3,'Agent 的主链路','输入摘录→生成草稿→本人核对确认→保存。无需自动发布或发送。'),
    (4,'什么算生成得好','原文不篡改、事实不编造；可容忍分类和排版瑕疵。数字标准待样例打分。'),
    (5,'交付阶段与优先级','后端先行，先验证文本整理与持久化，再做正式网页。暂缓OCR、多人、云部署。'),
    (6,'模型与成本约束','本机已有Codex CLI和ChatGPT登录，允许使用现有额度，最多60次尝试，每次120秒。额度不足暂停，不充值、不切API计费。模型账号可调用已实测，具体实现仍需验证。'),
    (7,'数据与非功能要求','仅发送本次非敏感摘录及整理要求，不处理个人、机密或受监管数据；不额外限制地域。密钥和完整文档不记日志，数据长期本地保存，重启可恢复。'),
    (8,'上线与账号','本机单人不登录，当前不部署；以后需发布或改变费用另行确认，不创建云资源。')])


def seed(app):
    store=app.state.store
    iv=Interview(product_name='技术方案真实样例',status='closed',idea_text='预设已确认PRD测试夹具，不代表真实用户项目')
    store.add(iv)
    values=['本人整理摘录','原文不变，数字标准待样例打分','允许排版分类瑕疵，禁止编造','已有Codex登录，60次尝试，120秒，不新增费用',
            '本机单人不登录','长期本地保留','仅发送本次非敏感摘录，不额外限制地域，不记原文日志','保存、发布、费用变化需本人确认','当前不部署，未来另行确认']
    decisions=[Decision(number=f'D{i}',topic=topic,conclusion=value,status='待样例打分' if i==2 else '已定') for i,(topic,value) in enumerate(zip(
        ['目标任务','验收标准','瑕疵容忍','模型成本','登录','留存','隐私','人工确认','上线'],values),1)]
    # This is an approved test input, not a mock result from a model. No live product is edited.
    app.state.workflow._write_back(iv,PRD,[],decisions)
    return iv.id


def body(w):
    p=w['proposal']
    return {'accepted':True,'proposal_version':p['version'],'source_digest':p['source_digest'],
            'proposal_digest':p['proposal_digest']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume',type=Path)
    args=parser.parse_args()
    root=args.resume.resolve() if args.resume else PRODUCT/'.runtime'/('planning-smoke-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if args.resume and (not root.is_relative_to((PRODUCT/'.runtime').resolve()) or not (root/'data/app.sqlite3').exists()):
        parser.error('resume只能指向本产品.runtime内的已有冒烟目录')
    settings=Settings(data_dir=str(root/'data'),projects_dir=str(root/'projects'),model_backend='codex')
    app=create_app(settings,on_call=lambda name,meta,error:record(name,meta,error,stage='阶段2-2'))
    with TestClient(app) as client:
        iv=app.state.store.list_interviews()[0].id if args.resume else seed(app)
        w=client.get(f'/api/v1/interviews/{iv}/workbench').json()
        if not w['proposal']:
            wait(client,client.post(f'/api/v1/interviews/{iv}/technical'))
        w=client.get(f'/api/v1/interviews/{iv}/workbench').json()
        if w['proposal']['version']==1:
            wait(client,client.post(f'/api/v1/interviews/{iv}/technical/revise',json={'content':'保持已确认范围，明确将自动云备份列为暂缓项，首版只在本机保存。不要新增待定问题。'}))
        w=client.get(f'/api/v1/interviews/{iv}/workbench').json()
        if w['proposal']['content']['questions']:
            raise RuntimeError('真实方案仍有待答问题，请先审阅样例后继续，未自动替用户决定')
        if not w['development_plan']:
            j=wait(client,client.post(f'/api/v1/interviews/{iv}/technical/confirm',json=body(w)))
        w=client.get(f'/api/v1/interviews/{iv}/workbench').json()
        assert w['state']=='tasks_ready' and w['proposal']['version']>=2
        assert w['development_plan']['content']['tasks']
        for path in w['proposal']['paths']+w['development_plan']['paths']:
            assert Path(path).is_relative_to(root) and Path(path).is_file()
        sample=PRODUCT/'docs/技术方案真实样例.md'
        sample.write_text('# 技术方案与开发任务真实样例\n\n预设已确认PRD作为输入，方案及任务来自真实Codex。脚本确认仅针对隔离样例，不代表实际产品已验收。\n\n'
            +w['proposal']['markdown']+'\n'+w['development_plan']['markdown']+'\n## 产物路径\n'+'\n'.join(w['proposal']['paths']+w['development_plan']['paths']))
        (root/'workbench.json').write_text(json.dumps(w,ensure_ascii=False,indent=2))
    print('阶段2-2真实链路通过：方案生成→修改→版本确认→开发任务→文件写回。',flush=True)
    print('隔离样例目录：',root,flush=True)


if __name__=='__main__':main()
