"""回放契约面测试（L1-G/H+L2-F+L3 精炼——change add-replay-engine
任务 2.2 完成标准）。"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.repository import paper
from core.replay import ReplayError, ReplayRunner
from tests.replay._env import ReplayEnv


class TestEndToEnd:
    """L3-A 端到端（单周期合成数据全链路）。"""

    def test_e2e_smoke_full_chain(self, tmp_path):
        """端到端：run()→pt_task 三态→明细三表→ReplayResult 全字段。"""
        env = ReplayEnv(tmp_path, n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        res = rr.run(env.db, env.strategy_id, start, end,
                     task_id="e2e0001", fee=0.001, slippage=0.0005)
        assert res.status in ("completed", "stopped")
        assert res.task_id == "e2e0001"
        assert res.symbol == "BTCUSDT" and res.timeframe == "4h"
        assert res.bars_processed > 0
        assert res.last_bar_time is not None
        task = paper.get_task(env.db, "e2e0001")
        assert task["status"] == res.status
        # 明细三表经既有查询通道可取（引用组契约）
        fills = paper.list_fills(env.db, "e2e0001", limit=10)
        eqs = paper.list_equity_snapshots(env.db, "e2e0001", limit=10)
        assert eqs, "权益快照在案（落库=执行行为一部分）"
        assert isinstance(fills, list)

    def test_determinism_same_input_same_output(self, tmp_path):
        """L2-F 确定性：同输入重跑两次——成交/权益/终态逐位一致。"""
        env = ReplayEnv(tmp_path, n_bars=500)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=200)
        r1 = rr.run(env.db, env.strategy_id, start, end, task_id="det01a")
        r2 = rr.run(env.db, env.strategy_id, start, end, task_id="det01b")
        assert r1.status == r2.status
        assert r1.total_trades == r2.total_trades
        assert r1.total_pnl == pytest.approx(r2.total_pnl)
        assert r1.bars_processed == r2.bars_processed
        f1 = paper.list_fills(env.db, "det01a", limit=1000)
        f2 = paper.list_fills(env.db, "det01b", limit=1000)
        assert len(f1) == len(f2)
        for a, b in zip(f1, f2):
            assert a["quantity"] == pytest.approx(b["quantity"])
            assert a["price"] == pytest.approx(b["price"])
            assert a["kind"] == b["kind"]


class TestPerformanceAnchor:
    """L3-A1 性能锚：半年级 1h 区间（≥4,400 根）单任务分钟级
    （≤60s 上限断言——实测预期秒级；超限即红，CI 超限单进程复跑
    分诊）。"""

    def test_half_year_1h_under_60s(self, tmp_path):
        import time

        env = ReplayEnv(tmp_path, n_bars=4400, freq="1h")
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=3000)
        t0 = time.monotonic()
        res = rr.run(env.db, env.strategy_id, start, end,
                     task_id="perf01")
        elapsed = time.monotonic() - t0
        assert res.status in ("completed", "stopped")
        assert res.bars_processed >= 3000, "半年级区间实际处理"
        assert elapsed < 60.0, f"分钟级上限（实测 {elapsed:.1f}s）"


class TestLoadRejections:
    """L1-H 装载失败码族（载入即拒——pt_task 行不建）。"""

    def test_strategy_not_found(self, tmp_path):
        env = ReplayEnv(tmp_path, n_bars=300)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, "st_not_exist", start, end)
        assert ei.value.code == "STRATEGY_NOT_FOUND"
        # 行不建纪律：库里只有装配时 0 条任务行
        tasks = paper.list_tasks(env.db, limit=10)
        assert tasks == []

    def test_data_insufficient(self, tmp_path):
        env = ReplayEnv(tmp_path, n_bars=300)
        (env.data_dir / "BTCUSDT_4h.parquet").unlink()  # 数据删除
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, env.strategy_id, start, end)
        assert ei.value.code == "RESEARCH_DATA_INSUFFICIENT"

    def test_warmup_insufficient_explicit_reject(self, tmp_path):
        """前缀不足显式拒绝（不静默缩短）——起点贴着数据头。"""
        env = ReplayEnv(tmp_path, n_bars=300)
        rr = ReplayRunner(env.data_dir)
        idx = env.df.index
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, env.strategy_id,
                   idx[10].isoformat(), idx[-1].isoformat())
        assert "WARMUP" in ei.value.code

    def test_series_mode_rejected(self, tmp_path):
        """series 档显式拒绝（能力边界表述，不静默降级）。"""
        import dataclasses

        from tests.helpers.data_factory import make_dna

        env = ReplayEnv(tmp_path, n_bars=300)
        # 结构合法的 series 档 dna（validate_dna 过、paper 链装载拒）
        dna = make_dna()
        series_dna = dataclasses.replace(
            dna,
            risk_genes=dataclasses.replace(
                dna.risk_genes, sl_mode="series",
                sl_series_source={
                    "indicator": "EMA", "params": {"period": 20}}))
        from core.persistence.repository.strategy import save_strategy

        save_strategy(env.db, strategy_id="st_series",
                      dna_json=series_dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      status="draft")  # draft 免去重（同签名分流）
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, "st_series", start, end)
        assert ei.value.code == "TRADING_INCOMPATIBLE"
        tasks = paper.list_tasks(env.db, limit=10)
        assert tasks == [], "行不建"
