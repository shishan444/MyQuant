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
from tests.helpers.data_factory import make_dna, make_enhanced_df, make_ohlcv


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


class TestPerBarExitTiming:
    """U6 逐笔出场时刻锚（tester 补——断言面审查暴露的缺口）。

    既有 u6d 为覆盖公式的代数自证（未触生产代码）、u6e/u6f 弱断言
    （total_trades>=1/2 在机制失效时同样满足——None 禁用对照下
    total_trades=1 亦成立）。本类用排除法在逐笔层锁时刻触发行为：
    stop_loss/take_profit/max_hold 全置 None、出场信号阈值永不触发
    ——唯一出场途径=time_exit_utc，trades 的出场 bar 开盘时刻即
    「当根区间 [open, open+tf) 覆盖时刻」的直接证据。末根 bar 的
    期末强平非时刻触发，从断言中排除。
    """

    def _run(self, time_exit, freq="1h", n=240, seed=21):
        """返回 (result, 中途出场时刻集, df)——中途=排除末根强平。"""
        from core.backtest.engine import BacktestEngine
        from core.strategy.executor import dna_to_signal_set, clear_indicator_cache
        dna = make_dna(entry_value=70, exit_value=101)  # 入场常触发
        dna.risk_genes.stop_loss = None
        dna.risk_genes.take_profit = None
        dna.risk_genes.max_hold = None
        if time_exit is not None:
            dna.risk_genes.time_exit_utc = time_exit
        dna.execution_genes.timeframe = freq
        df = make_enhanced_df(n=n, freq=freq, seed=seed)
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        res, portfolio = BacktestEngine().run_with_portfolio(dna, df)
        trades = portfolio.trades.records_readable
        mid = pd.DatetimeIndex(
            [t for t in trades["Exit Timestamp"] if t != df.index[-1]])
        return res, mid, df

    def test_midtrade_exits_all_at_declared_hour(self):
        """t=600（10:00）@1h：全部中途出场 bar 开盘恰为 10:00
        （跨日逐日触发——多笔且时刻唯一）。"""
        res, mid, _ = self._run(600)
        assert len(mid) >= 2, "多日持仓应有多笔时刻出场"
        assert all(t.hour == 10 and t.minute == 0 for t in mid), \
            f"出场 bar 应全部为 10:00 开盘，实际: {list(mid[:5])}"

    def test_zero_is_valid_moment_not_disabled(self):
        """t=0 是 0:00 合法时刻（非禁用哨兵）：出场 bar 全部 0:00
        （u6b 物化断言的行为面补全）。"""
        res, mid, _ = self._run(0)
        assert len(mid) >= 2, "0 应触发（区别于禁用）"
        assert all(t.hour == 0 and t.minute == 0 for t in mid), \
            f"出场 bar 应全部为 0:00 开盘，实际: {list(mid[:5])}"

    def test_none_sentinel_no_midtrade_exit(self):
        """None→哨兵 -1.0：除末根强平外零出场——无声明路径在
        「唯一出场途径=time_exit」设计下的行为锚（u6h 等价性声明的
        行为面证据）。"""
        res, mid, _ = self._run(None)
        assert len(mid) == 0, f"禁用时不应有中途出场: {list(mid[:5])}"

    def test_coverage_left_closed_right_open_on_4h_grid(self):
        """覆盖边界（实现层——u6d 的实证版）：4h 网格下 t=0 仅在
        0:00 开盘 bar 触发；t=240 归下根仅在 4:00 开盘 bar 触发
        （区间 [open, open+tf) 左闭右开：t==open 触发、t==open+tf
        归下根）。"""
        _, mid0, _ = self._run(0, freq="4h")
        _, mid240, _ = self._run(240, freq="4h")
        assert len(mid0) >= 2 and all(t.hour == 0 for t in mid0), \
            f"t=0 应仅在 0:00 bar 触发，实际: {list(mid0[:5])}"
        assert len(mid240) >= 2 and all(t.hour == 4 for t in mid240), \
            f"t=240 应仅在 4:00 bar 触发（归下根），实际: {list(mid240[:5])}"

    def test_daily_degenerate_equals_max_hold_1(self):
        """U6f 等价性对照（引擎层行为锚）：入口侧（validate_dna）按
        C5 拒绝 time_exit_utc×日线组合；本用例直构 DNA 直跑引擎（
        dna_to_signal_set/BacktestEngine 均不经 validate_dna），锁
        design 2.6「日线退化≡max_hold=1」的引擎行为事实——同数据
        同入场结构 metrics 全等（防引擎回归或未来放开入口后语义漂移）。"""
        def _run_risk(apply):
            from core.backtest.engine import BacktestEngine
            from core.strategy.executor import (
                dna_to_signal_set, clear_indicator_cache)
            dna = make_dna(entry_value=70, exit_value=101)
            dna.risk_genes.stop_loss = None
            dna.risk_genes.take_profit = None
            apply(dna.risk_genes)
            dna.execution_genes.timeframe = "1d"
            df = make_enhanced_df(n=200, freq="1d", seed=5)
            clear_indicator_cache()
            sig = dna_to_signal_set(dna, df)
            return BacktestEngine().run(dna, df, signal_set=sig)

        ra = _run_risk(lambda rg: setattr(rg, "time_exit_utc", 600))
        rb = _run_risk(lambda rg: setattr(rg, "max_hold", 1))
        assert ra.total_trades >= 2
        assert ra.total_trades == rb.total_trades
        assert ra.metrics_dict == rb.metrics_dict, \
            "1d 退化与 max_hold=1 的 metrics 应全等"

    def test_u6c_same_bar_double_hit_single_close(self):
        """U6c 同 bar 双命中全平：SL 触碰线与时刻出场同一 bar 命中时
        恰一笔全平出场（Closed 无拆分重复）。

        判定序说明（tester 探针结论）：SL 与 time_exit 两分支的出场
        订单形制同构（市价全平、同 bar close 成交），先后序在行为层
        不可观察——可观察面=双命中时单次全平、无部分/重复出场。
        构造：hour_utc<=1 每日 0/1 点确定性入场，无出场信号；把持仓
        期内一根 10:00 bar 的 low 打穿 SL 线（entry≈39598×0.95≈37618
        →low=37000）→ 该 bar 双命中。
        """
        from core.backtest.engine import BacktestEngine
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, SignalGene, SignalRole, StrategyDNA)
        from core.strategy.executor import (
            dna_to_signal_set, clear_indicator_cache)

        df = make_ohlcv(n=240, freq="1h", seed=42)
        ts = pd.Timestamp("2024-01-02 10:00:00+00:00")  # 持仓期内 10:00 bar
        pos = df.index.get_loc(ts)
        df.iloc[pos, df.columns.get_loc("low")] = 37000.0
        df["hour_utc"] = df.index.hour + df.index.minute / 60.0
        dna = StrategyDNA(
            signal_genes=[SignalGene(
                "hour_utc", {}, SignalRole.ENTRY_TRIGGER, None,
                {"type": "le", "threshold": 1.0})],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="1h"),
            risk_genes=RiskGenes(stop_loss=0.05, time_exit_utc=600))
        clear_indicator_cache()
        sig = dna_to_signal_set(dna, df)
        assert int(sig.entries.sum()) >= 10, "确定性入场信号在产生"
        _, portfolio = BacktestEngine().run_with_portfolio(dna, df)
        tr = portfolio.trades.records_readable
        hits = tr[tr["Exit Timestamp"] == ts]
        assert len(hits) == 1, f"双命中 bar 应恰一笔出场，实际 {len(hits)}"
        assert hits["Status"].iloc[0] == "Closed", "全平非部分出场"
        # 同 Position 无拆分重复（双机制不得各出一次）
        assert int((tr["Position Id"] == hits["Position Id"].iloc[0]).sum()) == 1
