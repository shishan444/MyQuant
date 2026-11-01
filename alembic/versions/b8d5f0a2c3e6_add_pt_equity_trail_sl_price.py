"""paper-trading-revamp: pt_equity trail_sl_price

Revision ID: b8d5f0a2c3e6
Revises: a7c3e9f1b2d4
Create Date: 2026-11-01

pt_equity 扩列 trail_sl_price REAL NULL（trail 观测序列组 E）：NULL
封闭语义=非 trail 档任务的 bar / trail 档任务的空仓 bar；非 NULL=
trail 档持仓 bar 的当 bar 止损价（决策时点值，非重估值）。观测面
纪律：决策路径零引用（出场判定消费 ledger 内部 trail_peak）。沿
PtFill 扩列先例（在场守卫防 create_all 形库撞列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d5f0a2c3e6'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9f1b2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    has_col = conn.execute(sa.text(
        "SELECT COUNT(*) FROM pragma_table_info('pt_equity') "
        "WHERE name='trail_sl_price'")).scalar() > 0
    if has_col:
        return
    op.add_column('pt_equity',
                  sa.Column('trail_sl_price', sa.REAL(), nullable=True))


def downgrade() -> None:
    """Downgrade schema: 数据不回滚，降级只拆结构。"""
    with op.batch_alter_table('pt_equity') as batch:
        batch.drop_column('trail_sl_price')
