"""fix-paper-deviation-contrast 组 0：final 固化路径接线行为锚。

端到端链路（任务 0.4——非直调固化函数）：with TestClient 起 lifespan
（app 装配注入 on_concluded）→ 真实 executor 链驱动至终态 → _finalize
回调链 → 固化落 pt_report → final 路由 200。MCP get_paper_report
(final=True) 的可达性=该 REST final 路由 200（工具层薄透传白名单
路径分派由 tests/mcpserver/test_paper_tools.py 既有覆盖——下游 200
即透传可用）。固化失败注入→终态不受影响（解耦断言）。

基建形制沿 tests/paper_trading/test_paper_executor.py _Harness（合成
parquet+dummy registry）；任务行直建沿 test_reporting.py 测试通道。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna, make_ohlcv

pytestmark = [pytest.mark.integration]

# 合成数据基线（沿 test_paper_executor 常量——seed 固定信号分布确定）
_N_BARS = 200
_SEED = 7
# 全回放边界（合成帧历史时刻均早于它——旁路 created_at 起始过滤）
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"
# 合成帧首 bar 时刻（started_at 对齐数据起点——对照区间非空）
_DATA_START = "2024-01-01T00:00:00+00:00"


class _WiringHarness:
    """接线装配（lifespan 起真 app+合成 parquet 数据域+任务行）。"""

    def __init__(self, tmp_path: Path):
        from api.app import create_app

        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        df = make_ohlcv(n=_N_BARS, freq="4h", seed=_SEED, base_price=100.0)
        save_parquet(df, self.data_dir / "BTCUSDT_4h.parquet")
        self.frames = compute_all_indicators(df)

        self.db = tmp_path / "wiring.db"
        init_db_ext(self.db)
        self.app = create_app(db_path=self.db, data_dir=self.data_dir)
        self.task_id = paper.create_task(
            self.db, strategy_id="strat-1",
            dna_snapshot_json=make_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=50)

    def drive_to_completed(self) -> None:
        """真实执行器链驱动至终态（窗口到点 completed）。

        on_concluded 接 lifespan 装配调度器的 _executor_concluded——
        executor._finalize 终态回调经调度器透传到 app 注入的固化回调
        （接线链的真实路径，非直调固化函数）。
        """
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        from core.paper_trading.runtime.feed import FeedRegistry

        scheduler = self.app.state.paper_scheduler
        scheduler.stop()  # 停守护循环（防拾取竞态——回调链不受影响）
        # dummy registry（不发网络——沿 _Harness 形制）
        registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=lambda *a, **k: None,
            funding_refresher=lambda **k: None)
        from core.paper_trading.runtime.feed import FeedSubscription

        dummy = FeedSubscription(on_frames=lambda df, idx: None)
        registry.acquire("BTCUSDT", "4h", dummy)
        row = paper.get_task(self.db, self.task_id)
        executor = PaperTaskExecutor(
            self.db, row, registry=registry,
            on_concluded=scheduler._executor_concluded,
            start_after=_REPLAY_ALL)
        assert executor.prepare() is True
        paper.update_task(
            self.db, self.task_id, status="running", started_at=_DATA_START)
        executor.process_frames(self.frames, 0)


class TestFinalizationWiring:
    """终态触发→固化落 pt_report→final 路由 200 的端到端链。"""

    def test_terminal_conclusion_finalizes_and_final_route_200(
            self, tmp_path):
        """端到端：executor 终态回调链触发固化（version=1 落行）→
        final 路由 200（此前终态恒 404——触发面未接线）；固化版报告
        结构完整（三层在場）。"""
        from fastapi.testclient import TestClient

        h = _WiringHarness(tmp_path)
        with TestClient(h.app) as client:
            h.drive_to_completed()

            task = paper.get_task(h.db, h.task_id)
            assert task["status"] == "completed"  # 窗口到点自动结题
            row = paper.get_report(h.db, h.task_id, 1)
            assert row is not None, "终态回调须已固化落 pt_report"
            assert row["report_version"] == 1

            resp = client.get(
                f"/api/paper-trading/tasks/{h.task_id}/report/final")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["report_version"] == 1
            for layer in ("performance", "deviation", "evidence"):
                assert layer in body["report"]

    def test_finalize_failure_does_not_rollback_terminal(
            self, tmp_path, monkeypatch):
        """固化失败与终态迁移解耦（任务 0.2）：finalize_report 注入
        抛错 → 终态照常落库（completed 不受影响）+回调不炸（驱动
        正常返回）+无固化行（失败事实如实——final 路由仍 404）。"""
        from fastapi.testclient import TestClient

        def _boom(*args, **kwargs):
            raise RuntimeError("injected finalize failure")

        monkeypatch.setattr(
            "core.paper_trading.reporting.finalize_report", _boom)
        h = _WiringHarness(tmp_path)
        with TestClient(h.app) as client:
            h.drive_to_completed()  # 回调内异常被吞——驱动不炸

            task = paper.get_task(h.db, h.task_id)
            assert task["status"] == "completed", "终态不受固化失败影响"
            assert paper.get_report(h.db, h.task_id, 1) is None
            resp = client.get(
                f"/api/paper-trading/tasks/{h.task_id}/report/final")
            assert resp.status_code == 404  # 无固化版（失败如实可见）
