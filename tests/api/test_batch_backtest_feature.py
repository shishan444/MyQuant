"""Batch backtest 特征网（修复期安全网，现状记录语义）。

覆盖 api/routes/strategies/batch.py：
- _BatchBacktestProcessor：init 分组/404、process_step 数据不足与正常路径、
  单策略异常与组级异常、finalize 聚合与排序、mark_failed no-op
- POST /api/strategies/batch-backtest/stream：无效策略 404、session_id 形制、
  后台执行落库（run_source=batch_backtest）
- GET /api/strategies/batch-backtest/{result_id}：404、JSON 字段解析与坏 JSON 容错

IO 边界（BacktestEngine.batch_run/df 装载/fitness 计算）mock 为固定输出，
只记录编排行为（Q-1=a 迁移后执行内核为回测引擎，替身随迁）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.schemas import BatchBacktestRequest
from tests.helpers.data_factory import make_dna, make_ohlcv


def _fake_engine_result(
    total_return: float = 0.05, total_trades: int = 0,
) -> SimpleNamespace:
    """BacktestEngine.batch_run 结果替身：零交易路径（win_rate=None 透传）。"""
    return SimpleNamespace(
        equity_curve=pd.Series([100000.0, 100050.0, 100100.0]),
        total_trades=total_trades,
        win_rate=None,
        total_return=total_return,
        liquidated=False,
        total_funding_cost=0.0,
        trades_df=None,
        metrics_dict={
            "sharpe_ratio": 0.1, "max_drawdown": 0.01,
            "win_rate": None, "profit_factor": 0.0,
        },
    )


def _fitness_result(fitness: float = 7.0, qualified: bool = True) -> dict:
    return {
        "fitness": fitness, "qualified": qualified, "total_score": fitness + 1.0,
        "dimension_scores": {"dim": 1}, "satisfaction": None,
    }


@pytest.fixture
def batch_db(tmp_path: Path) -> Path:
    from core.persistence.repository.strategy import save_strategy
    from core.persistence.db_ext import init_db_ext
    db_path = tmp_path / "batch.db"
    init_db_ext(db_path)
    save_strategy(
        db_path, strategy_id="s-high", dna_json=make_dna().to_json(),
        symbol="BTCUSDT", timeframe="4h", name="HighFit",
    )
    save_strategy(
        db_path, strategy_id="s-low",
        # direction 进 gene_signature（threshold 不进）——用 short 保证与
        # s-high 签名不同，避免 save_strategy 同签名去重吞掉第二条
        dna_json=make_dna(direction="short").to_json(),
        symbol="BTCUSDT", timeframe="4h", name="LowFit",
    )
    return db_path


def _make_processor(db_path: Path, data_dir: Path, strategy_ids=None, leverage=1):
    from api.routes.strategies.batch import _BatchBacktestProcessor
    from api.schemas import BatchBacktestRequest, VerifyDateRange
    payload = BatchBacktestRequest(
        strategy_ids=strategy_ids or ["s-high", "s-low"],
        data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
        leverage=leverage,
    )
    return _BatchBacktestProcessor(payload, db_path, data_dir)


class TestProcessorInit:
    def test_init_groups_and_builds_steps(self, batch_db: Path, tmp_path: Path) -> None:
        """有效策略按 symbol_timeframe 分组，steps = 组 x 数据区间。"""
        processor = _make_processor(batch_db, tmp_path)
        processor.init()
        assert "BTCUSDT_4h" in processor.strategies_by_group
        sids = [s["strategy_id"] for s in processor.strategies_by_group["BTCUSDT_4h"]]
        assert set(sids) == {"s-high", "s-low"}
        assert processor.strategy_names == {"s-high": "HighFit", "s-low": "LowFit"}
        assert len(processor.steps) == 1  # 1 组 x 1 区间
        assert set(processor.strategy_period_data) == {"s-high", "s-low"}

    def test_init_skips_invalid_ids_and_keeps_valid(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """不存在的 strategy_id 被跳过，其余照常入组。"""
        processor = _make_processor(batch_db, tmp_path, strategy_ids=["ghost", "s-high"])
        processor.init()
        sids = [s["strategy_id"] for s in processor.strategies_by_group["BTCUSDT_4h"]]
        assert sids == ["s-high"]

    def test_init_no_valid_strategies_raises_404(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """全部无效时 init 抛 404（No valid strategies found）。"""
        processor = _make_processor(batch_db, tmp_path, strategy_ids=["ghost"])
        with pytest.raises(HTTPException) as exc:
            processor.init()
        assert exc.value.status_code == 404


class TestProcessorStep:
    def test_insufficient_data_records_error(self, batch_db: Path, tmp_path: Path) -> None:
        """数据 <10 行时每策略记 Insufficient data 错误与 0 值周期。"""
        processor = _make_processor(batch_db, tmp_path)
        processor.init()
        processor._load_df = lambda *a, **kw: make_ohlcv(5, "4h")
        progress = processor.process_step(0)
        assert progress["current"] == 1
        assert progress["total"] == 1
        assert progress["group"] == "BTCUSDT_4h"
        assert len(progress["batch_results"]) == 2
        assert all(r["error"] == "Insufficient data" for r in progress["batch_results"])
        assert all(r.error == "Insufficient data for this period"
                   for r in processor.all_results)
        assert processor.strategy_period_data["s-high"][0]["fitness"] == 0.0

    def test_normal_step_persists_result(self, batch_db: Path, tmp_path: Path) -> None:
        """正常步骤：结果落库（run_source=batch_backtest）并产出 item。"""
        from core.persistence.repository.backtest_result import list_backtest_results
        processor = _make_processor(batch_db, tmp_path)
        processor.init()
        df = make_ohlcv(50, "4h")
        processor._load_df = lambda *a, **kw: df
        processor._load_mtf = lambda *a, **kw: None
        processor._compute_fitness = lambda *a, **kw: _fitness_result()
        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.return_value = [
                _fake_engine_result(), _fake_engine_result()]
            progress = processor.process_step(0)

        # BatchBacktestResultItem 序列化后 error=None 表示成功（键恒存在）
        assert all(r.get("error") is None for r in progress["batch_results"])
        rows = list_backtest_results(batch_db, run_source="batch_backtest", limit=10)
        assert len(rows) == 2
        assert rows[0]["fitness"] == 7.0
        item = processor.all_results[0]
        assert item.result_id  # uuid 已生成
        assert item.qualified is True

    def test_leverage_override_applied(self, batch_db: Path, tmp_path: Path) -> None:
        """payload.leverage != 1 时覆盖组内每个 DNA 的 risk_genes.leverage。"""
        captured = {}

        def _capture_batch_run(dnas, df, **kw):
            captured["leverages"] = [d.risk_genes.leverage for d in dnas]
            return [_fake_engine_result() for _ in dnas]

        processor = _make_processor(batch_db, tmp_path, leverage=3)
        processor.init()
        processor._load_df = lambda *a, **kw: make_ohlcv(50, "4h")
        processor._load_mtf = lambda *a, **kw: None
        processor._compute_fitness = lambda *a, **kw: _fitness_result()
        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.side_effect = _capture_batch_run
            processor.process_step(0)
        # 组内两策略（s-high/s-low）逐个 deepcopy 覆写后同批传入
        assert captured["leverages"] == [3, 3]

    def test_strategy_level_exception_recorded(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """单策略评估抛异常时仅该策略记 error，其余正常。"""
        processor = _make_processor(batch_db, tmp_path)
        processor.init()
        processor._load_df = lambda *a, **kw: make_ohlcv(50, "4h")
        processor._load_mtf = lambda *a, **kw: None

        calls = {"n": 0}

        def _flaky_fitness(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("eval boom")
            return _fitness_result()

        processor._compute_fitness = _flaky_fitness
        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.return_value = [
                _fake_engine_result(), _fake_engine_result()]
            processor.process_step(0)

        errors = [r for r in processor.all_results if r.error]
        ok = [r for r in processor.all_results if not r.error]
        # CARD-07 后未知异常脱敏为固定文案（原文仅入日志），
        # 「异常被记录且策略级隔离」语义不变
        assert len(errors) == 1 and errors[0].error == "内部错误"
        assert len(ok) == 1

    def test_group_level_exception_fails_all(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """数据装载抛异常时组内全部策略记 error。"""
        def _boom(*a, **kw):
            raise RuntimeError("load boom")

        processor = _make_processor(batch_db, tmp_path)
        processor.init()
        processor._load_df = _boom
        progress = processor.process_step(0)
        assert len(progress["batch_results"]) == 2
        # CARD-07 后未知异常脱敏为固定文案
        assert all(r["error"] == "内部错误" for r in progress["batch_results"])

    def test_corrupt_dna_json_strategy_skipped(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """存量 dna_json 解析失败时该策略被跳过（不 500 不 404）。"""
        from core.persistence.repository.strategy import save_strategy
        save_strategy(
            batch_db, strategy_id="s-corrupt", dna_json="{broken",
            symbol="BTCUSDT", timeframe="4h", name="Broken",
        )
        processor = _make_processor(
            batch_db, tmp_path, strategy_ids=["s-corrupt", "s-high"],
        )
        processor.init()
        assert "s-corrupt" not in processor.strategy_names
        assert set(processor.strategy_names) == {"s-high"}

    def test_equity_curve_serialized_when_trades_exist(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """引擎权益曲线（Series）按索引直序列化落库（无截尾对齐）。"""
        import json as _json
        from core.persistence.repository.backtest_result import get_backtest_result
        processor = _make_processor(batch_db, tmp_path, strategy_ids=["s-high"])
        processor.init()
        df = make_ohlcv(50, "4h")
        processor._load_df = lambda *a, **kw: df
        processor._load_mtf = lambda *a, **kw: None
        processor._compute_fitness = lambda *a, **kw: _fitness_result()
        fake = SimpleNamespace(
            equity_curve=pd.Series(
                [100000.0, 100500.0, 101000.0], index=df.index[:3]),
            total_trades=3,
            win_rate=0.5,
            total_return=0.01, liquidated=False, total_funding_cost=0.0,
            trades_df=None,
            metrics_dict={
                "sharpe_ratio": 0.1, "max_drawdown": 0.01,
                "win_rate": 0.5, "profit_factor": 0.0,
            },
        )
        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.return_value = [fake]
            processor.process_step(0)

        rows = [r for r in processor.all_results if not r.error]
        row = get_backtest_result(batch_db, rows[0].result_id)
        curve = _json.loads(row["equity_curve"])
        assert len(curve) == 3
        # 引擎语义：Series 索引直序列化（删截尾对齐），时间戳=曲线自身
        # 索引；替身取 df 前 3 根 → 首根为序列首根 2024-01-01 00:00
        assert curve[0]["timestamp"] == "2024-01-01 00:00:00"
        assert curve[0]["value"] == 100000.0
        assert curve[-1]["value"] == 101000.0
        # win_rate 直取引擎产物（三态透传——替身给数值即数值）
        assert rows[0].win_rate == 0.5

    def test_mtf_strategy_triggers_mtf_load(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """DNA 带 layers（MTF）时按 compute_needed_tfs 装载多周期数据。"""
        from core.persistence.repository.strategy import save_strategy
        from tests.helpers.data_factory import make_mtf_dna
        save_strategy(
            batch_db, strategy_id="s-mtf", dna_json=make_mtf_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="MtfStrat",
        )
        processor = _make_processor(batch_db, tmp_path, strategy_ids=["s-mtf"])
        processor.init()
        mtf_calls: list = []
        processor._load_df = lambda *a, **kw: make_ohlcv(50, "4h")
        processor._load_mtf = lambda *a, **kw: mtf_calls.append(kw) or None
        processor._compute_fitness = lambda *a, **kw: _fitness_result()
        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.return_value = [_fake_engine_result()]
            processor.process_step(0)
        assert len(mtf_calls) == 1  # MTF 装载被触发


class TestProcessorFinalize:
    def test_finalize_aggregates_and_sorts(self, batch_db: Path, tmp_path: Path) -> None:
        """summary 按策略聚合（avg/worst/qualified_count）并按 avg_fitness 降序。"""
        processor = _make_processor(batch_db, tmp_path)
        processor.init()
        processor._load_df = lambda *a, **kw: make_ohlcv(50, "4h")
        processor._load_mtf = lambda *a, **kw: None

        fitness_by_calls = iter([_fitness_result(fitness=2.0), _fitness_result(fitness=9.0)])
        processor._compute_fitness = lambda *a, **kw: next(fitness_by_calls)
        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.return_value = [
                _fake_engine_result(), _fake_engine_result()]
            processor.process_step(0)

        result = processor.finalize()
        summary = result["summary"]
        assert len(summary) == 2
        # s-low(2.0) 在前还是 s-high(9.0) 在前取决于调用顺序——按 fitness 降序断言
        assert summary[0]["avg_fitness"] >= summary[1]["avg_fitness"]
        assert summary[0]["avg_fitness"] == 9.0
        assert summary[0]["qualified_count"] == 1
        assert summary[0]["total_periods"] == 1
        # per_period_results 带回对应 result_id
        assert summary[0]["per_period_results"][0]["result_id"]

    def test_finalize_all_invalid_periods_zero_summary(
        self, batch_db: Path, tmp_path: Path,
    ) -> None:
        """全部周期失败（无 result_id）时 summary 全 0 但 per_period 保留。"""
        processor = _make_processor(batch_db, tmp_path)
        processor.init()
        processor._load_df = lambda *a, **kw: make_ohlcv(5, "4h")
        processor.process_step(0)
        result = processor.finalize()
        summary = result["summary"]
        assert len(summary) == 2
        assert all(s["avg_fitness"] == 0.0 and s["qualified_count"] == 0 for s in summary)
        assert all(len(s["per_period_results"]) == 1 for s in summary)

    def test_mark_failed_is_noop(self, batch_db: Path, tmp_path: Path) -> None:
        """batch 无 session 表：mark_failed 为 no-op（不抛异常）。"""
        processor = _make_processor(batch_db, tmp_path)
        processor.mark_failed("some error")  # 不应抛异常


class TestStreamEndpoint:
    def test_stream_no_valid_strategies_404(self, api_client: TestClient) -> None:
        """全部 strategy_id 无效时 POST stream 直接 404。"""
        response = api_client.post(
            "/api/strategies/batch-backtest/stream",
            json={
                "strategy_ids": ["ghost-1"],
                "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}],
            },
        )
        assert response.status_code == 404

    def test_stream_returns_session_id_and_runs_background(
        self, api_client: TestClient,
    ) -> None:
        """POST 返回 batch_ 前缀 session_id，后台执行完成后结果落库。"""
        from core.persistence.repository.backtest_result import list_backtest_results
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        data_dir = api_client.app.state.data_dir
        save_strategy(
            db_path, strategy_id="s-strm", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="StreamStrat",
        )

        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.return_value = [_fake_engine_result()]
            with patch(
                "core.data.mtf_loader.load_and_prepare_df",
                return_value=make_ohlcv(50, "4h"),
            ):
                # __init__ 内函数级 import 从 mtf_loader 源模块取符号，
                # patch 源模块入口即可在端点路径生效
                response = api_client.post(
                    "/api/strategies/batch-backtest/stream",
                    json={
                        "strategy_ids": ["s-strm"],
                        "data_ranges": [{"start": "2024-01-01", "end": "2024-12-31"}],
                    },
                )
                assert response.status_code == 200
                session_id = response.json()["session_id"]
                assert session_id.startswith("batch_")

                # 轮询等待后台任务落库（unregister 是 finally，结果先于其出现）
                deadline = time.monotonic() + 15.0
                rows = []
                while time.monotonic() < deadline:
                    rows = list_backtest_results(
                        db_path, run_source="batch_backtest", limit=5,
                    )
                    if rows:
                        break
                    time.sleep(0.05)
                assert rows, "后台批回测未在时限内落库"
                assert rows[0]["strategy_id"] == "s-strm"
        # data_dir 引用避免未使用告警（fixture 隐式使用）
        assert data_dir is not None

    def test_stream_cancel_before_first_step_skips_run(
        self, api_client: TestClient,
    ) -> None:
        """POST 后立即协作取消：任何 step 前取消则零结果落库。"""
        from api import stream_control
        from core.persistence.repository.backtest_result import list_backtest_results
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="s-cancel", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="CancelStrat",
        )
        with patch("core.backtest.engine.BacktestEngine") as engine_cls:
            engine_cls.return_value.batch_run.return_value = [_fake_engine_result()]
            with patch(
                "core.data.mtf_loader.load_and_prepare_df",
                return_value=make_ohlcv(50, "4h"),
            ):
                response = api_client.post(
                    "/api/strategies/batch-backtest/stream",
                    json={
                        "strategy_ids": ["s-cancel"],
                        "data_ranges": [{"start": "2024-01-01", "end": "2024-12-31"}],
                    },
                )
                session_id = response.json()["session_id"]
                # 响应返回即取消（首步检查点之前）
                stream_control.request_cancel(session_id)

                # 轮询等待后台任务退出（finally unregister）
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    if session_id not in stream_control._cancel_callbacks:
                        break
                    time.sleep(0.05)
                assert session_id not in stream_control._cancel_callbacks
        rows = list_backtest_results(db_path, run_source="batch_backtest", limit=5)
        assert rows == []

    def test_stream_background_error_unregisters_and_no_results(
        self, api_client: TestClient,
    ) -> None:
        """后台装载抛异常：任务退出（unregister）且零结果落库。"""
        from api import stream_control
        from core.persistence.repository.backtest_result import list_backtest_results
        from core.persistence.repository.strategy import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="s-err", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="ErrStrat",
        )

        def _boom(*a, **kw):
            raise RuntimeError("stream boom")

        with patch("core.data.mtf_loader.load_and_prepare_df", side_effect=_boom):
            response = api_client.post(
                "/api/strategies/batch-backtest/stream",
                json={
                    "strategy_ids": ["s-err"],
                    "data_ranges": [{"start": "2024-01-01", "end": "2024-12-31"}],
                },
            )
            assert response.status_code == 200
            session_id = response.json()["session_id"]

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if session_id not in stream_control._cancel_callbacks:
                    break
                time.sleep(0.05)
            assert session_id not in stream_control._cancel_callbacks
        rows = list_backtest_results(db_path, run_source="batch_backtest", limit=5)
        assert rows == []


class TestDetailEndpoint:
    def test_detail_unknown_result_404(self, api_client: TestClient) -> None:
        response = api_client.get("/api/strategies/batch-backtest/no-such-id")
        assert response.status_code == 404
        assert response.json()["detail"] == "Backtest result not found"

    def test_detail_parses_stored_json_fields(self, api_client: TestClient) -> None:
        """equity_curve/trades_json/dimension_scores 正常解析回结构化字段。"""
        from core.persistence.repository.backtest_result import save_backtest_result
        db_path = api_client.app.state.db_path
        save_backtest_result(
            db_path, result_id="res-ok", strategy_id="s1",
            symbol="BTCUSDT", timeframe="4h",
            data_start="2024-01-01", data_end="2024-06-01",
            init_cash=100000, fee=0.001, slippage=0.0005,
            total_return=0.1, sharpe_ratio=1.2, max_drawdown=0.05,
            win_rate=0.6, total_trades=8, total_score=5.0,
            template_name="explorer", run_source="batch_backtest",
            fitness=4.0, qualified=1,
            equity_curve=json.dumps([{"timestamp": "2024-01-01T00:00:00", "value": 1.0}]),
            trades_json=json.dumps([{"signal": "buy"}]),
            dimension_scores=json.dumps({"profitability": 3}),
        )
        response = api_client.get("/api/strategies/batch-backtest/res-ok")
        assert response.status_code == 200
        data = response.json()
        assert data["equity_curve"] == [{"timestamp": "2024-01-01T00:00:00", "value": 1.0}]
        assert data["signals"] == [{"signal": "buy"}]
        assert data["dimension_scores"] == {"profitability": 3}

    def test_detail_corrupt_json_fields_become_none(self, api_client: TestClient) -> None:
        """存量 JSON 字段损坏时对应字段回 None（容错不 500）。"""
        from core.persistence.repository.backtest_result import save_backtest_result
        db_path = api_client.app.state.db_path
        save_backtest_result(
            db_path, result_id="res-bad", strategy_id="s1",
            symbol="BTCUSDT", timeframe="4h",
            data_start="2024-01-01", data_end="2024-06-01",
            init_cash=100000, fee=0.001, slippage=0.0005,
            run_source="batch_backtest",
            equity_curve="{corrupt", trades_json="not-json",
            dimension_scores="[broken",
        )
        response = api_client.get("/api/strategies/batch-backtest/res-bad")
        assert response.status_code == 200
        data = response.json()
        assert data["equity_curve"] is None
        assert data["signals"] is None
        assert data["dimension_scores"] is None


class TestLeverageBoundValidation:
    """CARD-03：BatchBacktestRequest.leverage 值域 [1, MAX_LEVERAGE]（422 面）。

    与 VerifyRequest 同族 Field 形制（api/schemas 单源 MAX_LEVERAGE=
    10——core/strategy/validator 杠杆红线）。越界在路由装配层拦 422、
    先于 handler（strategy_ids 无效本会 404——422 优先证明校验层拦
    截）；域内 leverage=1 的流式端到端由本文件 TestStreamEndpoint
    既有用例承载（payload 缺省即 1——引用声明不重复建）。
    """

    @pytest.mark.parametrize(
        "leverage", [0, 11], ids=["下界外 0", "上界外 MAX_LEVERAGE+1"])
    def test_leverage_out_of_bounds_422(self, api_client: TestClient,
                                        leverage: int) -> None:
        """越界 leverage → 422（结构化校验拒绝——裸 leverage 不再
        覆盖组内 DNA 装配）。"""
        response = api_client.post(
            "/api/strategies/batch-backtest/stream",
            json={"strategy_ids": ["ghost-1"],
                  "data_ranges": [
                      {"start": "2024-01-01", "end": "2024-06-01"}],
                  "leverage": leverage})
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "leverage", [1, 5, 10],
        ids=["下界 1", "域内 5", "上界 MAX_LEVERAGE"])
    def test_leverage_in_bounds_schema_accepts(self, leverage: int) -> None:
        """域内含两侧边界 schema 构造照常、缺省=1（合法值不误拦）。"""
        req = BatchBacktestRequest(
            strategy_ids=["s-1"],
            data_ranges=[{"start": "2024-01-01", "end": "2024-06-01"}],
            leverage=leverage)
        assert req.leverage == leverage
        assert BatchBacktestRequest(
            strategy_ids=["s-1"],
            data_ranges=[{"start": "2024-01-01", "end": "2024-06-01"}],
        ).leverage == 1
