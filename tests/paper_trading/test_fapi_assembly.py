"""fapi 装配集成测试（change-1 任务 5：lifespan 装配+健康披露接线）。

覆盖：①data_dir 缺省切 FAPI_DATA_DIR（三处共用配置单点）④启动回补
后任务可装载运行（TestClient 集成型）⑤data/market 现货归档不再被
纸盘写（切换后旧目录文件 mtime 不变）⑥固定订阅常驻（无任务运行时
12 路订阅面装配+事件按需路由）。

装配旁路说明：全域 conftest 置 FAPI_STREAM_DISABLED=1；本文件各用例
经 monkeypatch.delenv 显式开启装配面，流经 fapi_stream_factory 注入
替身（零真实网络），建库经 monkeypatch _default_rest_fetcher 注入
帧脚本。
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]


class _AssemblyStream:
    """装配流替身（记录 start/stop+路由回调可直调；零网络）。"""

    instances: list["_AssemblyStream"] = []

    def __init__(self, *, on_closed_bar, ws_base_url=None, proxies=None):
        self.on_closed_bar = on_closed_bar
        self.ws_base_url = ws_base_url
        self.proxies = proxies
        self.started = 0
        self.stopped = 0
        _AssemblyStream.instances.append(self)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def _enable_assembly(monkeypatch, tmp_path, *, rest_frames=None):
    """开启装配面（清旁路变量+FAPI_DATA_DIR 指向 tmp）。"""
    monkeypatch.delenv("FAPI_STREAM_DISABLED", raising=False)
    fapi_dir = tmp_path / "market_fapi"
    monkeypatch.setenv("FAPI_DATA_DIR", str(fapi_dir))
    if rest_frames is not None:
        monkeypatch.setattr(
            "core.paper_trading.runtime.feed._default_rest_fetcher",
            rest_frames)
    return fapi_dir


class TestDataDirSwitch:
    """①data_dir 缺省=FAPI_DATA_DIR（配置单点）。"""

    def test_assembly_switches_registry_data_dir(self, tmp_path, monkeypatch):
        """装配后 registry.data_dir=FAPI_DATA_DIR（三处共用同一点）。"""
        fapi_dir = _enable_assembly(monkeypatch, tmp_path)
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(
            db_path=tmp_path / "q.db",
            fapi_stream_factory=lambda **kw: _AssemblyStream(**kw))
        with TestClient(app) as client:
            scheduler = app.state.paper_scheduler
            assert Path(scheduler.registry.data_dir) == fapi_dir
            assert Path(app.state.data_dir) == fapi_dir


class TestFixedSubscriptionAssembly:
    """⑥固定订阅常驻+事件按需路由。"""

    def test_stream_started_and_routed(self, tmp_path, monkeypatch):
        """装配起流（start 调用）+无实例键事件丢弃+有实例键路由到达。"""
        _AssemblyStream.instances.clear()
        _enable_assembly(monkeypatch, tmp_path)
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(
            db_path=tmp_path / "q.db",
            fapi_stream_factory=lambda **kw: _AssemblyStream(**kw))
        with TestClient(app):
            stream = _AssemblyStream.instances[-1]
            assert stream.started == 1
            scheduler = app.state.paper_scheduler
            assert scheduler.registry.fapi_stream is stream

            # 按需路由：无 feed 实例键的事件丢弃不炸
            from core.data.fapi.stream import ClosedBarEvent

            orphan = ClosedBarEvent(
                symbol="BTCUSDT", timeframe="1m",
                open_time=pd.Timestamp("2026-10-28T00:00:00Z"),
                open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
            stream.on_closed_bar(orphan)  # 无实例——丢弃即通过

            # 有实例键：先造 feed（acquire）再投事件——feed 内存推进
            from core.paper_trading.runtime.feed import FeedSubscription

            feed = scheduler.registry.acquire(
                "BTCUSDT", "1m",
                FeedSubscription(on_frames=lambda df, idx: None))
            # 冷启动键首事件建序列
            stream.on_closed_bar(orphan)
            assert feed.latest_df is not None
            assert feed.latest_df.index[-1] == orphan.open_time
        assert stream.stopped == 1  # 关停序断流

    def test_out_of_fixed_keys_event_dropped_not_persisted(
            self, tmp_path, monkeypatch):
        """A10 周期外数据拒收：12 路外周期（30m）闭合事件注入→不消费
        不落盘（无 30m feed 实例——路由丢弃；12 路内既有 feed 不受
        串键影响——路由按 (symbol,timeframe) 精确匹配）。"""
        _AssemblyStream.instances.clear()
        fapi_dir = _enable_assembly(monkeypatch, tmp_path)
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(
            db_path=tmp_path / "q.db",
            fapi_stream_factory=lambda **kw: _AssemblyStream(**kw))
        with TestClient(app):
            stream = _AssemblyStream.instances[-1]
            scheduler = app.state.paper_scheduler
            # 12 路内键 feed 在场（1m——有实例对照面）
            from core.paper_trading.runtime.feed import FeedSubscription

            feed_1m = scheduler.registry.acquire(
                "BTCUSDT", "1m",
                FeedSubscription(on_frames=lambda df, idx: None))
            assert feed_1m.latest_df is None  # 无 parquet（冷启动）

            from core.data.fapi.stream import ClosedBarEvent

            stray = ClosedBarEvent(
                symbol="BTCUSDT", timeframe="30m",
                open_time=pd.Timestamp("2026-10-28T00:00:00Z"),
                open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
            stream.on_closed_bar(stray)  # 周期外事件——丢弃即通过

            # 不落盘：data_dir 无 30m 文件（落盘只经 feed._persist）
            assert not (fapi_dir / "BTCUSDT_30m.parquet").exists()
            # 12 路内 feed 不受串键影响（30m 事件不进 1m 序列）
            assert feed_1m.latest_df is None
            assert scheduler.registry.get("BTCUSDT", "30m") is None

    def test_no_backfill_without_assembly(self, tmp_path, monkeypatch):
        """旁路面（FAPI_STREAM_DISABLED）下流与建库均不装配（K2 面）。"""
        monkeypatch.setenv("FAPI_STREAM_DISABLED", "1")
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(db_path=tmp_path / "q.db")
        with TestClient(app):
            scheduler = app.state.paper_scheduler
            assert scheduler.registry.fapi_stream is None
            assert scheduler.registry.backfill is None


class TestBackfillThenMount:
    """④启动回补后任务可装载运行（建库→挂载→running 全链）。"""

    def test_backfill_completes_then_task_runs(self, tmp_path, monkeypatch):
        """建库线程（注入帧脚本）落齐 12 键→挂任务→running。"""
        frame = None  # 建库帧（per 键同帧——装载语义验证不挑数据）

        def _fake_rest(symbol, timeframe, start_ts, end_ts):
            return frame if frame is not None else pd.DataFrame()

        _AssemblyStream.instances.clear()
        _enable_assembly(
            monkeypatch, tmp_path, rest_frames=_fake_rest)
        from tests.helpers.data_factory import make_ohlcv

        frame = make_ohlcv(n=120, freq="4h", seed=11)
        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(
            db_path=tmp_path / "q.db",
            fapi_stream_factory=lambda **kw: _AssemblyStream(**kw))
        with TestClient(app) as client:
            scheduler = app.state.paper_scheduler
            backfill = scheduler.registry.backfill
            assert backfill is not None
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if backfill.status().get("BTCUSDT_4h") == "done":
                    break
                time.sleep(0.05)
            assert backfill.status().get("BTCUSDT_4h") == "done"  # 建库就绪

            # 策略+任务挂载（装载校验过 no_data——建库后数据就绪）
            from core.persistence.db_ext import init_db_ext  # noqa: F401
            from core.persistence.repository import paper
            from core.persistence.repository.strategy import save_strategy
            from tests.helpers.data_factory import make_dna

            save_strategy(
                tmp_path / "q.db", strategy_id="st-fapi",
                dna_json=make_dna().to_json(),
                symbol="BTCUSDT", timeframe="4h", status="active",
                gene_signature="sig_fapi")
            task_id = paper.create_task(
                tmp_path / "q.db", strategy_id="st-fapi",
                dna_snapshot_json=make_dna().to_json(),
                symbol="BTCUSDT", timeframe="4h",
                research_status_snapshot="unverified",
                initial_cash=10_000.0, leverage=1, window_bars=10_000)
            deadline = time.monotonic() + 15.0
            status = None
            while time.monotonic() < deadline:
                row = paper.get_task(tmp_path / "q.db", task_id)
                status = row and row["status"]
                if status == "running":
                    break
                time.sleep(0.1)
            assert status == "running"  # 建库后任务装载运行

    def test_spot_market_not_written_after_switch(
            self, tmp_path, monkeypatch):
        """⑤现货归档不再被纸盘写：装配后旧目录文件 mtime 不变。"""
        _AssemblyStream.instances.clear()
        fapi_dir = _enable_assembly(monkeypatch, tmp_path)
        # 预置"现货旧归档"（data/market 目录+marker 文件）
        spot_dir = tmp_path / "market"
        spot_dir.mkdir(exist_ok=True)
        marker = spot_dir / "BTCUSDT_4h.parquet"
        marker.write_bytes(b"legacy-spot-archive")
        mtime_before = marker.stat().st_mtime_ns

        from fastapi.testclient import TestClient

        from api.app import create_app

        app = create_app(
            db_path=tmp_path / "q.db",
            fapi_stream_factory=lambda **kw: _AssemblyStream(**kw))
        with TestClient(app):
            scheduler = app.state.paper_scheduler
            assert Path(scheduler.registry.data_dir) == fapi_dir
            assert scheduler.registry.data_dir != spot_dir
            # 装配运行期旧归档未被触碰（mtime 逐纳秒不变）
            assert marker.stat().st_mtime_ns == mtime_before
            assert marker.read_bytes() == b"legacy-spot-archive"
