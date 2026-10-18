"""仓位参数两键 AI 全链流转端到端（add-position-sizing-params 5.3——
族 8c：L3 单条流跨层验收）。

单条流：登记（strategy_params 含键）→翻译（risk_genes 物化）→回测
执行消费（initial_fraction 首仓名义+max_position_loss 防线出场）→
纸盘执行消费（exit_max_loss 成交）→报告披露（evidence.exit_reasons）
+不声明路径零变化对照（platform_default 默认定仓/无防线）。

seed=7 路径锚：RSI<30 持续亮（i=20 起）→ 次根开盘入场；价格持续
下跌。声明侧（if=0.6、mpl=100）：首仓名义=10 万×0.6（对照默认 0.3
的 3 万）、防线达线次根开盘出场（trades 多笔=防线反复截断+再入场）；
不声明侧：首笔走 position_size=0.3 默认路径、出场走止损（更晚）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.hypothesis_to_strategy import translate_hypothesis
from core.backtest.engine import BacktestEngine
from core.features.indicators import compute_all_indicators
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import get_strategy
from core.strategy.dna import StrategyDNA
from tests.helpers.data_factory import make_ohlcv

_INIT_CASH = 100_000.0
_DECLARED = {"initial_fraction": 0.6, "max_position_loss": 100.0}


def _conds(period: int) -> dict:
    return {
        "when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "lt", "target": 30}],
        "then_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": period},
                         "field": "rsi"}, "action": "gt", "target": 70}],
    }


_RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-11T00:00:00Z"}]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


def _translated_dna(db: Path, period: int, sp: dict | None) -> StrategyDNA:
    """登记（strategy_params）→翻译 → 产物 DNA（period 异=异条件集，
    避开「声明块不进幂等身份」的同假设合并）。"""
    h = register_hypothesis(
        db, conditions=_conds(period), statement=f"e2e-p{period}",
        data_ranges=_RANGES, strategy_params=sp)
    t = translate_hypothesis(db, hypothesis_id=h["hypothesis_id"])
    return StrategyDNA.from_json(
        get_strategy(db, t["strategy_id"])["dna_json"])


def _run_backtest(dna: StrategyDNA):
    df = make_ohlcv(n=60, freq="4h", seed=7, base_price=100.0)
    enhanced = compute_all_indicators(df)
    return BacktestEngine(init_cash=_INIT_CASH).run(
        dna, enhanced, dfs_by_timeframe={"4h": enhanced})


class TestFullChainBacktest:
    """回测环境消费：登记→翻译→物化→首仓名义+防线出场。"""

    def test_declared_params_flow_into_execution(self, db):
        """各环节在场：strategy_params 含键→risk_genes 物化同值→首笔
        名义=权益×initial_fraction（6 万 vs 对照 3 万）→防线出场。"""
        dna = _translated_dna(db, period=14, sp=_DECLARED)
        # 物化环节：产物 risk_genes 两键=声明值
        assert dna.risk_genes.initial_fraction == 0.6
        assert dna.risk_genes.max_position_loss == 100.0

        result = _run_backtest(dna)
        trades = result.trades_df
        assert result.total_trades >= 3, "防线反复截断+再入场多笔形态"
        first = trades.iloc[0]
        # initial_fraction 消费：首仓名义=init_cash×0.6（引擎装配
        # effective_first_size 替换 position_size 消费点）
        assert float(first["Size"]) * float(first["Avg Entry Price"]) \
            == pytest.approx(_INIT_CASH * 0.6, rel=5e-3)
        # max_position_loss 消费：首笔出场=入场次根（收盘判定达线
        # 次根开盘——非止损路径的更晚出场）
        entry_ts = first["Entry Timestamp"]
        exit_ts = first["Exit Timestamp"]
        assert (exit_ts - entry_ts) == pd_timedelta_4h()

    def test_undeclared_control_unchanged_default_path(self, db):
        """不声明对照：产物两键 None（platform_default）→首仓名义=
        权益×position_size 默认 0.3（3 万）→出场走止损（更晚、笔数少）。"""
        dna = _translated_dna(db, period=20, sp=None)
        assert dna.risk_genes.initial_fraction is None
        assert dna.risk_genes.max_position_loss is None
        rg_dict = json.loads(dna.to_json())["risk_genes"]
        assert "initial_fraction" not in rg_dict  # 序列化超集不写缺省键

        result = _run_backtest(dna)
        declared = _run_backtest(_translated_dna(db, period=14, sp=_DECLARED))
        first = result.trades_df.iloc[0]
        # 默认定仓路径：首仓名义=init_cash×0.3
        assert float(first["Size"]) * float(first["Avg Entry Price"]) \
            == pytest.approx(_INIT_CASH * 0.3, rel=5e-3)
        # 无防线：出场更晚+总笔数显著少于防线侧
        exit_ts = first["Exit Timestamp"]
        declared_exit = declared.trades_df.iloc[0]["Exit Timestamp"]
        assert exit_ts > declared_exit
        assert result.total_trades < declared.total_trades


def pd_timedelta_4h():
    import pandas as pd
    return pd.Timedelta(hours=4)


class TestFullChainPaperAndReport:
    """纸盘环境消费+报告披露：同产物 DNA 回放→exit_max_loss 成交→
    evidence.exit_reasons 披露（回测与纸盘同语义执行的纸盘侧证据）。"""

    def test_paper_execution_consumes_and_report_discloses(self, tmp_path):
        from core.paper_trading.reporting import build_report
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.persistence.repository import paper
        from tests.paper_trading.test_paper_executor import _Harness

        # 登记链产物 DNA（独立小库——纸盘 _Harness 自建库装任务）
        import tempfile
        from core.analysis.executor import ensure_research_tables

        rdb = Path(tempfile.mkdtemp()) / "r.db"
        init_db_ext(rdb)
        ensure_research_tables(rdb)
        dna = _translated_dna(rdb, period=14, sp=_DECLARED)
        # stop_loss 走 platform_default 0.05：本数据路径下金额防线
        # （浮亏 100≈i=22 达线）先于止损线（entry×0.95≈i=30 触）——
        # 归因序不争抢（纸盘侧 3.4 测试已锚同一次序）

        h = _Harness(tmp_path, dna=dna, window_bars=50)
        ex = h.make_executor()
        ex.process_frames(h.enhanced, 0)

        # 执行消费：exit_max_loss 成交在场（kind/reason）
        exits = [f for f in paper.list_fills(
            h.db, h.task_id, limit=1000) if f["kind"] == "exit_max_loss"]
        assert exits, "纸盘侧防线须达线出场"
        assert exits[0]["reason"] == "max_loss"
        # initial_fraction 纸盘消费：首笔 open 成交名义=10 万×0.6
        open_fill = next(
            f for f in paper.list_fills(h.db, h.task_id, limit=1000)
            if f["kind"] == "open")
        assert open_fill["quantity"] * open_fill["price"] \
            == pytest.approx(10_000.0 * 0.6, rel=5e-3)

        # 报告披露：evidence.exit_reasons 含金额防线类目
        report = build_report(h.db, h.data_dir, h.task_id)
        assert report["evidence"]["exit_reasons"].get("exit_max_loss", 0) \
            >= 1
