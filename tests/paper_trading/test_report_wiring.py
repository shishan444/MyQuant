"""fix-paper-deviation-contrast 组 0+组 2：final 固化路径接线与对照源
行为锚。

端到端链路（任务 0.4——非直调固化函数）：with TestClient 起 lifespan
（app 装配注入 on_concluded）→ 真实 executor 链驱动至终态 → _finalize
回调链 → 固化落 pt_report → final 路由 200。MCP get_paper_report
(final=True) 的可达性=该 REST final 路由 200（工具层薄透传白名单
路径分派由 tests/mcpserver/test_paper_tools.py 既有覆盖——下游 200
即透传可用）。固化失败注入→终态不受影响（解耦断言）。

组 2（2.1）：终态固化对照源=replay；replay 直跑降级如实（warmup
不足场景）；REDUCE 基因 diff 纯净性——同构性探针实证 paper executor
与 replay 直跑在相同 DNA/参数/数据下逐 bar 一致（末权益差 0.0），
final 对照迁 replay 后 legacy VBT「减仓不终止」缺陷差不再混入 diff。

基建形制沿 tests/paper_trading/test_paper_executor.py _Harness（合成
parquet+dummy registry）；任务行直建沿 test_reporting.py 测试通道。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna, make_ohlcv, make_scaled_dna

pytestmark = [pytest.mark.integration]

# 合成数据基线（沿 test_paper_executor 常量——seed 固定信号分布确定）
_N_BARS = 200
_SEED = 7
# 全回放边界（合成帧历史时刻均早于它——旁路 created_at 起始过滤）
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"
# 合成帧首 bar 时刻（started_at 对齐数据起点——对照区间非空）
_DATA_START = "2024-01-01T00:00:00+00:00"
# REDUCE 同构性几何（2.1）：replay 直跑 warmup 下限 200（derive_warmup
# floor，D5）+window 100——区间起点对齐第 200 根，起点前恰足 warmup
_CONTRAST_WARMUP = 200
_CONTRAST_WINDOW = 100
_N_BARS_CONTRAST = _CONTRAST_WARMUP + _CONTRAST_WINDOW + 160


class _WiringHarness:
    """接线装配（lifespan 起真 app+合成 parquet 数据域+任务行）。

    REDUCE 同构性场景（组 2）：dna 工厂/window_bars/n_bars 可参数化
    ——缺省沿组 0 基线（200 根+简单 DNA+50 窗）。
    """

    def __init__(self, tmp_path: Path, *, dna=None, n_bars: int = _N_BARS,
                 window_bars: int = 50):
        from api.app import create_app

        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = make_ohlcv(
            n=n_bars, freq="4h", seed=_SEED, base_price=100.0)
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.frames = compute_all_indicators(self.df)

        self.db = tmp_path / "wiring.db"
        init_db_ext(self.db)
        self.app = create_app(db_path=self.db, data_dir=self.data_dir)
        self.window_bars = window_bars
        self.task_id = paper.create_task(
            self.db, strategy_id="strat-1",
            dna_snapshot_json=(dna or make_dna()).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1, window_bars=window_bars)

    def drive_to_completed(self, *, from_idx: int = 0,
                           started_at: str = _DATA_START) -> None:
        """真实执行器链驱动至终态（窗口到点 completed）。

        on_concluded 接 lifespan 装配调度器的 _executor_concluded——
        executor._finalize 终态回调经调度器透传到 app 注入的固化回调
        （接线链的真实路径，非直调固化函数）。固化回调内 replay 直跑
        同步执行（contrast_runner 已由装配注入）。
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
            self.db, self.task_id, status="running", started_at=started_at)
        executor.process_frames(self.frames, from_idx)


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
            # 组 2（2.1）：终态固化对照源=replay（真实回调链内装配注入的
            # contrast_runner 到达）；本几何区间起点=数据起点，起点前 0
            # 根不满足 warmup 下限 200——replay 直跑降级如实（原因含
            # warmup 语义，非静默 None），按形态披露对照引擎
            dev = body["report"]["deviation"]
            assert dev["contrast_source"] == "replay"
            assert dev["backtest"] is None
            assert "warmup" in dev["unavailable_reason"]

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


class TestFinalContrastPurity:
    """组 2（2.1）：REDUCE 基因 diff 纯净性——final 对照迁 replay 后
    与执行核心同构（相同 DNA/参数/数据逐 bar 一致），legacy VBT
    「减仓不终止」缺陷差不再混入 diff。"""

    def test_reduce_gene_final_diff_pure_and_no_vbt(
            self, tmp_path, monkeypatch):
        """含 REDUCE 基因策略终态固化：diff 四键=0（两侧同构）+对照成绩
        可对拍+final 路径全程不调 legacy VBT 桥（哨兵断言）。"""
        from fastapi.testclient import TestClient

        def _vbt_sentinel(*args, **kwargs):
            raise AssertionError(
                "final 路径不得调用 legacy VBT 桥（对照源已迁 replay）")

        monkeypatch.setattr(
            "core.analysis.backtest_bridge.run_backtest_on_range",
            _vbt_sentinel)
        h = _WiringHarness(
            tmp_path, dna=make_scaled_dna(),
            n_bars=_N_BARS_CONTRAST, window_bars=_CONTRAST_WINDOW)
        start_ts = h.df.index[_CONTRAST_WARMUP].isoformat()
        with TestClient(h.app):
            h.drive_to_completed(
                from_idx=_CONTRAST_WARMUP, started_at=start_ts)

            # 前提锚：场景真实触发 REDUCE（无 reduce 行则「纯净性」
            # 空转——同构性无从谈起）
            fills = paper.list_fills(h.db, h.task_id, limit=10**9)
            kinds = {f["kind"] for f in fills}
            assert "reduce" in kinds, f"REDUCE 未触发: {kinds}"

            row = paper.get_report(h.db, h.task_id, 1)
            assert row is not None
            report = json.loads(row["report_json"])
        dev = report["deviation"]
        # 对照源+成功态（同构几何下 warmup 恰足——不降级）
        assert dev["contrast_source"] == "replay"
        assert dev["unavailable_reason"] is None
        assert dev["backtest"] is not None
        # diff 纯净性：两侧逐 bar 一致（同构性探针实证末权益差 0.0）
        assert dev["diff"] == {"total_return": 0.0,
                               "sharpe_ratio": 0.0,
                               "max_drawdown": 0.0,
                               "win_rate": 0.0}
        # 对拍锚：diff.total_return=纸盘侧−对照侧——纸盘侧从库内权益
        # 行首末重算（可手工复算，不依赖报告内嵌值）
        eq_rows = paper.list_equity_snapshots(h.db, h.task_id, limit=10**9)
        paper_tr = float(eq_rows[-1]["equity"]) / float(eq_rows[0]["equity"]) - 1
        assert dev["diff"]["total_return"] == pytest.approx(
            paper_tr - dev["backtest"]["total_return"], abs=1e-12)
