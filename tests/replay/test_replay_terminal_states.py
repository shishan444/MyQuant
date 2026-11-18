"""回放终态与结果汇总契约面（spec add-replay-engine R4.2/R7 补齐——
三态终态承载+汇总组字段完整性；构造均经探针验证现状行为）。"""
from __future__ import annotations

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.repository import paper
from core.replay import ReplayRunner
from tests.replay._env import ReplayEnv, make_reduce_loop_dna


class TestExhaustedCompletion:
    """R7.1 区间耗尽完成：三字段精确锚+汇总组全字段在位。"""

    def test_completed_status_stop_reason_none_last_bar(self, tmp_path):
        """区间自然耗尽：status=completed、stop_reason=None、
        last_bar_time=区间末根（非「in (completed, stopped)」弱断言）。"""
        env = ReplayEnv(tmp_path, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="ts01")
        assert res.status == "completed"
        assert res.stop_reason is None
        assert pd.Timestamp(res.last_bar_time) == pd.Timestamp(end)

    def test_summary_groups_all_fields_present(self, tmp_path):
        """R7 结果汇总组完整性：统计组/身份组/明细引用组全字段
        在位（非 None 非空——后续记录与呈现层的输入契约）。"""
        env = ReplayEnv(tmp_path, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="ts02")
        # 统计组
        assert res.bars_processed > 0
        assert res.total_trades >= 0
        assert res.win_count + res.loss_count <= res.total_trades
        assert isinstance(res.total_pnl, float)
        assert res.final_balance > 0
        assert res.final_equity > 0
        # 身份组（区间字段与请求逐位一致）
        assert res.strategy_id == env.strategy_id
        assert res.symbol == "BTCUSDT" and res.timeframe == "4h"
        assert pd.Timestamp(res.start) == pd.Timestamp(start)
        assert pd.Timestamp(res.end) == pd.Timestamp(end)
        # 明细引用组：db_path 在盘+经既有查询通道可取明细
        assert res.db_path == env.db
        assert paper.get_task(env.db, res.task_id)["task_id"] == res.task_id
        assert isinstance(
            paper.list_equity_snapshots(env.db, res.task_id, limit=1), list)


class TestStopConditionTerminal:
    """R7.2 提前终止终态原因承载（stop_loss_pct → stop_condition）。"""

    def test_stop_loss_pct_stopped_with_reason(self, tmp_path):
        """stop_loss_pct 微阈值（开仓费用即拉权益破线）→ status=
        stopped、stop_reason=stop_condition，且终态原因同步落 pt_task
        行（后续记录层可直接消费——不止结果对象内存态）。"""
        env = ReplayEnv(tmp_path, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="ts03",
                     stop_loss_pct=0.0001)
        assert res.status == "stopped"
        assert res.stop_reason == "stop_condition"
        task = paper.get_task(env.db, "ts03")
        assert task["status"] == "stopped"
        assert task["stop_reason"] == "stop_condition"


class TestFinalEquityHoldingTerminal:
    """CARD-02 回归：终态持仓时 final_equity 含 position_margin
    （权益单源口径=Ledger.equity：balance+margin+unrealized_pnl）。"""

    def test_holding_terminal_final_equity_matches_snapshot(self, tmp_path):
        """恒入场永不出场（exit 基因构造时传 RSI>200 永假——事后改
        condition_value 不进 to_json）→ 终态持仓：结果对象 final_
        equity 与末行权益快照（executor 逐 bar 落库的 Ledger.equity
        单源）两路独立一致；修复前汇总漏 margin 项系统性偏低。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalRole,
            StrategyDNA)
        from tests.helpers.data_factory import make_signal_gene

        gene_entry = make_signal_gene(
            indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
            condition_type="gt", condition_value=0)
        gene_exit = make_signal_gene(
            indicator="RSI", role=SignalRole.EXIT_TRIGGER,
            condition_type="gt", condition_value=200)
        dna = StrategyDNA(
            signal_genes=[gene_entry, gene_exit],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(
                timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(
                stop_loss=0.05, take_profit=0.2, position_size=0.1,
                leverage=2, direction="long"))
        env = ReplayEnv(tmp_path, dna=dna, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="ts05")
        task = paper.get_task(env.db, "ts05")
        # 构造前提自证：终态确在持仓（margin 划出在场）
        assert task["position_side"] is not None
        assert task["position_margin"] > 0
        # 两路独立对账：runner 汇总口径 == executor 落库单源口径
        eqs = paper.list_equity_snapshots(env.db, "ts05", limit=10000)
        assert eqs, "权益快照在案"
        assert res.final_equity == pytest.approx(eqs[-1]["equity"])
        # margin 项确已加回：final_equity > balance（修复前两者差
        # 恰为一个 uPnL 而不含 margin）
        assert res.final_equity > res.final_balance + (
            eqs[-1]["unrealized_pnl"] or 0.0)

    def test_flat_terminal_balance_equals_equity(self, tmp_path):
        """终态空仓（entry/exit 皆永假）：margin=None→0 无偏差，
        final_balance == final_equity（回归锚——空仓态行为不变）。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalRole,
            StrategyDNA)
        from tests.helpers.data_factory import make_signal_gene

        gene_entry = make_signal_gene(  # 永假（构造时传，非事后改）
            indicator="RSI", role=SignalRole.ENTRY_TRIGGER,
            condition_type="gt", condition_value=200)
        gene_exit = make_signal_gene(
            indicator="RSI", role=SignalRole.EXIT_TRIGGER,
            condition_type="gt", condition_value=200)
        dna = StrategyDNA(
            signal_genes=[gene_entry, gene_exit],
            logic_genes=LogicGenes(entry_logic="AND", exit_logic="AND"),
            execution_genes=ExecutionGenes(
                timeframe="4h", symbol="BTCUSDT"),
            risk_genes=RiskGenes(direction="long"))
        env = ReplayEnv(tmp_path, dna=dna, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="ts06")
        assert res.status == "completed"
        assert res.final_balance == pytest.approx(res.final_equity)
        assert res.final_balance == pytest.approx(100_000.0)


class TestDepletedTerminal:
    """R4.2 枯竭提前终止继承（三连开仓拒绝+空仓 → depleted）。"""

    def test_depleted_stopped_with_reason(self, tmp_path, monkeypatch):
        """MIN_NOTIONAL 阈值抬到名义之上（模块级表单点——kernel 拦截
        判定读它；env 变量不经 load_min_notional 不生效）→ 恒开仓策略
        三连拒→ status=stopped、stop_reason=depleted 进入结果汇总。"""
        from core.paper_trading import min_notional as mn

        monkeypatch.setitem(mn.MIN_NOTIONAL, "BTCUSDT", 200000.0)
        env = ReplayEnv(tmp_path, dna=make_reduce_loop_dna(
            exit_fraction=0.5, position_size=0.1, leverage=10), n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="ts04")
        assert res.status == "stopped"
        assert res.stop_reason == "depleted"
        assert res.bars_processed < 300, "提前终止（非区间耗尽）"
