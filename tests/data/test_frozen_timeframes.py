"""超集周期冻结快照不变量锚（cleanup-data-segment 契约 5 / VS-3.10 补充面）。

30m/3d 四文件以冻结快照形态迁入活跃域（cp -p 保 mtime），契约 =
「无任何运行时写入者」。本文件把该契约钉成常驻断言：行数精确锚
（与迁移校验记录/设计卡快照同源：47628/319/52608/365）+ mtime
上界锚（早于迁移时刻 2026-10-29——cp -p 冻结不刷新语义）。任何
运行时写入者触碰（重采样补写/更新链误写/手工重灌）即红。

数据缺位时模块级 skip（离线环境，test_derivatives_contract 同款
纪律——不假绿）。数据集整体重建时按新实况重录锚值并更新本 docstring
（重录依据=迁移校验记录，works/历史脚本/data-market-cleanup-manifest.md）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from core.data.fapi.rest import default_data_dir

MARKET_DIR = default_data_dir()

import datetime as _dt

# 迁移执行时刻（2026-10-29，migrate_frozen_timeframes 归档留档日）：
# 冻结件 mtime 必须早于该时刻（cp -p 保持 2026-09-21 原值）。
_MIGRATION_EPOCH = _dt.datetime(
    2026, 10, 29, 0, 0, tzinfo=_dt.timezone.utc).timestamp()

FROZEN_ANCHORS = {
    "BTCUSDT_30m.parquet": 47628,
    "BTCUSDT_3d.parquet": 319,
    "ETHUSDT_30m.parquet": 52608,
    "ETHUSDT_3d.parquet": 365,
}

_missing = [n for n in FROZEN_ANCHORS if not (MARKET_DIR / n).exists()]
if _missing:
    pytest.skip(
        f"冻结快照四件不在库（data/market_fapi）：{_missing}，"
        "冻结不变量锚跳过", allow_module_level=True)


class TestFrozenSnapshotInvariants:
    """冻结快照双不变量：行数精确锚+mtime 冻结上界锚。"""

    @pytest.mark.parametrize("name,rows", sorted(FROZEN_ANCHORS.items()))
    def test_rows_unchanged_since_migration(self, name, rows):
        """行数=迁移定档值：任何写入者触碰（追加/重采样/截断）即红。"""
        import pandas as pd
        df = pd.read_parquet(MARKET_DIR / name)
        assert len(df) == rows, (
            f"{name} 行数 {len(df)} != 冻结定档 {rows}"
            "——冻结快照被触碰或数据集已重建（重建则重录锚值）")

    @pytest.mark.parametrize("name,rows", sorted(FROZEN_ANCHORS.items()))
    def test_mtime_frozen_before_migration(self, name, rows):
        """mtime 早于迁移时刻：cp -p 冻结不刷新语义的常驻断言
        （2026-09-21 原值在案——迁移后被任何进程覆写即红）。"""
        mtime = (MARKET_DIR / name).stat().st_mtime
        assert mtime < _MIGRATION_EPOCH, (
            f"{name} mtime {_dt.datetime.fromtimestamp(mtime)} 晚于迁移时刻"
            "——冻结快照被重写（违反无运行时写入者契约）")
