"""Persist version-bound checks and human acceptance; retain execution data."""
from alembic import op
from app.store import Verification

revision = '0004_verification'
down_revision = '0003_execution'
branch_labels = None
depends_on = None


def upgrade():
    Verification.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('禁止自动删除业务数据。')
