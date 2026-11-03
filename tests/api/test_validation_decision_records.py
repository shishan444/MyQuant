"""be-validation-decision-records 三面测试（迁移/写入/透传）。

D6 决策理由列：新验证逐笔含 decision_json（可得事实快照——trigger
如实 null）；存量行 NULL→None 透传；迁移可升级（守卫形）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]


class TestMigration:
    def test_upgrade_adds_column_nullable(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from sqlalchemy import create_engine, inspect
        db = tmp_path / "m.db"
        init_db_ext(db)
        insp = inspect(create_engine(f"sqlite:///{db}"))
        cols = {c["name"] for c in insp.get_columns("vr_trade_detail")}
        assert "decision_json" in cols
        # nullable（存量 NULL 不炸）
        col = next(c for c in insp.get_columns("vr_trade_detail")
                   if c["name"] == "decision_json")
        assert col.get("nullable", True) is True


class TestDetailPayload:
    def test_new_trades_carry_decision_json(self):
        """写入端：_build_detail_payload 产物逐笔含 decision_json——
        可得事实快照（trigger null 如实+side/direction/status）。"""
        import pandas as pd
        from core.analysis.validation_service import _build_detail_payload
        detail = {
            "equity": pd.Series(
                [100.0, 101.0],
                index=pd.DatetimeIndex(
                    ["2026-01-01", "2026-01-02"], freq="D")),
            "trades_df": pd.DataFrame({
                "Entry Timestamp": [pd.Timestamp("2026-01-01")],
                "Exit Timestamp": [pd.Timestamp("2026-01-02")],
                "Avg Entry Price": [100.0], "Avg Exit Price": [105.0],
                "Size": [1.0], "PnL": [5.0],
                "Direction": ["Long"], "Status": ["Closed Trade"]}),
            "init_cash": 100000.0, "fee": 0.001, "slippage": 0.0005,
        }
        out = _build_detail_payload(
            detail, exec_ref="main", exec_index=0,
            range_start="2026-01-01", range_end="2026-01-03",
            timeframe="1d")
        assert len(out["trades"]) == 1
        dec = json.loads(out["trades"][0]["decision_json"])
        assert dec["trigger"] is None  # 条件级不可得——如实 null 禁捏造
        assert dec["side"] == "entry"
        assert dec["direction"] == "Long"
        assert dec["status"] == "Closed Trade"


class TestTransmit:
    def _seed(self, db_path: Path):
        """造记录+两态 trades（一笔含 decision_json/一笔 NULL 存量形）。"""
        from core.persistence.repository import research as rr
        rr.save_validation_with_log(
            db_path, record_id="rec_d6_1", hypothesis_id="hyp_d6",
            strategy_id=None, level="isolated_backtest",
            params="{}", result=None, verdict="inconclusive",
            range_hash="rh",
            detail=[{
                "exec_ref": "main", "exec_index": 0,
                "range_start": "2026-01-01", "range_end": "2026-01-02",
                "timeframe": "1d",
                "equity": {
                    "exec_ref": "main", "exec_index": 0,
                    "range_start": "2026-01-01", "range_end": "2026-01-02",
                    "timeframe": "1d",
                    "grid_aligned": 1, "start": "2026-01-01", "length": 2,
                    "timestamps": None, "values": "[100,101]"},
                "trades": [
                    {"exec_ref": "main", "exec_index": 0, "seq": 0,
                     "range_start": "2026-01-01", "range_end": "2026-01-02",
                     "entry_time": "2026-01-01", "exit_time": "2026-01-02",
                     "entry_price": 100.0, "exit_price": 105.0,
                     "size": 1.0, "direction": "Long", "pnl": 5.0,
                     "decision_json": json.dumps(
                         {"trigger": None, "side": "entry",
                          "direction": "Long"})},
                    {"exec_ref": "main", "exec_index": 0, "seq": 1,
                     "range_start": "2026-01-01", "range_end": "2026-01-02",
                     "entry_time": "2026-01-02", "exit_time": None,
                     "entry_price": 105.0, "exit_price": 105.0,
                     "size": 1.0, "direction": "Long", "pnl": None,
                     "decision_json": None},  # 存量形（无理由留痕）
                ]}])
        return "rec_d6_1"

    def test_decision_two_state_transmit(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import research as rr
        db = tmp_path / "t.db"
        init_db_ext(db)
        rid = self._seed(db)
        execs = rr.get_validation_executions(db, rid)
        assert len(execs) == 1
        trades = execs[0]["trades"]
        # 新记录形：JSON 解析透传
        assert trades[0]["decision"] == {
            "trigger": None, "side": "entry", "direction": "Long"}
        # 存量形：NULL→None 如实
        assert trades[1]["decision"] is None

    def test_api_detail_carries_decision(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from api.app import create_app
        from fastapi.testclient import TestClient
        db = tmp_path / "t.db"
        init_db_ext(db)
        rid = self._seed(db)
        app = create_app(db_path=db, data_dir=tmp_path / "data")
        with TestClient(app) as c:
            r = c.get(f"/api/research/validation-records/{rid}/detail")
        assert r.status_code == 200
        body = r.json()
        assert body["detail_available"] is True
        tr = body["executions"][0]["trades"]
        assert tr[0]["decision"]["side"] == "entry"
        assert tr[1]["decision"] is None
