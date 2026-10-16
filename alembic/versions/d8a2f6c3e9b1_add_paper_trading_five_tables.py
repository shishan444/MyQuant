"""paper trading domain: 5 tables for rebuild-paper-trading

Revision ID: d8a2f6c3e9b1
Revises: c4e8a1f7b3d2
Create Date: 2026-10-16

纸盘交易新域五表（pt_task/pt_fill/pt_equity/pt_funding/pt_report）。
字段级权威源：openspec 变更 rebuild-paper-trading design.md §4.1-4.5
（含 Q-2 增量：pt_funding status 三态列+rate/amount 可空+唯一键
(task_id, settle_time)、pt_report 复合主键 (task_id, report_version)）。
与 ORM models.py 两处同步声明——两方不一致静默架空，唯一索引验证
须实库 PRAGMA index_list（工程坑位）。旧三表留档不删不改。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8a2f6c3e9b1'
down_revision: Union[str, Sequence[str], None] = 'c4e8a1f7b3d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('pt_task',
    sa.Column('task_id', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'pending'")),
    sa.Column('strategy_id', sa.Text(), nullable=False),
    sa.Column('dna_snapshot_json', sa.Text(), nullable=False),
    sa.Column('symbol', sa.Text(), nullable=False),
    sa.Column('timeframe', sa.Text(), nullable=False),
    sa.Column('initial_cash', sa.REAL(), nullable=False, server_default=sa.text('100000')),
    sa.Column('fee', sa.REAL(), nullable=False, server_default=sa.text('0.001')),
    sa.Column('slippage', sa.REAL(), nullable=False, server_default=sa.text('0.0005')),
    sa.Column('leverage', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('window_bars', sa.Integer(), nullable=True),
    sa.Column('stop_loss_pct', sa.REAL(), nullable=True),
    sa.Column('bars_processed', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('last_bar_time', sa.Text(), nullable=True),
    sa.Column('balance', sa.REAL(), nullable=True),
    sa.Column('position_side', sa.Text(), nullable=True),
    sa.Column('position_entry', sa.REAL(), nullable=True),
    sa.Column('position_quantity', sa.REAL(), nullable=True),
    sa.Column('position_margin', sa.REAL(), nullable=True),
    sa.Column('position_open_cost', sa.REAL(), nullable=True),
    sa.Column('position_sl_price', sa.REAL(), nullable=True),
    sa.Column('position_tp_price', sa.REAL(), nullable=True),
    sa.Column('position_trail_peak', sa.REAL(), nullable=True),
    sa.Column('position_entry_bar_seq', sa.Integer(), nullable=True),
    sa.Column('unrealized_pnl', sa.REAL(), nullable=True),
    sa.Column('pending_intent_json', sa.Text(), nullable=True),
    sa.Column('total_trades', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('win_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('loss_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('total_pnl', sa.REAL(), nullable=False, server_default=sa.text('0')),
    sa.Column('research_status_snapshot', sa.Text(), nullable=False),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.Column('started_at', sa.Text(), nullable=True),
    sa.Column('stopped_at', sa.Text(), nullable=True),
    sa.Column('stop_reason', sa.Text(), nullable=True),
    sa.Column('heartbeat_at', sa.Text(), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('task_id'),
    )

    op.create_table('pt_fill',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Text(), nullable=False),
    sa.Column('bar_time', sa.Text(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('side', sa.Text(), nullable=True),
    sa.Column('price', sa.REAL(), nullable=True),
    sa.Column('quantity', sa.REAL(), nullable=True),
    sa.Column('fee', sa.REAL(), nullable=True),
    sa.Column('realized_pnl', sa.REAL(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['task_id'], ['pt_task.task_id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ux_pt_fill_task_bar_seq', 'pt_fill',
        ['task_id', 'bar_time', 'seq'], unique=True,
    )

    op.create_table('pt_equity',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Text(), nullable=False),
    sa.Column('bar_time', sa.Text(), nullable=False),
    sa.Column('equity', sa.REAL(), nullable=False),
    sa.Column('balance', sa.REAL(), nullable=False),
    sa.Column('unrealized_pnl', sa.REAL(), nullable=False),
    sa.Column('position_side', sa.Text(), nullable=True),
    sa.Column('cumulative_funding', sa.REAL(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['pt_task.task_id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ux_pt_equity_task_bar', 'pt_equity',
        ['task_id', 'bar_time'], unique=True,
    )

    op.create_table('pt_funding',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.Text(), nullable=False),
    sa.Column('settle_time', sa.Text(), nullable=False),
    sa.Column('bar_time', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'settled'")),
    sa.Column('rate', sa.REAL(), nullable=True),
    sa.Column('amount', sa.REAL(), nullable=True),
    sa.Column('position_quantity', sa.REAL(), nullable=False),
    sa.Column('settle_price', sa.REAL(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['pt_task.task_id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ux_pt_funding_task_settle', 'pt_funding',
        ['task_id', 'settle_time'], unique=True,
    )

    op.create_table('pt_report',
    sa.Column('task_id', sa.Text(), nullable=False),
    sa.Column('report_version', sa.Integer(), nullable=False),
    sa.Column('generated_at', sa.Text(), nullable=False),
    sa.Column('as_of', sa.Text(), nullable=False),
    sa.Column('report_json', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('task_id', 'report_version'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('pt_report')
    op.drop_index('ux_pt_funding_task_settle', table_name='pt_funding')
    op.drop_table('pt_funding')
    op.drop_index('ux_pt_equity_task_bar', table_name='pt_equity')
    op.drop_table('pt_equity')
    op.drop_index('ux_pt_fill_task_bar_seq', table_name='pt_fill')
    op.drop_table('pt_fill')
    op.drop_table('pt_task')
