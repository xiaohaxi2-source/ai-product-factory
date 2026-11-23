"""Durable isolated code execution checkpoints, preserving all prior records."""
from alembic import op
from app.store import Execution

revision = '0003_execution'
down_revision = '0002_planning'
branch_labels = None
depends_on = None


def upgrade():
    Execution.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('禁止自动删除业务数据。')
