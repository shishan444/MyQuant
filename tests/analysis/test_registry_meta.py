"""指标元数据外化与双源合并单测（add-ai-research-mcp 1.3）。"""

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.registry_meta import (
    available_signal_columns,
    derive_warmup,
    derive_warmup_full,
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
        """单指标周期类参数上限之和×3 超过 200 时按推导值。"""
        assert derive_warmup(["EMA"]) == max(200, 200 * 3)  # 600

    def test_takes_max_across_indicators(self) -> None:
        """多指标取最大的（各指标周期类参数上限之和）。"""
        assert derive_warmup(["ATR", "EMA"]) == 600

    def test_empty_defaults_to_floor(self) -> None:
        assert derive_warmup([]) == 200


class TestDeriveWarmupFull:
    """两维 warmup 公共入口（add-hypothesis-precheck 1.2——预检与
    隔离回测 _derive_dna_warmup 薄壳共用的同源计算式单点）。"""

    def test_builtin_only_equals_derive_warmup(self) -> None:
        """纯内置组：与 derive_warmup 完全一致（floor 200 自带）。"""
        assert derive_warmup_full(None, ["ATR"]) == derive_warmup(["ATR"]) == 200
        assert derive_warmup_full(None, ["EMA"]) == derive_warmup(["EMA"]) == 600

    def test_empty_plugin_set_keeps_builtin(self) -> None:
        """空插件集跳过插件维度（None 与空 set 同语义）。"""
        assert derive_warmup_full(None, [], None) == 200
        assert derive_warmup_full(None, ["EMA"], set()) == 600

    def test_plugin_dimension_takes_max(self, db_path: Path) -> None:
        """含插件组：插件窗口 900 > 内置推导 → 两维取大 900。"""
        rr.save_plugin_with_registry(
            db_path, source="formula", plugin_id="p_full", name="fx_big_window",
            version=1, expression="rolling_mean(close, 900)",
            output_columns='["fx_big_window"]',
            entry_id="e_full", column_name="fx_big_window")
        assert derive_warmup_full(db_path, ["ATR"], {"fx_big_window"}) == 900
        assert derive_warmup_full(db_path, ["EMA"], {"fx_big_window"}) == 900

    def test_unreachable_plugin_silently_zero(self, db_path: Path) -> None:
        """不可达列静默贡献 0（floor 保底）——锚定该行为正是预检
        装载入口必须显式校验可达性的原因（specs R3：MUST NOT 静默降维）。"""
        assert derive_warmup_full(db_path, [], {"fx_not_registered"}) == 200


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

    def test_scopes_annotation_present_and_consistent(self, db_path: Path) -> None:
        """M5 域标注：每条目带三域标注，且与模板实际可用性一致——
        指标列可作假设 subject 与策略信号源，但不可直接用于列引用类
        模板（correlation_scan 的 df 为原始列，指标分析走 condition_stats
        的 indicator 参数）。"""
        cols = available_signal_columns(db_path)
        assert cols
        for c in cols:
            assert set(c["scopes"]) == {"hypothesis", "analysis", "strategy"}
            assert c["scopes"]["hypothesis"] is True
            assert c["scopes"]["strategy"] is c["strategy_visible"]
            if c["source"] == "builtin":
                assert c["scopes"]["analysis"] is False, (
                    "内置指标列不可直接用于列引用模板（rsi_14 引用即"
                    "RESEARCH_COLUMN_NOT_AVAILABLE——错误即文档）")


def test_signal_columns_carry_reference_form(db_path):
    """T5：条目附引用形制（指标=结构化构造依据；插件=字符串精确名）。"""
    cols = available_signal_columns(db_path)
    builtin = next(c for c in cols if c["source"] == "builtin")
    assert builtin["reference_form"]["kind"] == "structured"
    assert "params_space" in builtin["reference_form"]
    plugin = [c for c in cols if c["source"] in ("formula", "model")]
    if plugin:
        assert plugin[0]["reference_form"]["kind"] == "string"


class TestGapClosureMeta:
    def test_indicator_convergence未知指标_KeyError(self) -> None:
        from core.analysis.registry_meta import indicator_convergence
        with pytest.raises(KeyError, match="未知指标"):
            indicator_convergence("GHOST_IND", {})
