"""research domain: 8 tables for add-ai-research-mcp

Revision ID: f2a8c1d94b07
Revises: ae6e14d43dc5
Create Date: 2026-09-18

研究域八表（研究假设/研究日志/分析任务/公式插件/模型插件/信号注册表/
功能需求单/验证记录）。字段级权威源：openspec 变更 add-ai-research-mcp
design.md 附.1。仅走 Alembic 增量轨；legacy SQL 链（001-008）不动不扩。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a8c1d94b07'
down_revision: Union[str, Sequence[str], None] = 'ae6e14d43dc5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('research_hypotheses',
    sa.Column('hypothesis_id', sa.Text(), nullable=False),
    sa.Column('conditions', sa.Text(), nullable=False),
    sa.Column('conditions_hash', sa.Text(), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('data_ranges', sa.Text(), nullable=False),
    sa.Column('ranges_hash', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'active'")),
    sa.Column('terminal_ref', sa.Text(), nullable=True),
    sa.Column('superseded_by', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('hypothesis_id'),
    )
    op.create_index(
        'ux_hypotheses_active_identity', 'research_hypotheses',
        ['conditions_hash', 'ranges_hash'], unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table('research_log',
    sa.Column('log_id', sa.Text(), nullable=False),
    sa.Column('hypothesis_id', sa.Text(), nullable=True),
    sa.Column('entry_type', sa.Text(), nullable=False),
    sa.Column('ref_id', sa.Text(), nullable=True),
    sa.Column('data_ranges', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('log_id'),
    )
    op.create_index(
        'ix_research_log_hypothesis', 'research_log',
        ['hypothesis_id', 'created_at'], unique=False,
    )

    op.create_table('analysis_tasks',
    sa.Column('task_id', sa.Text(), nullable=False),
    sa.Column('template', sa.Text(), nullable=False),
    sa.Column('params', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'pending'")),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('result_ref', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.Column('finished_at', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('task_id'),
    )
    op.create_index(
        'ix_analysis_tasks_status', 'analysis_tasks',
        ['status', 'created_at'], unique=False,
    )

    op.create_table('formula_plugins',
    sa.Column('plugin_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('expression', sa.Text(), nullable=False),
    sa.Column('output_columns', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'valid'")),
    sa.Column('reject_reason', sa.Text(), nullable=True),
    sa.Column('strategy_visible', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('plugin_id'),
    )
    op.create_index(
        'ux_formula_plugins_name_version', 'formula_plugins',
        ['name', 'version'], unique=True,
    )

    op.create_table('model_plugins',
    sa.Column('plugin_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('config', sa.Text(), nullable=False),
    sa.Column('signal_column', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'pending'")),
    sa.Column('reject_reason', sa.Text(), nullable=True),
    sa.Column('oos_summary', sa.Text(), nullable=True),
    sa.Column('strategy_visible', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('plugin_id'),
    )
    op.create_index(
        'ux_model_plugins_name_version', 'model_plugins',
        ['name', 'version'], unique=True,
    )

    op.create_table('signal_registry',
    sa.Column('entry_id', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('plugin_id', sa.Text(), nullable=False),
    sa.Column('column_name', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('strategy_visible', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('entry_id'),
    )
    op.create_index(
        'ux_signal_registry_column_version', 'signal_registry',
        ['column_name', 'version'], unique=True,
    )

    op.create_table('feature_requests',
    sa.Column('request_id', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('payload', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'open'")),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('request_id'),
    )
    op.create_index(
        'ix_feature_requests_status', 'feature_requests',
        ['status', 'created_at'], unique=False,
    )

    op.create_table('validation_records',
    sa.Column('record_id', sa.Text(), nullable=False),
    sa.Column('hypothesis_id', sa.Text(), nullable=False),
    sa.Column('strategy_id', sa.Text(), nullable=True),
    sa.Column('level', sa.Text(), nullable=False),
    sa.Column('params', sa.Text(), nullable=False),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('verdict', sa.Text(), nullable=False),
    sa.Column('range_hash', sa.Text(), nullable=False),
    sa.Column('components', sa.Text(), nullable=True),
    sa.Column('created_at', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['hypothesis_id'], ['research_hypotheses.hypothesis_id'], ),
    sa.PrimaryKeyConstraint('record_id'),
    )
    op.create_index(
        'ux_validation_idem', 'validation_records',
        ['hypothesis_id', 'strategy_id', 'range_hash'], unique=True,
    )
    op.create_index(
        'ix_validation_hypothesis', 'validation_records',
        ['hypothesis_id', 'created_at'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_validation_hypothesis', table_name='validation_records')
    op.drop_index('ux_validation_idem', table_name='validation_records')
    op.drop_table('validation_records')
    op.drop_index('ix_feature_requests_status', table_name='feature_requests')
    op.drop_table('feature_requests')
    op.drop_index('ux_signal_registry_column_version', table_name='signal_registry')
    op.drop_table('signal_registry')
    op.drop_index('ux_model_plugins_name_version', table_name='model_plugins')
    op.drop_table('model_plugins')
    op.drop_index('ux_formula_plugins_name_version', table_name='formula_plugins')
    op.drop_table('formula_plugins')
    op.drop_index('ix_analysis_tasks_status', table_name='analysis_tasks')
    op.drop_table('analysis_tasks')
    op.drop_index('ix_research_log_hypothesis', table_name='research_log')
    op.drop_table('research_log')
    op.drop_index('ux_hypotheses_active_identity', table_name='research_hypotheses')
    op.drop_table('research_hypotheses')
