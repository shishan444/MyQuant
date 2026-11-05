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
    # 档案端点前置实体（Scenario 同源同值：GET /validation/{id}/report
    # 对不存在的 strategy 404——无实体则两视图交集不可构造）
    from core.persistence.repository.strategy import save_strategy
    save_strategy(db, strategy_id="st_wb", dna_json="{}", symbol="BTCUSDT",
                  timeframe="4h", gene_signature="sig_wb_test")
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
        c._db = db  # type: ignore[attr-defined]
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

    def test_invalid_value_structured_error_body(self, client):
        """Scenario 非法过滤值：422 结构化拒绝沿 research 错误通道形制
        （detail 三段 code/message/detail 定位字段——非裸串）。"""
        r = client.get("/api/research/validation-records",
                       params={"verdict": "promoted"})  # spec Scenario 原值
        assert r.status_code == 422
        d = r.json()["detail"]
        assert d["code"] == "RESEARCH_PARAM_INVALID"
        assert d["detail"]["field"] == "verdict"
        assert d["detail"]["value"] == "promoted"

    def test_strategy_id_and_null_semantics(self, client):
        r = client.get("/api/research/validation-records", params={"strategy_id": "st_wb"})
        assert all(x["strategy_id"] == "st_wb" for x in r.json()["records"])
        # NULL strategy_id（component 级三态）经全量响应可见
        full = client.get("/api/research/validation-records", params={"limit": 200}).json()["records"]
        assert any(x["strategy_id"] is None for x in full)

    def test_hypothesis_id_filter(self, client):
        """spec 四维筛选「假设 ID」维：命中该假设记录、排除其他假设
        （upgrade-validation-records-list tester-2 补齐——六维中
        hypothesis_id 此前无直接过滤用例）。"""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import ValidationRecord
        S = sessionmaker(bind=create_engine(f"sqlite:///{client._db}"))
        with S() as sess:
            sess.add(ValidationRecord(
                record_id="vr_other", hypothesis_id="fr_other",
                strategy_id="st_wb", level="isolated_backtest",
                segmentation="", verdict="pass", range_hash="rh_other",
                params="{}", result="{}",
                created_at="2026-11-20T00:00:00+00:00"))
            sess.commit()
        mine = client.get("/api/research/validation-records",
                          params={"hypothesis_id": "fr_wb"}).json()
        assert mine["total"] == 6
        assert all(x["hypothesis_id"] == "fr_wb" for x in mine["records"])
        other = client.get("/api/research/validation-records",
                           params={"hypothesis_id": "fr_other"}).json()
        assert [x["record_id"] for x in other["records"]] == ["vr_other"]


class TestOrderPaging:
    def test_created_at_desc_deterministic(self, client):
        recs = client.get("/api/research/validation-records").json()["records"]
        ts = [x["created_at"] for x in recs]
        assert ts == sorted(ts, reverse=True)

    def test_verdict_three_states_all_present(self, client):
        """Scenario「最近验证结论消费」：verdict 字段呈现三态——
        全量响应 pass/fail/inconclusive 三值均如实可见（种子 6 条
        三态循环各 2，降序时间流不吞任何一态）。"""
        recs = client.get("/api/research/validation-records",
                          params={"limit": 200}).json()["records"]
        verdicts = {x["verdict"] for x in recs}
        assert verdicts == {"pass", "fail", "inconclusive"}

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

    def test_offset_beyond_total_empty_no_more(self, client):
        """分页边界：offset 越过全量基数 → 空页且无后续（非报错）。"""
        r = client.get("/api/research/validation-records",
                       params={"offset": 100}).json()
        assert r["records"] == []
        assert r["has_more"] is False


