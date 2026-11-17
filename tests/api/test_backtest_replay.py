"""回放回测端点测试（add-replay-backtest-mcp 任务 1.1）。

POST /api/research/backtest-replay：薄封装 ReplayRunner（进程内
直调），装载失败经 ResearchError 单源转结构化 4xx。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.api]

from core.data.storage import save_parquet
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from tests.helpers.data_factory import make_dna, make_ohlcv


@pytest.fixture()
def replay_env(tmp_path: Path):
    """合成数据+临时库+策略行+TestClient（create_app 参数化装配——
    沿 test_sandbox_validation_records_api 形制）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=600, freq="4h", seed=7, base_price=100.0)
    save_parquet(df, data_dir / "BTCUSDT_4h.parquet")
    db = tmp_path / "paper.db"
    init_db_ext(db)
    dna = make_dna()
    save_strategy(db, strategy_id="st_replay_api",
                  dna_json=dna.to_json(), symbol="BTCUSDT", timeframe="4h")
    from api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app(db_path=db, data_dir=data_dir)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield {"client": c, "data_dir": data_dir, "db": db, "df": df,
               "strategy_id": "st_replay_api"}


class TestBacktestReplayEndpoint:
    """端点冒烟+错误通道。"""

    def test_replay_returns_summary(self, replay_env):
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": replay_env["strategy_id"],
            "start": idx[-300].isoformat(), "end": idx[-1].isoformat(),
            "task_id": "api-replay-01"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["task_id"] == "api-replay-01"
        assert body["status"] in ("completed", "stopped")
        assert body["bars_processed"] > 0
        assert body["symbol"] == "BTCUSDT"
        assert "total_pnl" in body and "final_balance" in body
        # level=replay 记录写入（任务 3.1）：record_id 回显+表内可查
        assert body["record_id"].startswith("rec_")
        from core.persistence.repository import research as rr

        rec = rr.get_validation_record_by_id(
            replay_env["db"], body["record_id"])
        assert rec["level"] == "replay"
        assert rec["verdict"] == ""  # 不参与三态
        import json as _json

        result_json = _json.loads(rec["result"])
        assert result_json["replay"]["task_id"] == "api-replay-01"

    def test_strategy_not_found_structured_4xx(self, replay_env):
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": "st_missing",
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat()})
        assert resp.status_code == 404, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "STRATEGY_NOT_FOUND"

    def test_series_mode_rejected(self, replay_env):
        import dataclasses

        dna = make_dna()
        series_dna = dataclasses.replace(
            dna, risk_genes=dataclasses.replace(
                dna.risk_genes, sl_mode="series",
                sl_series_source={"indicator": "EMA",
                                  "params": {"period": 20}}))
        save_strategy(replay_env["db"], strategy_id="st_series_api",
                      dna_json=series_dna.to_json(), symbol="BTCUSDT",
                      timeframe="4h", status="draft")
        idx = replay_env["df"].index
        resp = replay_env["client"].post("/api/research/backtest-replay", json={
            "strategy_id": "st_series_api",
            "start": idx[-100].isoformat(), "end": idx[-1].isoformat()})
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "TRADING_INCOMPATIBLE"
