"""交易链兼容性拒绝面测试（0.4.2 R2/D3，stop-loss-modes 规格交易链
Requirement，B3-17 判据面——评估问题 5 a1 收敛）。

双判据×三链：判据一模式×链路矩阵（trail 三链拒/atr 仅 live 拒/pct 全链
/atr×replay·paper 放行）；判据二分批角色（手工构造 DNA 预验，不依赖
翻译生产者——4.6 翻译生产路径收口）。分层锚：validate_dna 对同 DNA
不报错（trail/分批是合法 DNA 形态）。
"""

from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)
from core.strategy.validator import validate_dna, validate_trading_compatibility


def mk_dna(sl_mode="pct", roles=(), **risk):
    # 基础 entry+exit 结构（validate_dna 结构完整性要求）+ 追加角色基因
    genes = [SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                        condition={"type": "lt", "threshold": 30}),
             SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                        condition={"type": "gt", "threshold": 70})]
    genes += [SignalGene("RSI", {"period": 14}, r,
                         condition={"type": "lt", "threshold": 30})
              for r in roles]
    return StrategyDNA(signal_genes=genes,
                       risk_genes=RiskGenes(sl_mode=sl_mode, **risk))


class TestMatrixValidator:
    """validate_trading_compatibility 双判据矩阵（单点行为锚）。"""

    @pytest.mark.parametrize("sl_mode,link,rejected", [
        ("pct", "replay", False), ("pct", "paper", False), ("pct", "live", False),
        ("atr", "replay", False), ("atr", "paper", False), ("atr", "live", True),
        ("trail", "replay", True), ("trail", "paper", True), ("trail", "live", True),
    ])
    def test_mode_link_matrix(self, sl_mode, link, rejected):
        reasons = validate_trading_compatibility(mk_dna(sl_mode), link)
        assert (len(reasons) > 0) == rejected

    def test_atr_live_detail_names_danger(self):
        """拒绝 detail 链路语义可判定：指明模式×链路与具体危险形态。"""
        reasons = validate_trading_compatibility(mk_dna("atr"), "live")
        assert len(reasons) == 1
        assert "实盘" in reasons[0] and "atr" in reasons[0]
        assert "负价" in reasons[0]  # 危险形态披露（无效价格止损单）

    def test_trail_detail_names_scope(self):
        reasons = validate_trading_compatibility(mk_dna("trail"), "paper")
        assert "trail" in reasons[0] and "峰值回撤" in reasons[0]

    @pytest.mark.parametrize("role", [
        SignalRole.REDUCE_TRIGGER, SignalRole.REDUCE_GUARD,
        SignalRole.ADD_TRIGGER, SignalRole.ADD_GUARD,
        SignalRole.ADD_INHIBIT])
    @pytest.mark.parametrize("link", ["replay", "paper", "live"])
    def test_scaling_roles_all_links_rejected(self, role, link):
        """判据二：五分批角色×三链全拒（手工构造 DNA 预验）。"""
        reasons = validate_trading_compatibility(mk_dna(roles=(role,)), link)
        assert len(reasons) == 1 and role.value in reasons[0]

    def test_two_criteria_merged_report(self):
        """双判据合并报告（trail+分批 → 两条原因）。"""
        dna = mk_dna(sl_mode="trail", roles=(SignalRole.ADD_TRIGGER,))
        assert len(validate_trading_compatibility(dna, "live")) == 2

    def test_layer_genes_enumerated(self):
        """layers 展开判别：层内分批基因同样命中（auto-wrap 代理全集）。"""
        from core.strategy.dna import TimeframeLayer
        dna = mk_dna(roles=(SignalRole.ADD_INHIBIT,))
        dna.layers = [TimeframeLayer(timeframe="4h",
                                     signal_genes=dna.signal_genes)]
        reasons = validate_trading_compatibility(dna, "paper")
        assert len(reasons) == 1 and "add_inhibit" in reasons[0]

    def test_layering_anchor_validate_dna_clean(self):
        """分层锚两面：validate_dna 对 trail/分批/atr 倍数 DNA 不报错
        （合法 DNA 形态——全局校验不因此拒绝，回测与进化种子不受挡）。"""
        for dna in (mk_dna(sl_mode="trail", stop_loss=0.05),
                    mk_dna(sl_mode="atr", stop_loss=2.0, take_profit=4.0),
                    mk_dna(sl_mode="trail", stop_loss=0.05, take_profit=0.03),
                    mk_dna(roles=(SignalRole.ADD_TRIGGER,))):
            v = validate_dna(dna)
            assert v.is_valid, f"{dna.risk_genes.sl_mode} DNA 应合法: {v.errors}"

    def test_bad_link_raises(self):
        with pytest.raises(ValueError):
            validate_trading_compatibility(mk_dna(), "backtest")


