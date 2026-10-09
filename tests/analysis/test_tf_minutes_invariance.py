"""TF_MINUTES 收敛前统计输出不变性锚（CARD-04 前置特征网）。

背景：validation_service._TF_MINUTES 六档与 warmup_loader.TF_MINUTES
八档不一致，三消费点（:247 isolated+staged 共用 / :462 random_window /
:563 sandbox）走 .get(tf, 240) 兜底——1m/5m 前扩过量 48-240 倍。
收敛方案：TF_MINUTES 由 core/data/timeframes.py TF_SECONDS 派生。

不变性命题（本锚守护）：warmup 前扩量变化不影响请求区间内统计输出
——统计只算请求区间、warmup 只用不算。若修复后统计输出变化，即前视
泄漏或区间错切信号，本锚即红。

锚分面（random_window 特殊性）：其 tf_minutes 消费是窗口几何
（bar_delta 定义窗宽与步进）而非前扩——1m/5m 走 240 兜底时
window_bars=200 的实际窗宽为 200×240=48000 分钟（几何错乱，CARD-04
修复对象），修复后窗口集必然变化，故该两档 random 内容不冻结；
用表内档 1h（TF_MINUTES 修复前后同为 60 → 窗口集不变）冻结链路
统计输出。isolated/staged 请求区间固定、三档全冻结。

冻结值取自合成数据（seed=11）+ 确定性策略的当前实现输出（探针
2026-10-01）；数据集构造与请求区间一字不动即跨修复可比。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.validation_service import (
    random_window_test,
    run_isolated_backtest,
    run_staged_validation,
)
from core.persistence.repository.strategy import save_strategy
from core.persistence.db_ext import init_db_ext
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)

# 三档合成数据规模：1m 60000 根（兜底 240 前扩需求 200×240=48000 根
# + 请求区间余量）、5m 15000 根（48000 分钟=9600 根 + 余量）、
# 1h 5000 根（表内档，几何正确）
_SPECS = {"1m": 60000, "5m": 15000, "1h": 5000}
_FREQ = {"1m": "1min", "5m": "5min", "1h": "1h"}
_SIGMA = {"1m": 30, "5m": 60, "1h": 120}

# ---- 冻结值（当前实现输出；修复后必须逐键相等，否则即行为变化） ----
ISOLATED_FROZEN = {
    "1m": {"total_return": 0.01312489993600674, "sharpe_ratio": 0.0,
           "max_drawdown": -0.005595930502329647, "win_rate": 1.0,
           "total_trades": 1, "liquidated": False,
           "alpha": -71.21170763836355,
           "market_annual_return": 73.89553212064902,
           "benchmark_return": 0.04410592221559706,
           "excess_return": -0.03098102227959032},
    "5m": {"total_return": -0.03790990704991626,
           "sharpe_ratio": -24.22466525224047,
           "max_drawdown": -0.04249570702113313, "win_rate": 0.0,
           "total_trades": 3, "liquidated": False,
           "alpha": 0.24540469986210112,
           "market_annual_return": -0.9872502477947531,
           "benchmark_return": -0.11705577783402021,
           "excess_return": 0.07914587078410396},
    "1h": {"total_return": -0.05540448747677673,
           "sharpe_ratio": -14.698310106033595,
           "max_drawdown": -0.05909860652345216, "win_rate": 0.0,
           "total_trades": 4, "liquidated": False,
           "alpha": 0.27608024845212165,
           "market_annual_return": -0.4294028554427679,
           "benchmark_return": -0.17481517872597718,
           "excess_return": 0.11941069124920045},
}
STAGED_FROZEN = {
    "1m": [("前半", 0.0051735571, 1), ("后半", 0.0067094038, 1)],
    "5m": [("前半", -0.0158837703, 2), ("后半", -0.0218667748, 2)],
    "1h": [("前半", -0.0466444001, 4), ("后半", -0.0091840921, 1)],
}
RANDOM_1H_FROZEN = {
    "total_returns": [0.0131057558, -0.0069456814, 0.0073909989],
    "verdicts": ["inconclusive", "inconclusive", "inconclusive"],
}


def _write_ohlcv(d: Path, tf: str, rng) -> pd.DatetimeIndex:
    n = _SPECS[tf]
    idx = pd.date_range("2022-10-01", periods=n, freq=_FREQ[tf], tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, _SIGMA[tf], n))
    spread = _SIGMA[tf] / 2
    pd.DataFrame({"open": close, "high": close + spread,
                  "low": close - spread, "close": close,
                  "volume": rng.uniform(100, 800, n),
                  "trades": 40}, index=idx).to_parquet(
        d / f"BTCUSDT_{tf}.parquet")
    return idx


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """三档数据一次构造，三测试类共用（数据构造是最重开销）。

    单 rng 按固定档序（1m→5m→1h）连续消耗——随机流与冻结值绑定的
    生成序（改档序或分档独立 seeding 即换数据集，须重取冻结值）。"""
    tmp = tmp_path_factory.mktemp("tf_inv")
    d = tmp / "market"
    d.mkdir(exist_ok=True)
    rng = np.random.default_rng(11)
    idx = {tf: _write_ohlcv(d, tf, rng) for tf in ("1m", "5m", "1h")}
    db = tmp / "q.db"
    init_db_ext(db)
    ensure_research_tables(db)
    return db, d, idx


@pytest.fixture()
def strategy(env):
    """按档登记策略与假设（每链路独立 tag）。

    显式唯一 gene_signature：save_strategy 按 DNA 签名去重（同签名
    保留 best_score 更高者），三链路 DNA 相同会互相吞行——签名唯一化
    绕开去重路径，保证各 sid 独立在库。"""
    db, _, _ = env

    def _make(tf: str, tag: str) -> tuple:
        sid = f"st_{tf}_{tag}"
        idx = env[2][tf]
        n = _SPECS[tf]
        dna = StrategyDNA(
            layers=[TimeframeLayer(timeframe=tf, signal_genes=[
                SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                           "rsi_14", {"type": "lt", "threshold": 45})],
                logic_genes=LogicGenes())],
            execution_genes=ExecutionGenes(timeframe=tf),
            risk_genes=RiskGenes())
        save_strategy(db, strategy_id=sid, dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe=tf,
                      gene_signature=f"sig_tfinv_{tf}_{tag}")
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": {"indicator": "RSI", "params": {"period": 14}},
                     "action": "lt", "target": 45}],
                "then_conditions": [
                    {"subject": "close", "action": "gt", "target": 0}],
            },
            statement=f"不变性锚 {tf} {tag}",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": tf,
                          "start": idx[100].isoformat(),
                          "end": idx[1000].isoformat()}])["hypothesis_id"]
        return sid, hid, idx, n

    return _make


class TestIsolatedStatsInvariance:
    """隔离回测：三档 metrics 全键冻结（前扩量变化不得触及统计输出）。"""

    @pytest.mark.parametrize("tf", ["1m", "5m", "1h"])
    def test_metrics_frozen(self, env, strategy, tf):
        db, d, _ = env
        sid, hid, idx, n = strategy(tf, "iso")
        req_start, req_end = idx[n - 3000], idx[n - 1]
        out = run_isolated_backtest(
            db, d, hypothesis_id=hid, strategy_id=sid,
            request_range={"symbol": "BTCUSDT", "timeframe": tf,
                           "start": req_start.isoformat(),
                           "end": req_end.isoformat()})
        assert out["verdict"] == "inconclusive"  # 小样本证据闸门（<5 笔）
        assert out["metrics"] == ISOLATED_FROZEN[tf]


class TestStagedStatsInvariance:
    """分段验证：三档两段统计冻结（每段独立 warmup 前扩，同理不变）。"""

    @pytest.mark.parametrize("tf", ["1m", "5m", "1h"])
    def test_segments_frozen(self, env, strategy, tf):
        db, d, _ = env
        sid, _, idx, n = strategy(tf, "stg")
        req_start, req_end = idx[n - 3000], idx[n - 1]
        mid = idx[n - 2000]
        sv = run_staged_validation(
            db, d, strategy_id=sid, symbol="BTCUSDT", timeframe=tf,
            start=req_start.isoformat(), end=req_end.isoformat(),
            segmentation="explicit",
            segments=[
                {"label": "前半", "start": req_start.isoformat(),
                 "end": mid.isoformat()},
                {"label": "后半", "start": mid.isoformat(),
                 "end": req_end.isoformat()}])
        assert sv["verdict"] == "inconclusive"
        got = [(s["label"], round(s["metrics"]["total_return"], 10),
                s["metrics"]["total_trades"]) for s in sv["segments"]]
        assert got == STAGED_FROZEN[tf]


class TestRandomWindowStatsInvariance:
    """随机窗口：1h（表内档）统计冻结——tf_minutes 修复前后同为 60，
    窗口集不变，链路输出必须逐窗相等。

    1m/5m 修复前形态（记录不冻结）：窗口几何走 240 兜底——
    window_bars=200 的实际窗宽为 48000 分钟（1m 实测 2022-10-01T12:20
    → 2022-11-03T20:20），属 CARD-04 修复对象；修复后窗口集变化，
    内容不变性前提不成立（几何语义的正确化），由修复回归另行验证。
    """

    def test_windows_frozen_1h(self, env, strategy):
        db, d, _ = env
        sid, _, idx, n = strategy("1h", "rw")
        rw = random_window_test(
            db, d, strategy_id=sid, symbol="BTCUSDT", timeframe="1h",
            data_start=idx[500].isoformat(), data_end=idx[n - 1].isoformat(),
            window_bars=200, n_windows=3, seed=5)
        assert rw["summary"]["n_windows"] == 3
        trs = [round(w["metrics"]["total_return"], 10) for w in rw["windows"]]
        assert trs == RANDOM_1H_FROZEN["total_returns"]
        assert [w["verdict"] for w in rw["windows"]] \
            == RANDOM_1H_FROZEN["verdicts"]

    @pytest.mark.parametrize("tf,minutes", [("1m", 1), ("5m", 5)])
    def test_window_geometry_corrected(self, env, strategy, tf, minutes):
        """CARD-04 修复后几何断言：窗宽 == window_bars × 真实周期。

        修复前 240 兜底下 window_bars=200 的实际窗宽为 48000 分钟
        （几何错乱——本文件 docstring 记录的修复前形态）；修复后
        1m/5m 窗宽须为 200/1000 分钟。"""
        db, d, _ = env
        sid, _, idx, n = strategy(tf, "geo")
        rw = random_window_test(
            db, d, strategy_id=sid, symbol="BTCUSDT", timeframe=tf,
            data_start=idx[500].isoformat(), data_end=idx[n - 1].isoformat(),
            window_bars=200, n_windows=3, seed=5)
        assert rw["summary"]["n_windows"] == 3
        expected_span = pd.Timedelta(minutes=200 * minutes)
        for w in rw["windows"]:
            span = (pd.Timestamp(w["end"]) - pd.Timestamp(w["start"]))
            assert span == expected_span, f"{tf} 窗宽 {span} != {expected_span}"
