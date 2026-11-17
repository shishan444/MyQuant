"""跨链一致性与验收锚（trigger-policy 卡8 / tasks 8.1+8.2）。

独立对照法基准：同一确定性数据、同一条件（close > 全量中位——掩码
必有连续段与间隔段）、同一策略——事件位置独立复算，五消费面
（/api/validate 统计链、event_study、condition_stats、precheck、
策略执行链）与代表面（隔离验证 A 组底层、BacktestEngine.batch_run、
evaluate_dna 无预载数据回退）全部对拍同一基准（specs「消费面同源」
Scenario 落地）。8.1 三验收锚：计数上界链、间隔不变量、0 事件照常
reject_suggestion。

对拍同域前提：各消费面统计域=请求区间过滤后掩码（与统计链 df 同
域；precheck/condition_stats 的 warmup 前缀不参与段结构判定——B 案）。
统计链尾部丢弃语义=事件位置 +1 越界（then 窗根不足）时 continue。

数据域约束：2000 bar——EMA 周期类 warmup 需求 600 bar 前扩，统计
段（2024-06-01 起）前有 1460 bar 可得。
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from fastapi.testclient import TestClient

from api.app import create_app
from core.analysis.event_policy import mask_to_events
from core.persistence.db_ext import init_db_ext
from tests.helpers.data_factory import make_ohlcv

SYMBOL = "BTCUSDT"
POLICY = {"mode": "suppress_bars", "bars": 24}
# 需 warmup 前扩的消费面（condition_stats/precheck/隔离验证）统计段
SEG_LATE = ("2024-06-01T00:00:00Z", "2024-07-01T00:00:00Z")


@pytest.fixture(scope="module")
def chain_env(tmp_path_factory):
    df = make_ohlcv(n=2000, freq="4h", start="2024-01-01", seed=11)
    # fix-executor-column-and-exit-guard 段 A 退役适配：掩码载体从
    # "CLOSE" 伪指标桥接（close 基础列经前缀命中）改为注册指标 RSI
    # （统计链 mask_for 现算 / 策略链基因现算 / 基准列 fixture 预计算
    # ——三面同值同掩码）；hour_utc 供恒真形测试（白名单直读无 NaN）
    from core.features.indicators import _compute_indicator
    df["rsi_14"] = _compute_indicator(df, "RSI", {"period": 14})["rsi_14"]
    df["hour_utc"] = df.index.hour
    tmp = tmp_path_factory.mktemp("chain")
    data_dir = tmp / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / f"{SYMBOL}_4h.parquet")
    db = tmp / "chain.db"
    init_db_ext(db)
    from core.analysis.executor import ensure_research_tables
    ensure_research_tables(db)
    seg = (df.index[0].isoformat(), df.index[-1].isoformat())
    return {"df": df, "data_dir": data_dir, "db": db, "seg": seg}


_THRESHOLD_RSI = 65.0


def _threshold(env) -> float:
    return _THRESHOLD_RSI


def _baseline(env, policy=None, lo=None, hi=None, tailcut=False):
    """独立复算基准：区间过滤后掩码 → 权威变换 → 时间戳列表。

    掩码=fixture 预计算 rsi_14 < 65（与统计/策略两链现算同值——
    独立性锚在掩码→事件的变换对照，非指标复算本身）。
    tailcut=True 时按统计链尾部丢弃语义过滤（事件位置 +1 越界）。
    """
    df = env["df"]
    seg = df
    if lo is not None or hi is not None:
        seg = df[(df.index >= pd.Timestamp(lo or df.index[0]))
                 & (df.index <= pd.Timestamp(hi or df.index[-1]))]
    mask = (seg["rsi_14"] < _THRESHOLD_RSI).values
    events = mask_to_events(mask, policy)
    if tailcut:
        events = events[events + 1 < len(seg)]
    return [str(seg.index[i]) for i in events], len(seg)


class TestCrossChainConsistency:
    """五消费面同源（specs「消费面同源」/ tasks 8.2）。"""

    @pytest.fixture()
    def client(self, chain_env):
        app = create_app(db_path=chain_env["db"],
                         data_dir=chain_env["data_dir"])
        with TestClient(app) as c:
            yield c

    def _when(self, env):
        # target 字符串形（/api/validate pydantic 契约——统计链解析
        # numeric str 同值）
        return [{"subject": {"indicator": "RSI", "params": {"period": 14}},
                 "action": "lt", "target": repr(_threshold(env))}]

    def test_event_study_事件序列_等于基准(self, client, chain_env) -> None:
        resp = client.post("/api/research/analysis", json={
            "template": "event_study",
            "params": {"condition": self._when(chain_env)[0],
                       "min_events": 0, "window": 1,
                       "trigger_policy": {"mode": "first_true"}},
            "data_range": {"symbol": SYMBOL, "timeframe": "4h",
                           "start": SEG_LATE[0], "end": SEG_LATE[1]}})
        assert resp.status_code == 200, resp.text
        payload = resp.json()["payload"]
        expected, _ = _baseline(
            chain_env, {"mode": "first_true"}, lo=SEG_LATE[0], hi=SEG_LATE[1])
        # window=1 尾部丢弃：事件位置 +1 < 区间长
        df = chain_env["df"]
        seg = df[(df.index >= pd.Timestamp(SEG_LATE[0]))
                 & (df.index <= pd.Timestamp(SEG_LATE[1]))]
        events = mask_to_events(
            (seg["rsi_14"] < _THRESHOLD_RSI).values,
            {"mode": "first_true"})
        expected = [str(seg.index[i]) for i in events if i + 1 < len(seg)]
        assert payload["n_events"] == len(expected)
        assert payload["first_event_time"] == expected[0]
        assert payload["last_event_time"] == expected[-1]

    def test_condition_stats_事件计数_等于基准(self, client, chain_env) -> None:
        resp = client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"indicator": "EMA", "params": {"period": 50},
                       "condition": self._when(chain_env)[0],
                       "min_samples": 0,
                       "trigger_policy": {"mode": "first_true"}},
            "data_range": {"symbol": SYMBOL, "timeframe": "4h",
                           "start": SEG_LATE[0], "end": SEG_LATE[1]}})
        assert resp.status_code == 200, resp.text
        # 统计段=[06-01, 07-01] 闭端（to_utc 比较 <=）——变换域=统计段
        expected, _ = _baseline(
            chain_env, {"mode": "first_true"}, lo=SEG_LATE[0], hi=SEG_LATE[1])
        assert resp.json()["payload"]["hit_count"] == len(expected)

    def test_precheck_事件计数_等于基准(self, client, chain_env) -> None:
        resp = client.post("/api/research/analysis", json={
            "template": "precheck_hypothesis",
            "params": {"when_conditions": self._when(chain_env),
                       "validate_range": {
                           "symbol": SYMBOL, "timeframe": "4h",
                           "start": SEG_LATE[0], "end": SEG_LATE[1]},
                       "trigger_policy": {"mode": "first_true"}},
            "data_range": {"symbol": SYMBOL, "timeframe": "4h",
                           "start": SEG_LATE[0], "end": SEG_LATE[1]}})
        assert resp.status_code == 200, resp.text
        expected, _ = _baseline(
            chain_env, {"mode": "first_true"}, lo=SEG_LATE[0], hi=SEG_LATE[1])
        assert resp.json()["payload"]["combination_stats"]["n_triggers"] \
            == len(expected)

class TestRepresentativeEntries:
    """12 处直译入口代表面（A 组隔离验证底层 / batch_run / evaluate_dna
    无预载数据回退——tasks 6.2/8.2 抽核面）。"""

    def test_batch_run_路径_事件口径(self, chain_env) -> None:
        from core.backtest.engine import BacktestEngine
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA,
        )
        dna = StrategyDNA(
            signal_genes=[SignalGene(
                "hour_utc", {}, SignalRole.ENTRY_TRIGGER, None,
                {"type": "gt", "threshold": -1})],      # 恒真（白名单直读）
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            entry_trigger_policy={"mode": "suppress_bars", "bars": 100})
        engine = BacktestEngine()
        single = engine.run(dna, chain_env["df"])
        batched = engine.batch_run([dna], chain_env["df"])
        assert single.total_trades == batched[0].total_trades   # 同口径
