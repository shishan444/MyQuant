"""pollution_cause: validation_records + backtest_result 历史污染标注列

Revision ID: a7f3d2c8e9b4
Revises: de92805ed44c
Create Date: 2026-10-24

fix-mtf-execution-consistency S 卡 schema 三件之一：两表各 ALTER ADD
COLUMN pollution_cause TEXT NULL——历史回扫脚本写入 'gene_tf_drift'
（带基因级周期标注但执行周期漂移的策略关联记录）；NULL=未标注
（存量不回填）。

两表分界契约（design 卡 7）：validation_records（含 verdict 判定链）
=标注+重验取代通道；backtest_result（无 verdict 语义，工程回测自然
重跑即新证据）=仅标注。查询面走列（SQL WHERE pollution_cause=
'gene_tf_drift'）；自描述取代关系走记录 JSON 键（supersedes/
rerun_of——重验通道产物，本迁移不涉及）。

幂等性：ADD COLUMN 对已含列的库会撞 duplicate column——沿工程迁移
先例由 alembic 版本链保证单次执行（stamp 探测判据同步含本列，
见 db_ext._is_head_columns）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7f3d2c8e9b4'
down_revision: Union[str, Sequence[str], None] = 'de92805ed44c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: 两表各加 pollution_cause TEXT NULL（幂等）。

    列存在性检查（PRAGMA 探测跳过）：create_all 形库（ORM 全列建表，
    含本列）经 stamp 探测分支走「pre-lifecycle 前版+upgrade 补链」
    时，ADD COLUMN 会撞 duplicate column——幂等防御使混用形态全通。
    """
    conn = op.get_bind()

    def _add_column_if_missing(table: str) -> None:
        cols = [row[1] for row in conn.execute(
            sa.text(f"PRAGMA table_info({table})"))]
        if "pollution_cause" not in cols:
            op.add_column(
                table, sa.Column("pollution_cause", sa.Text(),
                                 nullable=True))

    _add_column_if_missing("validation_records")
    _add_column_if_missing("backtest_result")


def downgrade() -> None:
    """Downgrade schema: 两表各删 pollution_cause。"""
    op.drop_column("backtest_result", "pollution_cause")
    op.drop_column("validation_records", "pollution_cause")
