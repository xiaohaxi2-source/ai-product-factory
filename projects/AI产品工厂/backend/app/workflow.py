"""访谈状态机与编排。状态转换只允许前进；模型失败不改状态。

collecting → draft_pending → auditing → closed
"""
import logging
import re
import os
import uuid
from pathlib import Path

from .core import LOCAL_OWNER, Problem
from .schemas import (AuditResult, DraftPRD, FinalPRD, InterviewReply)
from .store import Asset, ChecklistItem, Confirmation, Interview, Message, PrdDraft, Store, dumps, utcnow

log = logging.getLogger(__name__)

ALLOWED_TRANSITIONS = {
    'collecting': {'draft_pending'},
    'draft_pending': {'auditing'},
    'auditing': {'closed'},
}


def _history(messages):
    return [{'role': m.role, 'content': m.content} for m in messages]


class Workflow:
    def __init__(self, gateway, store: Store, projects_dir: Path):
        self.gateway = gateway
        self.store = store
        self.projects_dir = Path(projects_dir)

    # ---------- 创建 ----------

    def create_interview(self, product_name: str | None, idea: str, prd_text: str = '') -> Interview:
        if not product_name:
            raise Problem('VALIDATION', '请先填写目标产品名。', 422)
        if not idea.strip() and not prd_text.strip():
            raise Problem('VALIDATION', '请填写想法或上传 PRD。', 422)
        self._safe_target(product_name)
        iv = Interview(product_name=product_name, idea_text=idea, prd_text=prd_text)
        self.store.add(iv)
        if prd_text:
            self._add_message(iv.id, 'user',
                              f'[已上传 PRD 文件，内容如下]\n{prd_text}', [], False)
        if idea:
            self._add_message(iv.id, 'user', idea, [], False)
        return iv

    def _add_message(self, interview_id: int, role: str, content: str,
                     questions: list, enough: bool) -> Message:
        msg = Message(interview_id=interview_id, role=role, content=content,
                      questions=questions, enough=enough)
        self.store.add(msg)
        return msg

    # ---------- 状态守卫 ----------

    def _transition(self, iv: Interview, target: str) -> None:
        if target not in ALLOWED_TRANSITIONS.get(iv.status, set()):
            raise Problem('INVALID_STATE', f'当前状态 {iv.status} 不能执行该操作。', 409)

    def _set_status(self, iv: Interview, status: str) -> None:
        iv.status = status
        if status == 'closed':
            iv.closed_at = utcnow()
        from sqlalchemy import update
        with self.store.Session.begin() as s:
            s.execute(update(Interview).where(Interview.id == iv.id)
                      .values(status=status, closed_at=iv.closed_at))

    # ---------- 主链路 ----------

    def handle_message(self, interview_id: int, content: str) -> tuple[Message, Interview]:
        iv = self.store.get_interview(interview_id)
        if iv.status != 'collecting':
            raise Problem('INVALID_STATE', '访谈已出草稿，不能再对话；请按当前状态继续。', 409)
        messages = self.store.messages(interview_id)
        reply, _ = self.gateway.complete('interview', {
            'product_name': iv.product_name,
            'idea': iv.idea_text,
            'prd_excerpt': iv.prd_text,
            'history': _history(messages),
            'user_answer': content,
        }, InterviewReply)
        qlist = [q.model_dump() for q in reply.questions][:5]
        if len(reply.questions) > 5:
            log.info('questions trimmed: %d -> 5', len(reply.questions))
        text = reply.note or ('信息已经足够，可以生成 PRD 草稿了。' if reply.enough
                              else self._questions_text(qlist))
        self._add_message(interview_id, 'user', content, [], False)
        msg = self._add_message(interview_id, 'assistant', text, qlist, reply.enough)
        return msg, iv

    @staticmethod
    def _questions_text(qlist: list[dict]) -> str:
        lines = []
        for i, q in enumerate(qlist, 1):
            opts = ' / '.join(q.get('options', []))
            lines.append(f"{i}. {q['title']}\n   选项：{opts}\n   推荐：{q['recommendation']}\n   不选的后果：{q['consequence']}")
        return '\n\n'.join(lines) or '（没有问题）'

    def make_draft(self, interview_id: int, feedback: str = '') -> PrdDraft:
        iv = self.store.get_interview(interview_id)
        if not (iv.status == 'draft_pending' and feedback):
            self._transition(iv, 'draft_pending')
        draft_prev = self.store.latest_draft(interview_id)
        result: DraftPRD = self.gateway.complete('draft_prd', {
            'product_name': iv.product_name,
            'idea': iv.idea_text,
            'prd_text': iv.prd_text,
            'history': _history(self.store.messages(interview_id)),
            'previous_draft': draft_prev.content if draft_prev else '',
            'revision_request': feedback,
        }, DraftPRD)[0]
        draft = PrdDraft(interview_id=interview_id, content=result.prd_markdown,
                         version=(draft_prev.version + 1) if draft_prev else 1)
        self.store.add(draft)
        if feedback:
            self._add_message(interview_id, 'user', feedback, [], False)
        self.store.add(Asset(interview_id=interview_id, kind='draft_assumptions',
                             data={'draft_id': draft.id, 'items': [a.model_dump() for a in result.assumptions]}))
        self._set_status(iv, 'draft_pending')
        self._add_message(interview_id, 'assistant',
                          f'[PRD 草稿 v{draft.version} 已生成，请确认或继续补充]', [], False)
        return draft

    def confirm_draft(self, interview_id: int) -> list[ChecklistItem]:
        iv = self.store.get_interview(interview_id)
        self._transition(iv, 'auditing')
        draft = self.store.latest_draft(interview_id)
        if draft is None:
            raise Problem('NOT_FOUND', '还没有 PRD 草稿。', 404)
        result: AuditResult = self.gateway.complete('audit_prd', {
            'prd_markdown': draft.content}, AuditResult)[0]
        items: list[ChecklistItem] = []
        for m in result.must_ask:
            items.append(ChecklistItem(interview_id=interview_id, kind='must', question=m.question,
                                       why=m.why, options=m.options, recommendation=m.recommendation,
                                       consequence=m.consequence))
        for d in result.defaults:
            items.append(ChecklistItem(interview_id=interview_id, kind='default', question=d.gap,
                                       why=d.reason, options=[d.default],
                                       recommendation=d.default, consequence=''))
        for c in result.contradictions:
            items.append(ChecklistItem(interview_id=interview_id, kind='conflict', question=c.point,
                         why='PRD 内部表述矛盾，需要产品经理裁决', options=[c.ruling, '自行补充'],
                         recommendation=c.ruling, consequence='未裁决前不能写回'))
        assumptions = self.store.asset(interview_id, 'draft_assumptions')
        for a in (assumptions.data['items'] if assumptions else []):
            if not any(it.question == a['gap'] for it in items):
                items.append(ChecklistItem(interview_id=interview_id, kind='default',
                    question=a['gap'], why=a['reason'], options=[a['default']], recommendation=a['default'], consequence=''))
        if not items:
            items.append(ChecklistItem(interview_id=interview_id, kind='default',
                                       question='PRD 体检无缺口', why='八个字段齐全且无矛盾',
                                       options=['无需补充'], recommendation='直接确认', consequence=''))
        with self.store.Session.begin() as s:
            for it in items:
                s.add(it)
        self._set_status(iv, 'auditing')
        self._add_message(interview_id, 'assistant', '[已定稿，体检完成，《PRD 补全清单》已生成]', [], False)
        return items

    def answer_item(self, item_id: int, answer: str) -> ChecklistItem:
        item = self.store.get_item(item_id)
        if self.store.get_interview(item.interview_id).status != 'auditing':
            raise Problem('INVALID_STATE', '当前不在清单问答阶段。', 409)
        if not answer.strip():
            raise Problem('VALIDATION', '请填写答案或明确按推荐。', 422)
        from sqlalchemy import update
        with self.store.Session.begin() as s:
            s.execute(update(ChecklistItem).where(ChecklistItem.id == item.id)
                      .values(answer=answer, closed=True))
        item.answer, item.closed = answer, True
        self.store.add(Confirmation(interview_id=item.interview_id, kind='checklist_answer',
                                   data={'item_id': item.id, 'answer': answer}))
        return item

    def decision_answers(self, interview_id: int) -> list[dict]:
        answers = []
        for i in range(1, 10):
            rows = self.store.asset_history(interview_id, f'decision_answer_D{i}')
            if not rows:
                continue
            history = []
            for row in rows:
                content = row.data['content']
                if not history or history[-1] != content:
                    history.append(content)
            answers.append(dict(rows[-1].data, history=history))
        return answers

    def pending_review(self, interview_id: int):
        if self.store.get_interview(interview_id).status != 'auditing':
            return None
        review = self.store.asset(interview_id, 'final_decision_review')
        if not review:
            return None
        answers = {a['number']: a for a in self.decision_answers(interview_id)}
        return {'review_id': review.id, 'decisions': [dict(d,
                answer=answers.get(d['number'], {}).get('content', ''),
                history=answers.get(d['number'], {}).get('history', []))
                for d in review.data['pending']]}

    def save_decision_answers(self, interview_id: int, body):
        review = self.pending_review(interview_id)
        if not review or review['review_id'] != body.review_id:
            raise Problem('REVIEW_CHANGED', '待确认清单已更新，请刷新后再填写。', 409)
        allowed = {d['number']: d for d in review['decisions']}
        numbers = [a.number for a in body.answers]
        if len(set(numbers)) != len(numbers) or any(n not in allowed for n in numbers):
            raise Problem('VALIDATION', '只能补充当前待确认的决策，每项填写一次。', 422)
        if any(not a.content.strip() for a in body.answers):
            raise Problem('VALIDATION', '请填写具体补充内容。', 422)
        with self.store.Session.begin() as session:
            for answer in body.answers:
                value = {'number': answer.number, 'topic': allowed[answer.number]['topic'],
                         'content': answer.content.strip(), 'review_id': review['review_id']}
                session.add(Asset(interview_id=interview_id, kind=f'decision_answer_{answer.number}', data=value))
                session.add(Confirmation(interview_id=interview_id, kind='decision_answer', data=value))
        return self.pending_review(interview_id)

    def finalize(self, interview_id: int) -> tuple[list[str], Interview]:
        iv = self.store.get_interview(interview_id)
        self._transition(iv, 'closed')
        items = self.store.checklist(interview_id)
        open_items = [it for it in items if not it.closed]
        if open_items:
            raise Problem('INVALID_STATE', f'还有 {len(open_items)} 个清单条目未关闭。', 409)
        if not iv.product_name:
            raise Problem('VALIDATION', '缺目标产品名：请在访谈开始时填写产品名。', 422)
        draft = self.store.latest_draft(interview_id)
        qa = [{'question': it.question, 'kind': it.kind, 'options': it.options,
               'recommendation': it.recommendation, 'answer': it.answer} for it in items]
        prepared = self.store.asset(interview_id, 'prepared_final')
        supplements = self.decision_answers(interview_id)
        if prepared and prepared.data.get('qa') == qa and prepared.data.get('decision_answers', []) == supplements:
            final = FinalPRD.model_validate(prepared.data['result'])
        else:
            final: FinalPRD = self.gateway.complete('finalize_prd', {
                'prd_draft': draft.content, 'checklist_qa': qa, 'decision_answers': supplements}, FinalPRD)[0]
            pending = [d for d in final.decisions if d.status == '待定']
            if pending:
                self.store.add(Asset(interview_id=interview_id, kind='final_decision_review',
                    data={'qa': qa, 'pending': [d.model_dump() for d in pending]}))
                details = '\n'.join(f'{d.number}「{d.topic}」：{d.conclusion}' for d in pending)
                raise Problem('DECISIONS_PENDING', '仍需补充以下决策：\n'+details+
                              '\n请在页面上方“补充待确认决策”中修改答案，保存后再生成终稿。', 409)
            self.store.add(Asset(interview_id=interview_id, kind='prepared_final',
                                 data={'qa': qa, 'decision_answers': supplements, 'result': final.model_dump()}))
        written = self._write_back(iv, final.prd_markdown, items, final.decisions)
        self._set_status(iv, 'closed')
        self._add_message(interview_id, 'assistant', '[清单已关闭，产物已写回目标产品文件夹]', [], False)
        return written, iv

    # ---------- 文件写回（确定性代码，不经模型） ----------

    def _safe_target(self, name):
        if not re.fullmatch(r'[^/\\\s]{1,60}', name or '') or name.startswith('.') or any(c in name for c in '|<>:'):
            raise Problem('VALIDATION', '产品名含非法字符。', 422)
        root = self.projects_dir.resolve() / name
        if root.is_symlink() or root.resolve().parent != self.projects_dir.resolve():
            raise Problem('PATH_INVALID', '目标目录不在受控产品目录内。', 422)
        # Reject symlinks anywhere under an existing docs directory before writes.
        docs = root / 'docs'
        if docs.is_symlink() or (docs.exists() and any(p.is_symlink() for p in docs.rglob('*'))):
            raise Problem('PATH_INVALID', '目标文档目录含符号链接，未执行写入。', 422)
        return root

    def _write_back(self, iv: Interview, prd_markdown: str,
                    items: list[ChecklistItem], decisions=None) -> list[str]:
        name = iv.product_name
        if not re.fullmatch(r'[^/\\\s]{1,60}', name or ''):
            raise Problem('VALIDATION', '产品名含非法字符，无法创建文件夹。', 422)
        root = self._safe_target(name)
        plan = self.store.asset(iv.id, 'export_plan')
        if not plan:
            existing = any((root / p).exists() for p in ('docs/PRD/PRD.md', 'docs/阶段文档/阶段0-PRD补全清单.md', 'docs/项目状态.md'))
            base = root / 'docs' / '工厂提案' / f'访谈-{iv.id}' if existing else root
            mapping = {
                'docs/PRD/PRD.md': prd_markdown.strip() + '\n',
                'docs/阶段文档/阶段0-PRD补全清单.md': self._render_checklist(name, items) +
                    ''.join(f'\n\n### 决策补充 {a["number"]}：{a["topic"]}\n\n'
                            '按提交顺序保留；互补内容合并，冲突内容以较新决定为准。\n\n' +
                            '\n\n'.join(a['history']) + '\n'
                            for a in self.decision_answers(iv.id)),
                'docs/项目状态.md': self._status_text(name, decisions or []),
            }
            plan = Asset(interview_id=iv.id, kind='export_plan', data={
                'files': {str(base / relative): text for relative, text in mapping.items()}, 'proposal': existing})
            self.store.add(plan)
        written = []
        for target, text in plan.data['files'].items():
            path = Path(target)
            if not path.is_relative_to(root) or any(p.is_symlink() for p in [path, *path.parents] if p != self.projects_dir.resolve().parent):
                raise Problem('PATH_INVALID', '导出路径不安全。', 422)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_text(encoding='utf-8') != text:
                    raise Problem('FILE_CONFLICT', '目标文件已有不同内容，未覆盖，请检查文件。', 409)
            else:
                # Hard-link a fully flushed temp file into place without replacing anything.
                temp = path.with_name('.factory-' + uuid.uuid4().hex + '.tmp')
                try:
                    with temp.open('x', encoding='utf-8') as stream:
                        stream.write(text)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.link(temp, path)
                    except FileExistsError:
                        if path.read_text() != text:
                            raise Problem('FILE_CONFLICT', '目标文件有并发变更，未覆盖。', 409)
                finally:
                    temp.unlink(missing_ok=True)
            written.append(str(path))
        for sub in ('PRD', '阶段文档', '设计参考', 'evidence'):
            folder = root / 'docs' / sub
            folder.mkdir(parents=True, exist_ok=True)
            note = folder / '说明.md'
            if not note.exists():
                with note.open('x', encoding='utf-8') as stream:
                    stream.write(f'# {sub}\n\n此目录存放{ sub }；由产品经理与 AI 按阶段维护。\n')
        return written

    @staticmethod
    def _status_text(name, decisions):
        def cell(text):
            return text.replace('|', '／').replace('\n', ' ')
        rows = '\n'.join(f'| {d.number} | {cell(d.topic)} | {d.status} | {cell(d.conclusion)} | 用户确认的访谈与清单答案 |' for d in decisions)
        return f'''# 项目状态

## 当前阶段
- 产品名：{name}
- 阶段：0 PRD 体检
- 最近更新：{utcnow()[:10]}
- 上次停在：PRD 与清单已写回，待产品经理核对、管家自检与验收；未自动推进阶段。

## 决策台账
| 编号 | 决策 | 状态 | 选定结论 | 依据 |
| --- | --- | --- | --- | --- |
{rows}

## 待确认问题
- D2 待样例打分时，第一次真实冒烟由产品经理定标准。

## 线上资源（阶段 4 起；不含任何密钥）
- 云开发环境 ID：未创建
- 是否与其他产品共用环境：待上线时核实

## 已发出的信息（不含任何密钥）
- 访问地址：无

## 阶段历史
- {utcnow()[:10]}：AI产品工厂 写回阶段0产物，待验收。
'''

    @staticmethod
    def _render_checklist(name: str, items: list[ChecklistItem]) -> str:
        must = [it for it in items if it.kind in ('must', 'conflict')]
        defaults = [it for it in items if it.kind == 'default']
        lines = ['# PRD 补全清单', '', f'- 产品：{name}',
                 f'- 日期：{utcnow()[:10]}', '- 状态：全部关闭（由 AI产品工厂 生成并经确认）。', '',
                 '## 必须问（已全部关闭）', '']
        for i, it in enumerate(must, 1):
            opts = ' / '.join(it.options)
            lines += [f'### 问题 {i}：{it.question}',
                      f'- 为什么重要：{it.why}',
                      f'- 选项：{opts}',
                      f'- 推荐：{it.recommendation}',
                      f'- 不选的后果：{it.consequence}',
                      f'- 回答：{it.answer}', '']
        if not must:
            lines.append('- 无必须问项。')
            lines.append('')
        lines += ['## 可默认（已确认）', '',
                  '| 缺口 | 默认值 | 理由 |', '| --- | --- | --- |']
        for it in defaults:
            lines.append(f'| {it.question} | {it.answer} | {it.why} |')
        lines += ['', '## 已发现的矛盾', *[f'- {it.question}；用户裁决：{it.answer}' for it in items if it.kind == 'conflict'], '']
        if not any(it.kind == 'conflict' for it in items):
            lines.append('- 体检未识别到矛盾。')
        return '\n'.join(lines)

    # ---------- 看板雏形 ----------

    def list_products(self) -> list[str]:
        if not self.projects_dir.exists():
            return []
        return sorted(p.name for p in self.projects_dir.iterdir() if p.is_dir())
