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


def _threshold(env) -> float:
    return float(env["df"]["close"].median())


def _baseline(env, policy=None, lo=None, hi=None, tailcut=False):
    """独立复算基准：区间过滤后掩码 → 权威变换 → 时间戳列表。

    tailcut=True 时按统计链尾部丢弃语义过滤（事件位置 +1 越界）。
    """
    df = env["df"]
    seg = df
    if lo is not None or hi is not None:
        seg = df[(df.index >= pd.Timestamp(lo or df.index[0]))
                 & (df.index <= pd.Timestamp(hi or df.index[-1]))]
    mask = (seg["close"] > _threshold(env)).values
    events = mask_to_events(mask, policy)
    if tailcut:
        events = events[events + 1 < len(seg)]
    return [str(seg.index[i]) for i in events], len(seg)


class TestAcceptanceInvariants:
    """proposal 三验收锚（可证伪不变量形制 / tasks 8.1）。"""

    @pytest.fixture()
    def client(self, chain_env):
        app = create_app(db_path=chain_env["db"],
                         data_dir=chain_env["data_dir"])
        with TestClient(app) as c:
            yield c

    def _validate(self, client, env, policy=None):
        body = {
            "pair": SYMBOL, "timeframe": "4h",
            "start": env["seg"][0], "end": env["seg"][1],
            "when": [{"subject": "close", "action": "gt",
                      "target": repr(_threshold(env))}],
            "then": [{"subject": "close", "action": "rise", "target": "0"}],
        }
        if policy is not None:
            body["trigger_policy"] = policy
        return client.post("/api/validate", json=body)

    def test_first_true_计数上界链(self, client, chain_env) -> None:
        """0 < 事件数 ≤ 逐 bar 触发数（≤ 而非 <——孤立触发时相等是
        正确行为；对触发数 >0 的已知条件成立）。"""
        plain = self._validate(client, chain_env).json()
        evented = self._validate(
            client, chain_env, {"mode": "first_true"}).json()
        assert plain["total_count"] > 0
        assert 0 < evented["total_count"] <= plain["total_count"]

    def test_suppress_bars_间隔不变量(self, client, chain_env) -> None:
        """任意相邻事件间隔 ≥ B bars（API 时间戳间隔 = 24×4h）。"""
        data = self._validate(client, chain_env, POLICY).json()
        import pandas as pd
        ts = pd.DatetimeIndex([t["time"] for t in data["triggers"]])
        assert len(ts) > 1
        assert np.all(np.diff(ts.values) >= np.timedelta64(24 * 4, "h"))

    def test_零事件_照常_reject_suggestion(self, chain_env) -> None:
        """0 事件形态照常 reject_suggestion——事件形制整理触发不
        创造触发（恒假条件：掩码全 False）。"""
        from core.analysis.templates.precheck_hypothesis import (
            precheck_hypothesis,
        )
        out = precheck_hypothesis(
            chain_env["db"], chain_env["data_dir"],
            rng={"symbol": SYMBOL, "timeframe": "4h",
                 "start": SEG_LATE[0], "end": SEG_LATE[1]},
            validate_range={"symbol": SYMBOL, "timeframe": "4h",
                            "start": SEG_LATE[0], "end": SEG_LATE[1]},
            when_conditions=[{"subject": "close", "action": "gt",
                              "target": repr(1e12)}],   # 恒假
            trigger_policy={"mode": "first_true"})
        assert out["verdict"] == "reject_suggestion"

    def test_无policy_直译计数_独立复算(self, client, chain_env) -> None:
        data = self._validate(client, chain_env).json()
        expected, _ = _baseline(chain_env, tailcut=True)
        assert data["total_count"] == len(expected)


