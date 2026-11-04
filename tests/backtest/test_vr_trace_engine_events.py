"""vr-signal-trace 引擎层端到端事件锚（tester 覆盖核对补齐）。

覆盖缺口来源：spec R1 Scenario「吞与拒不混」在既有集成测试（test_
vr_trace_integration.test_isolated）中为条件性断言——探针实证该样本
trace 仅 3 行 filled，swallowed/rejected 零行，断言空转。本文件以
golden 样本矩阵中契约点名的触发面样本（swallow_conflict/exposure_
add/trail_lock 系）建立确定性断言：

- T1-I3 端到端（求值层生产→SignalSet.swallowed→engine.swallowed_rows
  →resolver→行）：吞没两族（33/34）+决策层拒绝（30）各自在场、
  outcome 分立、reason 两族不串值、灰族行无 size/price。
- T1-I5 机械验（design §5「trail 线加仓 bar 无跳变——峰值连续」）：
  trail 档含加仓样本——trail_series 逐 bar 真值单调不降（多头峰值
  只升不降语义）+加仓 bar 处线值不回跳（加仓不重置峰值）。

期望值全部从 spec/design 契约推导（reason 枚举两族、outcome 三值与
码的绑定、trail 峰值语义），不从实现输出反推。
"""
from __future__ import annotations

import numpy as np
import pytest

from core.backtest.engine import BacktestEngine
from core.backtest.trace_resolver import resolve_signal_trace
from tests.fixtures.vr_trace_gen import SAMPLES, build_sample

# spec R2：reason 五值两族（决策层三值+求值层两值）——分立契约
DECISION_FAMILY = {"exposure_cap", "insufficient_funds",
                   "risk_budget_fallback"}
EVAL_FAMILY = {"entry_exit_conflict", "add_inhibited"}


def _resolve(name: str, risk_params: dict | None = None):
    """golden 样本 → (result, trace 行列表)（引擎+resolver 全链）。"""
    s = build_sample(name)
    eng = BacktestEngine(
        init_cash=s["init_cash"], fee=s["fee"], slippage=s["slippage"])
    r = eng.run(s["dna"], s["df"], dfs_by_timeframe=s["dfs_by_timeframe"])
    risk = risk_params or {
        "position_mode": "fixed",
        "size_pct": SAMPLES[name]["dna"]["risk_genes"]["position_size"],
        "sl_mode": SAMPLES[name]["dna"]["risk_genes"]["sl_mode"],
        "sl_stop": SAMPLES[name]["dna"]["risk_genes"]["stop_loss"],
    }
    rows = resolve_signal_trace(
        r.trace_buf, s["df"].index, r.swallowed_rows, r.trades_df,
        exec_ref="main", init_cash=s["init_cash"], risk_params=risk)
    return s, r, rows


class TestSwallowVsRejectEndToEnd:
    """T1-I3 端到端：吞与拒不混（spec R1 Scenario 2）。"""

    def test_swallowed_two_families_present(self):
        """求值层两分支（同 bar 互斥+加仓抑制）各产 swallowed 行。

        样本 swallow_conflict 契约面：RSI>50 同条件进出场（互斥清洗
        吞 entry）+同条件加仓抑制——两分支 bar 面重合，端到端行各≥1。
        """
        _, _, rows = _resolve("swallow_conflict")
        sw = [r for r in rows if r["outcome"] == "swallowed"]
        assert sw, "求值层吞没行缺失——swallowed 生产/传递链断裂"
        reasons = {r["reason"] for r in sw}
        assert reasons == EVAL_FAMILY, (
            f"吞没两分支须齐：{reasons}")
        for r in sw:
            assert r["event_code"] in (33, 34)
            assert r["size"] is None and r["price"] is None, (
                "swallowed=未达引擎——无成交语义，size/price 必空")

    def test_decision_reject_present(self):
        """决策层拒绝（敞口上限码 30）端到端在场+reason 绑定。

        样本 exposure_add 契约面：max_exposure=0.25——首轮建仓后加仓
        触发持续被敞口上限拦截（决策层拒绝分支）。
        """
        _, _, rows = _resolve("exposure_add")
        rej = [r for r in rows if r["outcome"] == "rejected"
               and r["event_code"] == 30]
        assert rej, "决策层拒绝行（码 30）缺失"
        for r in rej:
            assert r["reason"] == "exposure_cap"
            assert r["size"] is None and r["price"] is None, (
                "rejected=未发单——无成交语义")

    def test_two_families_never_crossover(self):
        """reason 分属两族不串值：swallowed 行 reason 恒求值族，
        30/31/32 拒绝行 reason 恒决策族（spec「不混」的字面断言）。"""
        for name in ("swallow_conflict", "exposure_add"):
            _, _, rows = _resolve(name)
            for r in rows:
                if r["outcome"] == "swallowed":
                    assert r["reason"] in EVAL_FAMILY
                elif r["outcome"] == "rejected" and r["reason"] is not None:
                    assert r["reason"] in DECISION_FAMILY, (
                        f"{name}: 拒绝行 reason 串值 {r['reason']!r}")
        assert not (DECISION_FAMILY & EVAL_FAMILY), (
            "两族值域必须互不相交（枚举稳定性契约）")


