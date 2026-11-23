import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import Settings
from app.main import create_app

PRD = '# PRD：读书助手\n\n' + '\n\n'.join(f'## {i}. {title}\n{body}' for i, title, body in [
    (1,'目标用户与核心任务','只给自己整理读书摘录。'),
    (2,'输入、过程和产物','输入可复制文本，返回分组摘录和Markdown。'),
    (3,'Agent 的主链路','输入后生成草稿，由用户确认，不发送给别人。'),
    (4,'什么算生成得好','不能篡改摘录，数字标准待样例打分。'),
    (5,'交付阶段与优先级','首版本地文本整理，暂不做扫描件与多人。'),
    (6,'模型与成本约束','使用已有Codex登录，不充值，额度不足暂停。'),
    (7,'数据与非功能要求','数据长期本地保存，不处理个人或机密信息。'),
    (8,'上线与账号','本机单人不登录，将来复用已有云环境，不创建资源。')])

DECISIONS = [{'number':f'D{i}', 'topic':topic, 'conclusion':'用户在样例中已明确确认；按 PRD 对应字段执行。',
              'status':'待样例打分' if i==2 else '已定'} for i,topic in enumerate(
    ['目标任务','验收标准','瑕疵容忍','模型成本','登录','留存','隐私','人工确认','上线套餐'],1)]


class FakeGateway:
    calls = []
    fail = False

    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.calls = []
        self.fail = False

    def complete(self, prompt, payload, schema, **kw):
        from app.core import Problem
        self.calls.append(prompt)
        if self.fail:
            raise Problem('MODEL_FAILED','模拟服务失败',502)
        if prompt=='interview':
            value={'questions':[{'title':'给谁用？','options':['自己','多人'],
                'recommendation':'先自己用','consequence':'多人需要权限隔离'}], 'enough':False,'note':'先确认使用者'}
        elif prompt=='draft_prd':
            value={'prd_markdown':PRD,'assumptions':[{'gap':'留存时间','default':'长期本地保存','reason':'支持重启恢复'}]}
        elif prompt=='audit_prd':
            value={'must_ask':[{'question':f'关键问题{i}','why':'影响范围','options':['A','B'],
                 'recommendation':'A','consequence':'未答不能定稿'} for i in range(6)],
                 'defaults':[], 'contradictions':[{'point':'公开上线与只自己使用冲突','ruling':'先自己用'}]}
        else:
            value={'prd_markdown':PRD,'assumptions':[],'decisions':DECISIONS}
        return schema.model_validate(value),{'model':'fake-for-test','duration_ms':1,'retry_count':0}


def wait_job(client, response):
    assert response.status_code==202, response.text
    job=response.json()
    for _ in range(300):
        job=client.get('/api/v1/jobs/'+job['id']).json()
        if job['status'] in ('succeeded','failed','interrupted','cancelled'):
            return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


@pytest.fixture
def env(tmp_path):
    settings=Settings(data_dir=str(tmp_path/'data'),projects_dir=str(tmp_path/'projects'))
    app=create_app(settings, gateway_factory=FakeGateway)
    with TestClient(app,raise_server_exceptions=False) as client:
        yield client,app,tmp_path


def create(client, name='测试产品'):
    r=client.post('/api/v1/interviews',data={'product_name':name,'idea':'整理读书摘录的小工具'})
    assert r.status_code==201,r.text
    return r.json()['id']


def to_auditing(client,iv):
    assert wait_job(client,client.post(f'/api/v1/interviews/{iv}/draft'))['status']=='succeeded'
    assert wait_job(client,client.post(f'/api/v1/interviews/{iv}/confirm',json={'accepted':True,'draft_version':1}))['status']=='succeeded'


def answer_all(client,iv):
    for it in client.get(f'/api/v1/interviews/{iv}').json()['checklist']:
        assert client.post(f'/api/v1/checklist-items/{it["id"]}/answer',json={'content':'按推荐'}).status_code==200
