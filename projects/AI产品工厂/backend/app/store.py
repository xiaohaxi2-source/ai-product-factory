"""SQLite 存储：表结构、repository 与 Alembic 迁移入口。"""
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (JSON, Boolean, Float, Integer, String, Text, create_engine,
                        select, update, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .core import LOCAL_OWNER, Settings


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


class Base(DeclarativeBase):
    pass


class Interview(Base):
    __tablename__ = 'interviews'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), default=LOCAL_OWNER)
    product_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='collecting')
    idea_text: Mapped[str] = mapped_column(Text, default='')
    prd_text: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[str] = mapped_column(String(19), default=utcnow)
    updated_at: Mapped[str] = mapped_column(String(19), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[str | None] = mapped_column(String(19), nullable=True)


class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(Integer, index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    enough: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(19), default=utcnow)


class PrdDraft(Base):
    __tablename__ = 'prd_drafts'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ChecklistItem(Base):
    __tablename__ = 'checklist_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(10))  # must / default
    question: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default='')
    options: Mapped[list] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(Text, default='')
    consequence: Mapped[str] = mapped_column(Text, default='')
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class Call(Base):
    __tablename__ = 'calls'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), default=LOCAL_OWNER)
    prompt: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_rmb: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(40), default='')
    created_at: Mapped[str] = mapped_column(String(19), default=utcnow)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(index=True)
    action: Mapped[str]
    status: Mapped[str] = mapped_column(default='queued')
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(default=lambda: datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
    updated_at: Mapped[str] = mapped_column(default=utcnow, onupdate=utcnow)


class JobEvent(Base):
    __tablename__ = 'job_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    job_id: Mapped[str] = mapped_column(index=True)
    kind: Mapped[str]
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class CodexThread(Base):
    __tablename__ = 'codex_threads'
    interview_id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    thread_id: Mapped[str]


class Execution(Base):
    __tablename__ = 'executions'
    id: Mapped[str] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(index=True)
    proposal_id: Mapped[int] = mapped_column(index=True)
    plan_digest: Mapped[str]
    status: Mapped[str] = mapped_column(default='pending')
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=utcnow)
    updated_at: Mapped[str] = mapped_column(default=utcnow, onupdate=utcnow)


class Verification(Base):
    __tablename__ = 'verifications'
    id: Mapped[str] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(index=True)
    execution_id: Mapped[str] = mapped_column(index=True)
    revision: Mapped[str]
    manifest_digest: Mapped[str]
    status: Mapped[str] = mapped_column(default='pending')
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=lambda: datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))
    updated_at: Mapped[str] = mapped_column(default=utcnow, onupdate=utcnow)


class Confirmation(Base):
    __tablename__ = 'confirmations'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str]
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=utcnow)


