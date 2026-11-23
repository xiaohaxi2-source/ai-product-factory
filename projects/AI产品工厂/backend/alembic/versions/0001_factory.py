"""Baseline existing interview tables and add durable runner tables without data loss."""
from alembic import op
from app.store import Base

revision = '0001_factory'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('自动降级会删除业务数据，禁止执行。')
