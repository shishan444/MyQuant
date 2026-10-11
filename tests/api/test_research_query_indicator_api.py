"""指标读数路由测试（add-query-indicator 任务 3.2）。

覆盖：POST 200 结构四区（echo/声明/披露/归约）、422 拒绝族结构化码、
零留痕契约（Q2 裁决——调用前后 research_log 计数不变，与 inspect_cases
的留痕对链形成行为对照）。fixture 照 inspect_cases api 先例
（tmp parquet + init_db_ext + app.state 注入）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.db_ext import init_db_ext
from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def q_env(tmp_path: Path):
    """800 bar 1d 数据 + 独立 db（零留痕断言需可查 log 基线）。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=800, freq="1d", start="2023-06-01", seed=13)
    df.to_parquet(data_dir / f"{SYMBOL}_1d.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _payload(**extra):
    p = {"symbol": SYMBOL, "timeframe": "1d", "indicator": "RSI"}
    p.update(extra)
    return p


class TestQueryIndicatorRoute:
    def test_post_200_structure(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        resp = api_client.post("/api/research/query-indicator",
                               json=_payload(params={"period": 38}))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 四区：echo（indicator+params 生效值）/ 声明（warmup_bars）/
        # 披露（range 三键）/ 归约（列名键 → 所选归约）
        assert body["indicator"] == "RSI"
        assert body["params"] == {"period": 38}
        assert body["warmup_bars"] == 200
        assert set(body["range"]) == {"effective_start", "effective_end",
                                      "bars"}
        assert set(body["reductions"]["rsi_38"]) == {"latest", "quantiles"}
        assert body["reductions"]["rsi_38"]["quantiles"]["p50"] > 0

    def test_422_family(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        cases = [
            (_payload(reductions=["nope"]), "RESEARCH_PARAM_INVALID"),
            (_payload(params={"period": 99}), "RESEARCH_PARAM_INVALID"),
            (_payload(params={"period": 14, "bogus": 1}),
             "RESEARCH_PARAM_INVALID"),
            (_payload(indicator="NoSuchInd"), "RESEARCH_INDICATOR_UNKNOWN"),
        ]
        for payload, code in cases:
            resp = api_client.post("/api/research/query-indicator",
                                   json=payload)
            assert resp.status_code == 422, payload
            detail = resp.json()["detail"]
            assert detail["code"] == code
            assert detail["message"]

    def test_zero_audit_trail(self, api_client, q_env):
        """Q2 契约：读数零留痕——成功调用前后 log 计数不变。"""
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        before = len(rr.list_research_log(q_env["db"]))
        for _ in range(3):  # 重复调用亦不产生累计留痕
            resp = api_client.post("/api/research/query-indicator",
                                   json=_payload())
            assert resp.status_code == 200
        after = len(rr.list_research_log(q_env["db"]))
        assert before == after

    def test_rejection_no_side_effect(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        before = len(rr.list_research_log(q_env["db"]))
        resp = api_client.post("/api/research/query-indicator",
                               json=_payload(params={"period": 99}))
        assert resp.status_code == 422
        assert len(rr.list_research_log(q_env["db"])) == before

    def test_single_group_response_has_no_groups_key(self, api_client,
                                                     q_env):
        """单组存量不变（规格「无 groups 键的请求零触达批量路径」——
        响应形制锚）：不含 groups 键的请求返回单组形（顶层无 groups
        键），批量响应形（顶层仅 groups 键）与之可辨。"""
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]
        resp = api_client.post("/api/research/query-indicator",
                               json=_payload())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "groups" not in body
        assert {"indicator", "params", "warmup_bars", "range",
                "reductions"} <= set(body)


class TestNonResearchErrorPassthrough:
    """CARD-03 修复资产：except 收窄为 ResearchError——服务层非
    ResearchError 异常原样穿透（不再坍缩为 research_error_to_http 内
    AttributeError 500，原始异常类型与栈可观测）。"""

    def _bind_env(self, api_client, q_env):
        api_client.app.state.db_path = q_env["db"]
        api_client.app.state.data_dir = q_env["data_dir"]

    def test_value_error_passthrough(self, api_client, q_env, monkeypatch):
        from core.analysis import indicator_query as iq

        def _boom(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(iq, "query_indicator", _boom)
        self._bind_env(api_client, q_env)
        with pytest.raises(ValueError, match="boom"):
            api_client.post("/api/research/query-indicator", json=_payload())

    def test_research_error_channel_unchanged(self, api_client, q_env, monkeypatch):
        """对照组：ResearchError 仍走结构化 422（收窄未放跑正主）。"""
        from core.analysis import indicator_query as iq
        from core.analysis.hypothesis_service import ResearchError

        def _re(*args, **kwargs):
            raise ResearchError("RESEARCH_PARAM_INVALID", "测试注入")

        monkeypatch.setattr(iq, "query_indicator", _re)
        self._bind_env(api_client, q_env)
        resp = api_client.post("/api/research/query-indicator", json=_payload())
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"


def _batch_payload(groups, **extra):
    """批量形制载荷（不含单组专属键——互斥契约）。"""
    p = {"symbol": SYMBOL, "timeframe": "1d", "groups": groups}
    p.update(extra)
    return p


class TestBatchQueryIndicator:
    """批量读数（0.4.1 R1 任务 2.1-2.3，B1 用例组）。"""

    @staticmethod
    def _wire(client, env):
        client.app.state.db_path = env["db"]
        client.app.state.data_dir = env["data_dir"]

    def test_batch_matches_single_requests_in_order(self, api_client, q_env):
        """批量-单请求一致（≥3 组，请求序为键）+ 独立直算防双错
        （抽一组经 _compute_indicator+numpy 独立核算 p50）。"""
        self._wire(api_client, q_env)
        batch = api_client.post("/api/research/query-indicator", json=_batch_payload(
            groups=[{"indicator": "RSI", "params": {"period": 38}},
                    {"indicator": "EMA", "params": {"period": 50},
                     "reductions": ["latest"]},
                    {"indicator": "MACD", "reductions": ["quantiles"]}]))
        assert batch.status_code == 200, batch.text
        body = batch.json()
        # groups 字段到达断言：返回数组形，不降级单组
        assert list(body) == ["groups"]
        assert len(body["groups"]) == 3

        for i, g in enumerate(body["groups"]):
            single = api_client.post("/api/research/query-indicator", json=_payload(
                indicator=g["indicator"],
                params=(g["params"] if g["indicator"] != "MACD" else None),
                reductions=(["quantiles"] if g["indicator"] == "MACD"
                            else (["latest"] if g["indicator"] == "EMA"
                                  else None))))
            assert single.status_code == 200, single.text
            assert single.json() == g, f"组 {i} 与单请求不一致"

        # 独立直算：RSI(38) 全量段 p50（不经被测服务）
        import numpy as np
        from core.features.indicators import _compute_indicator
        df = q_env["df"]
        cols = _compute_indicator(df, "RSI", {"period": 38})
        # 求值域按时间轴切（effective_start=df.index[200]）后再 dropna——
        # 与服务口径同（先切域后判空，非先 dropna 再切）
        valid = cols["rsi_38"][df.index >= df.index[200]].dropna()
        expected_p50 = float(np.percentile(valid.values, 50))
        got = body["groups"][0]["reductions"]["rsi_38"]["quantiles"]["p50"]
        assert got == pytest.approx(expected_p50, rel=1e-9)

    def test_groups_and_single_fields_mutually_exclusive(self, api_client,
                                                         q_env):
        self._wire(api_client, q_env)
        resp = api_client.post("/api/research/query-indicator", json=_payload(
            indicator="RSI",
            groups=[{"indicator": "RSI"}]))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["param"] == "groups"
        assert detail["detail"]["conflicting"] == ["indicator"]

    @pytest.mark.parametrize(
        ("groups", "kind"),
        [([], "空数组"), ([{"indicator": "RSI"}] * 9, "9 组超限")],
        ids=["空数组拒", "超上限拒"])
    def test_group_count_domain(self, api_client, q_env, groups, kind):
        self._wire(api_client, q_env)
        resp = api_client.post("/api/research/query-indicator", json=_batch_payload(
            groups=groups))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["legal_range"] == "1..8"

    def test_atomic_reject_validation_phase(self, api_client, q_env):
        """校验期整批原子：groups[1] 参数越界 → 整批 422 指明组序。"""
        self._wire(api_client, q_env)
        resp = api_client.post("/api/research/query-indicator", json=_batch_payload(
            groups=[{"indicator": "RSI"},
                    {"indicator": "EMA", "params": {"period": 99999}}]))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["group"] == "groups[1]"

    def test_atomic_reject_execution_phase(self, api_client, q_env):
        """执行期整批原子：groups[1] start 早于数据起点 → warmup 不足
        → 整批 422 无部分结果（组 0 不返回）。"""
        self._wire(api_client, q_env)
        resp = api_client.post("/api/research/query-indicator", json=_batch_payload(
            groups=[{"indicator": "RSI"},
                    {"indicator": "RSI", "start": "2022-01-01"}]))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_WARMUP_INSUFFICIENT"
        assert detail["detail"]["group"] == "groups[1]"
        assert "groups" not in (resp.json() or {})

    def test_per_group_ranges_independent(self, api_client, q_env):
        """各组区间独立：同指标不同区间 → range.bars 不同。"""
        self._wire(api_client, q_env)
        resp = api_client.post("/api/research/query-indicator", json=_batch_payload(
            groups=[{"indicator": "RSI",
                     "start": "2024-01-01", "end": "2024-03-01"},
                    {"indicator": "RSI",
                     "start": "2024-01-01", "end": "2024-06-01"}]))
        assert resp.status_code == 200, resp.text
        bars = [g["range"]["bars"] for g in resp.json()["groups"]]
        assert bars[0] < bars[1]

    def test_payload_tier_mechanism(self, api_client, q_env, monkeypatch):
        """载荷分档机制：monkeypatch 降阈触发拒绝（资源参数可调——
        机制验证不依赖 16KB 构造）；两档常量值锚定 2048/16384。"""
        from core.analysis import indicator_query as iq
        assert iq._MAX_SERIALIZED_BYTES == 2048
        assert iq._MAX_BATCH_SERIALIZED_BYTES == 16384
        self._wire(api_client, q_env)
        monkeypatch.setattr(iq, "_MAX_BATCH_SERIALIZED_BYTES", 200)
        resp = api_client.post("/api/research/query-indicator", json=_batch_payload(
            groups=[{"indicator": "RSI"}]))
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["tier"] == "batch"
        assert detail["detail"]["limit"] == 200

    def test_zero_side_effect(self, api_client, q_env):
        """零副作用：批量成功后 research_log 计数不变（Q2 同族）。"""
        self._wire(api_client, q_env)
        before = rr.count_research_log(q_env["db"]) \
            if hasattr(rr, "count_research_log") else None
        resp = api_client.post("/api/research/query-indicator", json=_batch_payload(
            groups=[{"indicator": "RSI"}, {"indicator": "EMA",
                                            "params": {"period": 50}}]))
        assert resp.status_code == 200, resp.text
        import sqlite3
        with sqlite3.connect(q_env["db"]) as conn:
            n = conn.execute("SELECT COUNT(*) FROM research_log").fetchone()[0]
        assert n == 0
