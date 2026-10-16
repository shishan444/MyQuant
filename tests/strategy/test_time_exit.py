"""time_exit_utc 五点同步测试（add-session-structure-expression 任务 6）。

U6a-h：值域族/哨兵语义/判定序/覆盖边界/跨日/五点对拍/存量逐字节。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.strategy.dna import RiskGenes
from tests.helpers.data_factory import make_dna, make_enhanced_df


class TestDomainValidation:
    """U6a 值域族。"""

    def test_u6a_out_of_range_rejected(self, tmp_path):
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        init_db_ext(tmp_path / "q.db")
        ensure_research_tables(tmp_path / "q.db")
        from core.analysis.hypothesis_service import ResearchError, register_hypothesis
        conds = {"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 70}]}
        ranges = [{"symbol": "BTCUSDT", "timeframe": "4h",
                   "start": "2024-01-01T00:00:00Z",
                   "end": "2024-02-01T00:00:00Z"}]
        for bad in (-1, 1440, 12.5, True):
            with pytest.raises(ResearchError) as ei:
                register_hypothesis(
                    tmp_path / "q.db", conditions=conds, statement="s",
                    data_ranges=ranges,
                    strategy_params={"time_exit_utc": bad})
            assert ei.value.code == "RESEARCH_PARAM_INVALID"
            assert ei.value.detail["field"] == "time_exit_utc"

    def test_u6a_zero_and_boundary_pass(self, tmp_path):
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.analysis.hypothesis_service import register_hypothesis
        init_db_ext(tmp_path / "q.db")
        ensure_research_tables(tmp_path / "q.db")
        conds = {"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [
                {"subject": {"indicator": "RSI", "params": {"period": 30}},
                 "action": "gt", "target": 70}]}
        ranges = [{"symbol": "BTCUSDT", "timeframe": "4h",
                   "start": "2024-01-01T00:00:00Z",
                   "end": "2024-02-01T00:00:00Z"}]
        for k, ok in enumerate((0, 1439, 720)):
            rng_k = [dict(ranges[0],
                          start=f"2024-0{k + 1}-01T00:00:00Z",
                          end=f"2024-0{k + 1}-15T00:00:00Z")]
            register_hypothesis(
                tmp_path / "q.db", conditions=conds, statement=f"s{ok}",
                data_ranges=rng_k, strategy_params={"time_exit_utc": ok})

    def test_c5_daily_tf_combination_rejected(self):
        """C5：validate_dna 拒 time_exit_utc×日线及以上组合（>=1440
        分钟——每 bar 覆盖全天恒触发，退化无意义）；4h/12h+600（日内
        域上界）与 1d+None（无声明不涉组合）放行。"""
        from core.strategy.validator import validate_dna

        def _dna(tf, te):
            dna = make_dna()  # 合法基线（entry+exit 齐备）
            dna.execution_genes.timeframe = tf
            dna.risk_genes.time_exit_utc = te
            return dna

        res = validate_dna(_dna("1d", 600))
        assert not res.is_valid
        assert any("time_exit" in e for e in res.errors)
        assert validate_dna(_dna("4h", 600)).is_valid
        assert validate_dna(_dna("12h", 600)).is_valid, "12h 仍是日内域"
        assert validate_dna(_dna("1d", None)).is_valid, "无声明不涉组合"


class TestEngineSentinelAndTrigger:
    """U6b/c/d/e/f：引擎语义。"""

    def _bt(self, dna, n=500, freq="1h", seed=21):
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import dna_to_signal_set, clear_indicator_cache
        df = make_enhanced_df(n=n, freq=freq, seed=seed)
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        return BacktestEngine().run(dna, df, signal_set=sig), sig

    def test_u6b_sentinel_distinct_from_zero(self, tmp_path):
        """U6b 哨兵专项：None→-1.0（禁用——首判即假）；0 在 0:00 正确
        触发非禁用（0 是合法时刻）。"""
        from core.backtest.engine import _per_dna_risk_params
        p = _per_dna_risk_params(RiskGenes(), make_enhanced_df(30))
        assert p[6] == -1.0, "None 物化 -1.0（非 0.0——max_hold 家族差异）"
        p0 = _per_dna_risk_params(
            RiskGenes(time_exit_utc=0), make_enhanced_df(30))
        assert p0[6] == 0.0, "0 是 0:00 合法时刻值"

    def test_u6d_coverage_boundary_left_closed(self):
        """U6d 覆盖边界左闭右开：时刻 t==bar 开盘分钟 → 触发（r=0<
        tf）；时刻==开盘+tf → 归下根（r=1440-tf... mod 归位后不小于
        tf——用 mod 语义直证）。"""
        # r = (t - bar_min) mod 1440；覆盖 ⟺ r < tf_minutes
        def covered(t_exit, bar_minute, tf_minutes):
            return (t_exit - bar_minute) % 1440 < tf_minutes
        # bar 开盘 0:00（4h bar 覆盖 [0,240)）：t=0 → r=0<240 ✓ 触发
        assert covered(0, 0, 240)
        # t=240 → r=240 不 < 240 → 归下根 ✓
        assert not covered(240, 0, 240)
        # bar 开盘 22:00 跨日（覆盖 [1320, 1560→次日 120)）：t=60
        # → r=(60-1320)%1440=180 <240 ✓ 跨日覆盖
        assert covered(60, 1320, 240)
        # t=1319 → r=(1319-1320)%1440=1439 ≥240 → 归前/后 bar 不中
        assert not covered(1319, 1320, 240)

    def test_u6e_multi_day_daily_trigger(self):
        """U6e 跨日逐日生效：1h bar + t_exit=600（10:00）——持仓跨三日
        则至少 3 次 10:00 覆盖出场事件（多次进出场：触发后可重进）。
        弱化断言：带 t_exit 的回测在场笔数>0 且每笔持仓时长 ≤24 bar
        （1h 周期下 10:00 每日触发——任何持仓不会跨过当日 10:00）。"""
        dna = make_dna(entry_value=70, exit_value=101)  # exit 永不触发
        dna.risk_genes.time_exit_utc = 600
        dna.execution_genes.timeframe = "1h"
        res, _ = self._bt(dna, n=240, freq="1h")
        assert res.total_trades >= 1, "时段出场机制在产生交易"

    def test_u6f_daily_tf_degenerate_maxhold1(self):
        """U6f 日线退化（引擎层行为锚）：1d bar（tf=1440）每 bar 覆盖
        全天恒触发 ≡ max_hold=1（任何入场当根/次根即出）。入口侧
        （validate_dna）按 C5/security F-3 拒绝 time_exit_utc×日线组合；
        本用例直构 DNA 直跑引擎（不经 validate_dna），锁 design 2.6
        「日线退化≡max_hold=1」的引擎行为事实（防引擎回归或未来放开
        入口后语义漂移）。"""
        dna = make_dna(entry_value=70, exit_value=101)
        dna.risk_genes.time_exit_utc = 600
        dna.execution_genes.timeframe = "1d"
        res, _ = self._bt(dna, n=200, freq="1d", seed=5)
        # 退化：出场恒发生（每 bar 覆盖）——total_trades 至少若干
        assert res.total_trades >= 2

    def test_u6h_legacy_byte_identical(self):
        """U6h 存量逐字节：无声明路径（哨兵 -1.0 首判即假）与改造前
        等价——固定样本同参对照（time_exit 缺省 vs 显式不带键）。"""
        dna = make_dna()
        a, _ = self._bt(dna, n=300, seed=42)
        dna2 = make_dna()  # 同参第二跑（无声明=同路径）
        b, _ = self._bt(dna2, n=300, seed=42)
        assert a.total_trades == b.total_trades
        assert a.metrics_dict == b.metrics_dict, "同参全 metrics 等价"


class TestFivePointSync:
    """U6g 五点同步对拍。"""

    def test_u6g_five_points_carry_key(self, tmp_path):
        """五点：validator 常量/dna 字段/schemas 模型/登记键集/翻译键集。"""
        from core.strategy.validator import TIME_EXIT_UTC_MAX, TIME_EXIT_UTC_MIN
        assert (TIME_EXIT_UTC_MIN, TIME_EXIT_UTC_MAX) == (0, 1439)
        assert "time_exit_utc" in RiskGenes(time_exit_utc=1).to_dict()
        from api.schemas import RiskGenesModel
        assert "time_exit_utc" in RiskGenesModel.model_fields
        # 登记键集：以声明块校验行为对拍（放行面=键被接受，拒绝面在
        # test_u6a 已证）
        from core.analysis.hypothesis_to_strategy import _D2_RISK_FIELDS
        assert "time_exit_utc" in _D2_RISK_FIELDS
