"""Version-bound dependency, pytest, startup and prerequisite checks for isolated Python apps."""
import copy
import hashlib
import json
import os
import re
import socket
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from .check_sandbox import command_run, environment, sandbox_profile, stop_process
from .codex import JOB_CONTEXT
from .core import Problem
from .execution import manifest, relative_path, sha, validate_text
from .planning import digest
from .schemas import CodeBundle, VerificationOut, AdoptionPlanOut
from .store import Confirmation, Verification

ALLOWED_PACKAGES = {'fastapi','uvicorn','pydantic','pydantic-settings','pytest','httpx','sqlalchemy',
    'alembic','python-multipart','pypdf','python-docx','openai','tenacity','pillow','requests','anyio'}


def requirements(text):
    result=[]
    for line in text.splitlines():
        line=line.split('#',1)[0].strip()
        if not line:continue
        match=re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)(?:(?:==|>=|<=|~=|!=|>|<)[0-9][A-Za-z0-9.*+_-]*(?:,(?:==|>=|<=|~=|!=|>|<)[0-9][A-Za-z0-9.*+_-]*)*)?',line)
        if not match or match[1].lower().replace('_','-') not in ALLOWED_PACKAGES:
            raise Problem('DEPENDENCY_REVIEW','依赖包含未支持的来源或包名，未执行安装；请先检查依赖清单。',409)
        result.append(line)
    if not result or len(result)>40:
        raise Problem('DEPENDENCY_REVIEW','未找到支持的Python依赖清单。',409)
    return '\n'.join(result)+'\n'


