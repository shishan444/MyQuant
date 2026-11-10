"""sandbox-and-report-supplement: pt_task 账户级硬熔断三列

Revision ID: d4c7b2e8f6a9
Revises: a8e4f7d2c9b3
Create Date: 2026-11-09

pt_task 扩列 halt_drawdown_pct REAL NULL（任务级覆写参数——NULL=
不启用，数值留空待用户给后续零代码启用）＋halted_at TEXT NULL＋
halt_reason TEXT NULL（触发运行态记录——首触发单次写，可查询）。
全 NULLable 零回填——存量 NULL=升级前任务无熔断语义（未配置任务
行为逐位不变）。沿 PtTask 扩列先例 a7c3e9f1b2d4（在场守卫防
create_all 形库撞列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4c7b2e8f6a9'
down_revision: Union[str, Sequence[str], None] = 'a8e4f7d2c9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    existing = {
        r[1] for r in conn.execute(sa.text(
            "SELECT * FROM pragma_table_info('pt_task')")).fetchall()}
    if "halt_drawdown_pct" not in existing:
        op.add_column(
            'pt_task',
            sa.Column('halt_drawdown_pct', sa.REAL(), nullable=True))
    if "halted_at" not in existing:
        op.add_column(
            'pt_task', sa.Column('halted_at', sa.Text(), nullable=True))
    if "halt_reason" not in existing:
        op.add_column(
            'pt_task', sa.Column('halt_reason', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema: 数据不回滚，降级只拆结构。"""
    with op.batch_alter_table('pt_task') as batch:
        batch.drop_column('halt_reason')
        batch.drop_column('halted_at')
        batch.drop_column('halt_drawdown_pct')