class Asset(Base):
    __tablename__ = 'assets'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str]
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class TechnicalProposal(Base):
    __tablename__ = 'technical_proposals'
    __table_args__ = (UniqueConstraint('owner_id', 'interview_id', 'version'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(index=True)
    version: Mapped[int]
    source_digest: Mapped[str]
    proposal_digest: Mapped[str]
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=utcnow)


class DevelopmentPlan(Base):
    __tablename__ = 'development_plans'
    __table_args__ = (UniqueConstraint('owner_id', 'proposal_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(default=LOCAL_OWNER)
    interview_id: Mapped[int] = mapped_column(index=True)
    proposal_id: Mapped[int]
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=utcnow)


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        data_dir = Path(settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f'sqlite:///{data_dir.resolve() / "app.sqlite3"}',
                                    connect_args={'timeout': 15})
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def migrate(self) -> None:
        """用 Alembic 升到最新（初始库等价 create_all）。"""
        from alembic import command
        from alembic.config import Config
        backend = Path(__file__).resolve().parents[1]
        cfg = Config(str(backend / 'alembic.ini'))
        cfg.set_main_option('sqlalchemy.url', str(self.engine.url))
        command.upgrade(cfg, 'head')

    # ---------- repository ----------

    def add(self, obj) -> None:
        with self.Session.begin() as s:
            s.add(obj)

    def get_interview(self, interview_id: int, owner: str = LOCAL_OWNER) -> Interview:
        from .core import Problem
        with self.Session() as s:
            row = s.get(Interview, interview_id)
        if row is None or row.owner_id != owner:
            raise Problem('NOT_FOUND', '访谈不存在。', 404)
        return row

    def list_interviews(self, owner: str = LOCAL_OWNER) -> list[Interview]:
        with self.Session() as s:
            return list(s.scalars(select(Interview).where(Interview.owner_id == owner)
                                  .order_by(Interview.updated_at.desc())))

    def messages(self, interview_id: int) -> list[Message]:
        with self.Session() as s:
            return list(s.scalars(select(Message)
                                  .where(Message.interview_id == interview_id, Message.owner_id == LOCAL_OWNER)
                                  .order_by(Message.id)))

    def latest_draft(self, interview_id: int) -> PrdDraft | None:
        with self.Session() as s:
            rows = s.scalars(select(PrdDraft)
                             .where(PrdDraft.interview_id == interview_id, PrdDraft.owner_id == LOCAL_OWNER)
                             .order_by(PrdDraft.version.desc())).all()
        return rows[0] if rows else None

    def checklist(self, interview_id: int) -> list[ChecklistItem]:
        with self.Session() as s:
            return list(s.scalars(select(ChecklistItem)
                                  .where(ChecklistItem.interview_id == interview_id, ChecklistItem.owner_id == LOCAL_OWNER)
                                  .order_by(ChecklistItem.id)))

    def get_item(self, item_id: int, owner: str = LOCAL_OWNER) -> ChecklistItem:
        from .core import Problem
        with self.Session() as s:
            row = s.get(ChecklistItem, item_id)
        if row is None or row.owner_id != owner:
            raise Problem('NOT_FOUND', '清单条目不存在。', 404)
        return row

    def budget_used(self) -> float:
        with self.Session() as s:
            return float(sum(s.scalars(select(Call.cost_rmb)) or [0.0]))

    def record_call(self, **kw) -> None:
        self.add(Call(**kw))

    def get_job(self, job_id: str) -> Job:
        from .core import Problem
        with self.Session() as s:
            row = s.get(Job, job_id)
        if row is None or row.owner_id != LOCAL_OWNER:
            raise Problem('NOT_FOUND', '任务不存在。', 404)
        return row

    def jobs(self, interview_id: int) -> list[Job]:
        with self.Session() as s:
            return list(s.scalars(select(Job).where(Job.interview_id == interview_id,
                                                    Job.owner_id == LOCAL_OWNER)
                                  .order_by(Job.created_at, Job.id)))

    def set_job(self, job_id: str, **values):
        with self.Session.begin() as s:
            s.execute(update(Job).where(Job.id == job_id, Job.owner_id == LOCAL_OWNER)
                      .values(**values, updated_at=utcnow()))

    def event(self, job_id: str, kind: str, data: dict):
        self.add(JobEvent(job_id=job_id, kind=kind, data=data))

    def events(self, job_id: str, after: int = 0):
        with self.Session() as s:
            return list(s.scalars(select(JobEvent).where(JobEvent.job_id == job_id,
                                  JobEvent.owner_id == LOCAL_OWNER, JobEvent.id > after)
                                  .order_by(JobEvent.id)))

    def thread(self, interview_id: int) -> str | None:
        with self.Session() as s:
            row = s.get(CodexThread, interview_id)
            return row.thread_id if row and row.owner_id == LOCAL_OWNER else None

    def save_thread(self, interview_id: int, thread_id: str):
        with self.Session.begin() as s:
            s.merge(CodexThread(interview_id=interview_id, owner_id=LOCAL_OWNER, thread_id=thread_id))

    def asset(self, interview_id: int, kind: str):
        with self.Session() as s:
            return s.scalar(select(Asset).where(Asset.interview_id == interview_id,
                             Asset.owner_id == LOCAL_OWNER, Asset.kind == kind).order_by(Asset.id.desc()))

    def asset_history(self, interview_id: int, kind: str):
        with self.Session() as s:
            return list(s.scalars(select(Asset).where(Asset.interview_id == interview_id,
                Asset.owner_id == LOCAL_OWNER, Asset.kind == kind).order_by(Asset.id)))

    def execution(self, execution_id: str):
        from .core import Problem
        with self.Session() as s:
            row = s.get(Execution, execution_id)
        if row is None or row.owner_id != LOCAL_OWNER:
            raise Problem('NOT_FOUND', '开发执行记录不存在。', 404)
        return row

    def latest_execution(self, interview_id: int, proposal_id: int):
        with self.Session() as s:
            return s.scalar(select(Execution).where(Execution.interview_id == interview_id,
                Execution.proposal_id == proposal_id, Execution.owner_id == LOCAL_OWNER)
                .order_by(Execution.created_at.desc(), Execution.id.desc()))

    def save_execution(self, execution_id: str, status: str, data: dict):
        with self.Session.begin() as s:
            s.execute(update(Execution).where(Execution.id == execution_id, Execution.owner_id == LOCAL_OWNER)
                .values(status=status, data=data, updated_at=utcnow()))

    def verification(self, verification_id):
        from .core import Problem
        with self.Session() as s:
            row = s.get(Verification, verification_id)
        if row is None or row.owner_id != LOCAL_OWNER:
            raise Problem('NOT_FOUND', '检查记录不存在。', 404)
        return row

    def latest_verification(self, interview_id):
        with self.Session() as s:
            return s.scalar(select(Verification).where(Verification.interview_id == interview_id,
                Verification.owner_id == LOCAL_OWNER).order_by(Verification.created_at.desc(), Verification.id.desc()))

    def save_verification(self, verification_id, status, data):
        with self.Session.begin() as s:
            s.execute(update(Verification).where(Verification.id == verification_id, Verification.owner_id == LOCAL_OWNER)
                .values(status=status, data=data, updated_at=utcnow()))

    def proposal(self, interview_id: int):
        with self.Session() as s:
            return s.scalar(select(TechnicalProposal).where(TechnicalProposal.interview_id == interview_id,
                  TechnicalProposal.owner_id == LOCAL_OWNER).order_by(TechnicalProposal.version.desc()))

    def development_plan(self, proposal_id: int):
        with self.Session() as s:
            return s.scalar(select(DevelopmentPlan).where(DevelopmentPlan.proposal_id == proposal_id,
                                                          DevelopmentPlan.owner_id == LOCAL_OWNER))

    def technical_approved(self, proposal):
        with self.Session() as s:
            rows = s.scalars(select(Confirmation).where(Confirmation.interview_id == proposal.interview_id,
                   Confirmation.owner_id == LOCAL_OWNER, Confirmation.kind == 'technical_approved'))
            return any(row.data.get('proposal_id') == proposal.id and
                       row.data.get('proposal_digest') == proposal.proposal_digest for row in rows)


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)
