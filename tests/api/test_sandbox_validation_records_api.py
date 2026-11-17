"""沙盒验证记录 API 面测试（add-sandbox-records-unified-flow B3——
TC-2-1/2-2/2-3/2-4：沙盒行入统一流的立即可查/三路筛选/混排同流/
level 六值扩面）。

真实跑沙盒产真行（模块级 fixture 一次共享）+直插正式行对照。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from core.analysis.executor import ensure_research_tables
from tests.helpers.vr_factory import insert_hypothesis
from core.analysis.validation_service import run_sandbox_backtest
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA)

_CONDS = {
    "when_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 35}],
    "then_conditions": [{"subject": {"indicator": "RSI", "params": {"period": 30}}, "action": "gt", "target": 60}],
}
_FORMAL_ID = "rec_formal_1"  # 直插正式对照行（created_at 过去——混排断言用）


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """模块级环境：库+合成数据+策略+假设+一次真实沙盒执行+正式对照行。"""
    tmp = tmp_path_factory.mktemp("sb_api")
    db = tmp / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    d = tmp / "market"
    d.mkdir()
    rng = np.random.default_rng(7)
    idx = pd.date_range("2022-10-01", periods=2000, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, 2000))
    pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                  "close": close, "volume": rng.uniform(100, 800, 2000),
                  "trades": 40}, index=idx).to_parquet(
        d / "BTCUSDT_4h.parquet")
    dna = StrategyDNA(
        signal_genes=[SignalGene("RSI", {"period": 14},
                                 SignalRole.ENTRY_TRIGGER, "rsi_14",
                                 {"type": "lt", "threshold": 35}),
                      SignalGene("RSI", {"period": 14},
                                 SignalRole.EXIT_TRIGGER, "rsi_14",
                                 {"type": "gt", "threshold": 1e9})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes())
    save_strategy(db, strategy_id="st_api", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h")
    hid = insert_hypothesis(
        db, conditions=_CONDS, statement="API 统一流测试",
        data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                      "start": "2023-01-01T00:00:00Z",
                      "end": "2023-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})
    out = run_sandbox_backtest(
        db, d, strategy_id="st_api", symbol="BTCUSDT", timeframe="4h",
        start="2023-03-01", end="2023-03-10", hypothesis_id=hid)
    # 正式对照行（直插——created_at 过去，降序流中排在沙盒行后）
    from sqlalchemy import text
    from core.persistence.orm.engine import engine_for
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, verdict, range_hash,"
            " params, result, created_at) VALUES"
            f" ('{_FORMAL_ID}', '{hid}', 'st_api', 'isolated_backtest',"
            " '', 'pass', 'rh_f1', '{}', '{}',"
            " '2026-01-01T00:00:00Z')"))
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, hid, out, db, d


class TestSandboxRecordsApi:
    def test_sandbox_record_immediately_visible(self, env):
        """TC-2-1：沙盒成功后立即 ?level=sandbox_backtest 与无过滤均含该行。"""
        client, hid, out, _db, _d = env
        r1 = client.get("/api/research/validation-records",
                        params={"level": "sandbox_backtest"})
        assert r1.status_code == 200
        assert out["record_id"] in {
            x["record_id"] for x in r1.json()["records"]}
        r2 = client.get("/api/research/validation-records")
        assert out["record_id"] in {
            x["record_id"] for x in r2.json()["records"]}

    def test_sandbox_filterable_three_ways(self, env):
        """TC-2-2：level/strategy_id/hypothesis_id 三路各自命中沙盒行。"""
        client, hid, out, _db, _d = env
        for params in ({"level": "sandbox_backtest"},
                       {"strategy_id": "st_api"},
                       {"hypothesis_id": hid}):
            r = client.get("/api/research/validation-records",
                           params=params)
            assert r.status_code == 200, params
            ids = {x["record_id"] for x in r.json()["records"]}
            assert out["record_id"] in ids, params

    def test_sandbox_rows_in_unified_stream(self, env):
        """TC-2-3：正式（早）+沙盒（晚）created_at 降序混排同一条记录流。"""
        client, hid, out, _db, _d = env
        r = client.get("/api/research/validation-records")
        recs = r.json()["records"]
        ids = [x["record_id"] for x in recs]
        assert out["record_id"] in ids and _FORMAL_ID in ids
        created = [x["created_at"] for x in recs]
        assert created == sorted(created, reverse=True)
        sbx = next(x for x in recs
                   if x["record_id"] == out["record_id"])
        assert sbx["level"] == "sandbox_backtest"
        assert sbx["verdict"] == ""  # 沙盒行 verdict 如实（不参与三态）

    def test_level_enum_six_values_422_face_unchanged(self, env):
        """TC-2-4：扩值不扩 422 面——新值 200、bogus 仍 422+结构化
        detail+值域清单含六值、旧五值逐位 200 不变。"""
        client, hid, out, _db, _d = env
        ok = client.get("/api/research/validation-records",
                        params={"level": "sandbox_backtest"})
        assert ok.status_code == 200
        bad = client.get("/api/research/validation-records",
                         params={"level": "bogus"})
        assert bad.status_code == 422
        det = bad.json()["detail"]
        assert det["code"] == "RESEARCH_PARAM_INVALID"
        assert det["detail"]["field"] == "level"
        # 值域披露含六值（message 内嵌值域清单——规整换行后匹配）
        assert "sandbox_backtest" in det["message"].replace("\n", "")
        for lv in ("isolated_backtest", "random_window",
                   "staged_validation", "composite", "component"):
            r = client.get("/api/research/validation-records",
                           params={"level": lv})
            assert r.status_code == 200, lv


def _insert(db: Path, record_id: str, hid: str, level: str,
            verdict: str, created_at: str) -> None:
    """直插正式对照行（沿 _seed_records 先例——测试专用种子）。"""
    from sqlalchemy import text
    from core.persistence.orm.engine import engine_for
    with engine_for(db).begin() as conn:
        conn.execute(text(
            "INSERT INTO validation_records (record_id, hypothesis_id,"
            " strategy_id, level, segmentation, verdict, range_hash,"
            " params, result, created_at) VALUES"
            f" ('{record_id}', '{hid}', 'st_api', '{level}', '',"
            f" '{verdict}', 'rh_{record_id}', '{{}}', '{{}}',"
            f" '{created_at}')"))


class TestVerdictCountsFourKeys:
    def test_verdict_counts_four_keys_three_pure(self, env):
        """TC-2-5：四键恒在场+sandbox 按 level 计数+三键纯净+互斥边界
        （G5/G10——verdict 筛选不命中沙盒行/composite 不落键）。"""
        client, hid, out, db, d = env
        # 补直插基线：module env 已有正式 pass×1（rec_formal_1）与真实
        # 沙盒×1；补 pass×1+fail×1+composite×1（verdict 非三态——死值域
        # 行不落键演示）
        _insert(db, "rec_c2", hid, "isolated_backtest", "pass",
                "2026-01-02T00:00:00Z")
        _insert(db, "rec_c3", hid, "random_window", "fail",
                "2026-01-03T00:00:00Z")
        _insert(db, "rec_c4", hid, "composite", "",
                "2026-01-04T00:00:00Z")
        base = client.get("/api/research/validation-records").json()
        assert base["verdict_counts"] == {
            "pass": 2, "fail": 1, "inconclusive": 0, "sandbox": 1}
        # 再真实跑沙盒 2 次 → 沙盒×3；三键纯净（沙盒插入前后不变）
        for _ in range(2):
            run_sandbox_backtest(
                db, d, strategy_id="st_api", symbol="BTCUSDT",
                timeframe="4h", start="2023-03-01", end="2023-03-10",
                hypothesis_id=hid)
        full = client.get("/api/research/validation-records").json()
        assert full["verdict_counts"] == {
            "pass": 2, "fail": 1, "inconclusive": 0, "sandbox": 3}
        assert full["total"] == 7  # 四键和 6<7——composite 不落键
        # level 互斥过滤：sandbox=0 恒在场
        iso = client.get("/api/research/validation-records",
                         params={"level": "isolated_backtest"}).json()
        assert iso["verdict_counts"] == {
            "pass": 2, "fail": 0, "inconclusive": 0, "sandbox": 0}
        assert iso["total"] == 2
        # verdict=pass 筛选：沙盒行不在 records 不在计数（G5）
        vp = client.get("/api/research/validation-records",
                        params={"verdict": "pass"}).json()
        assert not any(x["level"] == "sandbox_backtest"
                       for x in vp["records"])
        assert vp["verdict_counts"]["sandbox"] == 0
        assert vp["total"] == vp["verdict_counts"]["pass"]


class TestZeroChangeFaces:
    """组 6.2 零改动面显式用例（TC-2-6/2-7/2-8/TC-4-1/4-2——设计卡
    零改动面以验收防回归）。"""

    def test_sandbox_unsortable_in_total_return_sort(self, env):
        """TC-2-6：total_return_desc 沙盒行落尾+unsortable 计入；
        time_desc 语义键退化恒 0。"""
        client, hid, out, db, _d = env
        # 对照：插一条有 total_return 的正式行（有值行排前——沙盒
        # （无 worst 键位路由）与 rec_formal_1（result 无值）均落尾）
        _insert(db, "rec_v1", hid, "isolated_backtest", "pass",
                "2026-01-05T00:00:00Z")
        from sqlalchemy import text
        from core.persistence.orm.engine import engine_for
        with engine_for(db).begin() as conn:
            conn.execute(text(
                "UPDATE validation_records SET result="
                " '{\"metrics\": {\"total_return\": 0.5}}'"
                " WHERE record_id = 'rec_v1'"))
        out1 = client.get("/api/research/validation-records",
                          params={"sort": "total_return_desc",
                                  "limit": 200}).json()
        ids = [x["record_id"] for x in out1["records"]]
        # 共享库有多条无值行（rec_formal_1/composite 等）同落 unsortable
        # 段——「落尾」断言取段序语义：沙盒行位于确定有值对照行
        # （rec_v1——isolated 级 SQL worst 键位路由有值）之后；沙盒行
        # 响应 JSON 虽携带 total_return（ND2 完整 metrics），但 level
        # 无 worst 键位路由，SQL 侧判无值必落 unsortable 段
        assert ids.index(out["record_id"]) > ids.index("rec_v1"), \
            "沙盒行（无 worst 键位路由）在有值对照行之后"
        assert out1["unsortable_count"] >= 1
        out2 = client.get("/api/research/validation-records",
                          params={"sort": "time_desc"}).json()
        assert out2["unsortable_count"] == 0, "非排序键语义退化恒 0"

    def test_sandbox_bottom_in_verdict_quality_sort(self, env):
        """TC-2-7：verdict_quality 品质序 pass→inconclusive→fail→沙盒
        （else_=3 沉底——fail 与沙盒间不混排）。"""
        client, hid, out, db, _d = env
        _insert(db, "rec_q_fail", hid, "isolated_backtest", "fail",
                "2026-01-06T00:00:00Z")
        _insert(db, "rec_q_inc", hid, "isolated_backtest",
                "inconclusive", "2026-01-07T00:00:00Z")
        # rec_formal_1=pass（module env 既有）
        o = client.get("/api/research/validation-records",
                       params={"sort": "verdict_quality",
                               "limit": 200}).json()
        ids = [x["record_id"] for x in o["records"]]
        # 沉底断言取段序语义（共享库 composite 空串行同落 else_ 段）：
        # 沙盒行位于全部三态行（含 fail）之后——fail 与沙盒不混排
        sbx_pos = ids.index(out["record_id"])
        rank = {"pass": 0, "inconclusive": 1, "fail": 2}
        tri = [i for i, x in enumerate(o["records"])
               if x["verdict"] in rank]
        assert tri and sbx_pos > max(tri), "沙盒沉底（全部三态行之后）"
        vs = [x["verdict"] for x in o["records"] if x["verdict"] in rank]
        assert [rank[v] for v in vs] == sorted(rank[v] for v in vs)

    def test_sandbox_excluded_by_generalization_filter(self, env):
        """TC-2-8：generalization=true 沙盒行不在结果（symbol 表达式
        NULL 保守漏判——零改动面推论显式化）。"""
        client, hid, out, _db, _d = env
        o = client.get("/api/research/validation-records",
                       params={"generalization": "true"}).json()
        assert all(x["record_id"] != out["record_id"]
                   for x in o["records"])

    def test_sandbox_detail_rows_with_params(self, env):
        """TC-4-1（K1 新契约）：沙盒 detail 200 携带明细行+params
        顶层三键提取真值。

        非默认参数前置（tests-plan 权威定义：fee=0.002/init_cash=50000）
        ——三键值与 run_sandbox_backtest 缺省值全异，「params 顶层提取
        真值」与「端点硬编码默认值」可判别（P2 修复：原默认参前置
        两形态不可判别）。retire-legacy K1 后沙盒新记录必须保留明细
        （三明细有行+三布尔=1）。
        """
        client, hid, out, db, d = env
        o = run_sandbox_backtest(
            db, d, strategy_id="st_api", symbol="BTCUSDT", timeframe="4h",
            start="2023-03-11", end="2023-03-15", fee=0.002,
            slippage=0.001, init_cash=50000.0)
        r = client.get(
            f"/api/research/validation-records/{o['record_id']}/detail")
        assert r.status_code == 200
        body = r.json()
        assert body["detail_available"] is True, "K1：明细行在场"
        assert body["executions"], "K1：执行份非空"
        assert body["level"] == "sandbox_backtest"
        assert body["verdict"] == ""
        # 三键=params 顶层提取真值（与缺省 0.001/0.0005/100000 全异）
        assert body["fee"] == 0.002
        assert body["slippage"] == 0.001
        assert body["init_cash"] == 50000.0
        assert body["signal_trace"] is True, "K1：留痕三布尔=1"
        assert body["trade_semantics"] is True
        assert body["stop_price"] is True

    def test_sandbox_rejected_as_ruler_ab_source(self, env):
        """TC-4-2：沙盒 record_id 经公共校验件 resolve_validation_record
        拒绝（ruler_compare 与 ab_compare 同源消费——D4 公共校验件）。
        错误形制按现状校准：RESEARCH_PARAM_INVALID 422+结构化 detail
        （P1 授权——底线 422+结构化已满足且更精确）。"""
        client, hid, out, _db, _d = env
        from core.analysis.errors import ResearchError
        from core.analysis.templates.ruler_compare import (
            resolve_validation_record)
        rng = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2023-03-01", "end": "2023-03-10"}
        with pytest.raises(ResearchError) as ei:
            resolve_validation_record(_db, out["record_id"], rng)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.status == 422
        assert ei.value.detail["param"] == "trade_source.record_id"
        assert ei.value.detail["record_id"] == out["record_id"]

    def test_q_search_adhoc_sandbox_not_matched(self, env):
        """TC-2-9：q=H statement 词——挂 H 沙盒行经 statement 命中；
        adhoc 沙盒行 statement NULL 不命中（G6——P3 补显式两面，
        原仅由既有非沙盒用例+TC-6-1 机制拼合承接）。

        放本类（TC-2-5 之后执行）：本用例真实跑 adhoc 沙盒使共享库
        sandbox 计数 +1，若在 TestVerdictCountsFourKeys 之前执行会
        污染其 base 精确快照（sandbox: 1）——顺序隔离入此类尾。
        """
        client, hid, out, db, d = env
        # adhoc 沙盒行在库（隔离变量自证——「不命中」非因行未写入）
        run_sandbox_backtest(
            db, d, strategy_id="st_api", symbol="BTCUSDT", timeframe="4h",
            start="2023-03-16", end="2023-03-18")
        adhoc = client.get("/api/research/validation-records",
                           params={"hypothesis_id": "adhoc"}).json()
        assert adhoc["total"] >= 1
        # q=「统一流测试」（H statement「API 统一流测试」子串）
        hit = client.get("/api/research/validation-records",
                         params={"q": "统一流测试"}).json()
        ids = {x["record_id"] for x in hit["records"]}
        assert out["record_id"] in ids, "挂 H 沙盒行经 H statement 命中"
        hit_adhoc = [x for x in hit["records"]
                     if x["hypothesis_id"] == "adhoc"]
        assert not hit_adhoc, "adhoc 沙盒行（statement NULL）不命中 q"


class TestDataRangeItemTimestampValidation:
    """DataRangeItem 时间串可解析校验（阶段四②盲区消解——
    _require_parseable_timestamp 坏串分支零覆盖补强）。"""

    def test_unparseable_start_rejected(self):
        """坏时间串 422 形：pydantic 包装 ValueError（错误码+合法形
        hint 文案在场）。"""
        from pydantic import ValidationError
        from api.schemas import DataRangeItem
        with pytest.raises(ValidationError) as ei:
            DataRangeItem(symbol="BTCUSDT", timeframe="4h",
                          start="not-a-date", end="2024-02-01")
        msg = str(ei.value)
        assert "RESEARCH_PARAM_INVALID" in msg
        assert "UTC ISO8601" in msg

    def test_unparseable_end_rejected(self):
        from pydantic import ValidationError
        from api.schemas import DataRangeItem
        with pytest.raises(ValidationError):
            DataRangeItem(symbol="BTCUSDT", timeframe="4h",
                          start="2024-01-01", end="@@@")

    def test_parseable_forms_accepted(self):
        from api.schemas import DataRangeItem
        it = DataRangeItem(symbol="BTCUSDT", timeframe="4h",
                           start="2024-01-01", end="2024-01-02T00:00:00Z")
        assert it.start == "2024-01-01"