class TestTrailAddContinuity:
    """T1-I5 机械验：trail 档含加仓样本的 trail_series 峰值连续。

    契约（spec R4 Scenario「trail 逐 bar 真值」+design §5）：trail
    线为引擎逐 bar 真值；加仓 bar 无跳变——峰值连续（多头 peak 只升
    不降、sl_level=peak×(1-sl_stop)，加仓不重置峰值）。
    """

    @pytest.fixture()
    def trail_add(self):
        spec = SAMPLES["trail_lock"]
        risk = dict(spec["dna"]["risk_genes"])
        # 小份持续加仓（EMA7 上方常态真+add_fraction 0.05+敞口 2.0）：
        # 持仓期每 bar 成交加仓——机械验检查点最大化（任一 bar 线值
        # 回跳即红；构造过程见 tester 探针记录）
        risk.update({"add_fraction": 0.05, "max_exposure": 2.0,
                     "position_size": 0.3})
        from core.strategy.dna import StrategyDNA
        dna = StrategyDNA.from_dict({
            "signal_genes": [
                {"indicator": "EMA", "params": {"period": 21},
                 "role": "entry_trigger", "field": "ema",
                 "condition": {"type": "price_above"}},
                # 短周期 EMA 加仓触发：首轮建仓后短时间内触发加仓 bar
                {"indicator": "EMA", "params": {"period": 7},
                 "role": "add_trigger", "field": "ema",
                 "condition": {"type": "price_above"}},
                {"indicator": "EMA", "params": {"period": 21},
                 "role": "exit_trigger", "field": "ema",
                 "condition": {"type": "price_below"}},
            ],
            "logic_genes": {"entry_logic": "AND", "exit_logic": "AND",
                            "add_logic": "AND", "reduce_logic": "AND"},
            "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
            "risk_genes": risk,
        })
        s = build_sample("trail_lock")
        eng = BacktestEngine(
            init_cash=s["init_cash"], fee=s["fee"], slippage=s["slippage"])
        r = eng.run(dna, s["df"])
        rows = resolve_signal_trace(
            r.trace_buf, s["df"].index, r.swallowed_rows, r.trades_df,
            exec_ref="main")
        return s, r, rows

    def test_add_event_present(self, trail_add):
        _, _, rows = trail_add
        assert any(r["event_code"] == 11 for r in rows), (
            "样本未触发加仓 bar——机械验前提失效（样本须换）")

    def test_trail_series_monotonic_nondecreasing(self, trail_add):
        """多头峰值连续：有效值序列单调不降（peak 只升不降的机械表达）。"""
        _, r, _ = trail_add
        ts = r.trail_series
        assert ts is not None, "trail 档 trail_series 不得为 None"
        vals = ts.dropna().to_numpy(dtype=float)
        assert len(vals) >= 2, "有效线值段过短——机械验无判别力"
        assert np.all(np.diff(vals) >= 0.0), (
            "trail_series 出现回跳——峰值连续契约被破坏（或线非引擎真值）")

    def test_no_jump_at_add_bars(self, trail_add):
        """加仓 bar 无跳变：每个加仓 bar 的线值不低于前一有效值
        （加仓重置峰值=缺陷形态：线值跳回低位）。"""
        import pandas as pd
        s, r, rows = trail_add
        ts = r.trail_series
        add_bars = [r2["bar_time"] for r2 in rows
                    if r2["event_code"] == 11]
        checked = 0
        for bt in add_bars:
            i = list(ts.index).index(pd.Timestamp(bt))
            prev = ts.iloc[:i].dropna()
            if len(prev):
                checked += 1
                assert ts.iloc[i] >= prev.iloc[-1], (
                    f"加仓 bar {bt} 线值 {ts.iloc[i]} 低于前一有效值 "
                    f"{prev.iloc[-1]}——加仓重置了峰值（跳变红线）")
        assert checked, "无带前值的加仓 bar——机械验空转（样本须换）"


class TestInsufficientFundsAndFallback:
    """阶段四闸门补：31/32 码引擎侧确定性触发（行为覆盖消解）。

    扫参实证的触发条件已固化入 vr_trace_gen 样本（liq_reentry_funds
    =清算残值与开仓门槛的量级差；risk_budget_fallback=risk_fraction
    0.0 使 qty 恒 0 回退百分比仓）——码集断言+行语义断言双锚。
    """

    def test_insufficient_funds_code31_present(self):
        """31 码：清算后再入场资金不足——rejected+决策族 reason+无成交。

        行语义（engine :880-885）：is_liquidated 且仓位已平+entry 信号
        在场+value_now<min_required→NoOrder（未发单）。
        """
        _, _, rows = _resolve("liq_reentry_funds")
        rej = [r for r in rows if r["event_code"] == 31]
        assert rej, "31 码行缺失——清算后再入场资金不足分支未被触发"
        for r in rej:
            assert r["outcome"] == "rejected"
            assert r["reason"] == "insufficient_funds"
            assert r["size"] is None and r["price"] is None

    def test_risk_budget_fallback_code32_present(self):
        """32 码：qty≤0 回退百分比仓发单——有成交对照=filled+reason=32。

        行语义（engine :1311-1314）：risk_budget 档 qty≤0 保留 size_pct
        百分比仓发单（回退单）；outcome 由 resolver 与成交对照定。
        """
        _, _, rows = _resolve("risk_budget_fallback", risk_params={
            "position_mode": "risk_budget", "sl_mode": "pct",
            "sl_stop": 0.05, "risk_fraction": 0.0, "size_pct": 0.3,
            "add_fraction": 0.1})
        fb = [r for r in rows if r["event_code"] == 32]
        assert fb, "32 码行缺失——风险预算回退分支未被触发"
        for r in fb:
            assert r["reason"] == "risk_budget_fallback"
            # 有成交对照=filled（回退单实际发单）；rejected+32 需无成交
            # 对照——样本 EMA 触发面恒有成交，filled 形态为预期态
            assert r["outcome"] in ("filled", "rejected")
            if r["outcome"] == "filled":
                assert r["size"] is not None and r["price"] is not None
