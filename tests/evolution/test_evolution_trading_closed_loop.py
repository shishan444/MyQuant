"""L1-D 进化→冠军→交易 端到端闭环契约。

审计发现: champion 存 evolution_task.champion_dna(api/runner.py:700), 交易只读
paper_trading_task.dna_json(trading/runner.py:222), 串联靠前端——"进化冠军能否正确驱动
交易"这一产品关键流程无任何测试(grep 无测试同时覆盖 champion+trading)。

本契约用分层端到端(禁 mock 截断核心链路)验证闭环无损:
  1. 真实进化(max_gens=2/pop=4, gradient evaluate)产出 champion
  2. champion.to_json 经 paper_trading_task 表存取后还原一致
  3. 交易任务回读 DNA 产出的信号 == champion 直接产出的信号(交易用的信号=champion的)
  4. 回读 DNA 的回测结果 == champion 的(交易行为符合 champion 策略)

注: 真实 runner 在线联动不可行(主循环靠实时 _fetch_and_update, 单测无新 bar),
用历史 df 直接驱动 BacktestEngine 替代, 不损闭环验证价值。
"""
import random
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import StrategyDNA  # noqa: E402
from core.evolution.engine import EvolutionEngine  # noqa: E402
from core.evolution.population import create_random_dna  # noqa: E402
from core.backtest.engine import BacktestEngine  # noqa: E402
from core.strategy.executor import dna_to_signal_set  # noqa: E402
from core.persistence.db_ext import (  # noqa: E402
    init_db_ext, save_paper_trading_task, get_paper_trading_task,
)
from tests.helpers.data_factory import make_ohlcv  # noqa: E402


def _simple_evaluate(dna: StrategyDNA) -> float:
    """确定性 evaluate(进化有效性已由 L0-A 守护, 此处仅需产出合法 champion)。"""
    sl = float(dna.risk_genes.stop_loss)
    return round(100.0 * max(0.0, 1.0 - abs(sl - 0.075) / 0.075), 4)


@pytest.fixture
def evolved_champion() -> StrategyDNA:
    """真实进化(max_gens=2/pop=4)产出的 champion。"""
    random.seed(42)
    ancestor = create_random_dna(leverage=1, direction="long")
    engine = EvolutionEngine(
        target_score=200, max_generations=2, population_size=4, patience=10,
    )
    result = engine.evolve(ancestor, evaluate_fn=_simple_evaluate)
    champion = result["champion"]
    assert champion is not None, "进化应产出 champion"
    return champion


@pytest.fixture
def trading_db(tmp_path: Path, evolved_champion: StrategyDNA) -> tuple[Path, StrategyDNA]:
    """champion 经 paper_trading_task 表持久化, 返回 (db, 回读DNA)。"""
    db = tmp_path / "test.db"
    init_db_ext(db)
    save_paper_trading_task(
        db, task_id="t1", dna_json=evolved_champion.to_json(),
        symbol="BTCUSDT", timeframe="4h", initial_cash=100_000, fee=0.001,
    )
    return db, evolved_champion


class TestChampionToTradingClosedLoop:
    """L1-D: 进化冠军 → 交易任务 → 交易执行 闭环无损。"""

    def test_champion_is_valid_serializable_dna(self, evolved_champion):
        """环节1: 进化产出合法可序列化的 champion(进化发生)。"""
        assert isinstance(evolved_champion, StrategyDNA)
        # 序列化往返不抛异常
        roundtrip = StrategyDNA.from_json(evolved_champion.to_json())
        assert roundtrip.risk_genes.to_dict() == evolved_champion.risk_genes.to_dict()

    def test_champion_survives_paper_trading_task_persistence(self, trading_db):
        """环节2: champion 经 paper_trading_task 表存取后业务字段无损。"""
        db, champion = trading_db
        row = get_paper_trading_task(db, "t1")
        assert row is not None
        roundtrip = StrategyDNA.from_json(row["dna_json"])
        # 交易实际读的是 paper_trading_task.dna_json, 必须与 champion 一致
        assert roundtrip.risk_genes.to_dict() == champion.risk_genes.to_dict()
        assert len(roundtrip.signal_genes) == len(champion.signal_genes)
        for g1, g2 in zip(roundtrip.signal_genes, champion.signal_genes):
            assert g1.to_dict() == g2.to_dict()

    def test_trading_signal_set_matches_champion(self, trading_db):
        """环节3: 交易任务回读 DNA 产出的信号 == champion 直接产出的信号。

        证明'交易用的信号 = champion 的信号', 序列化不丢 executor 翻译所需信息。
        """
        db, champion = trading_db
        row = get_paper_trading_task(db, "t1")
        roundtrip = StrategyDNA.from_json(row["dna_json"])
        df = make_ohlcv(120, "4h")
        sig_champion = dna_to_signal_set(champion, df)
        sig_roundtrip = dna_to_signal_set(roundtrip, df)
        pd.testing.assert_series_equal(
            sig_champion.entries, sig_roundtrip.entries, check_names=False)
        pd.testing.assert_series_equal(
            sig_champion.exits, sig_roundtrip.exits, check_names=False)

    def test_trading_backtest_behavior_matches_champion(self, trading_db):
        """环节4: 回读 DNA 的回测结果 == champion 的(交易行为符合 champion 策略)。

        这是闭环的最终证明: 交易执行(回测代理)对回读 DNA 和 champion 产出完全一致的结果,
        即进化冠军的策略行为经持久化无损地驱动了交易。
        """
        db, champion = trading_db
        row = get_paper_trading_task(db, "t1")
        roundtrip = StrategyDNA.from_json(row["dna_json"])
        df = make_ohlcv(120, "4h")
        engine = BacktestEngine(init_cash=100_000)
        result_champion = engine.run(champion, df)
        result_roundtrip = engine.run(roundtrip, df)
        # 关键交易行为指标完全一致
        assert result_champion.total_trades == result_roundtrip.total_trades
        assert result_champion.liquidated == result_roundtrip.liquidated
        assert abs(result_champion.total_return - result_roundtrip.total_return) < 1e-9
        assert abs(result_champion.max_drawdown - result_roundtrip.max_drawdown) < 1e-9
