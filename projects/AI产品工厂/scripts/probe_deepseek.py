"""CLI wrapper for the fixed synthetic transport also used by the web demo."""
import json
import sys
from pathlib import Path
import httpx

PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT/'backend'))
from app.synthetic_demo import ENDPOINT, MODEL, FIXED_PROMPT, SyntheticReply, run_probe
SECRET = PRODUCT.parent/'医美小助手/backend/.env'


def main():
    if SECRET.is_symlink() or not SECRET.is_file():
        raise SystemExit('缺少安全本地配置，未调用。')
    key = next((line.split('=', 1)[1].strip() for line in SECRET.read_text().splitlines()
                if line.startswith('MODEL_API_KEY=')), '')
    if not key:
        raise SystemExit('密钥未配置，未调用。')
    # A fixed record prevents accidental repeats, including after an ambiguous timeout/restart.
    root = PRODUCT / '.runtime/deepseek-synthetic-20261123'
    with httpx.Client(timeout=httpx.Timeout(40, connect=10), follow_redirects=False,
                      trust_env=False, transport=httpx.HTTPTransport(retries=0)) as client:
        result = run_probe(key, root, client)
    output = PRODUCT / 'docs/DeepSeek虚构数据联调记录.md'
    output.write_text('# DeepSeek虚构数据联调记录（脚本自动生成）\n\n'
        '仅发送固定虚构标记，不读取PRD、访谈、医学资料或用户输入。不是医美业务冒烟，不启用真实问答。\n\n'
        '```json\n' + json.dumps(result, ensure_ascii=False, indent=2) + '\n```\n')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
