"""strategy lifecycle: event table + domain triggers + stock classification

Revision ID: de92805ed44c
Revises: d8a2f6c3e9b1
Create Date: 2026-10-18

策略生命周期三合一同 revision（build-strategy-lifecycle 1.2）：
①strategy_transition_event 建表（append-only 审计）②strategy 表双
域触发器（UPDATE 六值域+INSERT 初始集 {draft,active}——工程 DB
触发器首例）③存量分类 data migration（active 有活跃 pt_task→paper/
无→不动；draft/archived 沿用；幂等只动 active 行）。

字段级权威源：openspec 变更 build-strategy-lifecycle design.md §3.1
与 ORM models.py（两处同步声明，不一致静默架空——工程坑位）。值域
单源=core/strategy/lifecycle/machine.py（触发器 IN 列表经 import
常量拼装，不复制字面量）；活跃集单源=PT_STATUS_PREDECESSORS 派生
（终态=状态图无出边态，import 常量不复制字面量）。

存量分类零事件（纯净性原则）：归类审计事实=本文件本身，不产生
strategy_transition_event 行（防未来 backfill 形态污染序②判定）。
存量迁移走 UPDATE 与触发器无冲突（迁移值 paper 在六值内且不涉
INSERT）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'de92805ed44c'
down_revision: Union[str, Sequence[str], None] = 'd8a2f6c3e9b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ①事件表（append-only：无 update/delete 通道由 repository 层承担，
    # 表级无约束可加）
    op.create_table('strategy_transition_event',
    sa.Column('event_id', sa.Text(), nullable=False),
    sa.Column('strategy_id', sa.Text(), nullable=False),
    sa.Column('from_status', sa.Text(), nullable=False),
    sa.Column('to_status', sa.Text(), nullable=False),
    sa.Column('trigger', sa.Text(), nullable=False),
    sa.Column('evidence_json', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('event_id'),
    )
    op.create_index(
        'idx_strategy_transition_event_strategy_created',
        'strategy_transition_event', ['strategy_id', 'created_at'],
        unique=False,
    )

    # ②双域触发器：IN 列表从 machine 常量拼装（值域单源，不复制字面量）
    from core.strategy.lifecycle.machine import (
        INITIAL_STATUSES, STRATEGY_STATUSES)
    _domain = ",".join(f"'{s}'" for s in sorted(STRATEGY_STATUSES))
    _initial = ",".join(f"'{s}'" for s in sorted(INITIAL_STATUSES))
    # UPDATE：仅拦「status 变更且新值出域」（合法值直改不拦=诚实边界，
    # 防护归层②写者唯一纪律）；IS NOT 处理 NULL 比较
    op.execute(sa.text(f"""
        CREATE TRIGGER trg_strategy_status_domain
        BEFORE UPDATE ON strategy
        FOR EACH ROW
        WHEN NEW.status IS NOT OLD.status AND NEW.status NOT IN ({_domain})
        BEGIN
            SELECT RAISE(ABORT,
                'strategy.status domain violation: outside six-value set');
        END
    """))
    # INSERT：初始态白名单 {draft, active}（新策略只能以草稿或直接
    # 转正态入场）
    op.execute(sa.text(f"""
        CREATE TRIGGER trg_strategy_status_insert_domain
        BEFORE INSERT ON strategy
        FOR EACH ROW
        WHEN NEW.status NOT IN ({_initial})
        BEGIN
            SELECT RAISE(ABORT,
                'strategy.status initial value outside {{draft, active}}');
        END
    """))

    # ③存量分类（data migration）：活跃集派生自 pt_task 状态图单源
    # （终态=无出边态=keys−union(values)；import 常量不复制字面量）
    from core.persistence.repository.paper import PT_STATUS_PREDECESSORS
    _terminal = (frozenset(PT_STATUS_PREDECESSORS)
                 - set().union(*PT_STATUS_PREDECESSORS.values()))
    _active_set = ",".join(
        f"'{s}'" for s in sorted(frozenset(PT_STATUS_PREDECESSORS) - _terminal))
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE strategy SET status = 'paper' "
        "WHERE status = 'active' AND strategy_id IN ("
        " SELECT DISTINCT strategy_id FROM pt_task"
        f" WHERE status IN ({_active_set}))"
    ))
    # 零事件：分类不写 strategy_transition_event（纯净性原则）


def downgrade() -> None:
    """Downgrade schema."""
    # 数据不回滚（分类值留在六值域内合法；降级只拆结构）
    op.execute(sa.text("DROP TRIGGER IF EXISTS trg_strategy_status_domain"))
    op.execute(sa.text(
        "DROP TRIGGER IF EXISTS trg_strategy_status_insert_domain"))
    op.drop_index(
        'idx_strategy_transition_event_strategy_created',
        table_name='strategy_transition_event')
    op.drop_table('strategy_transition_event')
