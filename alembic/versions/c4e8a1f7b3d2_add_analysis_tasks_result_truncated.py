"""analysis_tasks.result_truncated: 长任务截断标记（变更三 8.1）

Revision ID: c4e8a1f7b3d2
Revises: b7d2e8f4a6c1
Create Date: 2026-10-14

轮询截断披露（TD-01 方案②）：写入侧 len(str(result)) > 200000（严格
大于——恰等长不截断）置 1；轮询侧按标记两态披露（result_truncated+
truncation_note 截断态 / result_invalid 畸形态），消除「任务成功但
结果静默消失」。存量行 server_default 0 自动归位（历史结果均为截断
上限内的尽力而为——未知态按未截断呈现与现状一致）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8a1f7b3d2'
down_revision: Union[str, Sequence[str], None] = 'b7d2e8f4a6c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'analysis_tasks',
        sa.Column('result_truncated', sa.Integer(),
                  nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('analysis_tasks', 'result_truncated')
