"""首次建库：完整业务表及唯一约束。"""
from alembic import op
from trippilot.database import Base
import trippilot.models  # noqa: F401

revision = "0001"
down_revision = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    Base.metadata.drop_all(op.get_bind())
