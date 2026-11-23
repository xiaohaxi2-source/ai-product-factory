"""verify.sh lifecycle hook: actual API, separate process, isolated database."""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

PRODUCT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ['RESTART_CHECK_DIR'])
MODE = sys.argv[1]

if MODE == 'start':
    state = ROOT / 'server.json'
    if state.exists():
        port = json.loads(state.read_text())['port']
    else:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', port)) == 0:
            sys.exit('检查端口被占用，未停止其他进程。')
    env = dict(os.environ, DATA_DIR=str(ROOT/'data'), PROJECTS_DIR=str(ROOT/'projects'),
               PORT=str(port), MODEL_API_KEY='', CODEX_BIN='/disabled-in-restart-check')
    with (ROOT/'server.log').open('a') as log:
        proc = subprocess.Popen([sys.executable, str(PRODUCT/'scripts/server.py')],
            env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    state.write_text(json.dumps({'port': port, 'pid': proc.pid}))
    for _ in range(100):
        if proc.poll() is not None:
            sys.exit('检查服务启动失败。')
        try:
            if httpx.get(f'http://127.0.0.1:{port}/api/v1/health',timeout=.3).status_code == 200:
                print(proc.pid)
                break
        except httpx.HTTPError:
            pass
        time.sleep(.1)
    else:
        proc.terminate()
        sys.exit('检查服务启动超时。')
elif MODE in ('write','read'):
    port = json.loads((ROOT/'server.json').read_text())['port']
    base = f'http://127.0.0.1:{port}/api/v1/interviews'
    if MODE == 'write':
        response = httpx.post(base,data={'product_name':'重启恢复检查','idea':sys.argv[2]})
        response.raise_for_status()
        # Persist an actual code revision and an in-flight checkpoint, not only a chat marker.
        sys.path.insert(0, str(PRODUCT/'backend'))
        from app.core import Settings
        from app.store import Store, Execution, Verification
        from app.workflow import Workflow
        from app.planning import Planning
        from app.execution import Executions, manifest
        store = Store(Settings(data_dir=str(ROOT/'data')))
        service = Executions(Planning(Workflow(None,store,ROOT/'projects')))
        execution_id = str(uuid.uuid4())
        files = {'README.md':sys.argv[2]}
        revision, _ = service.write_revision(execution_id,files)
        store.add(Execution(id=execution_id,interview_id=response.json()['id'],proposal_id=0,
            plan_digest='0'*64,status='running',data={'version':1,'revision':revision,'manifest':manifest(files),
                'steps':[{'task_id':'T1','title':'恢复检查','status':'running'}]}))
        verification_id=str(uuid.uuid4())
        service_record=Verification(id=verification_id,interview_id=response.json()['id'],execution_id=execution_id,
            revision=revision,manifest_digest='0'*64,status='running',data={'steps':[{'name':'恢复检查','status':'running','summary':sys.argv[2]}],'blockers':[]})
        store.add(service_record)
        store.engine.dispose()
        print(verification_id)
    else:
        response = httpx.get(f'http://127.0.0.1:{port}/api/v1/verifications/'+sys.argv[2])
        response.raise_for_status()
        check=response.json()
        assert check['status']=='interrupted'
        response=httpx.get(f'http://127.0.0.1:{port}/api/v1/executions/'+check['execution_id']);response.raise_for_status()
        run=response.json()
        assert run['status']=='interrupted' and run['steps'][0]['status']=='interrupted'
        workspace = Path(run['workspace'])
        assert workspace.is_relative_to(ROOT.resolve())
        print((workspace/'README.md').read_text())
elif MODE == 'stop':
    # verify.sh owns termination; no broad process matching or data deletion.
    print('done')
else:
    sys.exit('unknown action')
