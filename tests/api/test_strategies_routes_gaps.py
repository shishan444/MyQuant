"""Strategies 路由零散缺口特征网（修复期安全网，现状记录语义）。

补 backtest.py / compare.py / crud.py 合并视角未覆盖分支：
- POST /backtest：无 strategy_id 无 dna → 400；dataset 不存在 → 404；
  inline dna 模式（Lab 直传 DNA）
- POST /compare：dataset 404；strategy 不存在 → error item；存量 dna 损坏 →
  error item（不 500）
- GET /api/strategies：sort_by 非法 → 400
- PUT /api/strategies/{id}：draft 状态改 DNA → 409（M4 写锁）
- _gene_signature_or_derive：行内签名缺失时从 dna_json 兜底
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from tests.helpers.data_factory import make_dna, make_ohlcv


def _inline_dna_dict() -> dict:
    return {
        "signal_genes": [
            {"indicator": "RSI", "params": {"period": 14}, "role": "entry_trigger",
             "field": None, "condition": {"type": "lt", "threshold": 30}},
            {"indicator": "RSI", "params": {"period": 14}, "role": "exit_trigger",
             "field": None, "condition": {"type": "gt", "threshold": 70}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "AND"},
        "execution_genes": {"timeframe": "4h", "symbol": "BTCUSDT"},
        "risk_genes": {"stop_loss": 0.05, "take_profit": 0.10, "position_size": 0.3},
    }


@pytest.fixture
def seeded_client(api_client: TestClient) -> TestClient:
    """写入足量行情供真实回测。"""
    make_ohlcv(60, "4h").to_parquet(
        api_client.app.state.data_dir / "BTCUSDT_4h.parquet",
    )
    return api_client


class TestBacktestGaps:
    def test_missing_strategy_id_and_dna_400(
        self, seeded_client: TestClient,
    ) -> None:
        """既无 strategy_id 也无 dna → 400。"""
        response = seeded_client.post(
            "/api/strategies/backtest", json={"dataset_id": "BTCUSDT_4h"},
        )
        assert response.status_code == 400

    def test_unknown_dataset_404(self, seeded_client: TestClient) -> None:
        """dataset_id 无对应 parquet → 404。"""
        response = seeded_client.post(
            "/api/strategies/backtest",
            json={"dataset_id": "NOPE_4h", "dna": _inline_dna_dict(),
                  "symbol": "BTCUSDT", "timeframe": "4h"},
        )
        assert response.status_code == 404

    def test_inline_dna_mode_runs_and_returns_inline_id(
        self, seeded_client: TestClient,
    ) -> None:
        """inline dna 模式：直传 DNA 回测，strategy_id 回 'inline' 且不落库关联。"""
        response = seeded_client.post(
            "/api/strategies/backtest",
            json={"dataset_id": "BTCUSDT_4h", "dna": _inline_dna_dict(),
                  "symbol": "BTCUSDT", "timeframe": "4h"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["strategy_id"] == "inline"
        assert data["symbol"] == "BTCUSDT"
        assert data["timeframe"] == "4h"
        assert data["run_source"] == "lab"
        assert "total_return" in data


class TestCompareGaps:
    def test_unknown_dataset_404(self, seeded_client: TestClient) -> None:
        response = seeded_client.post(
            "/api/strategies/compare",
            json={"strategy_ids": ["x"], "dataset_id": "NOPE_4h"},
        )
        assert response.status_code == 404

    def test_unknown_strategy_yields_error_item(
        self, seeded_client: TestClient,
    ) -> None:
        """不存在的 strategy_id 不断流：返回带 error 的条目。"""
        response = seeded_client.post(
            "/api/strategies/compare",
            json={"strategy_ids": ["ghost-1"], "dataset_id": "BTCUSDT_4h"},
        )
        assert response.status_code == 200
        results = response.json()["results"]
        assert len(results) == 1
        assert results[0]["strategy_id"] == "ghost-1"
        assert results[0]["error"] == "Strategy not found"

    def test_corrupt_dna_yields_error_item(
        self, seeded_client: TestClient,
    ) -> None:
        """存量 dna_json 损坏：该条目记 error，其余正常（不 500）。"""
        from core.persistence.db_ext import save_strategy
        db_path = seeded_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="bad-dna", dna_json="{corrupt",
            symbol="BTCUSDT", timeframe="4h", name="Broken",
        )
        save_strategy(
            db_path, strategy_id="ok-dna", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="Fine",
        )
        response = seeded_client.post(
            "/api/strategies/compare",
            json={"strategy_ids": ["bad-dna", "ok-dna"],
                  "dataset_id": "BTCUSDT_4h"},
        )
        assert response.status_code == 200
        results = response.json()["results"]
        by_id = {r["strategy_id"]: r for r in results}
        assert by_id["bad-dna"].get("error"), "坏 DNA 条目应记 error"
        assert not by_id["ok-dna"].get("error"), "正常条目不应有 error"

    def test_main_path_no_full_parquet_load(
        self, seeded_client: TestClient,
    ) -> None:
        """CARD-26 后行为锚定：主路径 compare 自身零全量 load_parquet。

        计数口径：单策略请求恰 1 次——core load_and_prepare_df 内部的
        必需装载（spy 覆盖 core.data.storage.load_parquet，mtf_loader
        函数内 import 同一入口）；旧实现为 2 次/策略（compare 无条件
        预读 + core 内部），白读减半消除即断言 1。
        """
        from core.persistence.db_ext import save_strategy
        db_path = seeded_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="cmp-1", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="CmpOne",
        )
        from unittest.mock import patch
        from core.data import storage
        with patch.object(
            storage, "load_parquet", wraps=storage.load_parquet,
        ) as load_spy:
            response = seeded_client.post(
                "/api/strategies/compare",
                json={"strategy_ids": ["cmp-1"], "dataset_id": "BTCUSDT_4h"},
            )
        assert response.status_code == 200
        assert not response.json()["results"][0].get("error")
        # compare 端点自身的全量预读已消除（旧 2 次 → 现 1 次 core 必需）
        assert load_spy.call_count == 1, (
            f"单策略主路径应仅剩 core 内部 1 次装载，实调 {load_spy.call_count} 次"
        )
        # 落库统计仍正确（单列读路径与延迟装载解耦）
        from core.persistence.db_ext import list_backtest_results
        rows = list_backtest_results(
            db_path, strategy_id="cmp-1", run_source="lab", limit=1,
        )
        assert rows and rows[0]["data_start"]


class TestCrudGaps:
    def test_list_invalid_sort_by_400(self, api_client: TestClient) -> None:
        """非法 sort_by → 400（repository 白名单 ValueError 上抛）。"""
        response = api_client.get(
            "/api/strategies", params={"sort_by": "evil_column"},
        )
        assert response.status_code == 400

    def test_update_draft_dna_locked_409(self, api_client: TestClient) -> None:
        """M4 写锁：draft 状态策略 PUT dna → 409 RESEARCH_DRAFT_IMMUTABLE。"""
        from core.persistence.db_ext import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="draft-1", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="DraftStrat", status="draft",
        )
        response = api_client.put(
            "/api/strategies/draft-1", json={"dna": _inline_dna_dict()},
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "RESEARCH_DRAFT_IMMUTABLE"

    def test_update_draft_metadata_allowed(self, api_client: TestClient) -> None:
        """draft 状态仅锁 DNA：name/tags/notes 元数据仍可更新。"""
        from core.persistence.db_ext import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="draft-2", dna_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h", name="DraftStrat", status="draft",
        )
        response = api_client.put(
            "/api/strategies/draft-2", json={"notes": "still editable"},
        )
        assert response.status_code == 200
        assert response.json()["notes"] == "still editable"

    def test_get_strategy_corrupt_dna_returns_null_dna(
        self, api_client: TestClient,
    ) -> None:
        """存量 dna_json 损坏：GET 返回 dna=None（不 500）。"""
        from core.persistence.db_ext import save_strategy
        db_path = api_client.app.state.db_path
        save_strategy(
            db_path, strategy_id="bad-2", dna_json="{corrupt",
            symbol="BTCUSDT", timeframe="4h", name="Broken2",
        )
        response = api_client.get("/api/strategies/bad-2")
        assert response.status_code == 200
        assert response.json()["dna"] is None

    def test_gene_signature_fallback_derives_from_dna(self) -> None:
        """_gene_signature_or_derive：行内签名为空时从 dna_json 现算。

        现状记录：from_json 往返会补全 layers（execution 层入 L: 段），
        故兜底签名与活对象 property 存在 L: 段差异——以同一路径
        （from_json 后再取签名）为一致性基准。
        """
        from api.routes.strategies.crud import _gene_signature_or_derive
        from core.strategy.dna import StrategyDNA
        dna = make_dna()
        row = {"gene_signature": None, "dna_json": dna.to_json()}
        expected = StrategyDNA.from_json(row["dna_json"]).gene_signature
        assert _gene_signature_or_derive(row) == expected
        assert "L:" in _gene_signature_or_derive(row)

    def test_gene_signature_fallback_corrupt_dna_none(self) -> None:
        """dna_json 也损坏时兜底返回 None（不抛异常）。"""
        from api.routes.strategies.crud import _gene_signature_or_derive
        assert _gene_signature_or_derive(
            {"gene_signature": None, "dna_json": "{corrupt"},
        ) is None
