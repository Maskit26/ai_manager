"""Create durable jobs, inbox, outbox, and dialog session tables."""
from alembic import op

from ai_seller_core.persistence import models  # noqa: F401
from ai_seller_core.persistence.base import Base

revision = "0001_durable_processing"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
