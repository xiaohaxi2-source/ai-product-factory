"""Version-bound technical planning. Does not execute code or advance kit stages."""
import hashlib
import json
import os
import uuid
from pathlib import Path

from .core import Problem
from .schemas import (DevelopmentContent, DevelopmentOut, TargetStatus, TechnicalContent,
                      TechnicalOut, WorkbenchOut)
from .store import Asset, Confirmation, DevelopmentPlan, TechnicalProposal


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def render_proposal(data):
    p = TechnicalContent.model_validate(data)
    lines = ['# 技术适配声明', '', '> 本文件是待采用的方案；检查与实现均未自动执行。', '',
             '## 1. 产品形态判断', p.summary, '', '## 2. 采用的默认方案']
    lines += ['- ' + s for s in p.default_stack]
    lines += ['', '## 3. 按需模块']
    lines += [f'- {m.name}：{m.choice}。{m.reason}。位置：{m.location}' for m in p.modules] or ['- 无额外模块。']
    lines += ['', '## 4. 费用影响与暂缓项', p.cost_impact]
    lines += ['- ' + s for s in p.deferred]
    lines += ['', '## 5. 验证计划（尚未执行）'] + ['- ' + s for s in p.verification]
    lines += ['', '## 6. 需要产品经理决定的问题']
    for q in p.questions:
        lines += [f'- {q.question}', f'  原因：{q.why}', '  选项：' + ' / '.join(q.options),
                  f'  推荐：{q.recommendation}', f'  不选的后果：{q.consequence}']
    if not p.questions:
        lines.append('- 无。')
    lines += ['', '## 7. 子阶段规划']
    for phase in p.phases:
        lines += [f'### {phase.stage}', phase.goal, '产物：' + '、'.join(phase.deliverables),
                  '验收：' + phase.acceptance, '']
    return '\n'.join(lines).strip() + '\n'


def render_tasks(data):
    plan = DevelopmentContent.model_validate(data)
    lines = ['# 开发任务清单', '', '> 待执行任务，尚未生成或验证代码。', '', plan.note]
    for t in plan.tasks:
        lines += ['', f'## {t.id} {t.title}', f'- 阶段：{t.phase}', f'- 目标：{t.goal}',
                  '- 输入：' + '；'.join(t.inputs), '- 产物：' + '；'.join(t.deliverables),
                  '- 检查：' + '；'.join(t.checks), '- 人工确认：' + ('；'.join(t.confirmations) or '无'),
                  '- 依赖：' + ('、'.join(t.depends_on) or '无')]
    return '\n'.join(lines).strip() + '\n'


