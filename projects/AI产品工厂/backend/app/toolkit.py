"""按当前业务阶段装配工具包原文，不读取其他产品或本机秘密。"""
from .core import KIT_ROOT, Problem


def toolkit_context(root=KIT_ROOT, phase='prd'):
    parts = []
    for relative in (('AGENTS.md',) if phase == 'development' else ('AGENTS.md', 'docs/PRD模板.md')):
        path = root / relative
        if not path.is_file():
            raise Problem('TOOLKIT_INCOMPLETE', '工具包规则或模板缺失，未发起模型任务。', 503)
        value = path.read_text()
        if phase == 'development':
            try:
                value = value[value.index('## 2. 强制底线'):value.index('## 3. 追问规则')]
            except ValueError:
                raise Problem('TOOLKIT_INCOMPLETE', '工具包底线章节已变化，未开始编码。', 503)
        parts.append(f'\n<toolkit-source path="{relative}">\n{value}\n</toolkit-source>')
    manual = root / 'docs/手册/AI产品Vibe Coding通用技术栈手册.md'
    if not manual.is_file():
        raise Problem('TOOLKIT_INCOMPLETE', '工具包通用手册缺失，未发起模型任务。', 503)
    text = manual.read_text()
    try:
        if phase == 'development':
            start = text.index('## 九、工程底线与默认实现规则')
            end = text.index('## 十、双层验收体系', start)
        elif phase == 'technical':
            start = text.index('# 第一部分：使用说明')
            end = text.index('# 第二部分：AI 执行规范', start)
        else:
            start = text.index('## 二、与 PRD')
            end = text.index('## 三、默认后端', start)
    except ValueError:
        raise Problem('TOOLKIT_INCOMPLETE', '工具包手册章节已变化，请先核对模板。', 503)
    parts.append(text[start:end])
    return '\n'.join(parts)
