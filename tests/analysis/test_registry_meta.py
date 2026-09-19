"""指标元数据外化与双源合并单测（add-ai-research-mcp 1.3）。"""

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.registry_meta import (
    available_signal_columns,
    derive_warmup,
    indicator_metadata,
)
from core.features.registry import INDICATOR_REGISTRY
from core.persistence.repository import research as rr


@pytest.fixture(scope="module")
def db_path(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("reg_meta") / "reg.db"
    subprocess.run(
        ["venv/bin/python", "-m", "alembic", "-x", f"db_path={db}", "upgrade", "head"],
        capture_output=True, text=True, check=True,
    )
    return db


class TestIndicatorMetadata:
    def test_covers_all_builtin_indicators(self) -> None:
        """外化条目与注册表逐项一致（47 项）。"""
        meta = indicator_metadata()
        assert len(meta) == len(INDICATOR_REGISTRY) == 47
        assert {m["name"] for m in meta} == set(INDICATOR_REGISTRY)

    def test_every_entry_has_lookback(self) -> None:
        for m in indicator_metadata():
            assert isinstance(m["lookback"], int) and m["lookback"] >= 0, m["name"]

    def test_ema_lookback_is_200(self) -> None:
        """注册表参数上限最大值恰为 200（EMA.period）——D5 查证锚点。"""
        meta = {m["name"]: m for m in indicator_metadata()}
        assert meta["EMA"]["lookback"] == 200
        assert meta["SMA"]["lookback"] == 200  # 若存在同上限指标

    def test_unknown_indicator_rejected(self) -> None:
        with pytest.raises(KeyError):
            derive_warmup(["NOT_AN_INDICATOR"])


class TestDeriveWarmup:
    def test_floor_200_when_small_period(self) -> None:
        """小周期指标：下限 200 保底。"""
        # EMA 周期上限 200 已达上限；取一个短周期指标（如 ATR 50）：50*3=150 < 200
        assert derive_warmup(["ATR"]) == 200

    def test_3x_when_period_exceeds_floor(self) -> None:
        """最大周期×3 超过 200 时按推导值。"""
        assert derive_warmup(["EMA"]) == max(200, 200 * 3)  # 600

    def test_takes_max_across_indicators(self) -> None:
        """多指标取最大周期。"""
        assert derive_warmup(["ATR", "EMA"]) == 600

    def test_empty_defaults_to_floor(self) -> None:
        assert derive_warmup([]) == 200


class TestAvailableSignalColumns:
    def test_builtin_only_without_plugins(self, db_path: Path) -> None:
        cols = available_signal_columns(db_path)
        builtin = [c for c in cols if c["source"] == "builtin"]
        assert len(builtin) == 47

    def test_plugin_registration_appears_immediately(self, db_path: Path) -> None:
        """插件注册后合并查询即时出现新条目（双源一致）。"""
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_meta", name="meta_col",
            version=1, expression="zscore(close, 20)", output_columns='["meta_col"]',
            entry_id="e_meta", column_name="meta_col")
        cols = available_signal_columns(db_path)
        plugin_cols = [c for c in cols if c["source"] == "formula"]
        assert any(c["column_key"] == "meta_col" for c in plugin_cols)

    def test_only_visible_plugin_entries(self, db_path: Path) -> None:
        """strategy_visible=0 的插件列不进合并清单（list 过滤在 repository）。"""
        cols = available_signal_columns(db_path)
        assert all(c["strategy_visible"] for c in cols)
