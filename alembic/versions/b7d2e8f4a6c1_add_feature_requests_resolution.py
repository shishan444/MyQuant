"""feature_requests.resolution: 慢通道需求单回销出处载体（变更二 4.1）

Revision ID: b7d2e8f4a6c1
Revises: e5b9f3c7a1d4
Create Date: 2026-10-14

需求单表加回销出处列（JSON 串，可空）：结构化 {source, note, at,
by}——source=对应 change 名/出处标识（resolved 语义=已实现并附出处，
无出处不得标 resolved，校验在 REST PATCH 层）；wont_fix 可不附。
载体为独立表列而非 payload 内嵌：payload 是提单方（研究侧）写入的
原始诉求域，回销信息是工程侧写入——两个写入者不共域（权威单源）。
存量行 NULL=未回销，语义自洽无回填。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e8f4a6c1'
down_revision: Union[str, Sequence[str], None] = 'e5b9f3c7a1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'feature_requests',
        sa.Column('resolution', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('feature_requests', 'resolution')
