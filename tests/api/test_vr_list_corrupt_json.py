"""排序提取 JSON 先验防御测试（fix-audit-correctness D-01）。

result/params 列无机制保证恒 JSON——corrupt 行（裸串/坏 JSON）曾使
json_extract 直接求值抛 OperationalError 炸整端点（sort=total_return_desc
与 generalization 过滤面）。三表达式内嵌
json_valid 先验 CASE 后：corrupt 行提取值归 NULL（与「level 不匹配
→NULL」同 unsortable 语义），端点恒 200；合法行提取值逐字段不变。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.analysis.executor import ensure_research_tables
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr


def _seed_record(db, rid: str, level: str, *, result=None, params=None) -> None:
    """直插 validation_records 行（绕过 service，允许任意 result/params 形态）。"""
    import json
    from core.persistence.orm.engine import engine_for
    from sqlalchemy import text
    engine = engine_for(db)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records "
            "(record_id, hypothesis_id, strategy_id, level, segmentation, "
            "verdict, result, params, range_hash, created_at) VALUES "
            "(:rid, :hid, :sid, :level, '', :verdict, :result, :params, "
            ":rhash, '2026-11-15T00:00:00Z')"
        ), {
            "rid": rid, "hid": "hyp_corrupt", "sid": "st_corrupt",
            "level": level, "verdict": "pass", "rhash": f"rh-{rid}",
            "result": result if isinstance(result, str)
                      else json.dumps(result),
            "params": params if isinstance(params, str)
                      else json.dumps(params),
        })


@pytest.fixture()
def client(tmp_path):
    db = tmp_path / "corrupt.db"
    init_db_ext(db)
    ensure_research_tables(db)
    # 合法行（三型各一——提取值对照面）+ corrupt 行（result/params 裸串）
    _seed_record(db, "rec_iso_ok", "isolated_backtest",
                 result={"metrics": {"total_return": 0.05},
                         "symbol": "ETHUSDT", "timeframe": "4h"})
    _seed_record(db, "rec_iso_bad", "isolated_backtest",
                 result="not-json-at-all")
    _seed_record(db, "rec_rand_ok", "random_window",
                 result={"summary": {"worst_window": {
                     "metrics": {"total_return": -0.02}}}},
                 params={"symbol": "BTCUSDT", "timeframe": "1h"})
    _seed_record(db, "rec_rand_bad", "random_window",
                 result={"summary": {"worst_window": {
                     "metrics": {"total_return": -0.01}}}},
                 params="not-json-either")
    _seed_record(db, "rec_stg_ok", "staged_validation",
                 result={"summary": {"worst_segment": {
                     "total_return": 0.10}}},
                 params={"request_range": {
                     "symbol": "BTCUSDT", "timeframe": "4h"}})
    from api.app import create_app
    real_app = create_app(db_path=db, data_dir=tmp_path)
    with TestClient(real_app) as c:
        yield c


class TestCorruptJsonSortResilience:

    def test_corrupt_result_sort_total_return_desc_200(self, client):
        """corrupt result 行+sort=total_return_desc → 200 且 corrupt 行
        归 NULL 排序位（修复前 OperationalError 炸整端点）。"""
        r = client.get("/api/research/validation-records",
                       params={"sort": "total_return_desc"})
        assert r.status_code == 200, r.text
        ids = [row["record_id"] for row in r.json()["records"]]
        assert set(ids) == {"rec_iso_ok", "rec_iso_bad", "rec_rand_ok",
                            "rec_rand_bad", "rec_stg_ok"}
        # 按 result 提取值降序：stg 0.10 > iso 0.05 > rand_bad -0.01 >
        # rand_ok -0.02 > iso_bad（result corrupt→NULL 落尾）；
        # rand_bad 的 result 合法（-0.01 在场，仅 params corrupt 不影响
        # total_return 位）
        assert ids == ["rec_stg_ok", "rec_iso_ok", "rec_rand_bad",
                       "rec_rand_ok", "rec_iso_bad"], \
            f"corrupt result 行应归 NULL 落尾，实际序 {ids}"

    def test_corrupt_params_generalization_filter_200(self, client):
        """params corrupt（random 型）+generalization 过滤 → 200。

        _REC_SYMBOL_EXPR/_REC_TIMEFRAME_EXPR 的消费面是泛化过滤
        （_GENERALIZATION_EXPR and_ 条件——sort 枚举四值无 symbol/
        timeframe 形态，任务卡「symbol/timeframe 排序」系规划笔误，
        实锚为 generalization 请求面）；corrupt params 行提取值 NULL
        → isnot(None) 不中，被泛化条件滤除（保守漏判语义）。"""
        r = client.get("/api/research/validation-records",
                       params={"generalization": "true"})
        assert r.status_code == 200, r.text[:150]
        assert len(r.json()["records"]) == 0, \
            "corrupt params 行提取 NULL——泛化条件不命中（保守漏判）"

    def test_default_order_unchanged_with_corrupt_rows(self, client):
        """corrupt 行共存下默认序（time_desc）正常响应。"""
        r = client.get("/api/research/validation-records")
        assert r.status_code == 200
        assert len(r.json()["records"]) == 5

    def test_legal_extraction_values_unchanged(self, client):
        """合法行三型提取值与先验防御加入前逐字段一致（对照锚）。"""
        r = client.get("/api/research/validation-records",
                       params={"sort": "total_return_desc"})
        ordered = [row["record_id"] for row in r.json()["records"]]
        # 三型合法提取值各就各位：stg 路由 worst_segment=0.10 居首、
        # iso 路由 metrics.total_return=0.05 次之、rand 双行按
        # worst_window 降序 -0.01>-0.02——键位路由逐型正确（提取值
        # 与先验防御加入前一致以排序位证明）
        assert ordered == ["rec_stg_ok", "rec_iso_ok", "rec_rand_bad",
                           "rec_rand_ok", "rec_iso_bad"]
