"""C3 回测回执 signal_counts 披露锚（fix-mtf-execution-consistency 组 2）。

三态判读契约（design 卡 C3——「零交易无警告」静默面消除）：
①signal_counts.entries==0 → 条件未触发（装配异常已被 R1 结构化拒绝
  前置拦截——到达回执的零 entries=语义上条件全假）；
②entries>0 且 total_trades==0 → 信号有、执行段无成交（资金/风控/时序）；
③total_trades>0 → 正常。

另锚：四键在场+int+请求区间口径（与信号面直数对照）+四通道自动
携带（隔离端到端）。
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.backtest_bridge import run_backtest_on_range
from core.persistence.db_ext import init_db_ext
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA,
)
from core.strategy.executor import dna_to_signal_set


def _df(n: int = 1200, seed: int = 31) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    rets = rng.normal(0.0002, 0.008, n)
    close = 60000.0 * np.cumprod(1.0 + rets)
    open_ = np.empty(n)
    open_[0] = 60000.0
    open_[1:] = close[:-1]
    spread = np.abs(rng.normal(0, 0.004, n)) * close
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")
    return pd.DataFrame({"open": open_,
                         "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread,
                         "close": close,
                         "volume": np.abs(rng.normal(500, 150, n)) + 1.0},
                        index=idx)


def _dna(threshold: float = 35.0) -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi", {"type": "lt", "threshold": threshold}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi", {"type": "gt", "threshold": 65})],
        execution_genes=ExecutionGenes(timeframe="30m", symbol="BTCUSDT"),
        logic_genes=LogicGenes(), risk_genes=RiskGenes(direction="long"),
        signal_sources=[{"type": "rule"}])


@pytest.fixture()
def env(tmp_path):
    d = tmp_path / "market"
    d.mkdir()
    _df().to_parquet(d / "BTCUSDT_30m.parquet")
    db = tmp_path / "q.db"
    init_db_ext(db)
    return db, d


class TestC3SignalCountsDisclosure:
    def test_keys_int_and_request_range_semantics(self, env):
        """四键在场+int+请求区间口径（与信号面直数对照）。"""
        db, d = env
        dna = _dna()
        metrics, _, _, bars = run_backtest_on_range(
            db, d, dna, "BTCUSDT", "30m",
            "2024-01-20", "2024-01-30", 200)
        sc = metrics["signal_counts"]
        assert set(sc.keys()) == {"entries", "exits", "adds", "reduces"}
        assert all(isinstance(v, int) for v in sc.values())
        # 口径对照：与桥同款装载链（warmup 前缀收敛指标列）切回请求
        # 区间后求值——signal_counts 必须等于该信号面（裸 df 区间内
        # 现算会因头部收敛差异误对照）
        from core.data.mtf_loader import load_and_prepare_df
        enhanced = load_and_prepare_df(
            d, "BTCUSDT", "30m",
            data_start="2024-01-20", data_end="2024-01-30")
        # 桥内部会带 warmup 前扩装载——用同前扩口径
        from core.analysis.warmup_loader import TF_MINUTES, to_utc
        load_start = (to_utc(pd.Timestamp("2024-01-20"))
                      - pd.Timedelta(minutes=TF_MINUTES["30m"] * 200)
                      ).isoformat()
        enhanced = load_and_prepare_df(
            d, "BTCUSDT", "30m",
            data_start=load_start, data_end="2024-01-30")
        bt_df = enhanced[enhanced.index
                         >= to_utc(pd.Timestamp("2024-01-20"))]
        sig = dna_to_signal_set(dna, bt_df, None)
        assert sc["entries"] == int(sig.entries.fillna(False).sum())
        assert sc["exits"] == int(sig.exits.fillna(False).sum())
        assert sc["entries"] > 0  # 对照侧非空（RSI<35 有触发）

    def test_three_states_distinguishable(self, env):
        """三态判读：①条件全假 entries==0；②有信号无成交；③正常。"""
        db, d = env
        # ① 恒 False 条件（RSI>1e9 恒假）→ entries==0 可见于回执
        m1, _, _, _ = run_backtest_on_range(
            db, d, _dna(threshold=-1.0), "BTCUSDT", "30m",
            "2024-01-20", "2024-01-30", 200)  # RSI<-1 恒假
        assert m1["signal_counts"]["entries"] == 0
        assert m1["total_trades"] == 0
        # ② 有信号无成交：极小资金（买单价值过小无成交）
        m2, _, _, _ = run_backtest_on_range(
            db, d, _dna(), "BTCUSDT", "30m",
            "2024-01-20", "2024-01-30", 200, init_cash=1e-9)
        assert m2["signal_counts"]["entries"] > 0
        assert m2["total_trades"] == 0
        # ③ 正常：有信号有成交
        m3, _, _, _ = run_backtest_on_range(
            db, d, _dna(), "BTCUSDT", "30m",
            "2024-01-20", "2024-01-30", 200)
        assert m3["signal_counts"]["entries"] > 0
        assert m3["total_trades"] > 0

    def test_isolated_channel_carries_key(self, env):
        """存活通道自动携带：sandbox 端到端 result 含 signal_counts
        （retire-legacy 后 isolated 编排退役，record 统一流经 sandbox
        通道等价承载——result 列形制同源）。"""
        from core.analysis.executor import ensure_research_tables
        from core.analysis.validation_service import run_sandbox_backtest
        from core.persistence.repository.strategy import save_strategy

        db, d = env
        ensure_research_tables(db)
        dna = _dna()
        save_strategy(db, strategy_id="st_c3", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="30m",
                      gene_signature="sig_c3")
        res = run_sandbox_backtest(
            db, d, strategy_id="st_c3", symbol="BTCUSDT", timeframe="30m",
            start="2024-01-20", end="2024-01-30")
        result_json = json.loads(
            __import__("sqlite3").connect(db).execute(
                "SELECT result FROM validation_records WHERE record_id=?",
                (res["record_id"],)).fetchone()[0])
        assert "signal_counts" in result_json["metrics"]
        assert set(result_json["metrics"]["signal_counts"].keys()) == {
            "entries", "exits", "adds", "reduces"}


class TestCrossEngineCountConsistency:
    """A3 合成对照锚（fix-executor-column-and-exit-guard 5.1）：同 df
    下 executor 链进场触发数 == 统计层 mask_for AND 交集计数——跨引擎
    互证同构（BUG-1 修复前 executor 子串伪命中使两引擎计数分歧）。
    本锚同时守护 R3「统计层零改动」：condition_kernel 求值侧任何漂移
    都使两侧计数不等即红。

    真实数据 68 逐数锚为一次性留痕（works/研究档案/），不入常驻——
    真实数据断言随数据更新脆断。
    """

    def test_executor_entries_equal_kernel_mask_count(self):
        from core.analysis.condition_kernel import mask_for
        df = _df(n=800, seed=31)
        gene = SignalGene("RSI", {"period": 2}, SignalRole.ENTRY_TRIGGER,
                          None, {"type": "lt", "threshold": 10})
        dna = StrategyDNA(
            signal_genes=[gene],   # 无出场基因——零清洗干扰纯计数
            execution_genes=ExecutionGenes(timeframe="30m"),
            logic_genes=LogicGenes(), risk_genes=RiskGenes(direction="long"),
            signal_sources=[{"type": "rule"}])
        ss = dna_to_signal_set(dna, df)
        cond = {"subject": {"indicator": "RSI", "params": {"period": 2},
                            "field": "rsi"},
                "action": "lt", "target": 10}
        kernel_mask = mask_for(df, cond, None)
        # 无出场基因 → exits 恒 False → 清洗零作用，entries 即原始触发
        assert int(ss.entries.sum()) == int(kernel_mask.sum())
        assert int(ss.entries.sum()) > 0        # 非退化形态
