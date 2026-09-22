"""condition_stats 路由 warmup 前扩与参数透传测试（fix-condition-stats-
forward-return U3 / tasks 2.1）。

覆盖：warmup 生效（前缀只用不算、sample_size 只计请求区间信号）、起点
不足结构化拒绝（422 RESEARCH_WARMUP_INSUFFICIENT）、forward_bars 非法值
422 RESEARCH_PARAM_INVALID、合法透传产出 forward_return_{N}b 键，以及重组
（heavy）路径下 warmup 不足的结构化错误码——202 + 轮询 failed，reason
前缀 [RESEARCH_WARMUP_INSUFFICIENT] 且携带 message（executor 对
ResearchError 结构化收尾，闭合 422 语义在常用区间的缺口）。
"""

import time
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository import research as rr
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"


@pytest.fixture()
def warmup_env(tmp_path: Path):
    """600 bar 4h 数据（RSI lookback 50 → warmup=200）+ 独立 db。"""
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df = make_ohlcv(n=600, freq="4h", start="2024-01-01", seed=11)
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    from core.persistence.db_ext import init_db_ext
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return {"data_dir": data_dir, "db": db, "df": df}


def _payload(df, start_i, end_i, **extra):
    return {
        "template": "condition_stats",
        "params": {
            "indicator": "RSI", "params": {"period": 14},
            "condition": {"subject": "close", "action": "gt",
                          "target": float(df["close"].median())},
            **extra,
        },
        "data_range": {"symbol": SYMBOL, "timeframe": "4h",
                       "start": df.index[start_i].isoformat(),
                       "end": df.index[end_i].isoformat()},
    }


def _mount(api_client, env):
    api_client.app.state.db_path = env["db"]
    api_client.app.state.data_dir = env["data_dir"]


class TestConditionStatsWarmupRoute:
    def test_warmup_prefix_active_and_stats_scoped_to_request(
            self, api_client, warmup_env):
        """warmup 生效：payload.warmup_bars>0、total_bars>请求区间 bar 数、
        sample_size 只计请求区间信号（warmup 段命中被排除）。"""
        _mount(api_client, warmup_env)
        df = warmup_env["df"]
        start_i, end_i = 300, 599
        resp = api_client.post("/api/research/analysis",
                               json=_payload(df, start_i, end_i))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        payload = body["payload"]

        request_bars = int(((df.index[end_i] - df.index[start_i])
                            / pd.Timedelta(hours=4)) + 1)  # 300
        assert payload["warmup_bars"] == 200  # RSI → max(200, 50×3=150)=200
        assert payload["total_bars"] == request_bars + 200
        assert payload["total_bars"] > request_bars

        # 信号只计请求区间：warmup 段命中不进 hit_count（独立重算）
        med = float(df["close"].median())
        req = df.iloc[start_i:end_i + 1]
        n_req_hits = int((req["close"] > med).sum())
        n_all_hits = int((df["close"] > med).sum())
        assert n_all_hits > n_req_hits, "构造要求 warmup 段确有被排除的命中"
        assert payload["hit_count"] == n_req_hits
        # 有效前向信号 + 尾部丢弃 == 请求区间命中数
        assert body["sample_size"] + payload["dropped_tail_signals"] == n_req_hits

    def test_warmup_insufficient_rejected_422(self, api_client, warmup_env):
        """请求起点贴近数据起点（前仅 100 bar < 200）→ 422 结构化拒绝。"""
        _mount(api_client, warmup_env)
        resp = api_client.post("/api/research/analysis",
                               json=_payload(warmup_env["df"], 100, 500))
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_WARMUP_INSUFFICIENT"

    def test_forward_bars_invalid_rejected_422(self, api_client, warmup_env):
        """forward_bars=0 非法 → 422 RESEARCH_PARAM_INVALID（Decision 7）。"""
        _mount(api_client, warmup_env)
        resp = api_client.post("/api/research/analysis", json=_payload(
            warmup_env["df"], 300, 599, forward_bars=0))
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_PARAM_INVALID"

    def test_forward_bars_passthrough_output_key(self, api_client, warmup_env):
        """forward_bars=6 合法透传 → payload 含 forward_return_6b（无默认键）。"""
        _mount(api_client, warmup_env)
        resp = api_client.post("/api/research/analysis", json=_payload(
            warmup_env["df"], 300, 599, forward_bars=6))
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        assert "forward_return_6b" in payload
        assert "forward_return_24b" not in payload
        assert payload["forward_bars"] == 6

    def test_heavy_path_warmup_insufficient_reason_carries_code(
            self, api_client, tmp_path):
        """重组路径结构化错误码：>10000 bar → 202；warmup 不足在 _job 内抛
        ResearchError → executor 落 failed 终态，reason 前缀
        [RESEARCH_WARMUP_INSUFFICIENT] 并携带 message（AI 轮询可解析
        错误码，与快路径 422 语义对齐）。"""
        data_dir = tmp_path / "market"
        data_dir.mkdir()
        df = make_ohlcv(n=10500, freq="4h", start="2020-01-01", seed=3)
        df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
        api_client.app.state.data_dir = data_dir  # db 保持 fixture（executor 单例绑它）

        # executor 模块单例可能与其它测试的 tmp db 绑定——重置绑当前
        # fixture db（照 test_research_routes.TestWatchdogRaceViaApi 形制）
        import core.analysis.executor as exec_mod
        dbp = Path(api_client.app.state.db_path)
        old = exec_mod._EXECUTOR
        exec_mod._EXECUTOR = None
        exec_mod.init_executor(dbp)
        try:
            resp = api_client.post("/api/research/analysis",
                                   json=_payload(df, 100, 10499))
            assert resp.status_code == 202, resp.text
            task_id = resp.json()["task_id"]
            task = None
            deadline = time.time() + 30
            while time.time() < deadline:
                task = rr.get_analysis_task(dbp, task_id)
                if task and task["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.1)
            assert task is not None and task["status"] == "failed"
            assert task["reason"].startswith("[RESEARCH_WARMUP_INSUFFICIENT]")
            assert "warmup 不足" in task["reason"]
        finally:
            if exec_mod._EXECUTOR is not None:
                exec_mod._EXECUTOR.shutdown()
            exec_mod._EXECUTOR = old
