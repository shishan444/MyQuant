"""research_hypotheses.strategy_params: 策略层参数声明块（0.4.2 R1）

Revision ID: e5b9f3c7a1d4
Revises: a1c3e8f5b7d2
Create Date: 2026-10-11

假设表加策略层参数声明块列（JSON 串，可空）：风险五参数与分批
数值参数的登记载体（stop_loss/take_profit/sl_mode/position_size/
leverage/exit_fraction/add_fraction/max_exposure）。存量行 NULL=
无声明（翻译走平台默认，逐位兼容零回填）；不进 conditions JSON、
不进 conditions_hash（不参与假设身份指纹——参数变体经终态后
重登记产生，strategy-param-declaration 规格）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b9f3c7a1d4'
down_revision: Union[str, Sequence[str], None] = 'a1c3e8f5b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'research_hypotheses',
        sa.Column('strategy_params', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('research_hypotheses', 'strategy_params')
