"""paper-signal-log: pt_signal 22-column table

Revision ID: b8d4f2a6c9e1
Revises: a7f3d2c8e9b4
Create Date: 2026-10-28

纸盘信号记录表（决策过程结构化落库）。字段级权威源：openspec 变更
paper-signal-log design.md §2 与 specs/paper-signal-log/spec.md
「字段族明细」Scenario（22 列逐列语义）。
与 ORM models.py PtSignal 两处同步声明——两方不一致静默架空，唯一
索引验证须实库 PRAGMA index_list（工程坑位）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d4f2a6c9e1'
down_revision: Union[str, Sequence[str], None] = 'a7f3d2c8e9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    # 在场守卫（CARD-01 同款，de92805ed44c 先例）：create_all 形库
    # （init_db 产物）已按 models 建出本表与索引（stamp 至前一版后
    # upgrade 防撞表）
    has_table = conn.execute(sa.text(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name='pt_signal'")).scalar() > 0
    if has_table:
        return
    op.create_table('pt_signal',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Text(), nullable=False),
    sa.Column('strategy_id', sa.Text(), nullable=False),
    sa.Column('bar_time', sa.Text(), nullable=False),
    sa.Column('exec_bar_time', sa.Text(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('signal_kind', sa.Text(), nullable=False),
    sa.Column('direction', sa.Text(), nullable=True),
    sa.Column('size_fraction', sa.REAL(), nullable=True),
    sa.Column('gene_hits', sa.Text(), nullable=False),
    sa.Column('indicator_snapshot', sa.Text(), nullable=False),
    sa.Column('outcome', sa.Text(), nullable=False),
    sa.Column('reject_reason', sa.Text(), nullable=True),
    sa.Column('fill_seq', sa.Integer(), nullable=True),
    sa.Column('capped', sa.Integer(), nullable=True),
    sa.Column('equity', sa.REAL(), nullable=False),
    sa.Column('balance', sa.REAL(), nullable=False),
    sa.Column('unrealized_pnl', sa.REAL(), nullable=False),
    sa.Column('position_side', sa.Text(), nullable=True),
    sa.Column('position_quantity', sa.REAL(), nullable=True),
    sa.Column('position_margin', sa.REAL(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['pt_task.task_id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    # 幂等键：INSERT OR IGNORE 吸收崩溃重放（与 pt_fill 同款防线）
    op.create_index(
        'ux_pt_signal', 'pt_signal',
        ['task_id', 'exec_bar_time', 'seq'], unique=True,
    )
    # 跨仓位对照查询（strategy_id, bar_time）
    op.create_index(
        'ix_pt_signal_strategy', 'pt_signal',
        ['strategy_id', 'bar_time'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_pt_signal_strategy', table_name='pt_signal')
    op.drop_index('ux_pt_signal', table_name='pt_signal')
    op.drop_table('pt_signal')
