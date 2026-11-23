"""API 与模型输出的 Pydantic 契约。所有接口出入参都在本文件定义。"""
from typing import Optional

import re
from pydantic import BaseModel, Field, field_validator, model_validator


class SyntheticDemoRequest(BaseModel):
    model_config = {'extra': 'forbid', 'strict': True}
    accepted: bool


class SyntheticDemoOut(BaseModel):
    model_config = {'extra': 'forbid'}
    scope: str = 'fixed_synthetic_only'
    medical_enabled: bool = False
    configured: bool
    question: str
    status: str
    reply: dict[str, str] | None = None
    model: str
    time: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    duration_ms: int | None = None
    cost_upper_micro_rmb: int | None = None
    job_id: str | None = None


# ---------- 模型输出契约（JSON 模式 + 程序强校验） ----------

class Question(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    options: list[str] = Field(min_length=2, max_length=4)
    recommendation: str = Field(min_length=1, max_length=300)
    consequence: str = Field(min_length=1, max_length=300)


class InterviewReply(BaseModel):
    questions: list[Question] = Field(default_factory=list, max_length=5)
    enough: bool = False
    note: str = ''

    @model_validator(mode='after')
    def coherent(self):
        if self.enough and self.questions:
            raise ValueError('enough 时不得继续提问')
        if not self.enough and not self.questions:
            raise ValueError('信息不足时至少提出一个问题')
        return self


class Assumption(BaseModel):
    gap: str = Field(min_length=1, max_length=120)
    default: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=300)


class DraftPRD(BaseModel):
    prd_markdown: str = Field(min_length=50)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=20)

    @field_validator('prd_markdown')
    @classmethod
    def eight_sections(cls, value):
        sections = list(re.finditer(r'^##\s+([1-8])[.、．]?\s+[^\n]+', value, re.M))
        if [m.group(1) for m in sections] != list('12345678'):
            raise ValueError('PRD 必须包含顺序完整的八个字段')
        for i, section in enumerate(sections):
            end = sections[i + 1].start() if i < 7 else len(value)
            if not value[section.end():end].strip():
                raise ValueError('PRD 字段正文不得为空')
        return value


class MustAsk(BaseModel):
    question: str = Field(min_length=1, max_length=200)
    why: str = Field(min_length=1, max_length=300)
    options: list[str] = Field(min_length=2, max_length=4)
    recommendation: str = Field(min_length=1, max_length=300)
    consequence: str = Field(min_length=1, max_length=300)


class DefaultItem(BaseModel):
    gap: str = Field(min_length=1, max_length=120)
    default: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=300)


class Contradiction(BaseModel):
    point: str = Field(min_length=1, max_length=300)
    ruling: str = Field(min_length=1, max_length=300)


class AuditResult(BaseModel):
    must_ask: list[MustAsk] = Field(default_factory=list, max_length=40)
    defaults: list[DefaultItem] = Field(default_factory=list, max_length=20)
    contradictions: list[Contradiction] = Field(default_factory=list, max_length=10)


class Decision(BaseModel):
    number: str = Field(pattern=r'^D[1-9]$')
    topic: str = Field(min_length=1, max_length=100)
    conclusion: str = Field(min_length=1, max_length=1000)
    status: str = Field(pattern=r'^(已定|待定|待样例打分)$')


class FinalPRD(DraftPRD):
    decisions: list[Decision] = Field(min_length=9, max_length=9)

    @field_validator('decisions')
    @classmethod
    def all_decisions(cls, value):
        if {d.number for d in value} != {f'D{i}' for i in range(1, 10)}:
            raise ValueError('台账必须完整覆盖 D1-D9')
        return value


# ---------- API 出入参 ----------

class InterviewCreate(BaseModel):
    product_name: Optional[str] = Field(default=None, pattern=r'^[^/\\\s]{1,60}$')
    idea: str = Field(default='', max_length=20000)

    @field_validator('product_name')
    @classmethod
    def safe_product(cls, value):
        if value and (value.startswith('.') or any(c in value for c in '\n\r\t|<>:')):
            raise ValueError('产品名不合法')
        return value


class InterviewOut(BaseModel):
    id: int
    product_name: Optional[str]
    status: str
    idea_text: str
    updated_at: str


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    questions: list[dict] = Field(default_factory=list)
    enough: bool = False
    created_at: str


class DraftOut(BaseModel):
    content: str
    version: int
    assumptions: list[dict] = Field(default_factory=list)


class ChecklistItemOut(BaseModel):
    id: int
    kind: str
    question: str
    why: str = ''
    options: list[str] = Field(default_factory=list)
    recommendation: str = ''
    consequence: str = ''
    answer: Optional[str] = None
    closed: bool


