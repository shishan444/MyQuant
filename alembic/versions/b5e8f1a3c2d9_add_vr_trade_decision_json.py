"""vr_trade_detail.decision_json 列（be-validation-decision-records D6）

Revision ID: b5e8f1a3c2d9
Revises: a9d4c2f8e7b3
Create Date: 2026-11-03

决策理由 JSON 列（NULLable——存量行 NULL 不炸不回填；新验证逐笔写入
执行器可得判定事实）。ORM 权威源 models.py VrTradeDetail。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b5e8f1a3c2d9'
down_revision: Union[str, Sequence[str], None] = 'a9d4c2f8e7b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # 列在场守卫（create_all 形库先例同款）
    has_col = conn.execute(sa.text(
        "SELECT COUNT(*) FROM pragma_table_info('vr_trade_detail') "
        "WHERE name='decision_json'")).scalar() > 0
    if not has_col:
        op.add_column(
            "vr_trade_detail",
            sa.Column("decision_json", sa.Text()))


def downgrade() -> None:
    op.drop_column("vr_trade_detail", "decision_json")
