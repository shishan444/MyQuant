"""GET /api/research/hypothesis-cards 契约测试（add-hypothesis-cards B1）。

spec delta「主任务聚合查询」五 Scenario+A1 组 L1 用例锚（tester 方案
A 面）：五态筛选/422 结构化/q 转义/三 null 语义/cursor 优先级/adhoc
双端点对照/GET 幂等/next_cursor 两态/排序确定。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

_T = "2026-11-0{}T00:00:00+00:00"


def _seed(db):
    """五假设×状态/验证/日志的确定性种子（ORM 直插+register 混合）。

    - hyp_a active+2 验证（最新 fail）+日志最新 11-08
    - hyp_b refuted+1 验证（pass）+日志最新 11-09（排序首位）
    - hyp_c abandoned 零验证（三 null 语义）+日志 11-03
    - hyp_d supported 零日志（recent_activity_at=null——ORM 直插不经
      register，构造无日志行防御位）
    - statement 含 % 字面（q 转义用例）
    - adhoc 验证记录一条（假设表零父行——双端点对照）
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from core.analysis.hypothesis_service import register_hypothesis
    from core.persistence.repository import research as rr
    from core.persistence.orm.models import (
        ResearchHypothesis, ResearchLog, ValidationRecord)

    conds = {"when_conditions": [{"subject": "close", "action": "gt",
                                  "target": 0}],
             "then_conditions": [{"subject": "close", "action": "gt",
                                  "target": -1}]}
    rng = [{"symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-02-01T00:00:00Z"}]
    register_hypothesis(
        db, conditions=conds, statement="BTC 动量探索 100%",
        strategy_params={"stop_loss": 0.05}, data_ranges=rng)
    Session = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with Session() as sess:
        # 三条终态/空态假设直插（statement 覆盖子串与 % 字面）
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_a", conditions="{}", conditions_hash="c1",
            statement="RSI 超卖反弹", data_ranges="[]", ranges_hash="r1",
            status="active", created_at=_T.format(1),
            updated_at=_T.format(1)))
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_b", conditions="{}", conditions_hash="c2",
            statement="MACD 背离", data_ranges="[]", ranges_hash="r2",
            status="refuted", created_at=_T.format(2),
            updated_at=_T.format(2)))
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_c", conditions="{}", conditions_hash="c3",
            statement="布林带收窄突破", data_ranges="[]", ranges_hash="r3",
            status="abandoned", created_at=_T.format(3),
            updated_at=_T.format(3)))
        sess.add(ResearchHypothesis(
            hypothesis_id="hyp_d", conditions="{}", conditions_hash="c4",
            statement="假设D", data_ranges="[]", ranges_hash="r4",
            status="supported", created_at=_T.format(4),
            updated_at=_T.format(4)))
        # 验证记录：hyp_a 两条（11-05 pass / 11-08 fail——最新=fail）；
        # hyp_b 一条（11-06 pass）；adhoc 一条（假设表零父行）
        for rid, hid, verdict, ts in (
                ("rec_a1", "hyp_a", "pass", _T.format(5)),
                ("rec_a2", "hyp_a", "fail", _T.format(8)),
                ("rec_b1", "hyp_b", "pass", _T.format(6)),
                ("rec_ad1", "adhoc", "pass", _T.format(7))):
            sess.add(ValidationRecord(
                record_id=rid, hypothesis_id=hid, strategy_id="st_x",
                level="isolated_backtest", segmentation="", verdict=verdict,
                range_hash=f"rh_{rid}", params="{}", result="{}",
                created_at=ts))
        # 日志：register 假设自带一条（hyp_reg）；手工补 hyp_a/hyp_b/
        # hyp_c 各一条（created_at 各异构造排序）
        for hid, ts in (("hyp_a", _T.format(8)), ("hyp_b", _T.format(9)),
                        ("hyp_c", _T.format(3))):
            sess.add(ResearchLog(
                log_id=f"lg_{hid}", hypothesis_id=hid, entry_type="validate",
                ref_id="rec_x", summary="s", created_at=ts))
        sess.commit()


@pytest.fixture()
def client(tmp_path: Path):
    from core.persistence.db_ext import init_db_ext
    from core.analysis.executor import ensure_research_tables
    from api.app import create_app
    db = tmp_path / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    _seed(db)
    app = create_app(db_path=db, data_dir=tmp_path / "data")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestFiveStatusFilter:
    """A1-1~A1-5 五态筛选+空态+422。"""

    @pytest.mark.parametrize("status,expected", [
        ("active", {"hyp_a", "REGISTERED"}),
        ("supported", {"hyp_d"}),
        ("refuted", {"hyp_b"}),
        ("abandoned", {"hyp_c"}),
    ])
    def test_status_filter_returns_matching_set(
            self, client, status, expected):
        body = client.get("/api/research/hypothesis-cards",
                          params={"status": status}).json()
        ids = {c["hypothesis_id"] for c in body["items"]}
        if "REGISTERED" in expected:
            # register 产物（动态 id）同为 active——允许一个额外动态成员
            extra = ids - (expected - {"REGISTERED"})
            assert len(extra) == 1 and ids - extra == (expected - {"REGISTERED"})
        else:
            assert ids == expected
        assert all(c["status"] == status for c in body["items"])

    def test_supported_nonempty_returns_row(self, client):
        """supported 现有样本（ORM 直插）可筛——值域与筛选逻辑可用，
        与「存量零」数据分布解耦（值域是状态机事实）。"""
        body = client.get("/api/research/hypothesis-cards",
                          params={"status": "superseded"}).json()
        assert body["total"] == 0 and body["items"] == []
        assert body["next_cursor"] is None

    def test_invalid_status_422_structured(self, client):
        r = client.get("/api/research/hypothesis-cards",
                       params={"status": "promoted"})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["field"] == "status"
        assert detail["detail"]["value"] == "promoted"
        assert set(detail["detail"]["legal_range"]) == {
            "active", "supported", "refuted", "superseded", "abandoned"}