class TestFilterPagingInteraction:
    """特征网：level/verdict 行级过滤与分页窗口的组合语义（现状记录）。

    spec 只定义了四维过滤与分页各自行为，未定义组合口径；实现现状
    （records_list.py 注释自述）：页窗口按过滤前 created_at 时间流推进，
    level/verdict 在窗口内命中过滤；has_more 按过滤前段探测（limit+1）。
    种子时间流（降序 vr_05→vr_00）：isolated_backtest=vr_00-02（最老
    三行），random_window=vr_03-05；verdict 3 循环（vr_03=pass）。
    """

    def test_level_filter_page_empty_but_has_more(self, client):
        """B2 过滤基数分页（upgrade-validation-records-list 4.1 同批
        核改——旧行级过滤「页空而 has_more=True」行为退役）：level
        SQL 下推后当页即命中、total=过滤基数、页满恒 limit。"""
        r1 = client.get("/api/research/validation-records",
                        params={"level": "isolated_backtest", "limit": 3}).json()
        # 六维口径：isolated 基数 3（vr_00/01/02）——页 1 即满页命中
        assert [x["record_id"] for x in r1["records"]] == \
               ["vr_02", "vr_01", "vr_00"]
        assert r1["total"] == 3
        assert r1["has_more"] is False
        # 无命中 level：页空且 has_more=False（total=0 同步过滤基数）
        r0 = client.get("/api/research/validation-records",
                        params={"level": "component", "limit": 3}).json()
        assert r0["records"] == [] and r0["total"] == 0
        assert r0["has_more"] is False

    def test_level_filter_cross_page_collect_complete(self, client):
        """跨页遍历完整性：has_more 驱动步进至终页，命中总数==该级基数。"""
        collected: list[str] = []
        offset = 0
        while True:
            page = client.get("/api/research/validation-records",
                              params={"level": "isolated_backtest",
                                      "limit": 2, "offset": offset}).json()
            collected.extend(x["record_id"] for x in page["records"])
            if not page["has_more"]:
                break
            offset += 2
        assert sorted(collected) == ["vr_00", "vr_01", "vr_02"]

    def test_combined_level_verdict_filter(self, client):
        """组合维（level AND verdict）：两维同传交集命中，非任一满足。"""
        r = client.get("/api/research/validation-records",
                       params={"level": "random_window", "verdict": "pass"}).json()
        assert [x["record_id"] for x in r["records"]] == ["vr_03"]


class TestEmbeddedAndMissing:
    def test_bad_json_embedded_as_none_and_scalar_wrapped(self, client):
        """坏 JSON 嵌入兜底（阶段四闸门 missing 收口）：坏串→None 如实
        （不捏造不抛）；非 dict 标量 JSON→{"value": v} 包装。"""
        from sqlalchemy import create_engine, update as sa_update
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import ValidationRecord
        db = client._db
        S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
        with S() as sess:
            sess.add(ValidationRecord(
                record_id="vr_bad", hypothesis_id="fr_wb", strategy_id=None,
                level="isolated_backtest", segmentation="", verdict="pass",
                range_hash="rh_bad", params="{not-json",
                result='"123"',  # 合法 JSON 但非 dict（标量）
                created_at="2026-11-10T00:00:00+00:00"))
            sess.commit()
        by_id = {x["record_id"]: x for x in client.get(
            "/api/research/validation-records", params={"limit": 200}
        ).json()["records"]}
        assert by_id["vr_bad"]["params"] is None      # 坏串如实 None
        assert by_id["vr_bad"]["result"] == {"value": "123"}  # 标量包装

    def test_params_result_json_embedded_and_null(self, client):
        full = client.get("/api/research/validation-records", params={"limit": 200}).json()["records"]
        by_id = {x["record_id"]: x for x in full}
        assert by_id["vr_00"]["params"] == {"warmup": 30, "seed": 0}
        assert by_id["vr_00"]["result"] == {"total_return": 0.0}
        assert by_id["vr_04"]["result"] is None  # 三态缺失如实
        assert by_id["vr_01"]["pollution_cause"] is None  # 未标注 NULL

    def test_same_source_as_strategy_report(self, client):
        """与 GET /validation/{strategy_id}/report 同源同值（同一 record 两视图）。

        修正记录：原版在 report 404（strategy 实体未种）与交集空时
        零断言恒绿——Scenario 同源同值实际未被证明。现 fixture 已种
        st_wb 实体，两视图交集由种子确定（vr_00/02 isolated、vr_04
        random_window），硬断言共有字段一致。
        """
        rep = client.get("/api/research/validation/st_wb/report")
        assert rep.status_code == 200  # 实体已种——404 即种子失效
        rep_iso = {r["record_id"]: r for r in rep.json()["isolated_backtests"]}
        rep_win_ids = {r["record_id"] for r in rep.json()["random_window_summaries"]}
        mine = {r["record_id"]: r for r in client.get(
            "/api/research/validation-records",
            params={"strategy_id": "st_wb", "limit": 200}).json()["records"]}
        # 交集非空（同源性可证明）+ 两视图 record 归属一致
        assert set(mine) == (set(rep_iso) | rep_win_ids)
        assert {"vr_00", "vr_02"} <= set(rep_iso)
        assert "vr_04" in rep_win_ids
        # 共有字段同值：isolated 行 verdict 两视图一致
        for rid in ("vr_00", "vr_02"):
            assert mine[rid]["verdict"] == rep_iso[rid]["verdict"]


