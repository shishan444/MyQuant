"""B1 检索增量测试族（upgrade-validation-records-list 任务 3.1——
T2-A1~A9 锚）。

覆盖：sort 四值 422 全枚举+生效正例（A1）；无值落尾+计数（A2）；
品质序混排不沉底（A2）；q 三态含 % 字面（A3）；泛化三态+交集（A4）；
verdict_counts 同基数+双口径负例并存（A5）；statement 两态（A6）；
staged worst 三槽写入端（A7）。

tester-2 补齐（spec/design 契约面核查缺口）：泛化边界四条（设计卡
B1.4——保守漏判/跨周期排除/不限策略状态/基准兜底）；statement adhoc
null 态+q 不命中（spec 关键词搜索 Scenario）；q 空串忽略（B1.1）；
q 维分页页满恒 limit（spec 六维过滤基数 Scenario）；verdict_quality
组内次序键+time_desc/asc 逆序对照（B1.3 次序键契约）；生成物进面
防漂移锚（B1.7②——T2-A9 生成物面）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from api.app import app
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.executor import ensure_research_tables
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import StrategyDNA
from tests.fixtures.vr_trace_gen import build_sample


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """构造库：三记录（收益正/负/无值；verdict 三态；泛化一条）。"""
    d = tmp_path_factory.mktemp("b1")
    db = d / "b1.db"
    init_db_ext(db)
    ensure_research_tables(db)
    s = build_sample("pct_long")
    specs = [
        # (rid, rec_sym, base_sym, verdict, total_return, period 偏移)
        ("rec_g", "ETHUSDT", "ETHUSDT", "pass", 0.05, 0),
        ("rec_h", "BTCUSDT", "ETHUSDT", "fail", -0.02, 1),
        ("rec_i", "BTCUSDT", "BTCUSDT", "inconclusive", None, 2),
    ]
    # rec_i 陈述不含检索词（q miss 面）——statement 逐条定制见下
    for rid, rec_sym, base_sym, verdict, ret, k in specs:
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [{"subject": {"indicator": "RSI",
                                                 "params": {"period": 14}},
                                     "action": "lt", "target": 35 + k}],
                "then_conditions": [{"subject": "close", "action": "gt",
                                     "target": 0}]},
            statement=(f"假设原话{rid} 关键词XYZ" if rid != "rec_i"
                       else "假设原话rec_i 其他陈述"),
            data_ranges=[{"symbol": base_sym, "timeframe": "4h",
                          "start": "2024-01-01T00:00:00Z",
                          "end": "2024-02-01T00:00:00Z"}],
            strategy_params={"stop_loss": 0.05})["hypothesis_id"]
        dna_d = json.loads(s["dna"].to_json())
        dna_d["execution_genes"] = {"timeframe": "4h", "symbol": rec_sym}
        for g in dna_d["signal_genes"]:
            g["params"] = {kk: vv + k for kk, vv in g["params"].items()}
        sid = f"st_{rid}"
        save_strategy(db, strategy_id=sid,
                      dna_json=StrategyDNA.from_dict(dna_d).to_json(),
                      symbol=rec_sym, timeframe="4h")
        from core.persistence.orm.engine import engine_for
        from sqlalchemy import text
        with engine_for(db).begin() as conn:
            conn.execute(
                text("UPDATE strategy SET parent_ids=:p"
                     " WHERE strategy_id=:s"),
                {"p": json.dumps([hid]), "s": sid})
        rr.save_validation_with_log(
            db, record_id=rid, hypothesis_id=hid, strategy_id=sid,
            level="isolated_backtest", segmentation="",
            params=json.dumps({"symbol": rec_sym, "timeframe": "4h"}),
            verdict=verdict, range_hash=f"rh_{rid}",
            result=json.dumps({"symbol": rec_sym, "timeframe": "4h",
                               "metrics": ({"total_return": ret}
                                           if ret is not None else {})}))
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"db": db, "client": client}


def _list(env, **params):
    q = {k: v for k, v in params.items() if v is not None}
    r = env["client"].get(
        "/api/research/validation-records", params=q)
    assert r.status_code == 200, r.text
    return r.json()


class TestSortEnum:
    """T2-A1：sort 四值 422 全枚举+生效正例。"""

    @pytest.mark.parametrize("bad", ["", "desc", "time_desc ", "TOTAL",
                                     "return_desc", 0])
    def test_sort_422_full_enum(self, env, bad):
        r = env["client"].get(
            "/api/research/validation-records", params={"sort": bad})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"

    def test_sort_time_desc_default_equal(self, env):
        a = _list(env, sort="time_desc")
        b = _list(env)  # 缺省
        assert [x["record_id"] for x in a["records"]] == \
               [x["record_id"] for x in b["records"]]

    def test_sort_time_asc_ordering(self, env):
        out = _list(env, sort="time_asc")
        times = [x["created_at"] for x in out["records"]]
        assert times == sorted(times)

    def test_sort_total_return_desc_nulls_last(self, env):
        """T2-A2：无值落尾+unsortable_count。"""
        out = _list(env, sort="total_return_desc")
        rets = [
            (x["result"] or {}).get("metrics", {}).get("total_return")
            for x in out["records"]]
        non_null = [v for v in rets if v is not None]
        assert non_null == sorted(non_null, reverse=True), "值序降序"
        assert rets[-1] is None, "NULL 落尾"
        assert out["unsortable_count"] == 1

    def test_sort_unsortable_zero_on_other_sorts(self, env):
        for s in ("time_desc", "time_asc", "verdict_quality"):
            out = _list(env, sort=s)
            assert out["unsortable_count"] == 0, f"{s} 恒 0 语义键退化"

    def test_sort_verdict_quality_ordering(self, env):
        """T2-A2：品质序 pass→inconclusive→fail，fail 与 pass 时间混排
        不沉底（品质序在前时间序在后）。"""
        out = _list(env, sort="verdict_quality")
        vs = [x["verdict"] for x in out["records"]]
        rank = {"pass": 0, "inconclusive": 1, "fail": 2}
        ranks = [rank[v] for v in vs]
        assert ranks == sorted(ranks)
        assert set(vs) == {"pass", "fail", "inconclusive"}, "三态全在场"


class TestQSearch:
    """T2-A3：q 三态（命中/空集/通配符字面）。"""

    def test_q_hit(self, env):
        out = _list(env, q="关键词XYZ")
        assert out["total"] == 2
        assert all("关键词XYZ" in x["statement"] for x in out["records"])

    def test_q_miss_empty(self, env):
        out = _list(env, q="不存在的词")
        assert out["total"] == 0 and out["records"] == []
        assert out["verdict_counts"] == {
            "pass": 0, "fail": 0, "inconclusive": 0}

    def test_q_wildcard_literal(self, env):
        assert _list(env, q="关键词%XYZ")["total"] == 0, "% 字面不放大"
        assert _list(env, q="假设原话_")["total"] == 0, "_ 字面不放大"

    def test_q_empty_string_ignored(self, env):
        """B1.1：q 可空串忽略——等价不过滤（端点 q or None+仓储 if q）。"""
        a = _list(env, q="")
        b = _list(env)
        assert a["total"] == b["total"] == 3

    def test_q_filter_paging_full_pages(self, env):
        """spec 六维过滤基数 Scenario：q 过滤后结果集分页——页满恒
        limit（命中 2 条，limit=1 两页各满、has_more 两态如实）。"""
        p1 = _list(env, q="关键词XYZ", limit=1)
        assert p1["total"] == 2
        assert len(p1["records"]) == 1 and p1["has_more"] is True
        p2 = _list(env, q="关键词XYZ", limit=1, offset=1)
        assert len(p2["records"]) == 1 and p2["has_more"] is False
        assert p1["records"][0]["record_id"] != p2["records"][0]["record_id"]

    def test_statement_two_states(self, env):
        """T2-A6：statement 两态——关联行原话/adhoc null。"""
        out = _list(env)
        by_id = {x["record_id"]: x for x in out["records"]}
        assert by_id["rec_g"]["statement"] == "假设原话rec_g 关键词XYZ"


class TestGeneralization:
    """T2-A4：泛化三态+与 level 交集。"""

    def test_generalization_true(self, env):
        out = _list(env, generalization="true")
        assert [x["record_id"] for x in out["records"]] == ["rec_h"], (
            "仅记录标的≠原生标的且同周期者")

    def test_generalization_false_or_absent_no_filter(self, env):
        a = _list(env, generalization="false")
        b = _list(env)
        assert a["total"] == b["total"] == 3

    def test_generalization_with_level_intersection(self, env):
        # B2 六维下推（4.1 同批核改）：泛化 ∧ level 取交集进 total
        out = _list(env, generalization="true",
                    level="isolated_backtest")
        assert out["total"] == 1  # rec_h（泛化且 isolated）
        assert [x["record_id"] for x in out["records"]] == ["rec_h"]
        # 负对照：泛化 ∧ level=random_window → 六维基数 0
        out2 = _list(env, generalization="true", level="random_window")
        assert out2["total"] == 0 and out2["records"] == []

    def test_generalization_bad_form_422(self, env):
        r = env["client"].get(
            "/api/research/validation-records",
            params={"generalization": "maybe"})
        assert r.status_code == 422  # pydantic bool 形制拒绝（lenient
        # 域 yes/no/1/0 合法转 bool——maybe 真非法）


@pytest.fixture()
def benv(tmp_path):
    """泛化边界+adhoc 契约库（function 级隔离——ORM 直插假设/策略/
    记录三元链路）。

    设计卡 B1.4 边界四条的可测面。Strategy 列 symbol/timeframe
    NOT NULL（PRAGMA 实测）——基准侧 NULL 仅经 outer join 无策略行
    可达；记录侧 NULL 经 result JSON 键缺可达。基准=parent 假设
    data_ranges 首段（ETHUSDT/4h），策略列兜底路径由 st_gone 承载
    （parent_ids 指向不存在假设→落 Strategy.symbol/timeframe）。
    """
    d = tmp_path / "b1b"
    d.mkdir()
    db = d / "b1b.db"
    init_db_ext(db)
    ensure_research_tables(db)
    hid = register_hypothesis(
        db,
        conditions={
            "when_conditions": [{"subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt",
                                 "target": 0}]},
        statement="边界基准假设",
        data_ranges=[{"symbol": "ETHUSDT", "timeframe": "4h",
                      "start": "2024-01-01T00:00:00Z",
                      "end": "2024-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]
    from core.persistence.orm.models import Strategy, ValidationRecord
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with S() as sess:
        sess.add(Strategy(strategy_id="st_norm", dna_json="{}",
                          symbol="ETHUSDT", timeframe="4h",
                          parent_ids=json.dumps([hid]),
                          created_at="2026-11-01T00:00:00+00:00",
                          updated_at="2026-11-01T00:00:00+00:00"))
        sess.add(Strategy(strategy_id="st_arch", dna_json="{}",
                          symbol="ETHUSDT", timeframe="4h",
                          parent_ids=json.dumps([hid]),
                          created_at="2026-11-01T00:00:00+00:00",
                          updated_at="2026-11-01T00:00:00+00:00"))
        # archived 经生命周期转换到达（INSERT 值域触发器仅允许
        # active/draft，UPDATE 值域六值——trg_strategy_status_*）
        sess.flush()
        conn = sess.connection()
        conn.execute(text(
            "UPDATE strategy SET status='archived'"
            " WHERE strategy_id='st_arch'"))
        sess.add(Strategy(strategy_id="st_gone", dna_json="{}",
                          symbol="ETHUSDT", timeframe="4h",
                          parent_ids=json.dumps(["hGone"]),
                          created_at="2026-11-01T00:00:00+00:00",
                          updated_at="2026-11-01T00:00:00+00:00"))
        rows = [
            # (rid, strategy_id, result)——期望判定见各用例
            ("g_null_sym", "st_norm",
             '{"timeframe": "4h", "metrics": {}}'),
            ("g_null_tf", "st_norm",
             '{"symbol": "BTCUSDT", "metrics": {}}'),
            ("g_cross_tf", "st_norm",
             '{"symbol": "BTCUSDT", "timeframe": "1h"}'),
            ("g_archived", "st_arch",
             '{"symbol": "BTCUSDT", "timeframe": "4h"}'),
            ("g_base_fb", "st_gone",
             '{"symbol": "BTCUSDT", "timeframe": "4h"}'),
            ("g_no_strat", None,
             '{"symbol": "BTCUSDT", "timeframe": "4h"}'),
            ("adhoc_row", None,
             '{"symbol": "BTCUSDT", "timeframe": "4h"}'),
        ]
        for i, (rid, sid, result) in enumerate(rows):
            sess.add(ValidationRecord(
                record_id=rid,
                hypothesis_id=("adhoc" if rid == "adhoc_row" else hid),
                strategy_id=sid, level="isolated_backtest",
                segmentation="", verdict="pass",
                range_hash=f"rh_{rid}", params="{}", result=result,
                created_at=f"2026-11-0{i + 1}T00:00:00+00:00"))
        sess.commit()
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"client": client}


class TestGeneralizationBoundary:
    """T2-A4 边界四条（设计卡 B1.4——保守漏判/跨周期排除/不限策略
    状态/基准兜底；tasks 1.1 边界断言面由本类补齐承载）。"""

    def _gen_ids(self, benv):
        r = benv["client"].get(
            "/api/research/validation-records",
            params={"generalization": "true"})
        assert r.status_code == 200, r.text
        return [x["record_id"] for x in r.json()["records"]]

    def test_null_record_side_conservative_miss(self, benv):
        """边界①：记录侧 symbol/timeframe 键缺（NULL）→保守漏判——
        不可判非判为非泛化，且不带泛化参数时记录如实在场。"""
        ids = self._gen_ids(benv)
        assert "g_null_sym" not in ids, "symbol 键缺须保守漏判"
        assert "g_null_tf" not in ids, "timeframe 键缺须保守漏判"
        full = benv["client"].get(
            "/api/research/validation-records",
            params={"limit": 200}).json()
        by_id = {x["record_id"] for x in full["records"]}
        assert {"g_null_sym", "g_null_tf"} <= by_id, "漏判≠记录被过滤"

    def test_no_strategy_side_conservative_miss(self, benv):
        """边界①基准侧面：无策略关联（outer join 基准侧 NULL）→保守
        漏判（g_no_strat 记录标的 BTC≠ETH 但不可判）。"""
        ids = self._gen_ids(benv)
        assert "g_no_strat" not in ids

    def test_cross_timeframe_excluded(self, benv):
        """边界②：跨周期显式排除——symbol 不等但 timeframe 不等
        （1h vs 4h）不判泛化；对照 g_archived（symbol 不等+tf 相等）
        命中，判据是交集非单维。"""
        ids = self._gen_ids(benv)
        assert "g_cross_tf" not in ids
        assert "g_archived" in ids

    def test_strategy_status_not_filtering(self, benv):
        """边界③：不限策略状态——archived 策略的泛化记录同规判
        （泛化是标的事实非状态事实）。"""
        ids = self._gen_ids(benv)
        assert "g_archived" in ids

    def test_base_symbol_fallback_path(self, benv):
        """基准兜底路径：parent_ids 指向不存在假设→data_ranges 子查询
        空→落 Strategy.symbol/timeframe 列兜底判定（命中）。"""
        ids = self._gen_ids(benv)
        assert "g_base_fb" in ids

    def test_generalization_hit_set_exact(self, benv):
        """命中集恰为两侧可判且不等者——全库 7 条仅 g_archived/
        g_base_fb 两态命中（其余五条各有漏判/排除依据）。"""
        assert set(self._gen_ids(benv)) == {"g_archived", "g_base_fb"}


class TestStatementAdhoc:
    """T2-A6 补齐态+spec 关键词搜索 Scenario「adhoc 不命中」。"""

    def test_adhoc_statement_null_and_q_no_hit(self, benv):
        """adhoc（hypothesis_id 无假设行）→statement null 如实；
        q 匹配假设原话不命中 adhoc 行（LEFT JOIN 无行即无匹配面）。"""
        full = benv["client"].get(
            "/api/research/validation-records",
            params={"limit": 200}).json()
        by_id = {x["record_id"]: x for x in full["records"]}
        assert by_id["adhoc_row"]["statement"] is None
        assert by_id["g_null_sym"]["statement"] == "边界基准假设"
        hit = benv["client"].get(
            "/api/research/validation-records",
            params={"q": "边界基准假设"}).json()
        ids = {x["record_id"] for x in hit["records"]}
        assert "adhoc_row" not in ids
        assert hit["total"] == 6, "六条关联记录全命中，adhoc 不在"


@pytest.fixture()
def bareenv(tmp_path):
    """CARD-21 裸串形制锚库：parent_ids/data_ranges 两列均无机制保证
    恒 JSON——裸串 parent_ids（parse_parent_ids 先例形制）经基准侧
    json_valid 归一命中；data_ranges 裸串经 else_ 落 Strategy 列兜底
    （修复前裸串在 COALESCE 首参 json_extract 即抛 malformed JSON）。

    两支分离：g_bare 裸串 parent_ids 命中（归一支）；g_bare2 裸串
    data_ranges 走兜底（策略列 ETHUSDT）；g_bare3 合法 JSON 数组走
    json_extract 提取（假设首段 SOLUSDT≠策略列）——提取支与兜底支
    基准值来源可判别。
    """
    d = tmp_path / "b1bare"
    d.mkdir()
    db = d / "b1bare.db"
    init_db_ext(db)
    ensure_research_tables(db)
    hid = register_hypothesis(
        db,
        conditions={
            "when_conditions": [{"subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "lt", "target": 35}],
            "then_conditions": [{"subject": "close", "action": "gt",
                                 "target": 0}]},
        statement="裸串父假设",
        data_ranges=[{"symbol": "ETHUSDT", "timeframe": "4h",
                      "start": "2024-01-01T00:00:00Z",
                      "end": "2024-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]
    hid2 = register_hypothesis(
        db,
        conditions={
            "when_conditions": [{"subject": {"indicator": "RSI",
                                             "params": {"period": 14}},
                                 "action": "lt", "target": 36}],
            "then_conditions": [{"subject": "close", "action": "gt",
                                 "target": 0}]},
        statement="裸串数据段假设",
        data_ranges=[{"symbol": "SOLUSDT", "timeframe": "4h",
                      "start": "2024-01-01T00:00:00Z",
                      "end": "2024-02-01T00:00:00Z"}],
        strategy_params={"stop_loss": 0.05})["hypothesis_id"]
    from core.persistence.orm.models import Strategy, ValidationRecord
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    with S() as sess:
        # 裸串 parent_ids：直接写 hypothesis_id 不加 json.dumps
        sess.add(Strategy(strategy_id="st_bare", dna_json="{}",
                          symbol="ETHUSDT", timeframe="4h",
                          parent_ids=hid,
                          created_at="2026-11-01T00:00:00+00:00",
                          updated_at="2026-11-01T00:00:00+00:00"))
        sess.add(Strategy(strategy_id="st_bare2", dna_json="{}",
                          symbol="ETHUSDT", timeframe="4h",
                          parent_ids=hid2,
                          created_at="2026-11-01T00:00:00+00:00",
                          updated_at="2026-11-01T00:00:00+00:00"))
        sess.add(Strategy(strategy_id="st_bare3", dna_json="{}",
                          symbol="ETHUSDT", timeframe="4h",
                          parent_ids=json.dumps([hid2]),
                          created_at="2026-11-01T00:00:00+00:00",
                          updated_at="2026-11-01T00:00:00+00:00"))
        sess.flush()
        conn = sess.connection()
        # data_ranges 裸串化（无机制保证恒 JSON 的列形制）
        conn.execute(text(
            "UPDATE research_hypotheses SET data_ranges='ETHUSDT'"
            " WHERE hypothesis_id=:h"), {"h": hid2})
        rows = [
            ("g_bare", hid, "st_bare"),
            ("g_bare2", hid2, "st_bare2"),
            ("g_bare3", hid2, "st_bare3"),
        ]
        for i, (rid, h, sid) in enumerate(rows):
            sess.add(ValidationRecord(
                record_id=rid, hypothesis_id=h, strategy_id=sid,
                level="isolated_backtest", segmentation="", verdict="pass",
                range_hash=f"rh_{rid}", params="{}",
                result=json.dumps({"symbol": "BTCUSDT",
                                   "timeframe": "4h"}),
                created_at=f"2026-11-0{i + 1}T00:00:00+00:00"))
        sess.commit()
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"db": db, "client": client}


class TestBareStringAnchors:
    """CARD-21 裸串形制回归锚：裸串 parent_ids 泛化命中三路不炸+
    data_ranges 裸串落 Strategy 列兜底（防形制倒置掩护复发——现有
    泛化族 parent_ids 全 json.dumps 数组形制，裸串面此前零覆盖）。"""

    def test_bare_parent_ids_generalization_hit_three_paths(
            self, bareenv):
        """裸串 parent_ids：泛化命中三路（list/aggregates/count）一致
        且全程不炸——修复前 COALESCE 首参 json_extract 即抛
        OperationalError malformed JSON。"""
        r = bareenv["client"].get(
            "/api/research/validation-records",
            params={"generalization": "true"})
        assert r.status_code == 200, r.text  # 不炸即修复在位
        out = r.json()
        ids = [x["record_id"] for x in out["records"]]
        # list 路：三条全命中（裸串归一支/兜底支/提取支各一）
        assert sorted(ids) == ["g_bare", "g_bare2", "g_bare3"]
        # aggregates 路：total 与 verdict_counts 同基数
        assert out["total"] == 3
        assert out["verdict_counts"] == {
            "pass": 3, "fail": 0, "inconclusive": 0}
        # count 路：仓储直调同基数
        assert rr.count_validation_records(
            bareenv["db"], generalization=True) == 3

    def test_bare_data_ranges_falls_back_to_strategy_columns(
            self, bareenv):
        """data_ranges 裸串→基准侧 else_ 落 Strategy.symbol/timeframe
        兜底（值非 NULL——落 NULL 即保守漏判不命中，兜底分支形错即红）。

        g_bare2 基准值必来自策略列：其假设 data_ranges 已裸串化，
        json_extract 路径不可达（可达即 malformed 炸）；对照 g_bare3
        （合法 JSON 数组形制）基准取假设首段 SOLUSDT——提取支与兜底支
        值来源分离可判别。"""
        r = bareenv["client"].get(
            "/api/research/validation-records",
            params={"generalization": "true"})
        assert r.status_code == 200, r.text
        ids = {x["record_id"] for x in r.json()["records"]}
        assert "g_bare2" in ids, (
            "data_ranges 裸串须落策略列兜底命中——不命中即兜底分支"
            "形错（基准侧落 NULL 保守漏判）")
        assert "g_bare3" in ids, "合法 JSON 提取支（SOLUSDT）须正常命中"


class TestCountsDualCaliber:
    """T2-A5：verdict_counts 同基数+双口径负例并存。"""

    def test_counts_same_base_as_total(self, env):
        out = _list(env, q="关键词XYZ")
        assert sum(out["verdict_counts"].values()) == out["total"] == 2

    def test_six_dim_caliber(self, env):
        """B2 六维口径（4.1 同批核改——B1 双口径负例退役）：q∧level
        交集进 total。"""
        out = _list(env, q="关键词XYZ", level="random_window")
        assert out["total"] == 0 and out["records"] == []
        out2 = _list(env, q="关键词XYZ", level="isolated_backtest")
        assert out2["total"] == 2  # 两命中记录均 isolated

    def test_default_caliber_unchanged(self, env):
        """缺省（不传新参）total 与现状两维逐位一致。"""
        out = _list(env)
        assert out["total"] == 3
        assert out["verdict_counts"] == {
            "pass": 1, "fail": 1, "inconclusive": 1}


class TestStagedWorstSlots:
    """T2-A7：staged worst 五键写入端（新记录）+存量容缺。"""

    def test_new_staged_worst_five_keys(self, env, tmp_path):
        d = tmp_path / "mkt"
        d.mkdir()
        import numpy as np
        import pandas as pd
        rng = np.random.default_rng(5)
        idx = pd.date_range("2024-01-01", periods=620, freq="4h", tz="UTC",
                            name="timestamp")
        close = 40000 + np.cumsum(rng.normal(0, 100, 620))
        pd.DataFrame({"open": close, "high": close + 60, "low": close - 60,
                      "close": close, "volume": rng.uniform(100, 800, 620),
                      "trades": 40}, index=idx).to_parquet(
            d / "BTCUSDT_4h.parquet")
        hid = register_hypothesis(
            env["db"],
            conditions={
                "when_conditions": [{"subject": {"indicator": "RSI",
                                                 "params": {"period": 14}},
                                     "action": "lt", "target": 45}],
                "then_conditions": [{"subject": "close", "action": "gt",
                                     "target": 0}]},
            statement="staged 五键假设",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2024-01-01T00:00:00Z",
                          "end": "2024-01-10T00:00:00Z"}],
            strategy_params={"stop_loss": 0.05})["hypothesis_id"]
        from core.analysis.validation_service import run_staged_validation
        st_dna = json.loads(build_sample("pct_long")["dna"].to_json())
        st_dna["execution_genes"] = {"timeframe": "4h",
                                     "symbol": "BTCUSDT"}
        for g in st_dna["signal_genes"]:
            g["params"] = {kk: vv + 5 for kk, vv in g["params"].items()}
        save_strategy(env["db"], strategy_id="st_ws",
                      dna_json=StrategyDNA.from_dict(st_dna).to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        out = run_staged_validation(
            env["db"], d, hypothesis_id=hid, strategy_id="st_ws",
            symbol="BTCUSDT", timeframe="4h",
            start="2024-02-10T00:00:00Z", end="2024-03-25T00:00:00Z")
        result = json.loads(rr.get_validation_record_by_id(
            env["db"], out["record_id"])["result"])
        ws = result["summary"]["worst_segment"]
        for k in ("label", "total_return", "sharpe_ratio",
                  "max_drawdown", "total_trades"):
            assert k in ws, f"worst_segment 缺 {k}"


@pytest.fixture()
def sortenv(tmp_path):
    """排序次序键库：同 verdict 多条不同 created_at（ORM 直插——
    品质序组内次序可证；module 级 env 各 verdict 单条不可证）。"""
    d = tmp_path / "b1s"
    d.mkdir()
    db = d / "b1s.db"
    init_db_ext(db)
    ensure_research_tables(db)
    from core.persistence.orm.models import ValidationRecord
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    rows = [
        ("v_pass_a", "pass", "2026-11-05T00:00:00+00:00"),
        ("v_pass_b", "pass", "2026-11-07T00:00:00+00:00"),
        ("v_inc", "inconclusive", "2026-11-06T00:00:00+00:00"),
        ("v_fail_a", "fail", "2026-11-04T00:00:00+00:00"),
        ("v_fail_b", "fail", "2026-11-08T00:00:00+00:00"),
    ]
    with S() as sess:
        for rid, verdict, ts in rows:
            sess.add(ValidationRecord(
                record_id=rid, hypothesis_id="adhoc", strategy_id=None,
                level="isolated_backtest", segmentation="", verdict=verdict,
                range_hash=f"rh_{rid}", params="{}", result="{}",
                created_at=ts))
        sess.commit()
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"client": client}


class TestSortSecondaryKeys:
    """B1.3 次序键契约：品质序组内 created_at DESC+record_id 确定序；
    time_desc 显式传参与 time_asc 逆序对照（缺省零变化的两向证）。"""

    def test_verdict_quality_within_group_time_desc(self, sortenv):
        """verdict_quality：品质序主键（pass→inconclusive→fail）内
        各组按 created_at 倒序——fail 与 pass 时间序混排不沉底且组内
        新者在前。"""
        out = sortenv["client"].get(
            "/api/research/validation-records",
            params={"sort": "verdict_quality", "limit": 200}).json()
        assert [x["record_id"] for x in out["records"]] == [
            "v_pass_b", "v_pass_a", "v_inc", "v_fail_b", "v_fail_a"]

    def test_time_desc_explicit_reverse_of_asc(self, sortenv):
        """time_desc 显式传参=时间降序，与 time_asc 恰互逆（缺省
        time_desc 零变化的显式面证明）。"""
        desc = [x["record_id"] for x in sortenv["client"].get(
            "/api/research/validation-records",
            params={"sort": "time_desc", "limit": 200}).json()["records"]]
        asc = [x["record_id"] for x in sortenv["client"].get(
            "/api/research/validation-records",
            params={"sort": "time_asc", "limit": 200}).json()["records"]]
        assert desc == list(reversed(asc))


@pytest.fixture()
def worstrouteenv(tmp_path):
    """三型 worst 键位路由库：isolated/random/staged 各自键位取值
    参与统一降序（design B1.3 键位表——三型键位互不通用是必要性）。"""
    d = tmp_path / "b1wr"
    d.mkdir()
    db = d / "b1wr.db"
    init_db_ext(db)
    ensure_research_tables(db)
    from core.persistence.orm.models import ValidationRecord
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    S = sessionmaker(bind=create_engine(f"sqlite:///{db}"))
    rows = [
        ("r_iso", "isolated_backtest",
         {"metrics": {"total_return": 0.03}}),
        ("r_rnd", "random_window",
         {"summary": {"worst_window": {
             "metrics": {"total_return": 0.10}}}}),
        ("r_stg", "staged_validation",
         {"summary": {"worst_segment": {"total_return": -0.05}}}),
        ("r_rnd_null", "random_window",
         {"summary": {}}),  # 无 worst_window → NULL 落尾
    ]
    with S() as sess:
        for i, (rid, level, result) in enumerate(rows):
            sess.add(ValidationRecord(
                record_id=rid, hypothesis_id="adhoc", strategy_id=None,
                level=level, segmentation="", verdict="pass",
                range_hash=f"rh_{rid}", params="{}",
                result=json.dumps(result),
                created_at=f"2026-11-0{i + 1}T00:00:00+00:00"))
        sess.commit()
    from api.app import create_app
    app = create_app(db_path=db, data_dir=d)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield {"client": client}


class TestWorstRouteSorting:
    """B1.3 属性补强（阶段四闸门步骤四）：三型 level 的 worst 键位
    路由在 sort=total_return_desc 下各自取值参与统一降序——键位互不
    通用（random 路由错至 isolated 键位即 NULL 落尾即红）。"""

    def test_three_level_key_routes_unified_desc(self, worstrouteenv):
        out = worstrouteenv["client"].get(
            "/api/research/validation-records",
            params={"sort": "total_return_desc", "limit": 200}).json()
        assert [x["record_id"] for x in out["records"]] == [
            "r_rnd", "r_iso", "r_stg", "r_rnd_null"], (
            "三型各按自身键位取值统一降序；键位错路由即取不到值落尾红")
        assert out["unsortable_count"] == 1, "仅无 worst_window 一条不可排"


class TestGeneratedArtifacts:
    """T2-A9 生成物面（B1.7②）：openapi.json+api.ts 新参数新键进面
    防漂移锚——再生成漏项即红（沿 test_dna_policy_transport 先例形制）。"""

    def test_openapi_and_ts_cover_new_surface(self):
        root = Path(__file__).resolve().parents[2]
        oa = json.loads((root / "web" / "openapi.json").read_text())
        get = oa["paths"]["/api/research/validation-records"]["get"]
        params = {p["name"] for p in get["parameters"]}
        assert {"sort", "q", "generalization"} <= params, (
            "openapi 参数面缺新参")
        resp_ref = get["responses"]["200"]["content"][
            "application/json"]["schema"]["$ref"].split("/")[-1]
        resp = oa["components"]["schemas"][resp_ref]
        assert {"verdict_counts", "unsortable_count"} <= set(
            resp["properties"]), "响应 schema 缺新键"
        row_ref = resp["properties"]["records"]["items"][
            "$ref"].split("/")[-1]
        row = oa["components"]["schemas"][row_ref]
        assert "statement" in row["properties"], "行 schema 缺 statement"
        ts = (root / "web" / "src" / "types" / "generated" /
              "api.ts").read_text()
        for k in ("sort", "generalization", "verdict_counts",
                  "unsortable_count", "statement"):
            assert k in ts, f"api.ts 缺 {k}"
