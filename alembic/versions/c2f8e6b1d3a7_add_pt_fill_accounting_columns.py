"""paper-signal-log: pt_fill accounting detail columns

Revision ID: c2f8e6b1d3a7
Revises: b8d4f2a6c9e1
Create Date: 2026-10-28

pt_fill 核算明细三列（change paper-signal-log 任务 5）：equity_before
（成交前权益）/available_before（成交前 balance）/margin_used（open/add
=本笔占用；reduce/close=本笔释放正值）。全 NULLable 零回填——存量
NULL=升级前成交历史语义（design §7 自洽判据不适用于存量行）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2f8e6b1d3a7'
down_revision: Union[str, Sequence[str], None] = 'b8d4f2a6c9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    # 在场守卫（CARD-01 同款）：create_all 形库（init_db 产物）的
    # pt_fill 已按 models 含三列（stamp 至前一版后 upgrade 防撞列）
    has_col = conn.execute(sa.text(
        "SELECT COUNT(*) FROM pragma_table_info('pt_fill') "
        "WHERE name='equity_before'")).scalar() > 0
    if has_col:
        return
    op.add_column('pt_fill',
                  sa.Column('equity_before', sa.REAL(), nullable=True))
    op.add_column('pt_fill',
                  sa.Column('available_before', sa.REAL(), nullable=True))
    op.add_column('pt_fill',
                  sa.Column('margin_used', sa.REAL(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('pt_fill', 'margin_used')
    op.drop_column('pt_fill', 'available_before')
    op.drop_column('pt_fill', 'equity_before')
