"""研究执行器 lifespan 集成测试（add-ai-research-mcp 1.4 验收绑定）。

验收目标：长计算占满研究线程池 worker 期间，交易端点
（/api/trading/runner-status）与进化任务查询保持响应——计算压力
不得由交易服务承担（D3 验收绑定）。
"""

import sqlite3
import time

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import get_executor
from core.persistence.repository import research as rr


class TestLifespanWiring:
    def test_research_tables_selfhealed_on_startup(self, api_client) -> None:
        """init_db_ext 只建 legacy 表；研究域八表经 lifespan 自愈到位。"""
        # api_client 夹具已跑完 lifespan startup（TestClient 上下文内）
        from starlette.testclient import TestClient  # noqa: F401

        # 直接对测试库查表（夹具的 db_path 在 app.state 上）
        # —— 这里用执行器真实写读来证明表可用
        ex = get_executor()
        out = ex.submit("probe", lambda stop: "ok", estimated_cost_s=5.0)
        t = rr.get_analysis_task(ex._db_path, out["task_id"])
        assert t is not None  # analysis_tasks 表存在且可写


class TestTradingNotBlockedByResearch:
    def test_runner_status_responsive_during_heavy_research(self, api_client) -> None:
        """长计算运行期间 runner-status 响应（核心验收绑定）。"""
        ex = get_executor()
        started = {"flag": False}

        def heavy(stop):
            started["flag"] = True
            for _ in range(60):  # ~3s CPU 占用（worker=2，占一路仍留余量）
                if started.get("n") is None:
                    pass
                _spin(0.05)
                stop()
            return "heavy-done"

        def _spin(seconds: float) -> None:
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                pass  # 纯 CPU 自旋（模拟指标计算负载）

        out = ex.submit("heavy_template", heavy, estimated_cost_s=5.0, timeout_s=30.0)
        assert out["fast_path"] is False

        # 等任务真正开始占用 worker
        deadline = time.monotonic() + 3.0
        while not started["flag"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started["flag"], "重任务未开始"

        # 重计算运行期间：交易端点与进化 runner-status 必须快速响应
        for endpoint in ["/api/trading/runner-status", "/api/evolution/runner-status"]:
            t0 = time.monotonic()
            resp = api_client.get(endpoint)
            elapsed = time.monotonic() - t0
            assert resp.status_code == 200, f"{endpoint}: {resp.status_code}"
            assert elapsed < 2.0, f"{endpoint} 响应 {elapsed:.2f}s（被研究计算拖累）"