class TestReplayInstrumentation:
    """replay 链插桩：trail 载入即拒；atr 放行（既有能力锚——ATR 列在时
    DecisionPipeline 按 sl_mode 分派正确执行，拒绝面扩容必配正向放行锚）。"""

    def _df(self):
        idx = pd.DatetimeIndex(
            pd.date_range("2024-01-01", periods=60, freq="4h"), name="timestamp")
        import numpy as np
        rng = np.random.default_rng(7)
        close = 60000 + np.cumsum(rng.normal(0, 100, 60))
        return pd.DataFrame({
            "open": close, "high": close + 80, "low": close - 80,
            "close": close, "volume": rng.uniform(100, 900, 60),
            "atr_14": [np.nan] * 14 + [500.0] * 46,
        }, index=idx)

    def test_trail_rejected_at_load(self):
        from core.trading.replay import ReplayRunner
        runner = ReplayRunner()
        with pytest.raises(ValueError) as ei:
            runner.run(mk_dna(sl_mode="trail"), self._df())
        assert "回放链不支持" in str(ei.value) and "trail" in str(ei.value)

    def test_atr_replay_allowed_and_executes(self):
        """atr×replay 放行锚：正常按 ATR 倍数语义执行不误拒。"""
        from core.trading.replay import ReplayRunner
        runner = ReplayRunner()
        result = runner.run(
            mk_dna(sl_mode="atr", stop_loss=2.0, take_profit=4.0), self._df())
        assert result is not None  # 放行跑通（ATR 列在——按倍数执行）

    def test_pct_replay_unchanged(self):
        from core.trading.replay import ReplayRunner
        runner = ReplayRunner()
        assert runner.run(mk_dna(sl_mode="pct"), self._df()) is not None


class TestPaperInstrumentation:
    """paper 链插桩：任务装载点 trail/分批拒、atr 放行。"""

    def _task_row(self, dna):
        return {"task_id": "t1", "dna_json": dna.to_json(), "symbol": "BTCUSDT",
                "timeframe": "4h", "initial_cash": 100000.0, "fee": 0.001,
                "leverage": 1, "direction": "long", "created_at": "2024-01-01"}

    def test_trail_paper_rejected(self):
        from core.trading.runner import TradingRunner
        r = TradingRunner.__new__(TradingRunner)  # 不走线程 init
        with pytest.raises(ValueError) as ei:
            r._execute_task(self._task_row(mk_dna(sl_mode="trail")),
                            "t1", controller=None)
        assert "模拟盘链不支持" in str(ei.value)

    def test_atr_paper_allowed_past_gate(self):
        """atr×paper 放行锚：过兼容门（后续流程因无数据目录等自然失败
        ——非兼容性拒绝）。"""
        from core.trading.runner import TradingRunner
        import dataclasses
        r = TradingRunner.__new__(TradingRunner)
        # atr 应越过兼容性插桩（失败若发生应为 no_data 类而非 ValueError
        # 模拟盘链不支持）
        try:
            r._execute_task(self._task_row(mk_dna(sl_mode="atr")),
                            "t1", controller=None)
        except ValueError as exc:
            pytest.fail(f"atr×paper 不应被兼容性拒绝: {exc}")
        except Exception:
            pass  # 无数据目录等环境性失败——兼容门已过


class TestLiveInstrumentation:
    """live 链插桩：LiveTradingExecutor 装载即拒（挂 SL/TP 单之前）。"""

    def _mk_executor(self, dna):
        from core.live_trading.executor import LiveTradingExecutor
        # 组件占位（装载即拒——组件不会被触碰）
        return LiveTradingExecutor(
            pipeline=None, gateway=None, risk=None, oms=None, dna=dna,
            symbol="BTCUSDT", state=None)

    def test_trail_live_rejected_before_sl_tp(self):
        with pytest.raises(ValueError) as ei:
            self._mk_executor(mk_dna(sl_mode="trail"))
        assert "实盘链不支持" in str(ei.value)

    def test_atr_live_rejected_with_danger_form(self):
        with pytest.raises(ValueError) as ei:
            self._mk_executor(mk_dna(sl_mode="atr", stop_loss=2.0))
        assert "负价" in str(ei.value)  # 危险形态披露

    def test_pct_live_loads(self):
        assert self._mk_executor(mk_dna(sl_mode="pct")) is not None
