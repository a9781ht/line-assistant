"""建立 LINE 助理與記帳中心初始資料表。"""

from collections.abc import Sequence

from alembic import op

from line_assistant.db import models  # noqa: F401
from line_assistant.db.base import Base

revision: str = "20260907_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=False)
