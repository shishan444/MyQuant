"""param_sweep 路由与留痕测试（add-param-sweep 任务 3.3）。

覆盖：快路径 200 返回三值结论与 gates 披露、参数非法结构化拒绝
（422 RESEARCH_PARAM_INVALID）、专属留痕（entry_type="sweep"、ref_id=
中心条件指纹、不挂假设）。判定并集不含 sweep 的机制保证：
validation_service 的并集收集只查 screen/screen_use 类日志（源码事实，
add-param-sweep design Decision 5），本测试以留痕条目形态断言其入不了
并集收集集。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def sweep_env(tmp_path: Path):
    """500 bar 1h 数据（RSI warmup 200 → 请求起点留足前缀）+ 独立 db。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=500, freq="1h", start="2024-01-01", seed=11)
    df.to_parquet(data_dir / f"{SYMBOL}_1h.parquet")
    from core.persistence.db_ext import init_db_ext
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _payload(df, indicator="RSI", **extra):
    p = {
        "indicator": indicator,
        "condition": {"subject": "rsi_14", "action": "lt", "target": 65},
        "sweep": {"axis": "target", "values": [60, 65, 70]},
        "forward_bars": 12,
    }
    p.update(extra)
    return {
        "template": "param_sweep",
        "params": p,
        "data_range": {"symbol": SYMBOL, "timeframe": "1h",
                       "start": df.index[250].isoformat(),
                       "end": df.index[499].isoformat()},
    }


class TestParamSweepRoute:
    def test_fast_path_200_with_verdict_and_gates(
            self, api_client, sweep_env):
        api_client.app.state.db_path = sweep_env["db"]
        api_client.app.state.data_dir = sweep_env["data_dir"]
        resp = api_client.post("/api/research/analysis",
                               json=_payload(sweep_env["df"]))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert payload["verdict"] in {
            "plateau", "spike", "indeterminate", "unsupported"}
        assert payload["gates"] == {"q_threshold": 0.05,
                                    "min_samples": 30, "min_run": 2}
        assert {pt["state"] for pt in payload["points"]} <= {
            "effective", "ineffective", "insufficient_sample"}

    def test_unknown_indicator_structured_422(self, api_client, sweep_env):
        api_client.app.state.db_path = sweep_env["db"]
        api_client.app.state.data_dir = sweep_env["data_dir"]
        resp = api_client.post(
            "/api/research/analysis",
            json=_payload(sweep_env["df"], indicator="NOPE"))
        assert resp.status_code == 422
        body = resp.json()["detail"]
        assert body["code"] == "RESEARCH_PARAM_INVALID"
        assert body["detail"]["param"] == "indicator"

    def test_sweep_lineage_written_not_in_judged_union(
            self, api_client, sweep_env):
        """留痕：entry_type="sweep"、ref_id=中心条件指纹、不挂假设——
        形态上入不了判定并集收集集（validation_service 只收集
        screen/screen_use），检查不消耗验证数据区间。"""
        api_client.app.state.db_path = sweep_env["db"]
        api_client.app.state.data_dir = sweep_env["data_dir"]
        body = _payload(sweep_env["df"])
        resp = api_client.post("/api/research/analysis", json=body)
        assert resp.status_code == 200, resp.text

        logs = rr.list_research_log(sweep_env["db"], entry_type="sweep")
        assert len(logs) == 1, "param_sweep 不另写 analysis 条（一调用一留痕）"
        entry = logs[0]
        assert entry["hypothesis_id"] is None
        cond = resp.json()["payload"]["condition"]
        assert entry["ref_id"] == cond["fingerprint"], "ref_id=中心条件指纹"
        summary = json.loads(entry["summary"])
        assert summary["verdict"] == resp.json()["payload"]["verdict"]
        assert entry["entry_type"] not in {"screen", "screen_use", "analysis"}
        # 区间登记语义保留（漏报拦截 data_ranges 不可伪造）
        assert json.loads(entry["data_ranges"])[0]["symbol"] == SYMBOL