class Planning:
    def __init__(self, workflow):
        self.workflow = workflow
        self.store = workflow.store
        self.gateway = workflow.gateway

    def read(self, path, root):
        path, root = Path(path), Path(root).resolve()
        if not path.is_absolute() or not path.is_relative_to(root):
            raise Problem('PATH_INVALID', '方案来源路径不在对应产品内。', 422)
        current = path
        while current != root:
            if current.is_symlink():
                raise Problem('PATH_INVALID', '文档路径含符号链接，已停止读取。', 422)
            current = current.parent
        if not path.is_file():
            raise Problem('SOURCE_MISSING', '已确认的PRD或台账文件缺失，请先恢复文件。', 409)
        if path.stat().st_size > 256 * 1024:
            raise Problem('SOURCE_TOO_LARGE', '来源文档过大，请先整理到256KB内。', 422)
        try:
            return path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            raise Problem('SOURCE_INVALID', '来源文档无法读取为UTF-8文本。', 422)

    def source(self, interview_id):
        iv = self.store.get_interview(interview_id)
        if iv.status != 'closed':
            raise Problem('PRD_REQUIRED', '请先完成PRD、体检清单并确认写回，再生成技术方案。', 409)
        root = self.workflow._safe_target(iv.product_name)
        asset = self.store.asset(iv.id, 'export_plan')
        if not asset:
            raise Problem('SOURCE_MISSING', '没有已确认PRD的导出记录。', 409)
        files = list(asset.data.get('files', {}))
        prds = [p for p in files if Path(p).name == 'PRD.md' and Path(p).parent.name == 'PRD']
        ledgers = [p for p in files if Path(p).name == '项目状态.md']
        if len(prds) != 1 or len(ledgers) != 1:
            raise Problem('SOURCE_MISSING', 'PRD来源记录不完整。', 409)
        prd, ledger = self.read(prds[0], root), self.read(ledgers[0], root)
        return {'prd': prd, 'decision_ledger': ledger, 'root': root,
                'source_digest': digest([prd, ledger])}

    def assert_current(self, interview_id, source_digest, proposal_id=None):
        source = self.source(interview_id)
        if source['source_digest'] != source_digest:
            raise Problem('SOURCE_CHANGED', 'PRD或台账已变化，请重新生成技术方案。', 409)
        if proposal_id and self.store.proposal(interview_id).id != proposal_id:
            raise Problem('PROPOSAL_STALE', '方案已有新版本，请刷新后确认最新版本。', 409)
        return source

    def check_confirmation(self, interview_id, payload):
        source = self.source(interview_id)
        proposal = self.store.proposal(interview_id)
        if not proposal:
            raise Problem('PROPOSAL_REQUIRED', '请先生成技术方案。', 409)
        if (not payload.get('accepted') or payload.get('proposal_version') != proposal.version
                or payload.get('proposal_digest') != proposal.proposal_digest
                or payload.get('source_digest') != proposal.source_digest):
            raise Problem('PROPOSAL_STALE', '请审阅并确认页面显示的最新方案版本。', 409)
        if source['source_digest'] != proposal.source_digest:
            raise Problem('SOURCE_CHANGED', 'PRD或台账已变化，请重新生成方案。', 409)
        if proposal.data['questions']:
            raise Problem('QUESTIONS_PENDING', '技术方案还有待答问题，请在修改意见里回答后再确认。', 409)
        return proposal, source

    def export(self, proposal, filename, content):
        root = self.workflow._safe_target(self.store.get_interview(proposal.interview_id).product_name)
        path = root / 'docs' / '工厂方案' / f'访谈-{proposal.interview_id}' / filename
        kind = f'planning_export_{proposal.id}_{filename}'
        plan = self.store.asset(proposal.interview_id, kind)
        if not plan:
            plan = Asset(interview_id=proposal.interview_id, kind=kind,
                         data={'path': str(path), 'content': content})
            self.store.add(plan)
        if plan.data['path'] != str(path) or plan.data['content'] != content:
            raise Problem('FILE_CONFLICT', '导出计划与方案版本不一致，未覆盖文件。', 409)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if self.read(path, root) != content:
                raise Problem('FILE_CONFLICT', '方案文件已有不同内容，未覆盖，请检查文件。', 409)
        else:
            temporary = path.with_name('.factory-' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('x', encoding='utf-8') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if self.read(path, root) != content:
                        raise Problem('FILE_CONFLICT', '方案文件有并发变更，未覆盖。', 409)
            finally:
                temporary.unlink(missing_ok=True)
        return [str(path)]

    def generate(self, interview_id, feedback=''):
        source = self.source(interview_id)
        previous = self.store.proposal(interview_id)
        if feedback and not previous:
            raise Problem('PROPOSAL_REQUIRED', '请先生成技术方案再提出修改。', 409)
        content, _ = self.gateway.complete('technical_proposal', {
            'prd': source['prd'], 'decision_ledger': source['decision_ledger'],
            'previous_proposal': previous.data if previous else None,
            'revision_request': feedback,
        }, TechnicalContent)
        self.assert_current(interview_id, source['source_digest'])
        data = content.model_dump()
        proposal = TechnicalProposal(interview_id=interview_id,
            version=previous.version + 1 if previous else 1, source_digest=source['source_digest'],
            proposal_digest=digest(data), data=data)
        self.store.add(proposal)
        paths = self.export(proposal, f'技术方案-v{proposal.version}.md', render_proposal(data))
        return {'proposal_version': proposal.version, 'written_paths': paths}

    def confirm_and_plan(self, interview_id, payload):
        proposal, source = self.check_confirmation(interview_id, payload)
        if not self.store.technical_approved(proposal):
            self.store.add(Confirmation(interview_id=interview_id, kind='technical_approved',
                data={'proposal_id': proposal.id, 'proposal_digest': proposal.proposal_digest,
                      'source_digest': proposal.source_digest, 'version': proposal.version}))
        plan = self.store.development_plan(proposal.id)
        if not plan:
            content, _ = self.gateway.complete('development_tasks', {
                'prd': source['prd'], 'decision_ledger': source['decision_ledger'],
                'technical_proposal': proposal.data, 'approved_version': proposal.version,
                'first_phase': proposal.data['phases'][0],
            }, DevelopmentContent)
            if any(t.phase != proposal.data['phases'][0]['stage'] for t in content.tasks):
                raise Problem('PLAN_INVALID', '任务包含未确认的阶段，已停止保存，请重试。', 502)
            self.assert_current(interview_id, proposal.source_digest, proposal.id)
            plan = DevelopmentPlan(interview_id=interview_id, proposal_id=proposal.id, data=content.model_dump())
            self.store.add(plan)
        self.assert_current(interview_id, proposal.source_digest, proposal.id)
        paths = self.export(proposal, f'技术方案-v{proposal.version}.md', render_proposal(proposal.data))
        paths += self.export(proposal, f'开发任务-v{proposal.version}.md', render_tasks(plan.data))
        return {'proposal_version': proposal.version, 'task_count': len(plan.data['tasks']), 'written_paths': paths}

    def target_status(self, interview_id):
        iv = self.store.get_interview(interview_id)
        try:
            root = self.workflow._safe_target(iv.product_name)
            def optional(name):
                p = root / 'docs' / name
                return self.read(p, root) if p.exists() else ''
            raw = optional('流程状态.txt')
            state = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
            status = optional('项目状态.md')
            last = next((line.removeprefix('- 上次停在：') for line in status.splitlines() if line.startswith('- 上次停在：')), '')
            evidence = root / 'docs/evidence'
            entries = sorted(p.name for p in evidence.iterdir() if p.is_dir() and not p.is_symlink()) if evidence.is_dir() else []
            return TargetStatus(kit_stage=state.get('阶段', '未记录'), kit_status=state.get('状态', '未记录'),
                                last_stop=last[:1000], current_task=optional('本阶段任务.md'), evidence=entries)
        except Problem:
            return TargetStatus(last_stop='目标文件不可安全读取，请检查文件路径。')

    def workbench(self, interview_id):
        iv = self.store.get_interview(interview_id)
        proposal = self.store.proposal(interview_id)
        state, changed, error = 'prd_pending', False, None
        if iv.status == 'closed':
            try:
                source = self.source(interview_id)
                changed = bool(proposal and source['source_digest'] != proposal.source_digest)
                state = 'source_changed' if changed else 'technical_ready'
            except Problem as exc:
                state, error = 'source_unavailable', exc.message
        out, development = None, None
        if proposal:
            folder = self.workflow.projects_dir.resolve() / iv.product_name / 'docs/工厂方案' / f'访谈-{iv.id}'
            out = TechnicalOut(version=proposal.version, source_digest=proposal.source_digest,
                proposal_digest=proposal.proposal_digest, content=TechnicalContent.model_validate(proposal.data),
                markdown=render_proposal(proposal.data), confirmed=self.store.technical_approved(proposal),
                paths=[str(folder/f'技术方案-v{proposal.version}.md')])
            plan = self.store.development_plan(proposal.id)
            if not changed and not error and iv.status == 'closed':
                state = 'tasks_ready' if plan else 'technical_approved' if out.confirmed else 'technical_review'
                if plan:
                    development = DevelopmentOut(proposal_version=proposal.version,
                        content=DevelopmentContent.model_validate(plan.data), markdown=render_tasks(plan.data),
                        plan_digest=digest(plan.data),
                        paths=[str(folder/f'开发任务-v{proposal.version}.md')])
        return WorkbenchOut(state=state, source_changed=changed, source_error=error,
                            proposal=out, development_plan=development, target=self.target_status(interview_id))
