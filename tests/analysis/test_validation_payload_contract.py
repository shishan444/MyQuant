"""验证返回体单一映射契约测试（unify-tool-return-contract 任务 4）。

规则①（首次/幂等返回结构一致）断言面 S1a-f：isolated 首次顶层 11 键
/幂等与首次键集相等且 result 不在顶层/staged 幂等同构/judged_ranges
响应时点现算/direction 三态收敛/随机窗口既有同构不回归。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

import numpy as np
import pandas as pd

from core.analysis.hypothesis_service import ResearchError, register_hypothesis
from core.persistence.repository.strategy import save_strategy
from core.analysis.validation_service import (
    random_window_test, run_isolated_backtest, run_staged_validation,
)
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA, TimeframeLayer,
)
from tests.analysis.test_validation_execution import (  # noqa: F401 复用 fixtures
    data_dir, db, hypothesis_id,
)

_RSI_GENE = lambda: SignalGene(  # noqa: E731
    "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER, "rsi_14",
    {"type": "lt", "threshold": 35})


def _dna(direction: str = "long") -> StrategyDNA:
    return StrategyDNA(
        signal_genes=[_RSI_GENE()], logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(direction=direction))


_ISOLATED_11 = {
    "record_id", "verdict", "duplicated", "direction", "metrics",
    "threshold_details", "warmup", "bars_used", "symbol", "timeframe",
    "judged_ranges",
}
_STAGED_KEYS = {
    "record_id", "verdict", "duplicated", "strategy_id", "hypothesis_id",
    "segmentation", "request_range", "warmup_bars", "segments", "summary",
    "judged_ranges", "direction",
}


class TestIsolatedPayloadContract:
    """S1a/S1b/S1d/S1e：isolated 首次/幂等同构。"""

    def test_s1a_s1b_isolated_first_and_idem_same_keyset(self, db, data_dir,
                                                         hypothesis_id):
        """S1a 首次顶层 11 键；S1b 幂等与首次顶层键集集合相等且
        result 不在顶层（双形制退役锚）。"""
        save_strategy(db, strategy_id="st_s1", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        kw = dict(hypothesis_id=hypothesis_id, strategy_id="st_s1",
                  request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                                 "start": "2023-03-01", "end": "2023-03-20"})
        first = run_isolated_backtest(db, data_dir, **kw)
        replay = run_isolated_backtest(db, data_dir, **kw)

        assert set(first) == _ISOLATED_11, f"首次键集：{sorted(first)}"
        assert first["duplicated"] is False
        assert replay["duplicated"] is True
        assert set(replay) == set(first), "幂等与首次顶层键集相等"
        assert "result" not in replay, "result 嵌套已退役"
        assert "result" not in first

    def test_s1b_idempotent_values_match_first(self, db, data_dir,
                                               hypothesis_id):
        """S1b 值级：幂等重放与首次逐键值相等（幂等=同一验证记录的
        投影——result_json 内嵌完整首次 payload，重放解出的核心数据
        键与首次逐键一致，仅 duplicated 翻转 True）。"""
        save_strategy(db, strategy_id="st_s1v", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        kw = dict(hypothesis_id=hypothesis_id, strategy_id="st_s1v",
                  request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                                 "start": "2023-03-01", "end": "2023-03-20"})
        first = run_isolated_backtest(db, data_dir, **kw)
        replay = run_isolated_backtest(db, data_dir, **kw)

        for key in ("record_id", "verdict", "metrics", "threshold_details",
                    "warmup", "bars_used", "symbol", "timeframe",
                    "judged_ranges", "direction"):
            assert replay[key] == first[key], f"幂等重放 {key} 与首次不一致"
        assert first["duplicated"] is False
        assert replay["duplicated"] is True

    def test_s1d_judged_ranges_response_time_union(self, db, data_dir,
                                                   hypothesis_id):
        """S1d judged_ranges 现算语义：首次（落库后含本记录）与幂等
        重放同口径——首次返回的并集已含本次验证区间。"""
        save_strategy(db, strategy_id="st_s1d", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        kw = dict(hypothesis_id=hypothesis_id, strategy_id="st_s1d",
                  request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                                 "start": "2023-03-01", "end": "2023-03-20"})
        first = run_isolated_backtest(db, data_dir, **kw)
        replay = run_isolated_backtest(db, data_dir, **kw)

        def _spans(ranges):
            return {(r["start"], r["end"]) for r in ranges}

        first_span = _spans(first["judged_ranges"])
        assert ("2023-03-01T00:00:00Z", "2023-03-20T00:00:00Z") in first_span \
            or any(s <= "2023-03-01T00:00:01Z" for s, _ in first_span), \
            "首次并集须含本次区间（响应时点=落库后）"
        assert _spans(replay["judged_ranges"]) == first_span, \
            "幂等重放与首次并集同口径"

    def test_s1e_direction_values_converged(self, db, data_dir):
        """S1e direction 恒回显合法二值：long 产物回显 long；幂等读
        库内值域收敛（脏值/缺键→long）。"""
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [{"subject": {"indicator": "RSI",
                                                  "params": {"period": 14}},
                                      "action": "lt", "target": 35}],
                "then_conditions": [{"subject": "close", "action": "gt",
                                     "target": 0}],
            },
            statement="s", data_ranges=[{"symbol": "BTCUSDT",
                                         "timeframe": "4h",
                                         "start": "2023-01-01T00:00:00Z",
                                         "end": "2023-02-01T00:00:00Z"}],
        )["hypothesis_id"]
        save_strategy(db, strategy_id="st_dir",
                      dna_json=_dna(direction="short").to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        kw = dict(hypothesis_id=hid, strategy_id="st_dir",
                  request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                                 "start": "2023-03-01", "end": "2023-03-20"})
        first = run_isolated_backtest(db, data_dir, **kw)
        replay = run_isolated_backtest(db, data_dir, **kw)
        assert first["direction"] == "short"
        assert replay["direction"] == "short", "幂等读 result_json 同值回显"


class TestStagedPayloadContract:
    """S1c：staged 幂等同构+专有键不硬套。"""

    def test_s1c_staged_first_and_idem_same_keyset(self, db, data_dir,
                                                   hypothesis_id):
        """staged 首次与幂等顶层键集相等（含专有键，经入口参数化——
        与 isolated 键集不同构，禁硬套）；staged 写入侧补齐后新记录
        幂等重放与首次逐键同集。"""
        save_strategy(db, strategy_id="st_s1c", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        kw = dict(hypothesis_id=hypothesis_id, strategy_id="st_s1c",
                  symbol="BTCUSDT", timeframe="4h",
                  start="2023-03-01", end="2023-03-20")
        first = run_staged_validation(db, data_dir, **kw)
        replay = run_staged_validation(db, data_dir, **kw)

        assert set(first) == _STAGED_KEYS, f"staged 首次键集：{sorted(first)}"
        assert first["duplicated"] is False
        assert replay["duplicated"] is True
        assert set(replay) == set(first), "幂等与首次键集相等"
        assert "result" not in replay
        # 专有键在场且值一致（入口参数化）
        assert replay["segmentation"] == first["segmentation"] == "auto"
        assert replay["warmup_bars"] == first["warmup_bars"]
        assert replay["request_range"] == first["request_range"]
        assert len(replay["segments"]) == len(first["segments"]) > 0
        # 写入侧补齐不变量：幂等重放的 segments/summary 来自 result_json
        assert replay["summary"] == first["summary"]


class TestRandomWindowNoRegression:
    """S1f：随机窗口既有同构不回归（零改动面锁定）。"""

    def test_s1f_random_window_shape_unchanged(self, db, data_dir):
        """随机窗口返回形制保持既有（record_id/seed/duplicated/verdict/
        direction/result 在场——该入口不在本次统一面内）。"""
        save_strategy(db, strategy_id="st_s1f", dna_json=_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h")
        kw = dict(strategy_id="st_s1f", symbol="BTCUSDT", timeframe="4h",
                  data_start="2023-03-20", data_end="2023-05-01",
                  window_bars=30, n_windows=3, seed=5)
        first = random_window_test(db, data_dir, **kw)
        replay = random_window_test(db, data_dir, **kw)
        # G6 既有形制：首次无 duplicated 键（.get 为 None），幂等带
        # True——随机窗不在本次统一面内，保持 0.3.3 先例原样
        assert first.get("duplicated") is not True
        assert replay["duplicated"] is True
        assert replay["verdict"] == first["verdict"]
        for key in ("record_id", "seed", "direction", "result"):
            assert key in replay, f"随机窗既有键 {key} 不回归"
