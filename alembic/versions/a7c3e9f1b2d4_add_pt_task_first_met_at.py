"""paper-trading-revamp: pt_task first_met_at

Revision ID: a7c3e9f1b2d4
Revises: c2f8e6b1d3a7
Create Date: 2026-11-01

pt_task 扩列 first_met_at TEXT NULL（达标判定组 B）：首个两维
（收益>0 严格 AND 笔数≥5 含等）同时满足 bar 的 bar_time；NULL=从未
满足；幂等单次写（NULL→非 NULL 一次）。全 NULLable 零回填——存量
NULL=升级前任务无达标判定语义。沿 PtFill 扩列先例 c2f8e6b1d3a7
（在场守卫防 create_all 形库撞列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b2d4'
down_revision: Union[str, Sequence[str], None] = 'c2f8e6b1d3a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    has_col = conn.execute(sa.text(
        "SELECT COUNT(*) FROM pragma_table_info('pt_task') "
        "WHERE name='first_met_at'")).scalar() > 0
    if has_col:
        return
    op.add_column('pt_task', sa.Column('first_met_at', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema: 数据不回滚，降级只拆结构。"""
    with op.batch_alter_table('pt_task') as batch:
        batch.drop_column('first_met_at')
