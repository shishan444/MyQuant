"""fix-replay-task-lifecycle: pt_task 来源域标记 origin 列

Revision ID: e5f8a3c1d7b2
Revises: d4c7b2e8f6a9
Create Date: 2026-11-20

pt_task 加列 origin TEXT NOT NULL DEFAULT 'paper'（回放与纸盘实时
共表后的调度域判别——纸盘调度器全部自动扫描面仅处置 origin='paper'
行）。加列即回填：NOT NULL+server_default 使存量 11 行自动落
'paper'；再反查修正——validation_records.level='replay' 行的
result.replay.task_id 对应行置 'replay'（≥3 行，逐行 Python 解析
规避 json_extract 求值顺序坑——损坏 result 行跳过不阻断迁移）。
对账：SELECT origin, count(*) 分布与反查集一致（replay≥3）。
沿 PtTask 扩列先例 d4c7b2e8f6a9（在场守卫防 create_all 形库撞列）。
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f8a3c1d7b2'
down_revision: Union[str, Sequence[str], None] = 'd4c7b2e8f6a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema：加列（缺省回填 paper）→反查回填 replay 行。"""
    conn = op.get_bind()
    existing = {
        r[1] for r in conn.execute(sa.text(
            "SELECT * FROM pragma_table_info('pt_task')")).fetchall()}
    if "origin" not in existing:
        # NOT NULL+server_default：SQLite 对存量行以缺省值填充（11 行
        # 秒级，全终态无行为风险）
        op.add_column(
            'pt_task',
            sa.Column('origin', sa.Text(), nullable=False,
                      server_default=sa.text("'paper'")))
    # 反查回填：validation_records.level='replay' → result JSON 的
    # replay.task_id 对应行置 'replay'。表不在场（测试空库）或 result
    # 损坏均跳过该行——迁移对任意库形可重入
    tables = {
        r[0] for r in conn.execute(sa.text(
            "SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
    if "validation_records" not in tables:
        return
    replay_tids = set()
    for (raw,) in conn.execute(sa.text(
            "SELECT result FROM validation_records "
            "WHERE level='replay'")).fetchall():
        try:
            payload = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            continue  # 损坏 result 行不阻断迁移（如实留 paper 缺省）
        if not isinstance(payload, dict):
            continue
        tid = (payload.get("replay") or {}).get("task_id")
        if isinstance(tid, str) and tid:
            replay_tids.add(tid)
    for tid in sorted(replay_tids):
        conn.execute(sa.text(
            "UPDATE pt_task SET origin='replay' WHERE task_id=:tid"),
            {"tid": tid})


def downgrade() -> None:
    """Downgrade schema: 数据不回滚，降级只拆结构。"""
    with op.batch_alter_table('pt_task') as batch:
        batch.drop_column('origin')
