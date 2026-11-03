"""live_decisions 表 + live_trading_task.error_detail 列（be-live-trading-chain）

Revision ID: a9d4c2f8e7b3
Revises: c3f8a2e6d9b1
Create Date: 2026-11-03

D4 决策快照表（append-only——API 面无 update/delete 通道）+D5 状态面
扩 error_detail（heartbeat_at/last_bar_time 模型已带）。ORM 权威源
core/persistence/orm/models.py LiveDecision——Alembic 与 ORM 同构
（持久层 schema 声明坑纪律：两方不一致时 IF NOT EXISTS 静默架空）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9d4c2f8e7b3'
down_revision: Union[str, Sequence[str], None] = 'c3f8a2e6d9b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("decision_id", sa.Text(), nullable=False, unique=True),
        sa.Column("task_id", sa.Text(),
                  sa.ForeignKey("live_trading_task.task_id"), nullable=False),
        sa.Column("bar_time", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("price", sa.REAL()),
        sa.Column("size", sa.REAL()),
        sa.Column("reason_json", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_live_decisions_task_bar", "live_decisions",
        ["task_id", "bar_time"])
    op.add_column(
        "live_trading_task",
        sa.Column("error_detail", sa.Text()))


def downgrade() -> None:
    op.drop_column("live_trading_task", "error_detail")
    op.drop_index("ix_live_decisions_task_bar", "live_decisions")
    op.drop_table("live_decisions")