class TestCrossChainConsistency:
    """五消费面同源（specs「消费面同源」/ tasks 8.2）。"""

    @pytest.fixture()
    def client(self, chain_env):
        app = create_app(db_path=chain_env["db"],
                         data_dir=chain_env["data_dir"])
        with TestClient(app) as c:
            yield c

    def _when(self, env):
        return [{"subject": "close", "action": "gt",
                 "target": repr(_threshold(env))}]

    def test_统计链_事件时间序列_等于基准(self, client, chain_env) -> None:
        resp = client.post("/api/validate", json={
            "pair": SYMBOL, "timeframe": "4h",
            "start": chain_env["seg"][0], "end": chain_env["seg"][1],
            "when": self._when(chain_env),
            "then": [{"subject": "close", "action": "rise", "target": "0"}],
            "trigger_policy": POLICY})
        assert resp.status_code == 200
        times = [t["time"] for t in resp.json()["triggers"]]
        expected, _ = _baseline(chain_env, POLICY, tailcut=True)
        assert times == expected

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
            (seg["close"] > _threshold(chain_env)).values,
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

    def test_统计链与策略链_事件序列一致(self, chain_env) -> None:
        """specs 核心锚：验证引擎事件位置 == 策略入场信号位置（同一
        权威变换产出，无链间分歧）——同请求区间下两者逐元素相等。"""
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA,
        )
        from core.strategy.executor import dna_to_signal_set
        from core.validation.engine import validate_hypothesis

        r = validate_hypothesis(
            SYMBOL, "4h", chain_env["seg"][0], chain_env["seg"][1],
            when_conditions=self._when(chain_env), then_conditions=[],
            data_dir=str(chain_env["data_dir"]), trigger_policy=POLICY)
        engine_times = [t.time for t in r.triggers]

        dna = StrategyDNA(
            signal_genes=[SignalGene(
                "CLOSE", {}, SignalRole.ENTRY_TRIGGER, "close",
                {"type": "gt", "threshold": _threshold(chain_env)})],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            entry_trigger_policy=POLICY)
        ss = dna_to_signal_set(dna, chain_env["df"])
        strategy_times = [str(t) for t in ss.entries.index[ss.entries]]

        expected, _ = _baseline(chain_env, POLICY, tailcut=True)
        assert engine_times == expected
        all_events, _ = _baseline(chain_env, POLICY)
        assert strategy_times == all_events       # 策略链无尾部丢弃


class TestRepresentativeEntries:
    """12 处直译入口代表面（A 组隔离验证底层 / batch_run / evaluate_dna
    无预载数据回退——tasks 6.2/8.2 抽核面）。"""

    def test_隔离验证_带policy策略_全链走通(self, chain_env) -> None:
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.analysis.validation_service import run_isolated_backtest

        rng = {"symbol": SYMBOL, "timeframe": "4h",
               "start": SEG_LATE[0], "end": SEG_LATE[1]}
        hid = register_hypothesis(
            chain_env["db"],
            conditions={
                "when_conditions": [{
                    "subject": {"indicator": "RSI",
                                "params": {"period": 14}},
                    "action": "lt", "target": 65}],
                "then_conditions": [{
                    "subject": {"indicator": "RSI",
                                "params": {"period": 14}},
                    "action": "gt", "target": 35}]},
            statement="cross-chain", data_ranges=[rng],
            trigger_policy={"mode": "first_true"})["hypothesis_id"]
        out = translate_hypothesis(chain_env["db"], hypothesis_id=hid)
        # 验证区间与登记区间（判定并集自报部分）不重叠——后移一段
        verify_rng = {"symbol": SYMBOL, "timeframe": "4h",
                      "start": "2024-07-01T00:00:00Z",
                      "end": "2024-08-01T00:00:00Z"}
        rec = run_isolated_backtest(
            chain_env["db"], chain_env["data_dir"],
            hypothesis_id=hid, strategy_id=out["strategy_id"],
            request_range=verify_rng)
        assert rec["record_id"]
        assert rec["verdict"] in ("pass", "fail", "inconclusive")

    def test_batch_run_路径_事件口径(self, chain_env) -> None:
        from core.backtest.engine import BacktestEngine
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA,
        )
        dna = StrategyDNA(
            signal_genes=[SignalGene(
                "CLOSE", {}, SignalRole.ENTRY_TRIGGER, "close",
                {"type": "gt", "threshold": 0})],       # 恒真
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            entry_trigger_policy={"mode": "suppress_bars", "bars": 100})
        engine = BacktestEngine()
        single = engine.run(dna, chain_env["df"])
        batched = engine.batch_run([dna], chain_env["df"])
        assert single.total_trades == batched[0].total_trades   # 同口径

    def test_evaluate_dna_无预载数据回退_事件化传导(self, chain_env) -> None:
        from core.evolution.evaluation import evaluate_dna
        from core.strategy.dna import (
            ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
            StrategyDNA,
        )
        dna = StrategyDNA(
            signal_genes=[SignalGene(
                "CLOSE", {}, SignalRole.ENTRY_TRIGGER, "close",
                {"type": "gt", "threshold": 0})],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            entry_trigger_policy={"mode": "first_true"})
        task_row = {"symbol": SYMBOL, "timeframe": "4h",
                    "data_start": chain_env["seg"][0],
                    "data_end": chain_env["seg"][1]}
        out = evaluate_dna(dna, task_row, chain_env["data_dir"])
        assert out["used_real_data"] is True
        assert out["total_trades"] == 1   # 恒真 first_true 唯一事件一笔
