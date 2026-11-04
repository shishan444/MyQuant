"""vr_signal_trace 表 + validation_records 三布尔列（add-vr-signal-trace）

Revision ID: f7c3d9a5e2b8
Revises: b5e8f1a3c2d9
Create Date: 2026-11-04

信号留痕表（append-only——事件级行，repository 只 insert+query）+
ValidationRecord 能力标记三列（打标口径=「本批留痕管线执行成功」非
「事件有无」；NULL=产生于能力上线前）。ORM 权威源
core/persistence/orm/models.py VrSignalTrace——Alembic 与 ORM 同构
（持久层 schema 声明坑纪律：两方不一致时 IF NOT EXISTS 静默架空）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f7c3d9a5e2b8'
down_revision: Union[str, Sequence[str], None] = 'b5e8f1a3c2d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # 在场守卫（a9d4c2f8e7b3/b8d4f2a6c9e1 先例）：create_all 形库
    # （init_db/ensure_research_tables 产物）已按 models 建出本表——
    # stamp 旧 head 后 upgrade 防撞表
    has_table = conn.execute(sa.text(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
        "AND name='vr_signal_trace'")).scalar() > 0
    if has_table:
        # 三布尔列同守卫（列在场则跳过——create_all 形库已带）
        has_col = conn.execute(sa.text(
            "SELECT COUNT(*) FROM pragma_table_info"
            "('validation_records') WHERE name='signal_trace'")).scalar() > 0
        if not has_col:
            op.add_column("validation_records", sa.Column("signal_trace", sa.Integer()))
            op.add_column("validation_records", sa.Column("trade_semantics", sa.Integer()))
            op.add_column("validation_records", sa.Column("stop_price", sa.Integer()))
        return
    op.create_table(
        "vr_signal_trace",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("record_id", sa.Text(),
                  sa.ForeignKey("validation_records.record_id"),
                  nullable=False),
        sa.Column("exec_ref", sa.Text(), nullable=False),
        sa.Column("bar_time", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("event_code", sa.Integer(), nullable=False),
        sa.Column("size", sa.REAL()),
        sa.Column("price", sa.REAL()),
        sa.Column("capped", sa.REAL()),
        sa.Column("gene_hits", sa.Text()),
    )
    op.create_index(
        "ux_vr_signal_trace", "vr_signal_trace",
        ["record_id", "exec_ref", "bar_time", "seq"], unique=True)
    op.create_index(
        "ix_vr_signal_trace_query", "vr_signal_trace",
        ["record_id", "exec_ref"])
    op.add_column("validation_records", sa.Column("signal_trace", sa.Integer()))
    op.add_column("validation_records", sa.Column("trade_semantics", sa.Integer()))
    op.add_column("validation_records", sa.Column("stop_price", sa.Integer()))


def downgrade() -> None:
    op.drop_column("validation_records", "stop_price")
    op.drop_column("validation_records", "trade_semantics")
    op.drop_column("validation_records", "signal_trace")
    op.drop_index("ix_vr_signal_trace_query", table_name="vr_signal_trace")
    op.drop_index("ux_vr_signal_trace", table_name="vr_signal_trace")
    op.drop_table("vr_signal_trace")
