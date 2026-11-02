"""add-validation-detail 写侧全链（任务 2/3/4——tester B4 组）。

- 桥 with_detail：缺省四元组逐位不变/True 五元组结构+引用同一性
  （B4-11 面：六消费方零波及由既有域回归覆盖，此处钉参数契约）；
- _build_detail_payload：紧凑/降级两形态+开仓边界+pnl NULL 透传
  （B4-6/B4-7 单元面）；
- 三入口落盘：份数对齐（B4-1）/幂等零重写（B4-2）/原子性故障注入
  （B4-3）/三键跟随实参（B4-4）/历史容缺（B4-5）/沙盒不落（B4-8）/
  明细与汇总对账（B2-9 写侧锚）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from sqlalchemy.exc import IntegrityError

from core.analysis.backtest_bridge import run_backtest_on_range
from core.analysis.validation_service import (
    _build_detail_payload,
    random_window_test,
    run_isolated_backtest,
    run_sandbox_backtest,
    run_staged_validation,
)
from core.analysis.hypothesis_service import register_hypothesis
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)

_RSI_GENE = lambda: SignalGene(  # noqa: E731 —— 与既有测试同构的构造捷径
    "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
    {"type": "lt", "threshold": 35})

_EXIT_GENE = lambda: SignalGene(  # noqa: E731
    "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
    {"type": "gt", "threshold": 65})


def _mtf_dna(layer_tfs):
    return StrategyDNA(
        signal_genes=[_RSI_GENE(), _EXIT_GENE()],
        layers=[TimeframeLayer(timeframe=tf,
                               signal_genes=[_RSI_GENE(), _EXIT_GENE()],
                               logic_genes=LogicGenes())
                for tf in layer_tfs],
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    d = tmp_path / "q.db"
    init_db_ext(d)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(d)
    return d


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(7)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(d / "BTCUSDT_4h.parquet")
    return d


@pytest.fixture()
def hypothesis_id(db) -> str:
    return register_hypothesis(
        db,
        conditions={
            "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        },
        statement="s",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]


@pytest.fixture()
def strategy_saved(db) -> str:
    save_strategy(db, strategy_id="st_detail", dna_json=_mtf_dna([]).to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    return "st_detail"


def _vr_counts(db: Path) -> tuple:
    from core.persistence.orm.engine import engine_for
    from sqlalchemy import text
    with engine_for(db).connect() as conn:
        t = conn.execute(text("SELECT COUNT(*) FROM vr_trade_detail")).scalar()
        e = conn.execute(text("SELECT COUNT(*) FROM vr_equity_curve")).scalar()
    return t, e


class TestBridgeWithDetail:
    """任务 2：with_detail 参数契约（六消费方零波及由域回归覆盖）。"""

    def test_default_returns_four_tuple(self, db, data_dir, strategy_saved):
        out = run_backtest_on_range(
            db, data_dir, _mtf_dna([]), "BTCUSDT", "4h",
            "2023-03-01T00:00:00Z", "2023-03-20T00:00:00Z", 30,
            strategy_id=strategy_saved)
        assert len(out) == 4, "缺省 with_detail=False 必须保持四元组"

    def test_true_returns_five_tuple_keys(
            self, db, data_dir, strategy_saved):
        out = run_backtest_on_range(
            db, data_dir, _mtf_dna([]), "BTCUSDT", "4h",
            "2023-03-01T00:00:00Z", "2023-03-20T00:00:00Z", 30,
            strategy_id=strategy_saved, with_detail=True)
        assert len(out) == 5
        metrics, trade_returns, actual_warmup, bars_used, detail = out
        assert set(detail) == {"equity", "trades_df",
                               "init_cash", "fee", "slippage"}
        # 缺省三键=管道默认值（实际生效实参——未显式传参形态）
        assert detail["init_cash"] == 100000.0
        assert detail["fee"] == 0.001
        assert detail["slippage"] == 0.0005
        assert isinstance(detail["equity"], pd.Series)

    def test_detail_references_engine_result_objects(
            self, db, data_dir, strategy_saved, monkeypatch):
        """引用同一性：第五元素 equity/trades_df 是 engine result 对象
        本体（传引用不复制——非拷贝 Series）。"""
        from core.backtest import engine as bt_engine_mod
        captured = {}
        orig_run = bt_engine_mod.BacktestEngine.run

        def spy_run(self, dna, bt_df, **kw):
            result = orig_run(self, dna, bt_df, **kw)
            captured["result"] = result
            return result

        monkeypatch.setattr(bt_engine_mod.BacktestEngine, "run", spy_run)
        out = run_backtest_on_range(
            db, data_dir, _mtf_dna([]), "BTCUSDT", "4h",
            "2023-03-01T00:00:00Z", "2023-03-20T00:00:00Z", 30,
            strategy_id=strategy_saved, with_detail=True)
        detail = out[4]
        assert detail["equity"] is captured["result"].equity_curve
        assert detail["trades_df"] is captured["result"].trades_df

    def test_custom_args_echoed_in_detail(
            self, db, data_dir, strategy_saved):
        """B4-4 桥面：自定义 init_cash/fee/slippage 回显（反默认值——
        三键来源是实参非硬编码）。"""
        out = run_backtest_on_range(
            db, data_dir, _mtf_dna([]), "BTCUSDT", "4h",
            "2023-03-01T00:00:00Z", "2023-03-20T00:00:00Z", 30,
            strategy_id=strategy_saved, init_cash=50000.0, fee=0.002,
            slippage=0.001, with_detail=True)
        detail = out[4]
        assert detail["init_cash"] == 50000.0
        assert detail["fee"] == 0.002
        assert detail["slippage"] == 0.001


class TestBuildDetailPayload:
    """任务 3：组装 helper 两形态+边界（B4-6/B4-7 单元面）。"""

    @staticmethod
    def _detail(equity: pd.Series, trades_df=None) -> dict:
        return {"equity": equity, "trades_df": trades_df,
                "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005}

    def test_continuous_grid_compact(self):
        idx = pd.date_range("2023-03-01", periods=50, freq="4h", tz="UTC")
        equity = pd.Series(np.linspace(1e5, 1.1e5, 50), index=idx)
        out = _build_detail_payload(
            self._detail(equity), exec_ref="main", exec_index=0,
            range_start="2023-03-01T00:00:00Z",
            range_end="2023-03-10T00:00:00Z", timeframe="4h")
        eq = out["equity"]
        assert eq["grid_aligned"] == 1
        assert eq["start"] == str(idx[0])
        assert eq["length"] == 50
        assert eq["timestamps"] is None
        assert len(json.loads(eq["values"])) == 50
        assert out["trades"] == []

    def test_shifted_regular_grid_still_compact(self):
        """R-1 自锚定语义：序列等距但与请求区间起点错位 → 仍紧凑
        （断言=序列自身等距性，非与装载区间对齐——契约语义备忘）。"""
        idx = pd.date_range("2023-03-01", periods=50, freq="4h",
                            tz="UTC") + pd.Timedelta(hours=8)
        equity = pd.Series(np.linspace(1e5, 1.1e5, 50), index=idx)
        out = _build_detail_payload(
            self._detail(equity), exec_ref="main", exec_index=0,
            range_start="2023-03-01T00:00:00Z",
            range_end="2023-03-20T00:00:00Z", timeframe="4h")
        assert out["equity"]["grid_aligned"] == 1

    def test_hole_index_degrades(self):
        """构造洞 index（抽 bar）→ 降级形制（B4-7：禁静默网格重建）。"""
        idx = pd.date_range("2023-03-01", periods=50, freq="4h", tz="UTC")
        hole_idx = idx.delete([10, 11, 25])
        equity = pd.Series(np.linspace(1e5, 1.1e5, len(hole_idx)),
                           index=hole_idx)
        out = _build_detail_payload(
            self._detail(equity), exec_ref="main", exec_index=0,
            range_start="2023-03-01T00:00:00Z",
            range_end="2023-03-12T00:00:00Z", timeframe="4h")
        eq = out["equity"]
        assert eq["grid_aligned"] == 0
        assert eq["start"] is None and eq["length"] is None
        ts = json.loads(eq["timestamps"])
        assert ts == [str(t) for t in hole_idx]
        assert len(ts) == len(json.loads(eq["values"]))

    def test_open_trade_exit_time_null_and_pnl_nan_null(self):
        """开仓边界（vbt Status=Open→Exit Timestamp=NaT）→ exit_time
        None；PnL NaN→None 透传。"""
        idx = pd.date_range("2023-03-01", periods=10, freq="4h", tz="UTC")
        equity = pd.Series(np.linspace(1e5, 1.05e5, 10), index=idx)
        tdf = pd.DataFrame({
            "Entry Timestamp": [idx[1], idx[3]],
            "Exit Timestamp": [idx[5], pd.NaT],
            "Avg Entry Price": [100.0, 102.0],
            "Avg Exit Price": [110.0, 105.0],
            "Size": [1.0, -2.0],
            "Direction": ["Long", "Short"],
            "PnL": [10.0, np.nan],
        })
        out = _build_detail_payload(
            self._detail(equity, tdf), exec_ref="main", exec_index=0,
            range_start="s", range_end="e", timeframe="4h")
        trades = out["trades"]
        assert len(trades) == 2
        assert trades[0]["exit_time"] == str(idx[5])
        assert trades[1]["exit_time"] is None, "开仓行 exit_time=NULL"
        assert trades[1]["pnl"] is None, "PnL NaN→None"
        assert trades[1]["size"] == 2.0, "size 取绝对值（Short 负 Size）"
        assert trades[0]["seq"] == 0 and trades[1]["seq"] == 1

    def test_zero_trade_exec_equity_still_written(self):
        """③零交易份：trades 零行、equity 照落。"""
        idx = pd.date_range("2023-03-01", periods=20, freq="4h", tz="UTC")
        equity = pd.Series(np.full(20, 1e5), index=idx)
        out = _build_detail_payload(
            self._detail(equity, pd.DataFrame()), exec_ref="main",
            exec_index=0, range_start="s", range_end="e", timeframe="4h")
        assert out["trades"] == []
        assert out["equity"]["length"] == 20


class TestIsolatedDetailWrite:
    """任务 4：isolated 入口真实落盘链路（B4-1/B4-2/B4-4 入口面）。"""

    def test_single_execution_main_with_detail_rows(
            self, db, data_dir, hypothesis_id, strategy_saved):
        out = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved,
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01T00:00:00Z",
                           "end": "2023-04-01T00:00:00Z"})
        assert out["duplicated"] is False
        execs = rr.get_validation_executions(db, out["record_id"])
        assert len(execs) == 1, "isolated 单份（B4-1）"
        ex = execs[0]
        assert ex["exec_ref"] == "main" and ex["exec_index"] == 0
        assert ex["range_start"] == "2023-03-01T00:00:00Z"
        assert ex["range_end"] == "2023-04-01T00:00:00Z"
        # B2-9 对账（isolated 面）：逐笔笔数==metrics.total_trades
        assert len(ex["trades"]) == out["metrics"]["total_trades"]
        # equity 紧凑形制（fixture 数据连续无洞）
        assert ex["equity"]["grid_aligned"] is True
        assert ex["equity"]["length"] == len(ex["equity"]["values"])
        # params 三键=默认生效值（入口未显式传参形态）
        row = rr.get_validation_record_by_id(db, out["record_id"])
        params = json.loads(row["params"])
        assert params["init_cash"] == 100000.0
        assert params["fee"] == 0.001
        assert params["slippage"] == 0.0005

    def test_idempotent_replay_zero_rewrite(
            self, db, data_dir, hypothesis_id, strategy_saved):
        """B4-2：同五元重发→duplicated=true、明细行数不变、返回体
        同构（顶层键集合一致）。"""
        rng = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2023-03-01T00:00:00Z",
               "end": "2023-04-01T00:00:00Z"}
        first = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved, request_range=rng)
        before = _vr_counts(db)
        second = run_isolated_backtest(
            db, data_dir, hypothesis_id=hypothesis_id,
            strategy_id=strategy_saved, request_range=rng)
        assert second["duplicated"] is True
        assert second["record_id"] == first["record_id"]
        assert _vr_counts(db) == before, "幂等重放明细零重写"
        assert set(second.keys()) == set(first.keys()), "返回体同构"


class TestRandomDetailWrite:
    """任务 4：random 逐窗真实落盘（份数/窗口对齐）。"""

    def test_window_executions_aligned(self, db, data_dir, hypothesis_id,
                                       strategy_saved):
        out = random_window_test(
            db, data_dir, strategy_id=strategy_saved,
            hypothesis_id=hypothesis_id,
            symbol="BTCUSDT", timeframe="4h",
            data_start="2023-01-01T00:00:00Z",
            data_end="2023-06-01T00:00:00Z",
            window_bars=100, n_windows=2, seed=11)
        execs = rr.get_validation_executions(db, out["record_id"])
        assert len(execs) == 2 == out["summary"]["n_windows"], "窗数=份数"
        for k, ex in enumerate(execs):
            assert ex["exec_ref"] == f"win_{k}"
            assert ex["exec_index"] == k
            w = out["windows"][k]
            assert ex["range_start"] == w["start"], "份区间与窗口对齐"
            assert ex["range_end"] == w["end"]
            # B2-9：逐窗对账——逐笔笔数==该窗 metrics.total_trades、
            # equity 长度==length
            assert len(ex["trades"]) == w["metrics"]["total_trades"]
            assert ex["equity"]["length"] == len(ex["equity"]["values"])
        row = rr.get_validation_record_by_id(db, out["record_id"])
        params = json.loads(row["params"])
        assert {"init_cash", "fee", "slippage"} <= set(params)

    def test_idempotent_replay_zero_rewrite(self, db, data_dir, hypothesis_id,
                                            strategy_saved):
        """specs「幂等重放零重写」random 面：同五元重发→duplicated=true、
        明细行数不变、重放体不因明细落盘引入新键（键集合全等条款
        主 spec :587 限定隔离回测/分段验证两入口——random 重放 payload
        的 duplicated/result 增键为存量形制，不在全等面；isolated 面
        见 TestIsolatedDetailWrite）。"""
        kw = dict(strategy_id=strategy_saved, hypothesis_id=hypothesis_id,
                  symbol="BTCUSDT", timeframe="4h",
                  data_start="2023-01-01T00:00:00Z",
                  data_end="2023-06-01T00:00:00Z",
                  window_bars=100, n_windows=2, seed=11)
        first = random_window_test(db, data_dir, **kw)
        before = _vr_counts(db)
        second = random_window_test(db, data_dir, **kw)
        assert second["duplicated"] is True
        assert second["record_id"] == first["record_id"]
        assert _vr_counts(db) == before, "幂等重放明细零重写"
        # 明细不进返回体（design 3.3）：重放体不得因明细落盘引入
        # executions/detail 类新键——键差异面限于 random 存量形制
        assert not ({"executions", "detail", "detail_available"}
                    & set(second)), "重放体不得引入明细新键"


class TestStagedDetailWrite:
    """任务 4：staged 逐段真实落盘（exec_ref=段 label）。"""

    def test_segment_executions_labelled(self, db, data_dir, hypothesis_id,
                                         strategy_saved):
        out = run_staged_validation(
            db, data_dir, strategy_id=strategy_saved,
            hypothesis_id=hypothesis_id, symbol="BTCUSDT", timeframe="4h",
            start="2023-02-01T00:00:00Z", end="2023-05-01T00:00:00Z",
            segmentation="explicit", segments=[
                {"label": "segA", "start": "2023-02-01T00:00:00Z",
                 "end": "2023-03-15T00:00:00Z"},
                {"label": "segB", "start": "2023-03-15T00:00:00Z",
                 "end": "2023-05-01T00:00:00Z"}])
        assert out["record_id"] is not None
        execs = rr.get_validation_executions(db, out["record_id"])
        assert len(execs) == 2, "段数=份数"
        assert [e["exec_ref"] for e in execs] == ["segA", "segB"]
        assert [e["exec_index"] for e in execs] == [0, 1]
        for k, ex in enumerate(execs):
            seg = out["segments"][k]
            assert ex["range_start"] == seg["start"]
            assert ex["range_end"] == seg["end"]
            assert len(ex["trades"]) == seg["metrics"]["total_trades"]

    def test_idempotent_replay_zero_rewrite_and_isomorphic(
            self, db, data_dir, hypothesis_id, strategy_saved):
        """B4-2 staged 面（主 spec :587 明文枚举分段验证）：同五元重发
        →duplicated=true、明细行数不变、顶层键集合与首次相等。"""
        kw = dict(strategy_id=strategy_saved, hypothesis_id=hypothesis_id,
                  symbol="BTCUSDT", timeframe="4h",
                  start="2023-02-01T00:00:00Z", end="2023-05-01T00:00:00Z",
                  segmentation="explicit", segments=[
                      {"label": "segA",
                       "start": "2023-02-01T00:00:00Z",
                       "end": "2023-03-15T00:00:00Z"},
                      {"label": "segB",
                       "start": "2023-03-15T00:00:00Z",
                       "end": "2023-05-01T00:00:00Z"}])
        first = run_staged_validation(db, data_dir, **kw)
        before = _vr_counts(db)
        second = run_staged_validation(db, data_dir, **kw)
        assert second["duplicated"] is True
        assert second["record_id"] == first["record_id"]
        assert _vr_counts(db) == before, "幂等重放明细零重写"
        assert set(second.keys()) == set(first.keys()), "返回体同构（:587）"


class TestAtomicityAndEdges:
    """B4-3/B4-5/B4-8：原子性/历史容缺/沙盒不落。"""

    def test_third_write_failure_rolls_back_record(self, db, hypothesis_id):
        """B4-3：第四写（逐笔）NOT NULL 违例 → validation_records 亦
        回滚（「记录在而明细残缺」不可达）。"""
        bad_detail = [{
            "exec_ref": "main", "exec_index": 0,
            "range_start": "s", "range_end": "e",
            "trades": [{"exec_ref": "main", "exec_index": 0,
                        "range_start": "s", "range_end": "e", "seq": 0,
                        "entry_time": None,  # NOT NULL 违例——故障注入
                        "exit_time": "t", "entry_price": 1.0,
                        "exit_price": 1.0, "size": 1.0, "direction": "Long",
                        "pnl": 0.0}],
            "equity": {"exec_ref": "main", "exec_index": 0,
                       "range_start": "s", "range_end": "e",
                       "grid_aligned": 1, "timeframe": "4h", "start": "x",
                       "length": 1, "timestamps": None, "values": "[1.0]"},
        }]
        with pytest.raises(IntegrityError):
            rr.save_validation_with_log(
                db, record_id="rec_atomic1", hypothesis_id=hypothesis_id,
                strategy_id="st_x", level="isolated_backtest",
                params="{}", result="{}", verdict="pass", range_hash="rh_a1",
                detail=bad_detail)
        assert rr.get_validation_record_by_id(db, "rec_atomic1") is None, \
            "四写原子性：明细失败则记录整体回滚"
        assert _vr_counts(db) == (0, 0)

    def test_no_detail_writes_no_rows(self, db, hypothesis_id):
        """缺省 detail=None 不写明细（签名向后兼容）+ B4-5：无明细
        记录 get_validation_executions 返回 []（历史容缺面）。"""
        rr.save_validation_with_log(
            db, record_id="rec_hist2", hypothesis_id=hypothesis_id,
            strategy_id="st_x", level="isolated_backtest", params="{}",
            result="{}", verdict="fail", range_hash="rh_h2")
        assert _vr_counts(db) == (0, 0)
        assert rr.get_validation_executions(db, "rec_hist2") == []

    def test_sandbox_writes_zero_detail_rows(
            self, db, data_dir, hypothesis_id, strategy_saved):
        """B4-8：沙盒只写日志不落表——零明细行+零 params 三键。"""
        out = run_sandbox_backtest(
            db, data_dir, strategy_id=strategy_saved,
            hypothesis_id=hypothesis_id, symbol="BTCUSDT", timeframe="4h",
            start="2023-03-01T00:00:00Z", end="2023-03-15T00:00:00Z")
        assert out["sandbox"] is True
        assert _vr_counts(db) == (0, 0), "沙盒路径零明细行"


class TestHoleDataDegradedWrite:
    """specs「洞数据集降级形制」链路面（B4-7 素材=自造缺口 parquet）：
    装载层不网格化 → equity 轴带洞 → 真实回测落盘降级行。helper 单元
    面（合成 index）见 TestBuildDetailPayload，此处证全链传导。"""

    def test_hole_parquet_degrades_in_real_pipeline(
            self, db, tmp_path, hypothesis_id, strategy_saved):
        d = tmp_path / "hole_market"
        d.mkdir()
        rng = np.random.default_rng(7)
        idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                            name="timestamp")
        close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
        df = pd.DataFrame({"open": close, "high": close + 60,
                           "low": close - 60, "close": close,
                           "volume": rng.uniform(100, 800, 2000),
                           "trades": 40}, index=idx)
        # 请求区间内抽三根 bar（自造缺口——落在该次回测 equity 轴上）
        drop_ts = pd.DatetimeIndex(
            [t for t in idx
             if pd.Timestamp("2023-03-10T00:00:00Z") <= t
             < pd.Timestamp("2023-03-11T00:00:00Z")])
        assert len(drop_ts) == 6, "4h 周期一日应 6 根（抽取前提自检）"
        df.drop(index=drop_ts).to_parquet(d / "BTCUSDT_4h.parquet")

        out = run_isolated_backtest(
            db, d, hypothesis_id=hypothesis_id, strategy_id=strategy_saved,
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-03-01T00:00:00Z",
                           "end": "2023-04-01T00:00:00Z"})
        assert out["duplicated"] is False
        ex = rr.get_validation_executions(db, out["record_id"])[0]
        eq = ex["equity"]
        assert eq["grid_aligned"] is False, "洞数据集必须显式降级"
        ts = [pd.Timestamp(t) for t in eq["timestamps"]]
        assert ts, "降级形制 timestamps 非空"
        assert len(ts) == len(eq["values"]), "B2-9：timestamps 与 values 等长"
        # 缺口如实呈现：被抽 bar 不在时间轴、相邻间隔存在 >4h（禁静默
        # 按理想网格重建时间轴的直接行为证明）
        ts_set = {str(t) for t in drop_ts}
        assert not ts_set & {str(t) for t in ts}, "缺口 bar 不得被网格重建补回"
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        assert max(gaps) > pd.Timedelta(hours=4), "时间轴含洞（间隔>TF）"
        assert min(gaps) == pd.Timedelta(hours=4), "其余间隔保持 4h 等距"
