"""add-validation-detail: vr_trade_detail + vr_equity_curve

Revision ID: c3f8a2e6d9b1
Revises: b8d5f0a2c3e6
Create Date: 2026-11-02

验证明细两表（逐笔成交每笔一行+资金曲线每执行份一行）。字段级权威
源：openspec 变更 add-validation-detail design.md §3.1 与 specs/
research-validation-protocol/spec.md「验证明细原子落盘」。与 ORM
models.py VrTradeDetail/VrEquityCurve、executor.ensure_research_tables
三处同步声明（三重声明纪律——不一致静默架空，索引验证须实库
PRAGMA index_list，工程坑位）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f8a2e6d9b1'
down_revision: Union[str, Sequence[str], None] = 'b8d5f0a2c3e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    # 在场守卫（b8d4f2a6c9e1 先例）：create_all 形库（init_db 产物/
    # ensure_research_tables）已按 models 建出本表（stamp 至前一版后
    # upgrade 防撞表）
    has_table = conn.execute(sa.text(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name='vr_trade_detail'")).scalar() > 0
    if has_table:
        return
    op.create_table('vr_trade_detail',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Text(), nullable=False),
    sa.Column('exec_ref', sa.Text(), nullable=False),
    sa.Column('exec_index', sa.Integer(), nullable=False),
    sa.Column('range_start', sa.Text(), nullable=False),
    sa.Column('range_end', sa.Text(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('entry_time', sa.Text(), nullable=False),
    # NULL=尾部未平仓（vbt Status=Open——保真不伪造平仓时点）
    sa.Column('exit_time', sa.Text(), nullable=True),
    sa.Column('entry_price', sa.REAL(), nullable=False),
    sa.Column('exit_price', sa.REAL(), nullable=False),
    sa.Column('size', sa.REAL(), nullable=False),
    sa.Column('direction', sa.Text(), nullable=False),
    sa.Column('pnl', sa.REAL(), nullable=True),
    sa.ForeignKeyConstraint(['record_id'], ['validation_records.record_id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    # 幂等锚（防御层——幂等查询前置使重插理论不可达）
    op.create_index(
        'ux_vr_trade_ident', 'vr_trade_detail',
        ['record_id', 'exec_ref', 'seq'], unique=True,
    )
    # 明细整份取回查询（record_id, exec_index, seq）
    op.create_index(
        'ix_vr_trade_record', 'vr_trade_detail',
        ['record_id', 'exec_index', 'seq'], unique=False,
    )
    op.create_table('vr_equity_curve',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('record_id', sa.Text(), nullable=False),
    sa.Column('exec_ref', sa.Text(), nullable=False),
    sa.Column('exec_index', sa.Integer(), nullable=False),
    sa.Column('range_start', sa.Text(), nullable=False),
    sa.Column('range_end', sa.Text(), nullable=False),
    sa.Column('grid_aligned', sa.Integer(), nullable=False),
    sa.Column('timeframe', sa.Text(), nullable=False),
    sa.Column('start', sa.Text(), nullable=True),
    sa.Column('length', sa.Integer(), nullable=True),
    sa.Column('timestamps', sa.Text(), nullable=True),
    sa.Column('values', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['validation_records.record_id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    # 一份一行幂等锚（防御层）
    op.create_index(
        'ux_vr_equity_ident', 'vr_equity_curve',
        ['record_id', 'exec_ref'], unique=True,
    )
    op.create_index(
        'ix_vr_equity_record', 'vr_equity_curve',
        ['record_id', 'exec_index'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_vr_equity_record', table_name='vr_equity_curve')
    op.drop_index('ux_vr_equity_ident', table_name='vr_equity_curve')
    op.drop_table('vr_equity_curve')
    op.drop_index('ix_vr_trade_record', table_name='vr_trade_detail')
    op.drop_index('ux_vr_trade_ident', table_name='vr_trade_detail')
    op.drop_table('vr_trade_detail')