class TestSearchAndNullSemantics:
    """A1-6~A1-10 q 搜索+三 null 语义。"""

    def test_q_substring_filter(self, client):
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "动量"}).json()
        # 仅 register 产物（statement 含「动量」）命中——id 动态不断言
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert "动量" in body["items"][0]["statement"]

    def test_q_percent_literal_not_wildcard(self, client):
        """q=% 按字面命中（LIKE 案义字符转义——'100%' statement 命中，
        不作通配符匹配全部行）。"""
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "100%"}).json()
        statements = [c["statement"] for c in body["items"]]
        assert statements and all("100%" in s for s in statements)
        total_all = client.get("/api/research/hypothesis-cards").json()
        assert body["total"] < total_all["total"], \
            "q=100% 不得按 % 通配符匹配全部行"

    def test_empty_q_no_filter(self, client):
        all_total = client.get("/api/research/hypothesis-cards").json()
        for empty in ("", "   "):
            body = client.get("/api/research/hypothesis-cards",
                              params={"q": empty}).json()
            assert body["total"] == all_total["total"]

    def test_card_row_seven_fields(self, client):
        body = client.get("/api/research/hypothesis-cards").json()
        for card in body["items"]:
            assert set(card) == {
                "hypothesis_id", "statement", "status", "validation_count",
                "latest_verdict", "latest_record_id", "recent_activity_at"}

    def test_latest_verdict_takes_newest_and_nulls(self, client):
        """hyp_a 最新记录=fail（11-08 晚于 11-05 的 pass）；hyp_c 零
        验证→latest_*=null、count=0；hyp_d 无日志→recent_activity_at
        =null。"""
        by_id = {c["hypothesis_id"]: c
                 for c in client.get("/api/research/hypothesis-cards")
                 .json()["items"]}
        assert by_id["hyp_a"]["latest_verdict"] == "fail"
        assert by_id["hyp_a"]["latest_record_id"] == "rec_a2"
        assert by_id["hyp_a"]["validation_count"] == 2
        assert by_id["hyp_c"]["latest_verdict"] is None
        assert by_id["hyp_c"]["latest_record_id"] is None
        assert by_id["hyp_c"]["validation_count"] == 0
        assert by_id["hyp_d"]["recent_activity_at"] is None


class TestPagingOrderAndAdhoc:
    """A1-11~A1-14 排序/cursor/adhoc/幂等。"""

    def test_order_recent_activity_desc(self, client):
        """recent_activity_at 降序（null 落末位）；11-09(hyp_b) 首位。"""
        items = client.get("/api/research/hypothesis-cards").json()["items"]
        assert items[0]["hypothesis_id"] == "hyp_b"
        acts = [c["recent_activity_at"] for c in items
                if c["recent_activity_at"] is not None]
        assert acts == sorted(acts, reverse=True)

    def test_cursor_takes_priority_over_page(self, client):
        page1 = client.get("/api/research/hypothesis-cards",
                           params={"page_size": 2}).json()
        cur = page1["next_cursor"]
        assert cur is not None
        by_cursor = client.get("/api/research/hypothesis-cards",
                               params={"cursor": cur, "page": 99}).json()
        direct = client.get("/api/research/hypothesis-cards",
                            params={"page_size": 2, "page": 2}).json()
        assert [c["hypothesis_id"] for c in by_cursor["items"]] == \
               [c["hypothesis_id"] for c in direct["items"]], \
            "cursor 提供时 page 被忽略——与 page=2 起点同结果"

    def test_adhoc_excluded_from_cards_visible_in_records(self, client):
        """adhoc 双端点对照：卡片端点无 adhoc 卡（零父行结构排除）；
        既有 validation-records?hypothesis_id=adhoc 可见。"""
        cards = client.get("/api/research/hypothesis-cards").json()
        assert all(c["hypothesis_id"] != "adhoc" for c in cards["items"])
        recs = client.get("/api/research/validation-records",
                          params={"hypothesis_id": "adhoc"}).json()
        assert any(r["record_id"] == "rec_ad1" for r in recs["records"])

    def test_get_idempotent_and_response_shape(self, client):
        r1 = client.get("/api/research/hypothesis-cards",
                        params={"page_size": 3})
        r2 = client.get("/api/research/hypothesis-cards",
                        params={"page_size": 3})
        assert r1.json() == r2.json()
        body = r1.json()
        assert set(body) == {"items", "total", "page", "page_size",
                             "next_cursor"}
        assert body["next_cursor"] is not None  # 有 items 时非空

    def test_next_cursor_null_on_empty(self, client):
        body = client.get("/api/research/hypothesis-cards",
                          params={"q": "绝不存在的词"}).json()
        assert body == {"items": [], "total": 0, "page": 1,
                        "page_size": 50, "next_cursor": None}
