"""paper_equity_snapshot: idx_equity_task_time 补唯一约束（CARD-01）

Revision ID: 04b595687d2f
Revises: d4e7f9a2c5b8
Create Date: 2026-10-08

唯一索引三处矛盾收口：legacy_init 建 UNIQUE、ORM models 与 Alembic
baseline 均为非唯一——非唯一轨先建索引后，legacy 的 CREATE UNIQUE
INDEX IF NOT EXISTS 被架空，save_equity_snapshots 的 OR IGNORE 幂等
失效，重放产生重复行。本迁移对齐 B1.3 幂等语义（同 paper_trade
先例）：存量重复保留最早一条（MIN(id)）后重建唯一索引；models.py
声明同步补 unique=True。
"""
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04b595687d2f'
down_revision: Union[str, Sequence[str], None] = 'd4e7f9a2c5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.04b595687d2f")


def upgrade() -> None:
    """去重（保留 MIN(id)）→ 重建唯一索引（存量库唯一升级路径）。"""
    conn = op.get_bind()
    # 形制同 legacy_init paper_trade 去重先例：重复 (task_id, timestamp)
    # 保留最早一条，重放残余不带入唯一约束
    result = conn.execute(sa.text(
        "DELETE FROM paper_equity_snapshot "
        "WHERE id NOT IN ("
        "    SELECT MIN(id) FROM paper_equity_snapshot "
        "    GROUP BY task_id, timestamp"
        ")"))
    logger.info("equity 快照去重：删除 %d 行重复", result.rowcount)
    op.drop_index('idx_equity_task_time', table_name='paper_equity_snapshot')
    op.create_index(
        'idx_equity_task_time', 'paper_equity_snapshot',
        ['task_id', 'timestamp'],
        unique=True)


def downgrade() -> None:
    """回退：重建非唯一索引（回到 baseline 形态）。去重删除的重复行
    不可恢复；回退后 OR IGNORE 幂等再次失效，重放将产生重复行。"""
    op.drop_index('idx_equity_task_time', table_name='paper_equity_snapshot')
    op.create_index(
        'idx_equity_task_time', 'paper_equity_snapshot',
        ['task_id', 'timestamp'],
        unique=False)
