"""validation_records: 幂等身份五元化（0.3.3 G7 / Q1 裁决迁移）

Revision ID: d4e7f9a2c5b8
Revises: b7c4e9a2f1d3
Create Date: 2026-10-05

三元组唯一索引扩为五元 (hypothesis_id, strategy_id, range_hash, level,
segmentation)——level 入身份修复隔离/分段互撞现状缺陷；segmentation
编码分段身份（auto 字面量 / explicit 段清单内容摘要 / 其余空串——
NOT NULL 空串判重，NULL 不判重会丢非分段记录的库级幂等保护）。
回填自 params 实况；同区间不同分段方案自此各自独立（spec 裁定）。
"""
import hashlib
import json
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e7f9a2c5b8'
down_revision: Union[str, Sequence[str], None] = 'b7c4e9a2f1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.d4e7f9a2c5b8")


def _segmentation_identity_copy(mode, segments):
    """编码函数内联副本（与服务层 segmentation_identity 保持一致，
    改动须双向同步——迁移自包含：未来新环境重放不 import 当时的
    服务层函数版本，防重放漂移；test 断言两实现同输出）。

    输入形制：与 params 落库同形的字符串化段清单。
    """
    if mode == "auto":
        return "auto"
    if mode == "explicit":
        norm = [
            {"label": s["label"],
             "start": s["start"].isoformat()
             if hasattr(s["start"], "isoformat") else str(s["start"]),
             "end": s["end"].isoformat()
             if hasattr(s["end"], "isoformat") else str(s["end"])}
            for s in (segments or [])]
        canon = json.dumps(
            sorted(norm, key=lambda s: (s["start"], s["end"], s["label"])),
            sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
    return ""


def upgrade() -> None:
    """加列 → 回填 → 五元唯一索引重建（同一迁移内完成）。"""
    conn = op.get_bind()
    op.add_column(
        'validation_records',
        sa.Column('segmentation', sa.Text(), nullable=False,
                  server_default=sa.text("''")),
    )
    # 存量回填：分段记录按 params 实况（explicit 现算段清单摘要 /
    # auto 字面量）；隔离与随机窗保持空串（level 已区分类型）。
    # 解析失败行落警告清单人工核（不静默吞、不阻断迁移）。
    rows = conn.execute(sa.text(
        "SELECT record_id, level, params FROM validation_records "
        "WHERE level = 'staged_validation'")).fetchall()
    backfilled = warned = 0
    for record_id, _level, params_json in rows:
        try:
            params = json.loads(params_json or "{}")
            mode = params.get("segmentation", "auto")
            value = _segmentation_identity_copy(
                mode, params.get("segments"))
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            logger.warning(
                "segmentation 回填解析失败 record_id=%s params=%.200s",
                record_id, params_json)
            warned += 1
            continue
        conn.execute(sa.text(
            "UPDATE validation_records SET segmentation = :v "
            "WHERE record_id = :r"), {"v": value, "r": record_id})
        backfilled += 1
    logger.info("segmentation 回填：成功 %d 行 / 警告 %d 行",
                backfilled, warned)
    op.drop_index('ux_validation_idem', table_name='validation_records')
    op.create_index(
        'ux_validation_idem', 'validation_records',
        ['hypothesis_id', 'strategy_id', 'range_hash',
         'level', 'segmentation'],
        unique=True)


def downgrade() -> None:
    """回退：重建三元组唯一索引（segmentation 列可留——空串容缺
    无消费者）；回退后同区间异分段记录回到不可插入（现状约束），
    已插入多行保留可读。"""
    op.drop_index('ux_validation_idem', table_name='validation_records')
    op.create_index(
        'ux_validation_idem', 'validation_records',
        ['hypothesis_id', 'strategy_id', 'range_hash'],
        unique=True)
