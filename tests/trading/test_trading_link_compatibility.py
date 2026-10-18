"""live 链兼容性拒绝面测试（0.4.2 R2/D3，stop-loss-modes 规格交易链
Requirement，B3-17 判据面——评估问题 5 a1 收敛；frontend-switch-and-
retire R.1 值域收敛 ("live",)——回放/模拟旧链退役）。

双判据×live 链：判据一模式矩阵（trail live 链拒/atr live 链拒/pct
放行）；判据二分批角色（手工构造 DNA 预验，不依赖翻译生产者——
4.6 翻译生产路径收口）。分层锚：validate_dna 对同 DNA 不报错
（trail/分批是合法 DNA 形态）。
"""

from __future__ import annotations

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

    @pytest.mark.parametrize("sl_mode,rejected", [
        ("pct", False), ("atr", True), ("trail", True),
    ])
    def test_mode_link_matrix(self, sl_mode, rejected):
        reasons = validate_trading_compatibility(mk_dna(sl_mode), "live")
        assert (len(reasons) > 0) == rejected

    def test_atr_live_detail_names_danger(self):
        """拒绝 detail 链路语义可判定：指明模式×链路与具体危险形态。"""
        reasons = validate_trading_compatibility(mk_dna("atr"), "live")
        assert len(reasons) == 1
        assert "实盘" in reasons[0] and "atr" in reasons[0]
        assert "负价" in reasons[0]  # 危险形态披露（无效价格止损单）

    def test_trail_detail_names_scope(self):
        reasons = validate_trading_compatibility(mk_dna("trail"), "live")
        assert "trail" in reasons[0] and "峰值回撤" in reasons[0]

    @pytest.mark.parametrize("role", [
        SignalRole.REDUCE_TRIGGER, SignalRole.REDUCE_GUARD,
        SignalRole.ADD_TRIGGER, SignalRole.ADD_GUARD,
        SignalRole.ADD_INHIBIT])
    def test_scaling_roles_live_rejected(self, role):
        """判据二：五分批角色×live 链拒（手工构造 DNA 预验）。"""
        reasons = validate_trading_compatibility(mk_dna(roles=(role,)), "live")
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
        reasons = validate_trading_compatibility(dna, "live")
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

    @pytest.mark.parametrize("link", ["backtest", "replay", "paper"])
    def test_bad_link_raises(self, link):
        """值域收敛锚：收敛后 replay/paper 连同 backtest 一律非法。"""
        with pytest.raises(ValueError):
            validate_trading_compatibility(mk_dna(), link)


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
