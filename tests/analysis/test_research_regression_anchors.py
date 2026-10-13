"""研究链路回归锚（fix-research-c1-bb-std-param · C1.4，系列先行义务）。

三锚锁「同一条件在同一真实数据上的触发计数跨代码变更不变」——参数
修复、装载改造、模板重构任一环节改变锚值即红，防无声漂移。

锚与数据（真实数据 BTCUSDT，data/market 在库；缺失时模块级 skip
不假绿——test_derivatives_contract 同款纪律）：
- A1：1h 2024-02-01~2026-04-01，close lt bb_lower_20_2（BB std=2
  未失真路径），期望 1119；双写法一致性断言（price_below 写法同得
  1119）已随 C2 批次启用。
- A2：4h 2024-05-01~2026-04-01，EMA5 gt ema_20 且 close lt ema_20
  双条件 AND，期望 213（序列比较面）；已随 C6 批次启用。
- A3：1h 2024-02-01~2026-04-01，SMA5 lt sma_20（subject=结构化
  SMA(5) 引用 + target=编码列名 sma_20 的序列对序列比较），期望
  9026——差分不变性断言（本变更启用）。

基线记录（2026-10-13，main 593b90f 修复前采集，修复后复跑一致）：
- A3 = 9026（total_bars 19561）。与需求档案 9017 的差异原因：档案
  值采集于 2026-09-20 数据集归档重建前的旧数据；本基线以当前数据
  复跑口径定档（修复前后同口径同值，差分断言不受影响）。
- A1 = 1119（total_bars 19161）——与 reviewer 复现值一致。
- KC-S3 退化等值锚基线（atr_period=中轨周期=20、mult=2）：修复前
  ta.kc 与自算式 BTCUSDT 1h 全区间 18941 行三列零差异（列 hash：
  upper=66684aecc47e84ce、middle=820d44a13a8d8d1b、
  lower=de90be031718b9a2）——KC 存量债务全量特殊性下的唯一修复前
  对照点，断言落 tests/infra/test_indicator_params_effective.py。
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]

MARKET_DIR = Path("data/market")

if not (MARKET_DIR / "BTCUSDT_1h.parquet").exists():
    pytest.skip("真实数据 BTCUSDT 1h/4h 不在库（data/market），回归锚跳过",
                allow_module_level=True)

from api.routes.research.analysis import _load_with_condition_refs
from core.analysis.templates.condition_stats import condition_stats


def _hit_count(indicator, params, condition, tf, start, end):
    """路由同款链路（_load_with_condition_refs → condition_stats）跑
    触发计数——与 condition_stats 路由分支同口径（warmup 前扩装载 +
    request_start 切回请求区间）。"""
    rng = {"symbol": "BTCUSDT", "timeframe": tf, "start": start, "end": end}
    df, request_start = _load_with_condition_refs(
        MARKET_DIR, rng, indicator, condition=condition)
    d = condition_stats(
        df, indicator=indicator, params=params, condition=condition,
        data_ranges=[{**rng}], forward_bars=24,
        request_start=request_start).to_dict()
    return d["payload"]["hit_count"]


class TestA3SmaCrossSeries:
    """A3：SMA5 lt sma_20 差分不变性（本变更启用）。"""

    def test_a3_hit_count_unchanged(self):
        cond = {"subject": {"indicator": "SMA", "params": {"period": 5},
                            "field": "sma"},
                "action": "lt", "target": "sma_20"}
        got = _hit_count("SMA", {"period": 5}, cond,
                         "1h", "2024-02-01", "2026-04-01")
        assert got == 9026


class TestA1BbLowerTouch:
    """A1：close lt bb_lower_20_2（std=2 未失真路径的锚基础）。"""

    def test_a1_hit_count_unchanged(self):
        cond = {"subject": "close", "action": "lt", "target": "bb_lower_20_2"}
        got = _hit_count("BB", {"period": 20, "std": 2}, cond,
                         "1h", "2024-02-01", "2026-04-01")
        assert got == 1119

    def test_a1_dual_writing_parity(self):
        """双写法一致性（fix-research-c2 启用）：price_below 写法
        （subject=BB 结构化 lower 引用，target 占位）与等价 lt 写法
        （subject=close、target=编码列名）同得 1119——统计面动作语义
        与回测面先例逐字对齐的直接证据。"""
        cond = {"subject": {"indicator": "BB",
                            "params": {"period": 20, "std": 2},
                            "field": "lower"},
                "action": "price_below", "target": 0}
        got = _hit_count("BB", {"period": 20, "std": 2}, cond,
                         "1h", "2024-02-01", "2026-04-01")
        assert got == 1119


class TestA2EmaCrossClose:
    """A2：EMA5 gt ema_20 且 close lt ema_20 双条件 AND，期望 213。

    fix-research-c6 启用（序列比较统计面锚——翻译面等价形制锚见
    tests/analysis/test_dna_series_reference.py）。
    """

    def test_a2_hit_count(self):
        """EMA5 gt ema_20 ∧ close lt ema_20 == 213（4h 区间锚）。"""
        conds = [
            {"subject": {"indicator": "EMA", "params": {"period": 5},
                         "field": "ema"},
             "action": "gt", "target": "ema_20"},
            {"subject": "close", "action": "lt", "target": "ema_20"},
        ]
        rng = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2024-05-01", "end": "2026-04-01"}
        df, request_start = _load_with_condition_refs(
            MARKET_DIR, rng, None, conditions=conds)
        d = condition_stats(
            df, conditions=conds, data_ranges=[{**rng}], forward_bars=24,
            request_start=request_start).to_dict()
        assert d["payload"]["hit_count"] == 213
