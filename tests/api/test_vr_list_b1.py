"""B1 检索增量测试族（upgrade-validation-records-list 任务 3.1——
T2-A1~A9 锚）。

覆盖：sort 四值 422 全枚举+生效正例（A1）；无值落尾+计数（A2）；
品质序混排不沉底（A2）；q 三态含 % 字面（A3）；泛化三态+交集（A4）；
verdict_counts 同基数+双口径负例并存（A5）；statement 两态（A6）；
staged worst 三槽写入端（A7）。
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
        out = _list(env, generalization="true",
                    level="isolated_backtest")
        assert out["total"] == 1  # 泛化一维进 total（rec_h）
        # 行级交集：rec_h 亦 isolated——仍出记录（交集非并集）
        assert [x["record_id"] for x in out["records"]] == ["rec_h"]
        # 负对照：泛化 ∧ level=random_window → 行空而 total 不变
        out2 = _list(env, generalization="true", level="random_window")
        assert out2["total"] == 1 and out2["records"] == []

    def test_generalization_bad_form_422(self, env):
        r = env["client"].get(
            "/api/research/validation-records",
            params={"generalization": "maybe"})
        assert r.status_code == 422  # pydantic bool 形制拒绝（lenient
        # 域 yes/no/1/0 合法转 bool——maybe 真非法）


class TestCountsDualCaliber:
    """T2-A5：verdict_counts 同基数+双口径负例并存。"""

    def test_counts_same_base_as_total(self, env):
        out = _list(env, q="关键词XYZ")
        assert sum(out["verdict_counts"].values()) == out["total"] == 2

    def test_dual_caliber_coexists(self, env):
        """B1 过渡态双口径并存可判别：q 进 total，level 不进。"""
        out = _list(env, q="关键词XYZ", level="random_window")
        # total=四维（q 过滤基数 2）——level 行级过滤不改 total
        assert out["total"] == 2
        # 当页行=行级过滤后清空（无 random_window 记录）
        assert out["records"] == []

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
