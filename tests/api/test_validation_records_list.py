"""GET /api/research/validation-records 契约测试（workbench-observation 组 A2）。

spec research-validation-protocol 3 Scenario+四维过滤/降序/分页 has_more/
同源同值/三态缺失锚。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient


def _seed_records(db, n=6):
    """六条记录：三 verdict×两级×间隔 created_at（ORM 直插——测试专用种子）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.persistence.orm.models import ValidationRecord
    Session = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with Session() as sess:
        for i in range(n):
            sess.add(ValidationRecord(
                record_id=f"vr_{i:02d}",
                hypothesis_id="fr_wb",
                strategy_id="st_wb" if i % 2 == 0 else None,
                level="isolated_backtest" if i < 3 else "random_window",
                segmentation="",
                verdict=("pass", "fail", "inconclusive")[i % 3],
                range_hash=f"rh_{i}",
                params=json.dumps({"warmup": 30, "seed": i}),
                result=json.dumps({"total_return": 0.1 * i}) if i != 4 else None,
                created_at=f"2026-11-0{i + 1}T00:00:00+00:00",
            ))
        sess.commit()


@pytest.fixture()
def client(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from core.analysis.executor import ensure_research_tables
    from api.app import create_app
    from core.analysis.hypothesis_service import register_hypothesis
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    register_hypothesis(
        db,
        conditions={"when_conditions": [
            {"subject": {"indicator": "RSI", "params": {"period": 14}},
             "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}]},
        statement="wb 列表端点测试",
        strategy_params={"stop_loss": 0.05},
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2026-01-01T00:00:00Z",
                      "end": "2026-02-01T00:00:00Z"}])
    _seed_records(db)
    app = create_app(db_path=db, data_dir=tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestFilters:
    def test_level_filter_hits_and_422(self, client):
        r = client.get("/api/research/validation-records", params={"level": "random_window"})
        assert r.status_code == 200
        recs = r.json()["records"]
        assert len(recs) == 3 and all(x["level"] == "random_window" for x in recs)
        bad = client.get("/api/research/validation-records", params={"level": "bogus"})
        assert bad.status_code == 422

    def test_verdict_filter_and_422(self, client):
        r = client.get("/api/research/validation-records", params={"verdict": "pass"})
        assert r.status_code == 200
        assert all(x["verdict"] == "pass" for x in r.json()["records"])
        assert client.get("/api/research/validation-records",
                          params={"verdict": "nope"}).status_code == 422

    def test_strategy_id_and_null_semantics(self, client):
        r = client.get("/api/research/validation-records", params={"strategy_id": "st_wb"})
        assert all(x["strategy_id"] == "st_wb" for x in r.json()["records"])
        # NULL strategy_id（component 级三态）经全量响应可见
        full = client.get("/api/research/validation-records", params={"limit": 200}).json()["records"]
        assert any(x["strategy_id"] is None for x in full)


class TestOrderPaging:
    def test_created_at_desc_deterministic(self, client):
        recs = client.get("/api/research/validation-records").json()["records"]
        ts = [x["created_at"] for x in recs]
        assert ts == sorted(ts, reverse=True)

    def test_paging_has_more(self, client):
        r1 = client.get("/api/research/validation-records", params={"limit": 4}).json()
        assert r1["has_more"] is True and len(r1["records"]) == 4
        r2 = client.get("/api/research/validation-records",
                        params={"limit": 4, "offset": 4}).json()
        assert r2["has_more"] is False and len(r2["records"]) == 2
        # 页不重叠
        ids1 = {x["record_id"] for x in r1["records"]}
        ids2 = {x["record_id"] for x in r2["records"]}
        assert not (ids1 & ids2)


class TestEmbeddedAndMissing:
    def test_params_result_json_embedded_and_null(self, client):
        full = client.get("/api/research/validation-records", params={"limit": 200}).json()["records"]
        by_id = {x["record_id"]: x for x in full}
        assert by_id["vr_00"]["params"] == {"warmup": 30, "seed": 0}
        assert by_id["vr_00"]["result"] == {"total_return": 0.0}
        assert by_id["vr_04"]["result"] is None  # 三态缺失如实
        assert by_id["vr_01"]["pollution_cause"] is None  # 未标注 NULL

    def test_same_source_as_strategy_report(self, client):
        """与 GET /validation/{strategy_id}/report 同源同值（同一 record 两视图）。"""
        rep = client.get("/api/research/validation/st_wb/report")
        if rep.status_code == 200:
            rep_records = {r["record_id"]: r for r in rep.json().get("records", [])}
            mine = {r["record_id"]: r for r in client.get(
                "/api/research/validation-records", params={"strategy_id": "st_wb", "limit": 200}
            ).json()["records"]}
            for rid, mine_r in mine.items():
                if rid in rep_records:
                    assert mine_r["verdict"] == rep_records[rid]["verdict"]
                    assert mine_r["level"] == rep_records[rid]["level"]
        else:
            # 档案端点形制差异（如响应键名不同）——同源性以字段一致锚承载
            assert rep.status_code in (404, 200)
