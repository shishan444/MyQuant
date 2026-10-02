"""Verify 路由端点特征网（修复期安全网，现状记录语义）。

覆盖 api/routes/strategies/verify.py 的 REST 端点层（处理器内部已有
test_verify_processor.py 覆盖）：
- POST /verify：空 ranges 400、无效策略 404、正常同步执行（session completed、
  策略 verify 字段滚动更新）
- POST /verify/stream：session_id 形制、后台完成落库、mark_failed 状态
- GET /verify/history / /verify/sessions / /verify/sessions/{id}/results

IO 边界（BacktestEngine.batch_run、df 装载、fitness 计算）mock 为固定输出。
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna, make_ohlcv


def _fake_bt_result(
    total_return: float = 0.10, sharpe: float = 1.5,
) -> SimpleNamespace:
    return SimpleNamespace(
        total_return=total_return, sharpe_ratio=sharpe, max_drawdown=0.05,
        win_rate=0.6, total_trades=10, liquidated=False,
    )


def _fake_fitness(**kw) -> dict:
    return {
        "fitness": 7.0, "qualified": True, "total_score": 8.0,
        "dimension_scores": {"d": 1}, "satisfaction": None,
    }


@pytest.fixture
def verify_client(api_client: TestClient) -> TestClient:
    """预置一条策略供 verify 端点使用。"""
    from core.persistence.db_ext import save_strategy
    db_path = api_client.app.state.db_path
    save_strategy(
        db_path, strategy_id="strat-v1",
        dna_json=make_dna().to_json(),
        symbol="BTCUSDT", timeframe="4h", name="VerifyMe",
    )
    return api_client


def _payload(**overrides) -> dict:
    body = {
        "strategy_ids": ["strat-v1"],
        "data_ranges": [{"start": "2024-01-01", "end": "2024-06-01"}],
        "init_cash": 100_000, "fee": 0.001, "slippage": 0.0005, "leverage": 1,
    }
    body.update(overrides)
    return body


def _mocked_engine():
    """BacktestEngine 替身：batch_run 返回固定结果。"""
    engine_mod = type("M", (), {})
    engine_mod.BacktestEngine = lambda **kw: SimpleNamespace(
        batch_run=lambda dnas, df, **kw: [_fake_bt_result() for _ in dnas],
    )
    return engine_mod


class TestVerifySyncEndpoint:
    def test_empty_data_ranges_400(self, verify_client: TestClient) -> None:
        response = verify_client.post(
            "/api/strategies/verify", json=_payload(data_ranges=[]),
        )
        assert response.status_code == 400
        assert "data_ranges" in response.json()["detail"]

    def test_no_valid_strategies_404(self, verify_client: TestClient) -> None:
        response = verify_client.post(
            "/api/strategies/verify", json=_payload(strategy_ids=["ghost"]),
        )
        assert response.status_code == 404

    def test_verify_completes_and_updates_strategy(
        self, verify_client: TestClient,
    ) -> None:
        """同步 verify：结果与 summary 返回、session completed、
        策略 verify_star/new_score 滚动更新。"""
        from core.persistence.db_ext import get_strategy, get_verify_session
        db_path = verify_client.app.state.db_path
        with patch("api.routes.strategies.verify._bt_engine_mod", _mocked_engine()), \
             patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(50, "4h")), \
             patch("core.scoring.scorer.compute_fitness",
                   side_effect=lambda *a, **kw: _fake_fitness()):
            response = verify_client.post("/api/strategies/verify", json=_payload())
        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["strategy_id"] == "strat-v1"
        assert data["results"][0]["fitness"] == 7.0
        summary = data["summary"][0]
        # 全周期 qualified：comprehensive = 7.0 * 1.0 * 1.2 = 8.4
        assert summary["avg_fitness"] == 7.0
        assert summary["comprehensive_score"] == 8.4
        assert summary["qualified_count"] == 1

        # session 落库为 completed
        from core.persistence.db_ext import list_verify_sessions
        sessions = list_verify_sessions(db_path, limit=5)
        assert len(sessions) == 1
        assert sessions[0]["status"] == "completed"
        assert sessions[0]["total_results"] == 1

        # CARD-02 后正确行为：时间戳为真 UTC（naive 本地时间贴 Z 后缀会差 +8h）
        from datetime import datetime, timezone
        for raw_ts in (sessions[0]["completed_at"], ):
            assert raw_ts, "completed_at 应已落库"
            parsed = datetime.strptime(raw_ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc,
            )
            delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
            assert delta < 60, f"completed_at 应为真 UTC，偏差 {delta}s"

        # 策略 verify 字段滚动更新：star 5（fitness 7.0 >= 6.0 且全 qualified）、
        # comprehensive_score 8.4 进 verify_best/verify_avg
        strat = get_strategy(db_path, "strat-v1")
        assert strat["verify_star"] == 5
        assert strat["verify_count"] == 1
        assert strat["verify_best_score"] == 8.4
        assert strat["verify_avg_score"] == 8.4
        # last_verified_at 同为真 UTC（CARD-02）
        assert strat["last_verified_at"] is not None
        parsed_lv = datetime.strptime(
            strat["last_verified_at"], "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
        delta_lv = abs((datetime.now(timezone.utc) - parsed_lv).total_seconds())
        assert delta_lv < 60, f"last_verified_at 应为真 UTC，偏差 {delta_lv}s"

    def test_verify_with_insufficient_data_records_error(
        self, verify_client: TestClient,
    ) -> None:
        """数据不足的周期记 error 结果（fitness 0、star 0）。"""
        from core.persistence.db_ext import get_strategy
        db_path = verify_client.app.state.db_path
        with patch("api.routes.strategies.verify._bt_engine_mod", _mocked_engine()), \
             patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(5, "4h")):
            response = verify_client.post("/api/strategies/verify", json=_payload())
        assert response.status_code == 200
        results = response.json()["results"]
        assert results[0]["error"] == "Insufficient data for this period"
        # 全周期失败：qualified_count 0、无 star
        strat = get_strategy(db_path, "strat-v1")
        assert strat["verify_star"] == 0


class TestVerifyStreamEndpoint:
    def test_stream_no_valid_strategies_404(self, verify_client: TestClient) -> None:
        response = verify_client.post(
            "/api/strategies/verify/stream", json=_payload(strategy_ids=["ghost"]),
        )
        assert response.status_code == 404

    def test_stream_runs_and_completes_session(
        self, verify_client: TestClient,
    ) -> None:
        """stream 端点返回 session_id，后台执行完成后 session 状态 completed。"""
        from core.persistence.db_ext import get_verify_session
        db_path = verify_client.app.state.db_path
        with patch("api.routes.strategies.verify._bt_engine_mod", _mocked_engine()), \
             patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(50, "4h")), \
             patch("core.scoring.scorer.compute_fitness",
                   side_effect=lambda *a, **kw: _fake_fitness()):
            response = verify_client.post(
                "/api/strategies/verify/stream", json=_payload(),
            )
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        deadline = time.monotonic() + 15.0
        session = None
        while time.monotonic() < deadline:
            session = get_verify_session(db_path, session_id)
            if session and session.get("status") == "completed":
                break
            time.sleep(0.05)
        assert session is not None and session["status"] == "completed"
        assert session["total_results"] == 1
        # CARD-02 后正确行为：_VerifyProcessor.finalize 的 completed_at 为真 UTC
        from datetime import datetime, timezone
        parsed = datetime.strptime(
            session["completed_at"], "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
        delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        assert delta < 60, f"stream 路径 completed_at 应为真 UTC，偏差 {delta}s"

    def test_mark_failed_sets_session_failed(
        self, verify_client: TestClient, tmp_path: Path,
    ) -> None:
        """mark_failed 将 session 置 failed（B8.0 ① 取消路径的落库语义）。"""
        from core.persistence.db_ext import get_verify_session
        from api.routes.strategies.verify import _VerifyProcessor
        from api.schemas import VerifyRequest, VerifyDateRange
        db_path = verify_client.app.state.db_path
        payload = VerifyRequest(
            strategy_ids=["strat-v1"],
            data_ranges=[VerifyDateRange(start="2024-01-01", end="2024-06-01")],
        )
        processor = _VerifyProcessor(payload, db_path, tmp_path)
        processor.init()
        processor.mark_failed("Cancelled by client")
        session = get_verify_session(db_path, processor.session_id)
        assert session["status"] == "failed"
        assert session["error_message"] == "Cancelled by client"


class TestVerifyHistoryAndSessions:
    def test_history_empty(self, verify_client: TestClient) -> None:
        response = verify_client.get("/api/strategies/verify/history")
        assert response.status_code == 200
        assert response.json() == {"items": [], "total": 0}

    def test_history_after_verify_run(self, verify_client: TestClient) -> None:
        """跑一次 verify 后 history 回溯 run_source=verify 的结果与策略名。"""
        with patch("api.routes.strategies.verify._bt_engine_mod", _mocked_engine()), \
             patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(50, "4h")), \
             patch("core.scoring.scorer.compute_fitness",
                   side_effect=lambda *a, **kw: _fake_fitness()):
            verify_client.post("/api/strategies/verify", json=_payload())
        response = verify_client.get("/api/strategies/verify/history")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["strategy_id"] == "strat-v1"
        assert item["strategy_name"] == "VerifyMe"
        assert item["fitness"] == 7.0

    def test_history_filter_by_strategy(self, verify_client: TestClient) -> None:
        with patch("api.routes.strategies.verify._bt_engine_mod", _mocked_engine()), \
             patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(50, "4h")), \
             patch("core.scoring.scorer.compute_fitness",
                   side_effect=lambda *a, **kw: _fake_fitness()):
            verify_client.post("/api/strategies/verify", json=_payload())
        other = verify_client.get(
            "/api/strategies/verify/history",
            params={"strategy_id": "someone-else"},
        )
        assert other.json()["total"] == 0
        mine = verify_client.get(
            "/api/strategies/verify/history",
            params={"strategy_id": "strat-v1"},
        )
        assert mine.json()["total"] == 1

    def test_sessions_empty(self, verify_client: TestClient) -> None:
        response = verify_client.get("/api/strategies/verify/sessions")
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_sessions_after_run(self, verify_client: TestClient) -> None:
        with patch("api.routes.strategies.verify._bt_engine_mod", _mocked_engine()), \
             patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(50, "4h")), \
             patch("core.scoring.scorer.compute_fitness",
                   side_effect=lambda *a, **kw: _fake_fitness()):
            verify_client.post("/api/strategies/verify", json=_payload())
        response = verify_client.get("/api/strategies/verify/sessions")
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "completed"

    def test_session_results_unknown_404(self, verify_client: TestClient) -> None:
        response = verify_client.get(
            "/api/strategies/verify/sessions/none-such/results",
        )
        assert response.status_code == 404

    def test_session_results_returns_items(self, verify_client: TestClient) -> None:
        """session 结果端点回该 session 的验证结果（按 session_id 过滤）。"""
        with patch("api.routes.strategies.verify._bt_engine_mod", _mocked_engine()), \
             patch("core.data.mtf_loader.load_and_prepare_df",
                   return_value=make_ohlcv(50, "4h")), \
             patch("core.scoring.scorer.compute_fitness",
                   side_effect=lambda *a, **kw: _fake_fitness()):
            verify_client.post("/api/strategies/verify", json=_payload())
        from core.persistence.db_ext import list_verify_sessions
        session_id = list_verify_sessions(
            verify_client.app.state.db_path, limit=1,
        )[0]["session_id"]

        response = verify_client.get(
            f"/api/strategies/verify/sessions/{session_id}/results",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["strategy_id"] == "strat-v1"
        assert data["items"][0]["strategy_name"] == "VerifyMe"

    @staticmethod
    def _save_verify_result(db_path, result_id: str, session_id: str) -> None:
        from core.persistence.db_ext import save_backtest_result
        save_backtest_result(
            db_path, result_id=result_id, strategy_id="strat-v1",
            symbol="BTCUSDT", timeframe="4h",
            data_start="2024-01-01", data_end="2024-06-01",
            init_cash=100000, fee=0.001, slippage=0.0005,
            total_return=0.1, sharpe_ratio=1.0, max_drawdown=0.05,
            win_rate=0.5, total_trades=5, total_score=3.0,
            template_name="explorer", run_source="verify",
            session_id=session_id,
        )

    def test_submerged_session_results_fully_retrievable(
        self, verify_client: TestClient,
    ) -> None:
        """CARD-03 修复锚定：session 结果被 500 窗口挤出后仍完整取回。

        受害场景：victim session 3 条先写，flood session 501 条后写——
        旧实现（拉最新 500 条后内存过滤）返回 0 条静默丢失；
        DB 层过滤（session_id where）应返回 victim 全部 3 条。
        """
        from core.persistence.db_ext import save_verify_session
        db_path = verify_client.app.state.db_path
        save_verify_session(
            db_path, session_id="sess-victim",
            strategy_ids='["strat-v1"]', data_ranges="[]",
            init_cash=100000, fee=0.001, slippage=0.0005,
        )
        for i in range(3):
            self._save_verify_result(db_path, f"res-victim-{i}", "sess-victim")
        for i in range(501):
            self._save_verify_result(db_path, f"res-flood-{i}", "sess-flood")

        response = verify_client.get(
            "/api/strategies/verify/sessions/sess-victim/results",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert {i["result_id"] for i in data["items"]} == {
            "res-victim-0", "res-victim-1", "res-victim-2",
        }
        # 所有条目带策略名回填（转换路径正常）
        assert all(i["strategy_name"] == "VerifyMe" for i in data["items"])

    def test_session_results_limit_500_cap_preserved(
        self, verify_client: TestClient,
    ) -> None:
        """limit=500 防护上限保留：单 session 超 500 条时截断不炸。"""
        from core.persistence.db_ext import save_verify_session
        db_path = verify_client.app.state.db_path
        save_verify_session(
            db_path, session_id="sess-big",
            strategy_ids='["strat-v1"]', data_ranges="[]",
            init_cash=100000, fee=0.001, slippage=0.0005,
        )
        for i in range(505):
            self._save_verify_result(db_path, f"res-big-{i}", "sess-big")
        response = verify_client.get(
            "/api/strategies/verify/sessions/sess-big/results",
        )
        assert response.status_code == 200
        assert response.json()["total"] == 500

    def test_history_unfiltered_by_session_change(
        self, verify_client: TestClient,
    ) -> None:
        """不传 session_id 的 history 调用行为不变（无 session 过滤全量返回）。"""
        db_path = verify_client.app.state.db_path
        for i in range(3):
            self._save_verify_result(db_path, f"res-hist-{i}", "sess-hist-x")
        response = verify_client.get(
            "/api/strategies/verify/history",
            params={"strategy_id": "strat-v1", "limit": 100},
        )
        assert response.status_code == 200
        ids = {i["result_id"] for i in response.json()["items"]}
        assert {"res-hist-0", "res-hist-1", "res-hist-2"} <= ids
