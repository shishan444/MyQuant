"""vr_trade_detail 决策列退役（retire-vr-decision-json）

Revision ID: a8e4f7d2c9b3
Revises: f7c3d9a5e2b8
Create Date: 2026-11-04

decision_json 列退役（C-019 能力承诺由 vr_signal_trace 兑现——写入/
读出/ORM 三面同批撤净）。零数据迁移：生产库 vr_trade_detail 全表 0
行（2026-11-04 实查）。SQLite ALTER TABLE DROP COLUMN（b5e8f1a3c2d9
downgrade 已用同款，无技术障碍）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a8e4f7d2c9b3'
down_revision: Union[str, Sequence[str], None] = 'f7c3d9a5e2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    has_col = conn.execute(sa.text(
        "SELECT COUNT(*) FROM pragma_table_info"
        "('vr_trade_detail') WHERE name='decision_json'")).scalar() > 0
    if has_col:
        op.drop_column("vr_trade_detail", "decision_json")


def downgrade() -> None:
    conn = op.get_bind()
    has_col = conn.execute(sa.text(
        "SELECT COUNT(*) FROM pragma_table_info"
        "('vr_trade_detail') WHERE name='decision_json'")).scalar() > 0
    if not has_col:
        op.add_column("vr_trade_detail", sa.Column("decision_json", sa.Text()))
