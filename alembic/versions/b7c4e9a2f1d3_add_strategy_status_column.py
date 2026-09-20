"""strategy.status: draft 生命周期列（improve-mcp-agent-loop M4）

Revision ID: b7c4e9a2f1d3
Revises: f2a8c1d94b07
Create Date: 2026-09-20

策略库加生命周期状态列：draft（假设转化草稿——验证对象、不可直接
消费）/ active（可消费）/ archived。存量行默认 active，零数据回填；
PUT 更新白名单不加 status（转正只经 transition 原子路径）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c4e9a2f1d3'
down_revision: Union[str, Sequence[str], None] = 'f2a8c1d94b07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'strategy',
        sa.Column('status', sa.Text(), nullable=False,
                  server_default=sa.text("'active'")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('strategy', 'status')
