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


class TestBranchCoverage:
    """主体路径补面：atr 档补列/funding 归档索引/坏 dna 拒绝。"""

    def test_atr_mode_loads_and_runs(self, tmp_path):
        """atr 档策略：装载链补 atr_{period} 列+回放走通。"""
        import dataclasses

        from tests.helpers.data_factory import make_dna

        dna = make_dna()
        atr_dna = dataclasses.replace(
            dna, risk_genes=dataclasses.replace(
                dna.risk_genes, sl_mode="atr", atr_period=14,
                stop_loss=2.0, take_profit=4.0))  # ATR 倍数值域
        env = ReplayEnv(tmp_path, dna=atr_dna, n_bars=400)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=200)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="atr01")
        assert res.status in ("completed", "stopped")
        assert res.bars_processed > 0

    def test_funding_archive_indexed(self, tmp_path):
        """funding 归档在案：装载建索引+结算行走 settled（非全挂起）。"""
        import pandas as pd

        env = ReplayEnv(tmp_path, n_bars=400)
        # 造 8h 网格 funding 归档（区间内每 48h 一个结算点）
        idx = pd.date_range(
            env.df.index[0], env.df.index[-1], freq="48h", tz="UTC")
        frame = pd.DataFrame({"funding_rate": [0.0001] * len(idx)},
                             index=idx)
        from core.data.storage import save_parquet

        save_parquet(frame, env.data_dir / "BTCUSDT_funding.parquet")
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=200)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="fund01")
        records = paper.list_funding_records(
            env.db, "fund01", limit=1000)
        assert records, "结算点在案"
        settled = [r for r in records if r["status"] == "settled"]
        assert settled, "归档在案时结算非全挂起"
        assert settled[0]["rate"] == pytest.approx(0.0001)

    def test_invalid_dna_value_range_rejected(self, tmp_path):
        """结构合法但值域不过的 dna（INVALID_DNA 分支——区别于坏
        json 快照的 RESEARCH_STORED_DATA_INVALID）：position_size 超
        0-1 值域，validate_dna 拒。"""
        import dataclasses

        from core.persistence.repository.strategy import save_strategy
        from tests.helpers.data_factory import make_dna

        env = ReplayEnv(tmp_path, n_bars=300)
        bad = dataclasses.replace(
            make_dna(), risk_genes=dataclasses.replace(
                make_dna().risk_genes, position_size=5.0))
        save_strategy(env.db, strategy_id="st_val",
                      dna_json=bad.to_json(), symbol="BTCUSDT",
                      timeframe="4h", status="draft")
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, "st_val", start, end)
        assert ei.value.code == "INVALID_DNA"

    def test_plugin_sources_rejected(self, tmp_path):
        """插件信号源在场即拒（能力边界——回放走纸盘执行链未接入
        插件组装；含混合形态，防静默失真）。"""
        import dataclasses

        from core.persistence.repository.strategy import save_strategy
        from tests.helpers.data_factory import make_dna

        env = ReplayEnv(tmp_path, n_bars=300)
        plugin_dna = dataclasses.replace(
            make_dna(),
            signal_sources=[{
                "type": "formula", "ref": "exit_distance_box"}])
        save_strategy(env.db, strategy_id="st_plug",
                      dna_json=plugin_dna.to_json(), symbol="BTCUSDT",
                      timeframe="4h", status="draft")
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, "st_plug", start, end)
        assert ei.value.code == "TRADING_INCOMPATIBLE"

    def test_empty_funding_archive_treated_as_missing(self, tmp_path):
        """空 funding 归档（parquet 在盘但空帧）=空索引全挂起——与
        归档缺文件同语义（feeder._load_funding_index 空帧分支）。"""
        import pandas as pd

        env = ReplayEnv(tmp_path, n_bars=400)
        from core.data.storage import save_parquet

        save_parquet(
            pd.DataFrame({"funding_rate": pd.Series(dtype=float)}),
            env.data_dir / "BTCUSDT_funding.parquet")
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=200)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="fund03")
        assert res.status in ("completed", "stopped"), "空归档非错误"
        from core.persistence.repository import paper

        records = paper.list_funding_records(env.db, "fund03", limit=1000)
        assert all(r["status"] == "pending" for r in records), (
            "空归档=全挂起（与缺文件同语义）")

    def test_atr_nonstandard_period_computed_on_load(self, tmp_path):
        """atr 档非标周期（21）现算分支：compute_all_indicators 产
        标准参数集（atr_14），atr_21 不在主帧列——_ensure_atr 现算
        补列后回放走通（atr_{period} 供数契约）。"""
        import dataclasses

        from tests.helpers.data_factory import make_dna

        dna = make_dna()
        atr_dna = dataclasses.replace(
            dna, risk_genes=dataclasses.replace(
                dna.risk_genes, sl_mode="atr", atr_period=21,
                stop_loss=2.0, take_profit=4.0))
        env = ReplayEnv(tmp_path, dna=atr_dna, n_bars=400)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=200)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="atr21")
        assert res.status in ("completed", "stopped")
        assert res.bars_processed > 0, "非标周期现算后回放走通"

    def test_funding_archive_missing_all_pending(self, tmp_path):
        """funding 归档缺值挂起（与 test_funding_archive_indexed 互补
        的另一态）：不造归档→区间结算点全落 pending 行不扣款、非错误
        （与实时态 feed 缺值同语义——供数契约 R5 场景）。"""
        env = ReplayEnv(tmp_path, n_bars=400)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=200)
        res = rr.run(env.db, env.strategy_id, start, end, task_id="fund02")
        assert res.status in ("completed", "stopped"), "缺归档非错误"
        records = paper.list_funding_records(env.db, "fund02", limit=1000)
        assert records, "结算点在案（挂起留痕）"
        assert all(r["status"] == "pending" for r in records), (
            "归档缺值全挂起不扣款")

    def test_bad_dna_rejected(self, tmp_path):
        """坏 dna 快照：RESEARCH_STORED_DATA_INVALID 拒绝（行不建）。"""
        env = ReplayEnv(tmp_path, n_bars=300)
        from core.persistence.repository.strategy import save_strategy

        save_strategy(env.db, strategy_id="st_bad",
                      dna_json="{not-valid-json", symbol="BTCUSDT",
                      timeframe="4h", status="draft")
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, "st_bad", start, end)
        assert ei.value.code == "RESEARCH_STORED_DATA_INVALID"


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

    def test_symbol_invalid_rejected_library_path(self, tmp_path):
        """CARD-03：库策略路径坏 symbol（dna_json 内 execution_
        genes.symbol——库路径消费面是 dna 内值，非 strategy 行的
        symbol 列）→ RESEARCH_PARAM_INVALID 即拒+行不建；时序先于
        feeder 归档读取（未校验符号原直达 parquet 拼接点）。"""
        import dataclasses

        from core.persistence.repository.strategy import save_strategy

        env = ReplayEnv(tmp_path, n_bars=300)
        evil_dna = dataclasses.replace(
            env.dna,
            execution_genes=dataclasses.replace(
                env.dna.execution_genes, symbol="../../etc/passwd"))
        save_strategy(env.db, strategy_id="st_evil",
                      dna_json=evil_dna.to_json(), symbol="BTCUSDT",
                      timeframe="4h", status="draft")
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, "st_evil", start, end)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert paper.list_tasks(env.db, limit=10) == [], "行不建"

    def test_symbol_invalid_rejected_override_path(self, tmp_path):
        """CARD-03：内存 DNA override 路径（run_from_dna）坏 symbol
        同码即拒——两路径汇聚点单次校验覆盖。"""
        import dataclasses

        env = ReplayEnv(tmp_path, n_bars=300)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=100)
        bad_dna = dataclasses.replace(
            env.dna,
            execution_genes=dataclasses.replace(
                env.dna.execution_genes, symbol="BTC/../USDT"))
        with pytest.raises(ReplayError) as ei:
            rr.run_from_dna(env.db, bad_dna, start, end, task_id="evil1")
        assert ei.value.code == "RESEARCH_PARAM_INVALID"

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
