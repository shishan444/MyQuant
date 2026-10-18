"""L1-D 进化→冠军→纸盘 端到端闭环契约（frontend-switch-and-retire
R.2 重生：旧 paper_trading_task 载体退役，接驳面改为「冠军入策略库
→ strategy_id 一键上盘」——与 rebuild-paper-trading design §11 偏差 2
「REST 只收 strategy_id、DNA 由后端从策略库流转快照」同一接驳语义）。

本契约用分层端到端(禁 mock 截断核心链路)验证闭环无损:
  1. 真实进化(max_gens=2/pop=4, gradient evaluate)产出 champion
  2. champion 入策略库(strategy 表)后经 strategy_id 上盘, pt_task.
     dna_snapshot_json 快照还原一致(上盘流转=库内 dna_json 原文)
  3. 上盘快照回读 DNA 产出的信号 == champion 直接产出的信号
  4. 回读 DNA 的回测结果 == champion 的(执行行为符合 champion 策略)

注: 真实 runner 在线联动不可行(主循环靠实时数据推进, 单测无新 bar),
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
from core.persistence.repository import paper  # noqa: E402
from core.persistence.repository.strategy import (  # noqa: E402
    get_strategy, save_strategy,
)
from core.persistence.db_ext import init_db_ext  # noqa: E402
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
def mounted_task(tmp_path: Path, evolved_champion: StrategyDNA
                 ) -> tuple[Path, str, StrategyDNA]:
    """champion 入策略库 → strategy_id 一键上盘, 返回 (db, task_id, champion)。

    上盘流转与 REST 端点同一语义(api/routes/paper_trading.py create_task):
    快照取库内 dna_json 原文(保真不重序列化)——此处按测试直建通道
    等价复现(不经 HTTP, 断言面聚焦持久化与执行链)。
    """
    db = tmp_path / "test.db"
    init_db_ext(db)
    save_strategy(
        db, strategy_id="champ-1", dna_json=evolved_champion.to_json(),
        symbol="BTCUSDT", timeframe="4h", name="EvolvedChampion",
        source="evolution",
    )
    strategy = get_strategy(db, "champ-1")
    assert strategy is not None, "champion 应已入策略库"
    task_id = paper.create_task(
        db, strategy_id="champ-1",
        dna_snapshot_json=strategy["dna_json"],
        symbol="BTCUSDT", timeframe="4h",
        research_status_snapshot=paper.resolve_research_status(strategy),
    )
    return db, task_id, evolved_champion


class TestChampionToPaperMountClosedLoop:
    """L1-D: 进化冠军 → 策略库 → strategy_id 上盘 → 执行 闭环无损。"""

    def test_champion_is_valid_serializable_dna(self, evolved_champion):
        """环节1: 进化产出合法可序列化的 champion(进化发生)。"""
        assert isinstance(evolved_champion, StrategyDNA)
        # 序列化往返不抛异常
        roundtrip = StrategyDNA.from_json(evolved_champion.to_json())
        assert roundtrip.risk_genes.to_dict() == evolved_champion.risk_genes.to_dict()

    def test_champion_survives_strategy_to_paper_mount(self, mounted_task):
        """环节2: champion 入库→上盘快照 dna_snapshot_json 业务字段无损。"""
        db, task_id, champion = mounted_task
        row = paper.get_task(db, task_id)
        assert row is not None
        assert row["strategy_id"] == "champ-1"
        roundtrip = StrategyDNA.from_json(row["dna_snapshot_json"])
        # 纸盘实际执行读的是 pt_task.dna_snapshot_json, 必须与 champion 一致
        assert roundtrip.risk_genes.to_dict() == champion.risk_genes.to_dict()
        assert len(roundtrip.signal_genes) == len(champion.signal_genes)
        for g1, g2 in zip(roundtrip.signal_genes, champion.signal_genes):
            assert g1.to_dict() == g2.to_dict()

    def test_paper_signal_set_matches_champion(self, mounted_task):
        """环节3: 上盘快照回读 DNA 产出的信号 == champion 直接产出的信号。

        证明'纸盘用的信号 = champion 的信号', 序列化不丢 executor 翻译所需信息。
        """
        db, task_id, champion = mounted_task
        row = paper.get_task(db, task_id)
        roundtrip = StrategyDNA.from_json(row["dna_snapshot_json"])
        df = make_ohlcv(120, "4h")
        sig_champion = dna_to_signal_set(champion, df)
        sig_roundtrip = dna_to_signal_set(roundtrip, df)
        pd.testing.assert_series_equal(
            sig_champion.entries, sig_roundtrip.entries, check_names=False)
        pd.testing.assert_series_equal(
            sig_champion.exits, sig_roundtrip.exits, check_names=False)

    def test_paper_backtest_behavior_matches_champion(self, mounted_task):
        """环节4: 回读 DNA 的回测结果 == champion 的(执行行为符合 champion 策略)。

        这是闭环的最终证明: 执行(回测代理)对回读 DNA 和 champion 产出完全
        一致的结果, 即进化冠军的策略行为经上盘流转无损地驱动了纸盘。
        """
        db, task_id, champion = mounted_task
        row = paper.get_task(db, task_id)
        roundtrip = StrategyDNA.from_json(row["dna_snapshot_json"])
        df = make_ohlcv(120, "4h")
        engine = BacktestEngine(init_cash=100_000)
        result_champion = engine.run(champion, df)
        result_roundtrip = engine.run(roundtrip, df)
        # 关键交易行为指标完全一致
        assert result_champion.total_trades == result_roundtrip.total_trades
        assert result_champion.liquidated == result_roundtrip.liquidated
        assert abs(result_champion.total_return - result_roundtrip.total_return) < 1e-9
        assert abs(result_champion.max_drawdown - result_roundtrip.max_drawdown) < 1e-9