class FinalizeOut(BaseModel):
    written_paths: list[str]
    status: str


class HealthOut(BaseModel):
    status: str
    stage: str
    model_configured: bool
    backend: str = 'codex'
    model: str = ''


class ProductOut(BaseModel):
    name: str


class WorkspaceProjectOut(BaseModel):
    id: int
    name: str
    stage: str
    state: str
    updated_at: str
    running: bool
    files_count: int


class ArtifactOut(BaseModel):
    id: str
    name: str
    bytes: int
    download_url: str
    local_path: str = ''


class ArtifactContentOut(ArtifactOut):
    content: str


class LocalFileOut(BaseModel):
    name: str
    kind: str
    local_path: str
    bytes: int
    updated_at: str
    artifact_id: str = ''
    download_url: str = ''


class LocalFilesOut(BaseModel):
    document_directory: str
    code_directory: str = ''
    files: list[LocalFileOut]


class InterviewDetail(BaseModel):
    interview: InterviewOut
    messages: list[MessageOut]
    draft: Optional[DraftOut]
    checklist: list[ChecklistItemOut]
    jobs: list['JobOut']
    written_paths: list[str] = Field(default_factory=list)
    pending_review: Optional['DecisionReviewOut'] = None


class PendingDecisionOut(Decision):
    answer: str = ''
    history: list[str] = Field(default_factory=list)


class DecisionReviewOut(BaseModel):
    review_id: int
    decisions: list[PendingDecisionOut]


class DecisionAnswerIn(BaseModel):
    number: str = Field(pattern=r'^D[1-9]$')
    content: str = Field(min_length=1, max_length=4000)


class DecisionAnswersRequest(BaseModel):
    review_id: int = Field(ge=1)
    answers: list[DecisionAnswerIn] = Field(min_length=1, max_length=9)


class JobOut(BaseModel):
    id: str
    interview_id: int
    action: str
    user_content: str = ''
    status: str
    result: Optional[dict] = None
    error: Optional[dict] = None
    created_at: str
    updated_at: str


class ConfirmRequest(BaseModel):
    draft_version: int = Field(ge=1)
    accepted: bool


class ExportRequest(BaseModel):
    accepted: bool