class Verifications:
    def __init__(self, executions):
        self.executions=executions
        self.store=executions.store
        self.base=Path(self.store.settings.data_dir).resolve()/'verifications'
        self.previews={}

    def assert_execution(self, execution_id, revision=None, expected_digest=None):
        run=self.store.execution(execution_id)
        if run.status!='generated':
            raise Problem('CODE_REQUIRED','请先完成当前开发执行，再运行检查。',409)
        proposal=self.store.proposal(run.interview_id)
        self.executions.planning.assert_current(run.interview_id,proposal.source_digest,run.proposal_id)
        files=self.executions.current_files(run)
        if revision and (run.data['revision']!=revision or digest(run.data['manifest'])!=expected_digest):
            raise Problem('CHECK_SOURCE_CHANGED','代码版本已改变，请检查最新版本。',409)
        return run,files

    def prepare(self, interview_id, body):
        if not body.get('accepted'):
            raise Problem('CONFIRM_REQUIRED','请确认在隔离副本安装依赖、运行测试及本机预览。',409)
        execution,files=self.assert_execution(body['execution_id'],body['revision'],body['manifest_digest'])
        if execution.interview_id!=interview_id:
            raise Problem('NOT_FOUND','执行记录不属于此产品。',404)
        requirements(files.get('requirements.txt',files.get('backend/requirements.txt','')))
        return Verification(id=str(uuid.uuid4()),interview_id=interview_id,execution_id=execution.id,
            revision=execution.data['revision'],manifest_digest=digest(execution.data['manifest']),
            status='pending',data={'steps':[],'blockers':[]})

    def root(self, record):
        if not re.fullmatch(r'[a-f0-9-]{36}',record.id):raise Problem('PATH_INVALID','检查目录记录无效。',409)
        root=self.base/record.id
        if any(p.is_symlink() for p in (root,*root.parents)):
            raise Problem('PATH_INVALID','检查目录含符号链接。',409)
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        return root

    def save(self, record, status=None):
        self.store.save_verification(record.id,status or record.status,record.data)
        if status:record.status=status
        root=self.root(record)
        temporary=root/'report.tmp'
        temporary.write_text(json.dumps({'schema_version':1,'verification_id':record.id,'execution_id':record.execution_id,
            'revision':record.revision,'status':record.status,**record.data},ensure_ascii=False,indent=2))
        os.replace(temporary,root/'report.json')

    def step(self, record, name, status, summary='', **values):
        row={'name':name,'status':status,'summary':summary,**values}
        existing=next((i for i,s in enumerate(record.data['steps']) if s['name']==name),None)
        if existing is None:record.data['steps'].append(row)
        else:record.data['steps'][existing]=row
        self.save(record)
        ctx=JOB_CONTEXT.get()
        if ctx:self.store.event(ctx['job_id'],'progress',{'phase':name+' · '+status,'verification_id':record.id})

    def ensure_environment(self, record, text):
        root=self.root(record);env=environment(root)
        key=hashlib.sha256((text+sys.version+str(Path(sys.base_prefix))).encode()).hexdigest()[:24]
        folder=self.base.parent/'check_envs'/key
        if any(p.is_symlink() for p in (folder,*folder.parents)):
            raise Problem('PATH_INVALID','依赖环境包含符号链接目录。',409)
        python=folder/'bin/python'
        marker=folder/'ready.json'
        if marker.is_file() and python.is_file():
            self.step(record,'依赖环境','passed','已复用相同依赖清单的独立环境。',details=marker.read_text()[:6000])
            return python
        self.step(record,'依赖环境','running','正在安装受控PyPI wheel依赖，不运行项目安装脚本。')
        folder.parent.mkdir(parents=True,exist_ok=True)
        code,out=command_run([sys.executable,'-I','-m','venv',str(folder)],root,env,timeout=60)
        if code:raise Problem('ENVIRONMENT_FAILED','独立Python环境创建失败。',409)
        req=root/'requirements-validated.txt';req.write_text(text)
        code,out=command_run([python,'-I','-m','pip','--isolated','install','--only-binary=:all:',
            '--no-input','--disable-pip-version-check','--no-cache-dir','--index-url','https://pypi.org/simple','-r',req],root,env,timeout=240)
        if code:
            self.step(record,'依赖环境','failed','依赖安装失败，未运行生成的代码。',exit_code=code,details=out)
            raise Problem('DEPENDENCY_FAILED','依赖安装失败，请查看检查报告。',409)
        code,versions=command_run([python,'-I','-m','pip','--isolated','freeze'],root,env,timeout=30)
        marker.write_text(versions)
        self.step(record,'依赖环境','passed','依赖已安装到独立环境，解析版本已记录。',exit_code=0,details=versions)
        return python

    def run(self, verification_id):
        record=self.store.verification(verification_id)
        try:
            execution,files=self.assert_execution(record.execution_id,record.revision,record.manifest_digest)
            root=self.root(record);work=root/'workspace';work.mkdir(exist_ok=True)
            # Every check has a fresh copy; pytest, migrations and preview never mutate generated revisions.
            for name,text in files.items():
                relative_path(name);path=work/name;path.parent.mkdir(parents=True,exist_ok=True)
                if path.exists() and path.read_text()!=text:raise Problem('CHECK_COPY_CHANGED','检查副本已被修改，请新建一轮检查。',409)
                path.write_text(text)
            self.save(record,'running')
            python=self.ensure_environment(record,requirements(files.get('requirements.txt',files.get('backend/requirements.txt',''))))
            record.data['python']=str(python)
            env=environment(root);profile=sandbox_profile(root,python)
            self.step(record,'自动化测试','running','正在隔离副本运行pytest。')
            paths=[name for name in ('tests','backend/tests') if (work/name).is_dir()]
            if not paths:raise Problem('TESTS_MISSING','没有可执行的测试目录，不能记为通过。',409)
            report=root/'pytest.xml'
            code,out=command_run([python,'-m','pytest',*paths,'-q','--tb=short','--junitxml='+str(report)],work,env,timeout=240,profile=profile)
            passed=failed=skipped=0
            if report.is_file() and report.stat().st_size<2*1024*1024:
                try:
                    for case in ET.parse(report).iter('testcase'):
                        if case.find('failure') is not None or case.find('error') is not None:failed+=1
                        elif case.find('skipped') is not None:skipped+=1
                        else:passed+=1
                except ET.ParseError:pass
            tests_ok=code==0 and passed>0 and failed==0 and skipped==0
            self.step(record,'自动化测试','passed' if tests_ok else 'failed',
                f'通过{passed}，失败{failed}，跳过{skipped}；退出码{code}。',exit_code=code,
                passed=passed,failed=failed,skipped=skipped,details=out)
            blockers=['真实业务链路与人工样例验收尚待完成']
            preflight_ok=True
            if (work/'scripts/smoke_preflight.py').is_file():
                self.step(record,'真实业务前置检查','running','只检查配置条件，不调用付费模型。')
                pre_code,pre_out=command_run([python,'-m','scripts.smoke_preflight'],work,env,timeout=30,profile=profile)
                try:
                    pre=json.loads(pre_out.splitlines()[-1]);blockers=list(pre.get('blockers',[]))
                    if pre.get('clinical_review')=='pending':blockers.append('医学专业审核待完成')
                    if pre.get('product_scoring')=='pending':blockers.append('产品经理样例评分待完成')
                    preflight_ok=pre_code in (0,2)
                except (ValueError,IndexError):blockers=['前置检查脚本报错，尚未得到有效报告'];preflight_ok=False
                self.step(record,'真实业务前置检查',('pending' if blockers else 'passed') if preflight_ok else 'failed',
                    '真实业务尚有待验条件。' if blockers else '前置配置检查通过，仍需真实样例验收。',exit_code=pre_code,details=pre_out)
            record.data['blockers']=blockers
            if tests_ok:
                self.start_preview(record,python,work)
            else:
                self.step(record,'本地启动','not_run','先修复自动化测试，再启动本地预览。')
            self.assert_execution(record.execution_id,record.revision,record.manifest_digest)
            self.save(record,'passed' if tests_ok and preflight_ok and record.data.get('preview_url') else 'failed')
            if record.status=='failed':raise Problem('CHECKS_FAILED','检查发现失败项，请查看报告后修复并重检。',409)
            return {'verification_id':record.id,'status':record.status,'preview_url':record.data.get('preview_url')}
        except Exception as exc:
            record.data['error']={'code':exc.code,'message':exc.message} if isinstance(exc,Problem) else {
                'code':'CHECK_FAILED','message':'检查过程失败，已有报告保留。'}
            self.save(record,'cancelled' if record.data['error']['code']=='CANCELLED' else 'failed')
            raise

    def start_preview(self, record, python, work):
        root=self.root(record)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=environment(root,port);profile=sandbox_profile(root,python,port)
        if (work/'scripts/migrate.py').is_file():
            code,out=command_run([python,'-m','scripts.migrate'],work,env,timeout=60,profile=profile)
            self.step(record,'数据库迁移','passed' if code==0 else 'failed','仅使用本轮临时数据库。',exit_code=code,details=out)
            if code:raise Problem('MIGRATION_FAILED','隔离数据库迁移失败。',409)
        if not (work/'backend/app/main.py').is_file():raise Problem('PREVIEW_UNSUPPORTED','未找到支持的FastAPI启动入口。',409)
        self.step(record,'本地启动','running','正在启动回环地址上的隔离应用。')
        logfile=(root/'preview.txt').open('w')
        launcher=root/'preview_runner.py'
        launcher.write_text('''import os,signal,sys,threading,time
parent=os.getppid()
def watch():
    while os.getppid()==parent: time.sleep(.3)
    if os.getpgrp()==os.getpid(): os.killpg(os.getpgrp(),signal.SIGKILL)
    os._exit(1)
threading.Thread(target=watch,daemon=True).start()
sys.path.insert(0,sys.argv[1])
import uvicorn
uvicorn.run('backend.app.main:app',host='127.0.0.1',port=int(sys.argv[2]),workers=1,access_log=False,log_level='error')
''')
        proc=subprocess.Popen(['/usr/bin/sandbox-exec','-f',str(profile),str(python),'-I',str(launcher),str(work),str(port)],
            cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=logfile,stderr=logfile,start_new_session=True)
        self.previews[record.id]=(proc,logfile)
        base=f'http://127.0.0.1:{port}'
        for _ in range(100):
            ctx=JOB_CONTEXT.get()
            if ctx and ctx['cancelled']():break
            if proc.poll() is not None:break
            try:
                with httpx.Client(trust_env=False,timeout=.3) as client:
                    if client.get(base+'/api/v1/health').status_code==200:
                        url=base+'/acceptance'
                        page=client.get(url)
                        if page.status_code==404:url=base
                        record.data['preview_url']=url
                        record.data['preview_pid']=proc.pid
                        self.step(record,'本地启动','passed','健康接口已返回200，隔离预览已启动。',exit_code=0)
                        if (work/'backend/app/repositories/identity.py').is_file():
                            record.data['preview_login_available']=True
                        return
            except httpx.HTTPError:pass
            time.sleep(.1)
        self.stop_preview(record.id)
        self.step(record,'本地启动','failed','应用启动或健康检查失败，请查看隔离运行配置。')
        raise Problem('PREVIEW_FAILED','隔离应用未通过启动检查。',409)

    def stop_preview(self, verification_id):
        entry=self.previews.pop(verification_id,None)
        if entry:stop_process(entry[0]);entry[1].close()

    def close(self):
        for key in list(self.previews):self.stop_preview(key)

    def output(self, record):
        try:self.assert_execution(record.execution_id,record.revision,record.manifest_digest);changed=False
        except Problem:changed=True
        url=record.data.get('preview_url') if record.id in self.previews and not changed else None
        return VerificationOut(id=record.id,execution_id=record.execution_id,revision=record.revision,
            manifest_digest=record.manifest_digest,status=record.status,source_changed=changed,
            steps=record.data.get('steps',[]),preview_url=url,evidence_path=str(self.root(record)/'report.json'),
            preview_login_available=bool(url and record.data.get('preview_login_available')),
            blockers=record.data.get('blockers',[]),accepted=record.data.get('accepted',False),
            adopted=record.data.get('adopted',False),
            error=record.data.get('error'),updated_at=record.updated_at)

    def preview_login(self, verification_id):
        record=self.store.verification(verification_id)
        out=self.output(record)
        if record.status!='passed' or not out.preview_url or not out.preview_login_available:
            raise Problem('PREVIEW_NOT_READY','本地预览尚未就绪，请先运行工程检查。',409)
        root=self.root(record);work=root/'workspace';config=root/'preview-credentials.json'
        python=Path(record.data['python']);port=int(out.preview_url.split(':')[2].split('/')[0])
        if not config.exists():
            script=root/'seed_preview.py'
            script.write_text('''import json,os,sys
sys.path.insert(0,sys.argv[1])
from backend.app.repositories.database import Database
from backend.app.repositories.identity import IdentityRepository
database=Database(os.environ['DATABASE_PATH'])
owner,code=IdentityRepository(database).provision(lifetime_seconds=3600)
fd=os.open(sys.argv[2],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as stream: json.dump({'code':code},stream)
database.close()
''')
            status,_=command_run([python,'-I',script,work,config],work,environment(root,port),
                timeout=30,profile=sandbox_profile(root,python,port))
            if status:raise Problem('PREVIEW_LOGIN_FAILED','本地验收账号创建失败，请检查临时数据库。',409)
        base=f'http://127.0.0.1:{port}'
        with httpx.Client(trust_env=False,timeout=5) as client:
            response=client.post(base+'/api/v1/auth/login',headers={'Origin':base},
                json={'invite_code':json.loads(config.read_text())['code'],'adult_confirmed':True})
        if response.status_code!=200:
            raise Problem('PREVIEW_LOGIN_FAILED','本地验收账号登录失败，未显示任何凭据。',409)
        return out.preview_url,response.headers.get_list('set-cookie')

    def adoption_plan(self, verification_id):
        record=self.store.verification(verification_id)
        run,files=self.assert_execution(record.execution_id,record.revision,record.manifest_digest)
        if record.status!='passed':raise Problem('CHECKS_REQUIRED','请先通过本版本工程检查。',409)
        target=self.executions.workflow._safe_target(self.store.get_interview(record.interview_id).product_name)
        initial=next((s.get('before_revision') for s in run.data['steps'] if s.get('before_revision')),None)
        original=self.executions.read_files(self.executions.revision_path(run.id,initial)) if initial else {}
        entries=[];conflicts=[]
        for name,text in sorted(files.items()):
            relative_path(name);path=target/name
            if any(p.is_symlink() for p in (path,*path.parents)):
                raise Problem('PATH_INVALID','目标代码目录含符号链接，未准备采用。',409)
            try:before=path.read_text() if path.exists() else None
            except (OSError,UnicodeError):
                conflicts.append(name);continue
            if before==text:continue
            before_hash=sha(before) if before is not None else 'new'
            expected=sha(original[name]) if name in original else 'new'
            if before_hash!=expected:conflicts.append(name)
            entries.append({'path':name,'action':'新增' if before is None else '替换已确认旧版',
                'before_sha256':before_hash,'after_sha256':sha(text)})
        plan={'verification_id':record.id,'target':str(target),'files':entries,'conflicts':conflicts}
        return AdoptionPlanOut(**plan,plan_digest=digest(plan),adopted=record.data.get('adopted',False))

    def adopt(self, verification_id, body):
        record=self.store.verification(verification_id)
        if record.data.get('adopted'):
            return self.adoption_plan(record.id)
        plan=self.adoption_plan(record.id)
        if not body.accepted or body.plan_digest!=plan.plan_digest:
            raise Problem('ADOPTION_CHANGED','采用范围已改变，请重新审阅文件清单。',409)
        if plan.conflicts:
            raise Problem('FILE_CONFLICT','目标文件有独立改动，未采用，请先人工核对冲突。',409)
        run,files=self.assert_execution(record.execution_id,record.revision,record.manifest_digest)
        target=Path(plan.target);root=self.root(record)
        record.data['adoption_plan']=plan.model_dump();self.save(record)
        for entry in plan.files:
            path=target/entry.path
            if any(p.is_symlink() for p in (path,*path.parents)):raise Problem('PATH_INVALID','目标路径被改变，已停止采用。',409)
            current=path.read_text() if path.exists() else None
            current_hash=sha(current) if current is not None else 'new'
            if current_hash==entry.after_sha256:continue
            if current_hash!=entry.before_sha256:raise Problem('FILE_CONFLICT','目标代码在采用期间改变，已停止，已写文件和备份保留。',409)
            path.parent.mkdir(parents=True,exist_ok=True)
            if current is not None:
                backup=root/'adoption-backup'/entry.path;backup.parent.mkdir(parents=True,exist_ok=True)
                if not backup.exists():backup.write_text(current)
            temporary=path.with_name('.factory-adopt-'+uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('x') as stream:
                    stream.write(files[entry.path]);stream.flush();os.fsync(stream.fileno())
                if current is None:os.link(temporary,path)
                else:os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
        record.data['adopted']=True;self.save(record)
        self.store.add(Confirmation(interview_id=record.interview_id,kind='adopt_code',data={
            'verification_id':record.id,'revision':record.revision,'plan_digest':plan.plan_digest,'files':[f.path for f in plan.files]}))
        return self.adoption_plan(record.id)

    def accept(self, verification_id, body):
        record=self.store.verification(verification_id)
        self.assert_execution(record.execution_id,body.revision,record.manifest_digest)
        if body.revision!=record.revision or record.status!='passed' or not body.accepted:
            raise Problem('CHECK_ACCEPTANCE_BLOCKED','请先通过当前版本工程检查并明确确认。',409)
        self.store.add(Confirmation(interview_id=record.interview_id,kind='technical_acceptance',data={
            'verification_id':record.id,'revision':record.revision,'comment':body.comment,
            'business_blockers':record.data.get('blockers',[])}))
        record.data['accepted']=True;self.save(record)
        return self.output(record)

    def repair(self, verification_id, feedback=''):
        record=self.store.verification(verification_id)
        execution,files=self.assert_execution(record.execution_id,record.revision,record.manifest_digest)
        if record.status!='failed':raise Problem('REPAIR_NOT_REQUIRED','当前检查没有失败项。',409)
        source=self.executions.planning.source(execution.interview_id)
        proposal=self.store.proposal(execution.interview_id)
        bundle,_=self.executions.gateway.complete('development_code',{'prd':source['prd'],
            'decision_ledger':source['decision_ledger'],'technical_proposal':proposal.data,
            'current_task':{'id':'T99','phase':proposal.data['phases'][0]['stage'],'title':'修复当前工程检查失败',
                'goal':'修复报告中的真实失败。已有tests和backend/tests文件完全只读，不删改已有测试或断言；可新增回归测试。只修复相关源码和必要配置，不伪造通过。',
                'deliverables':['当前失败相关源码或配置'],'checks':['重新运行完整检查']},
            'check_failures':record.data['steps'],'feedback':feedback,
            'files':[{'path':p,'sha256':sha(t),'content':t} for p,t in sorted(files.items())],
            'saved_batches':0,'batch_number':1},CodeBundle,max_tokens=12000)
        self.assert_execution(record.execution_id,record.revision,record.manifest_digest)
        if bundle.questions:raise Problem('REPAIR_INPUT_REQUIRED','修复需要补充：'+'；'.join(bundle.questions),409)
        updated=dict(files)
        for change in bundle.changes:
            relative_path(change.path);validate_text(change.content)
            if change.before_sha256!=(sha(files[change.path]) if change.path in files else 'new'):
                raise Problem('CODE_BASE_CHANGED','修复基准已改变，未写入改动。',409)
            if change.path in files and change.path.startswith(('tests/','backend/tests/')):
                raise Problem('TEST_CHANGE_REVIEW','自动修复不改已有测试；如测试本身有误须单独审阅。',409)
            updated[change.path]=change.content
        if manifest(updated)==manifest(files):raise Problem('CODE_NO_CHANGES','没有可采用的修复改动。',409)
        self.executions.check_size(updated)
        revision,folder=self.executions.write_revision(execution.id,updated)
        checks=self.executions.syntax_checks(folder,updated,bundle.changes)
        if any(c['status']=='failed' for c in checks):raise Problem('CODE_CHECK_FAILED','修复代码语法检查失败，原版本保留。',409)
        execution.data=copy.deepcopy(execution.data)
        execution.data.setdefault('repairs',[]).append({'verification_id':record.id,'before_revision':record.revision,
            'after_revision':revision,'summary':bundle.summary,'paths':[c.path for c in bundle.changes]})
        execution.data.update(revision=revision,manifest=manifest(updated))
        self.store.save_execution(execution.id,'generated',execution.data)
        next_record=self.prepare(execution.interview_id,{'accepted':True,'execution_id':execution.id,
            'revision':revision,'manifest_digest':digest(manifest(updated))})
        self.store.add(next_record)
        return self.run(next_record.id)