class TestStagedLevelFilter:
    """B3 值域修正（add-hypothesis-cards 任务 1，tester A3 组）：
    _LEVELS 补 staged_validation——staged 过滤从 422 转绿；五值全合法
    （composite/component 死值域保留：200 空结果非 422——契约保留
    死值域，与数据分布无关）。"""

    def _insert_staged(self, db):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from core.persistence.orm.models import ValidationRecord
        Session = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
        with Session() as sess:
            sess.add(ValidationRecord(
                record_id="vr_staged_1", hypothesis_id="fr_wb",
                strategy_id="st_wb", level="staged_validation",
                segmentation="auto", verdict="pass", range_hash="rh_st1",
                params="{}", result="{}", created_at="2026-11-02T00:00:00+00:00"))
            sess.commit()

    def test_staged_filter_returns_staged_rows(self, client):
        """A3-1：staged_validation 过滤 200 且行全 staged 级（行集与 DB
        对账——瞬时计数不断言）。"""
        self._insert_staged(client._db)
        r = client.get("/api/research/validation-records",
                       params={"level": "staged_validation"})
        assert r.status_code == 200
        body = r.json()
        assert body["records"], "staged 记录在库——过滤不得空"
        assert all(rec["level"] == "staged_validation"
                   for rec in body["records"])

    @pytest.mark.parametrize("level,expect_rows", [
        ("isolated_backtest", True), ("random_window", True),
        ("staged_validation", False),  # 未插种子时该客户端组为空但合法
        ("composite", False), ("component", False),
    ])
    def test_five_levels_all_legal_no_422(self, client, level, expect_rows):
        """A3-2：五值枚举全 200（死值域 composite/component=200 空结果
        非 422——值域内保留）。expect_rows 仅标注该组种子形态，断言锚
        是状态码与空集结构。"""
        r = client.get("/api/research/validation-records",
                       params={"level": level})
        assert r.status_code == 200
        body = r.json()
        if not expect_rows:
            # 本客户端组未插该级种子（staged 逐用例自插隔离）——空集合法
            assert all(rec["level"] == level for rec in body["records"])


class TestQMaxLength:
    """TD-06：q 搜索词长度上限 200（FastAPI 原生 validation 形制——
    与 limit ge/le 同族传输层约束，不走 RESEARCH_ 结构化码；负向断言
    防形制漂移）。"""

    def test_q_200_chars_accepted(self, client):
        r = client.get("/api/research/validation-records",
                       params={"q": "a" * 200})
        assert r.status_code == 200
        assert isinstance(r.json()["records"], list)

    def test_q_201_chars_rejected_native_422(self, client):
        r = client.get("/api/research/validation-records",
                       params={"q": "a" * 201})
        assert r.status_code == 422
        body = r.json()
        # FastAPI 原生 validation error：detail 为数组形
        assert isinstance(body["detail"], list), body
        # 负向断言：不走 RESEARCH_ 结构化码（两形制并存边界）
        assert "code" not in body
        assert not any("RESEARCH_" in str(item) for item in body["detail"])
