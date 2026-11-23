"""Fixed subprocess transport for generated code; no user/model supplied shell commands."""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from .codex import JOB_CONTEXT
from .core import Problem


def clean_output(text):
    lines = []
    for line in text.splitlines():
        if re.search(r'(?:authorization|set-cookie|csrf|password|api[_-]?key)\s*[:=]',line,re.I):
            line = '[凭据相关诊断已隐藏]'
        line = re.sub(r'(?:sk-[A-Za-z0-9_-]{12,}|AKID[A-Za-z0-9]{10,})','[已隐藏]',line)
        lines.append(line)
    return '\n'.join(lines)[-18000:]


def environment(root, port=0):
    root = Path(root)
    for name in ('home','tmp','state'):
        (root/name).mkdir(parents=True,exist_ok=True,mode=0o700)
    return {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin', 'HOME':str(root/'home'), 'TMPDIR':str(root/'tmp'),
        'LANG':'en_US.UTF-8','PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1',
        'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONHASHSEED':'0', 'APP_ENV':'local',
        'ALLOW_INSECURE_LOCAL_COOKIE':'true','DATABASE_PATH':str(root/'state/preview.sqlite3'),
        'TRUSTED_ORIGINS':f'http://127.0.0.1:{port or 8000}', 'MODEL_API_KEY':''}


def sandbox_profile(root, python, port=0):
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise Problem('SANDBOX_UNAVAILABLE','当前环境没有已验证的进程隔离器，未运行生成代码。',409)
    q = lambda p: json.dumps(str(Path(p).resolve()),ensure_ascii=False)
    reads = [root, Path(python).absolute().parents[1], Path(sys.base_prefix).resolve()]
    allowed = '(require-any '+' '.join('(subpath '+q(p)+')' for p in reads)+')'
    # OS libraries and metadata remain readable; user projects, credentials and user documents do not.
    profile = '(version 1)\n(allow default)\n'
    profile += '(deny file-read-data (require-all (subpath "/Users") (require-not '+allowed+')))\n'
    profile += '(deny file-write* (require-not (require-any (subpath '+q(root)+') (literal "/dev/null"))))\n'
    if port:
        # Server replies are allowed; outbound connects to other ports/hosts are denied.
        profile += f'(deny network-outbound (require-not (remote ip "localhost:{int(port)}")))\n'
        profile += f'(deny network-inbound (require-not (local ip "localhost:{int(port)}")))\n'
    else:
        profile += '(deny network*)\n'
    path = Path(root)/('preview.sb' if port else 'checks.sb')
    path.write_text(profile)
    return path


def stop_process(proc):
    if proc.poll() is None:
        try:
            os.killpg(proc.pid,signal.SIGTERM);proc.wait(timeout=3)
        except (OSError,subprocess.TimeoutExpired):
            try:os.killpg(proc.pid,signal.SIGKILL)
            except OSError:pass
            proc.wait()


def command_run(argv, cwd, env, timeout=180, profile=None):
    root = Path(env['TMPDIR']).parent
    args=list(map(str,argv))
    if profile:
        guard=root/'process_guard.py'
        guard.write_text('''import os,signal,subprocess,sys,threading,time
parent=os.getppid()
def watch():
    while os.getppid()==parent: time.sleep(.3)
    if os.getpgrp()==os.getpid(): os.killpg(os.getpgrp(),signal.SIGKILL)
    os._exit(1)
threading.Thread(target=watch,daemon=True).start()
raise SystemExit(subprocess.call(sys.argv[1:]))
''')
        args=['/usr/bin/sandbox-exec','-f',str(profile),args[0],'-I',str(guard),*args]
    log = root/('process-'+str(time.time_ns())+'.txt')
    ctx=JOB_CONTEXT.get()
    try:
        with log.open('w+') as stream:
            proc=subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=stream,
                stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+timeout
            try:
                while proc.poll() is None:
                    if ctx and ctx['cancelled']():
                        raise Problem('CANCELLED','检查已停止，已有报告保留。',409)
                    if time.monotonic()>deadline:
                        raise Problem('CHECK_TIMEOUT','本项检查超时，报告和代码保留。',504)
                    if log.stat().st_size>2*1024*1024:
                        raise Problem('CHECK_OUTPUT_LIMIT','检查输出过多，已停止进程。',409)
                    time.sleep(.1)
                stream.seek(0);output=clean_output(stream.read())
            finally:
                stop_process(proc)
    finally:
        # Interrupted commands are sanitized too; no raw diagnostics remain on disk.
        log.write_text(clean_output(log.read_text(errors='replace')) if log.exists() else '')
    return proc.returncode,output
