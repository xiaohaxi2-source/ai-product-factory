"""Add immutable technical proposals and development plans; retain all interview data."""
from alembic import op
from app.store import TechnicalProposal, DevelopmentPlan

revision = '0002_planning'
down_revision = '0001_factory'
branch_labels = None
depends_on = None


def upgrade():
    TechnicalProposal.__table__.create(op.get_bind(), checkfirst=True)
    DevelopmentPlan.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('禁止自动删除业务数据。')