class ModuleChoice(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    choice: str = Field(pattern=r'^(采用|不采用)$')
    reason: str = Field(min_length=1, max_length=500)
    location: str = Field(min_length=1, max_length=200)


class ProductPhase(BaseModel):
    stage: str = Field(pattern=r'^阶段[23]-\d+$')
    goal: str = Field(min_length=1, max_length=500)
    deliverables: list[str] = Field(min_length=1, max_length=12)
    acceptance: str = Field(min_length=1, max_length=800)


class TechnicalContent(BaseModel):
    summary: str = Field(min_length=10, max_length=1800)
    default_stack: list[str] = Field(min_length=1, max_length=12)
    modules: list[ModuleChoice] = Field(default_factory=list, max_length=15)
    cost_impact: str = Field(min_length=1, max_length=1500)
    deferred: list[str] = Field(default_factory=list, max_length=15)
    verification: list[str] = Field(min_length=1, max_length=15)
    questions: list[MustAsk] = Field(default_factory=list, max_length=5)
    phases: list[ProductPhase] = Field(min_length=1, max_length=12)

    @field_validator('phases')
    @classmethod
    def unique_phases(cls, phases):
        if len({p.stage for p in phases}) != len(phases):
            raise ValueError('阶段编号不得重复')
        return phases


class DevelopmentTask(BaseModel):
    id: str = Field(pattern=r'^T[1-9]\d*$')
    phase: str = Field(pattern=r'^阶段[23]-\d+$')
    title: str = Field(min_length=1, max_length=150)
    goal: str = Field(min_length=1, max_length=800)
    inputs: list[str] = Field(min_length=1, max_length=12)
    deliverables: list[str] = Field(min_length=1, max_length=12)
    checks: list[str] = Field(min_length=1, max_length=12)
    confirmations: list[str] = Field(default_factory=list, max_length=10)
    depends_on: list[str] = Field(default_factory=list, max_length=20)


class DevelopmentContent(BaseModel):
    tasks: list[DevelopmentTask] = Field(min_length=1, max_length=30)
    note: str = Field(min_length=1, max_length=1000)

    @field_validator('tasks')
    @classmethod
    def valid_dependencies(cls, tasks):
        seen = set()
        for task in tasks:
            if task.id in seen or not set(task.depends_on) <= seen:
                raise ValueError('任务编号重复或依赖未定义/存在环路')
            seen.add(task.id)
        return tasks


class TechnicalConfirm(BaseModel):
    accepted: bool
    proposal_version: int = Field(ge=1)
    source_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    proposal_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class TechnicalOut(BaseModel):
    version: int
    source_digest: str
    proposal_digest: str
    content: TechnicalContent
    markdown: str
    confirmed: bool
    paths: list[str]


class DevelopmentOut(BaseModel):
    proposal_version: int
    content: DevelopmentContent
    markdown: str
    paths: list[str]
    plan_digest: str = ''


class TargetStatus(BaseModel):
    kit_stage: str = '未记录'
    kit_status: str = '未记录'
    last_stop: str = ''
    current_task: str = ''
    evidence: list[str] = Field(default_factory=list)


class WorkbenchOut(BaseModel):
    state: str
    source_changed: bool = False
    source_error: Optional[str] = None
    proposal: Optional[TechnicalOut] = None
    development_plan: Optional[DevelopmentOut] = None
    target: TargetStatus


class ExecutionRequest(TechnicalConfirm):
    plan_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    execution_id: Optional[str] = Field(default=None, pattern=r'^[a-f0-9-]{36}$')
    feedback: str = Field(default='', max_length=4000)


class CodeChange(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    before_sha256: str = Field(pattern=r'^(new|[a-f0-9]{64})$')
    content: str = Field(max_length=100000)


class CodeBundle(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    changes: list[CodeChange] = Field(default_factory=list, max_length=4)
    questions: list[str] = Field(default_factory=list, max_length=5)
    task_complete: bool = True

    @model_validator(mode='after')
    def useful(self):
        if not self.changes and not self.questions:
            raise ValueError('须返回文件改动或需补充的问题')
        if len({c.path for c in self.changes}) != len(self.changes):
            raise ValueError('文件路径不能重复')
        return self


class ExecutionCheckOut(BaseModel):
    path: str
    kind: str
    status: str
    message: str


class ExecutionChangeOut(BaseModel):
    path: str
    before_sha256: str
    after_sha256: str
    added: int
    removed: int


class ExecutionStepOut(BaseModel):
    task_id: str
    title: str
    status: str
    summary: str = ''
    changes: list[ExecutionChangeOut] = Field(default_factory=list)
    checks: list[ExecutionCheckOut] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    chunks: int = 0
    active_batch: int = 0
    batch_started_at: Optional[float] = None


class ExecutionOut(BaseModel):
    id: str
    interview_id: int
    proposal_version: int
    status: str
    workspace: Optional[str] = None
    steps: list[ExecutionStepOut]
    updated_at: str
    error: Optional[dict[str, str]] = None
    timeout_per_batch: float = 300
    manifest_digest: str = ''
    revision: str = ''
    note: str = '代码仅在隔离工作目录；语法检查不代表功能验收，尚未合入原产品或推进其阶段。'


class ExecutionDiffOut(BaseModel):
    path: str
    diff: str
    truncated: bool


class VerificationRequest(BaseModel):
    accepted: bool
    execution_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    revision: str = Field(pattern=r'^[a-f0-9]{32}$')
    manifest_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


class CheckRepairRequest(BaseModel):
    accepted: bool
    verification_id: str = Field(pattern=r'^[a-f0-9-]{36}$')
    feedback: str = Field(default='', max_length=4000)


class CheckStepOut(BaseModel):
    name: str
    status: str
    summary: str = ''
    exit_code: Optional[int] = None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    details: str = ''


class VerificationOut(BaseModel):
    id: str
    execution_id: str
    revision: str
    manifest_digest: str
    status: str
    source_changed: bool = False
    steps: list[CheckStepOut] = Field(default_factory=list)
    preview_url: Optional[str] = None
    preview_login_available: bool = False
    evidence_path: str = ''
    blockers: list[str] = Field(default_factory=list)
    accepted: bool = False
    adopted: bool = False
    error: Optional[dict[str, str]] = None
    updated_at: str


class AcceptanceRequest(BaseModel):
    accepted: bool
    revision: str = Field(pattern=r'^[a-f0-9]{32}$')
    comment: str = Field(default='', max_length=4000)


class PreviewLoginOut(BaseModel):
    url: str


class AdoptionFileOut(BaseModel):
    path: str
    action: str
    before_sha256: str
    after_sha256: str


class AdoptionPlanOut(BaseModel):
    verification_id: str
    target: str
    plan_digest: str
    files: list[AdoptionFileOut]
    conflicts: list[str]
    adopted: bool = False


class AdoptionRequest(BaseModel):
    accepted: bool
    plan_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
