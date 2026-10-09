"""baseline 缺口补齐：两个排序索引（TD-10 stamp 前验证）

Revision ID: a1c3e8f5b7d2
Revises: 04b595687d2f
Create Date: 2026-10-08

stamp 前三库 diff 实证：alembic baseline autogenerate 跳过表达式
索引（text("col DESC") 形），idx_strategy_best_score 与
idx_verify_session_created 两索引仅在 ORM create_all 轨存在——
纯 alembic 轨新库缺建。本迁移补建；普通列形（不带 DESC）——
SQLite 反射不解析索引方向、DESC 与 ASC 查询等价，普通形与
models.py 修正后的声明一致（autogenerate 幂等前提）。IF NOT
EXISTS 幂等：存量库（ORM 轨已建同名索引）stamp 后 upgrade
跳过，纯 alembic 新库补建。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3e8f5b7d2'
down_revision: Union[str, Sequence[str], None] = '04b595687d2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """补建两个排序索引（幂等）。"""
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_strategy_best_score "
        "ON strategy(best_score)"))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_verify_session_created "
        "ON verify_session(created_at)"))


def downgrade() -> None:
    """回退：删两索引（ORM create_all 轨重建库不受影响）。"""
    op.execute(sa.text("DROP INDEX IF EXISTS idx_strategy_best_score"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_verify_session_created"))
