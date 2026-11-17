"""retire-legacy-subsystems 任务 15.4：REST 退役语义终验。

全域退役端点逐名不可达断言（404 语义）——覆盖进化/实盘/假设/旧验证/
场景/规则校验/pools/feature-requests 八域；strategies 域四路由
（backtest/compare/batch-backtest/batch-backtest/stream，撞
{strategy_id} 动态段实测 405）由 tests/api/test_retire_verify_sessions.py
承载，本文件不重复。保留端点可用性锚：backtest-sandbox 200（真跑）、
log 200、strategies CRUD 200。

路径来源：git main 各退役路由文件实查（evolution.py 11 路由/
live_trading.py 8/hypotheses.py 4+hypothesis_cards.py 1/
validation.py 旧五/scene.py 2/validate.py 2/plugins pools 1/
log.py feature-requests 3）——与 frontend-handover.md 清单的差异
（scenes 实为 /api/scene/types+/api/validate/scene；进化无
batch-delete 路由；history 挂 /tasks/{id} 下）以路由实现为准。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

# (method, path) —— 预期全部 404（无撞路径）
RETIRED_ENDPOINTS = [
    # ── 进化（11）──
    ("POST", "/api/evolution/tasks"),
    ("GET", "/api/evolution/tasks"),
    ("GET", "/api/evolution/tasks/ev-x"),
    ("GET", "/api/evolution/tasks/ev-x/history"),
    ("POST", "/api/evolution/tasks/ev-x/pause"),
    ("POST", "/api/evolution/tasks/ev-x/stop"),
    ("POST", "/api/evolution/tasks/ev-x/resume"),
    ("GET", "/api/evolution/tasks/ev-x/strategies"),
    ("GET", "/api/evolution/tasks/ev-x/discovered-strategies"),
    ("GET", "/api/evolution/runner-status"),
    ("GET", "/api/evolution/strategies"),
    # ── 实盘（8，前缀 /api/live-trading）──
    ("POST", "/api/live-trading/tasks"),
    ("POST", "/api/live-trading/tasks/lt-x/start"),
    ("POST", "/api/live-trading/tasks/lt-x/stop"),
    ("GET", "/api/live-trading/tasks/lt-x/decisions"),
    ("GET", "/api/live-trading/tasks"),
    ("GET", "/api/live-trading/tasks/lt-x"),
    ("PATCH", "/api/live-trading/tasks/lt-x"),
    ("DELETE", "/api/live-trading/tasks/lt-x"),
    # ── 假设（4）+ cards（1）──
    ("GET", "/api/research/hypotheses"),
    ("POST", "/api/research/hypotheses"),
    ("GET", "/api/research/hypotheses/hyp-x"),
    ("POST", "/api/research/hypotheses/hyp-x/transition"),
    ("GET", "/api/research/hypothesis-cards"),
    # ── 旧验证（6）──
    ("POST", "/api/research/validate-backtest"),
    ("POST", "/api/research/staged-validation"),
    ("POST", "/api/research/random-window-test"),
    ("GET", "/api/research/union-balance"),
    ("GET", "/api/research/validation/st-x/report"),
    ("POST", "/api/research/translate/hyp-x"),
    # ── 场景/规则校验（4）──
    ("GET", "/api/scene/types"),
    ("POST", "/api/validate/scene"),
    ("POST", "/api/validate"),
    ("POST", "/api/validate/rules"),
    # ── pools（1）+ feature-requests（3）──
    ("GET", "/api/research/plugins/pools"),
    ("POST", "/api/research/feature-requests"),
    ("GET", "/api/research/feature-requests"),
    ("PATCH", "/api/research/feature-requests/fr-x"),
]


class TestRetiredEndpointsUnreachable:
    """八域退役端点逐名 404——「路由未注册」的运行时证明。"""

    @pytest.mark.parametrize(("method", "path"), RETIRED_ENDPOINTS)
    def test_retired_404(self, api_client, method: str, path: str) -> None:
        if method == "POST":
            resp = api_client.post(path, json={})
        elif method == "PATCH":
            resp = api_client.patch(path, json={})
        elif method == "DELETE":
            resp = api_client.delete(path)
        else:
            resp = api_client.get(path)
        assert resp.status_code == 404, (
            f"{method} {path} 退役端点意外可达: {resp.status_code}")

    def test_openapi_free_of_retired_prefixes(self, api_client) -> None:
        """OpenAPI 路由表静态证明：退役前缀与退役子路径零注册。"""
        paths = api_client.get("/openapi.json").json()["paths"]
        banned = ("/api/evolution/", "/api/live-trading/",
                  "/hypotheses", "/hypothesis-cards",
                  "/validate-backtest", "/staged-validation",
                  "/random-window-test", "/union-balance",
                  "/translate/", "/scene/types", "/validate/scene",
                  "/api/validate", "/plugins/pools", "/feature-requests")
        hit = [p for p in paths if any(p.startswith(b) or b in p
                                       for b in banned)]
        assert not hit, f"OpenAPI 残留退役路径: {hit}"


class TestRetainedEndpointsAlive:
    """保留端点可用性（15.4：backtest-sandbox/log/strategies CRUD）。"""

    def test_log_200(self, api_client) -> None:
        assert api_client.get("/api/research/log").status_code == 200

    def test_strategies_list_200(self, api_client) -> None:
        assert api_client.get("/api/strategies").status_code == 200

    def test_backtest_sandbox_200(self, api_client, tmp_data_dir) -> None:
        """唯一回测通道真跑 200（K1 后含 record_id 与明细三表写入）。"""
        import json

        import pandas as pd

        from tests.helpers.data_factory import make_dna, make_ohlcv

        make_ohlcv(n=300, freq="4h", base_price=40000.0,
                   seed=42).to_parquet(tmp_data_dir / "BTCUSDT_4h.parquet")
        r = api_client.post("/api/strategies", json={
            "name": "retire-404-anchor",
            "dna": json.loads(make_dna().to_json()),
            "symbol": "BTCUSDT", "timeframe": "4h",
            "source": "manual", "tags": "", "notes": ""})
        assert r.status_code == 201, r.text
        sid = r.json()["strategy_id"]
        df = pd.read_parquet(tmp_data_dir / "BTCUSDT_4h.parquet")
        resp = api_client.post("/api/research/backtest-sandbox", json={
            "strategy_id": sid, "symbol": "BTCUSDT", "timeframe": "4h",
            "start": str(df.index[220].date()),
            "end": str(df.index[-1].date())})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["record_id"], "sandbox 须落验证记录（K1）"
        assert body["sandbox"] is True
