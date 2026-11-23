"""Real Codex E2E on isolated samples. Never advances kit stages or edits real products."""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT/'backend'))

from fastapi.testclient import TestClient
from app.core import Settings
from app.main import create_app

EVIDENCE = PRODUCT/'docs/evidence/阶段2-1'
SAMPLES = PRODUCT/'docs/真实样例与评分.md'


def record(prompt, meta, error, stage='阶段2-1'):
    evidence = PRODUCT/'docs/evidence'/stage
    evidence.mkdir(parents=True, exist_ok=True)
    path = evidence/'冒烟记录.md'
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    if not path.exists():
        path.write_text('# 冒烟记录（冒烟脚本自动写入，不得手填）\n- 产品：AI产品工厂\n'+f'- 阶段：{stage}\n'
            f'- 模型：Codex / {meta["model"]}\n- 写入时间：{stamp}\n\n'
            '| 时间 | 链路 | 请求 ID | 输入 token | 输出 token | 首字耗时 | 总耗时 | 结构校验 | 重试次数 | 结果 |\n'
            '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n')
    with path.open('a') as stream:
        stream.write(f'| {stamp} | {prompt} | {meta["request_id"]} | {meta["tokens_in"]} | {meta["tokens_out"]} | '
            f'{(meta["first_ms"] or 0)/1000:.2f}s | {meta["duration_ms"]/1000:.2f}s | '
            f'{"首测合规" if not error and not meta["retry_count"] else "重试后合规" if not error else error} | '
            f'{meta["retry_count"]} | {"失败" if error else "通过"} |\n')
    print(prompt, meta['model'], '失败:'+error if error else '结构通过', flush=True)


def wait(client, response):
    response.raise_for_status()
    job = response.json()
    for _ in range(600):
        job = client.get('/api/v1/jobs/'+job['id']).json()
        if job['status'] in ('succeeded', 'failed', 'cancelled', 'interrupted'):
            if job['status'] != 'succeeded':
                raise RuntimeError(job['error']['code']+': '+job['error']['message'])
            return job
        time.sleep(.3)
    raise RuntimeError('冒烟等待超时')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, choices=range(1, 11), default=5)
    parser.add_argument('--resume', type=Path, help='继续.runtime中已有的冒烟，复用成功访谈样例')
    args = parser.parse_args()
    root = args.resume.resolve() if args.resume else PRODUCT/'.runtime'/('smoke-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    if args.resume and (not root.is_relative_to((PRODUCT/'.runtime').resolve()) or not (root/'data/app.sqlite3').is_file()):
        parser.error('resume必须指向本产品.runtime内已有的冒烟数据库')
    settings = Settings(data_dir=str(root/'data'), projects_dir=str(root/'projects'), model_backend='codex')
    app = create_app(settings, on_call=record)
    # These are fabricated test cases, not claims about actual users or their choices.
    cases = ['读书摘录整理助手','大学生实习信息整理','个人周报草稿助手','家庭菜谱记录助手','个人旅行清单助手']
    rubric = '# 真实样例与评分\n\n这些是预设测试输入，输出来自真实 Codex；脚本代答仅用于测试，不代表产品经理验收。\n'
    rubric += '\n每例请按1-3分评价：覆盖关键缺口、推荐可执行、不编造已定事项、表达清楚。标出不能接受项；D2 待你打分后确定。\n'
    with TestClient(app) as client:
        first_id = None
        if args.resume:
            rows=client.get('/api/v1/interviews').json()
            if len(rows)<args.samples:
                raise RuntimeError('现有访谈样例不足，不能跳过样例生成')
            first_id=min(row['id'] for row in rows)
            for row in rows:
                d=client.get(f'/api/v1/interviews/{row["id"]}').json()
                if not any(m['role']=='assistant' for m in d['messages']):
                    raise RuntimeError('已有样例未成功生成，不能直接跳过')
            rubric=SAMPLES.read_text()
        for i in range(0 if args.resume else args.samples):
            title = cases[i % len(cases)] + str(i+1)
            idea = (f'为自己做一个{title}。只在本机用，不登录；输入可复制的普通文本，输出 Markdown 草稿。'
                '不处理个人信息、企业机密或受监管数据；所有数据长期保留在本机。'
                '模型复用已有Codex ChatGPT登录，不充值、不切API计费，额度不足暂停。'
                '首版只做文本整理，原文不篡改，事实不能编造，数字效果标准待真实样例打分。'
                'PRD定稿、清单关闭、发布和费用变更需要人工确认。没有截止日期；先不部署，将来复用已有云环境，不开新资源。')
            response = client.post('/api/v1/interviews', data={'product_name': title, 'idea': idea})
            response.raise_for_status();iv = response.json()['id']
            first_id = first_id or iv
            wait(client, client.post(f'/api/v1/interviews/{iv}/messages',json={'content':'按以上边界检查，有关键缺口就问，没有就说明可以起草。'}))
            d=client.get(f'/api/v1/interviews/{iv}').json()
            rubric += f'\n## 样例 {i+1} {title}\n\n输入\n\n{idea}\n\n输出\n\n'
            for message in d['messages']:
                if message['role']=='assistant':
                    rubric += message['content']+'\n\n'+json.dumps(message['questions'],ensure_ascii=False,indent=2)+'\n'
            rubric += '\n评分（待产品经理填写）\n\n覆盖 __ / 推荐 __ / 不编造 __ / 表达 __\n\n不能接受项 __\n'
            SAMPLES.write_text(rubric)
        iv=first_id
        d=client.get(f'/api/v1/interviews/{iv}').json()
        if d['interview']['status']=='collecting':
            wait(client,client.post(f'/api/v1/interviews/{iv}/draft'))
        d=client.get(f'/api/v1/interviews/{iv}').json()
        if d['interview']['status']=='draft_pending':
            wait(client,client.post(f'/api/v1/interviews/{iv}/confirm',json={'accepted':True,'draft_version':d['draft']['version']}))
        detail=client.get(f'/api/v1/interviews/{iv}').json()
        for item in detail['checklist']:
            # Scope the fixture outside the business answer. Adding "test only"
            # inside the answer makes the agent correctly leave real decisions pending.
            old_fixture_answer=(item.get('answer') or '').startswith('按推荐；仅适用于本条预设测试样例')
            if not item['closed'] or (args.resume and old_fixture_answer):
                client.post(f'/api/v1/checklist-items/{item["id"]}/answer',json={'content':'按推荐'}).raise_for_status()
        if detail['interview']['status']=='closed':
            job=next(j for j in detail['jobs'] if j['action']=='finalize' and j['status']=='succeeded')
        else:
            job=wait(client,client.post(f'/api/v1/interviews/{iv}/finalize',json={'accepted':True}))
        stream=client.get('/api/v1/jobs/'+job['id']+'/events').text
        assert 'event: done' in stream and 'event: error' not in stream
        for file in job['result']['written_paths']:
            assert Path(file).is_file() and Path(file).is_relative_to(root)
        rubric += '\n## 完整链路产物\n\n'+ '\n'.join(job['result']['written_paths'])+'\n\n'+Path(job['result']['written_paths'][0]).read_text()
        SAMPLES.write_text(rubric)
    print('真实链路通过；评分待产品经理完成。隔离数据：',root)


if __name__=='__main__':
    main()
