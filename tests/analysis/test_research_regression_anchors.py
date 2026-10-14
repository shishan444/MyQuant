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

import json
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


# ---------------------------------------------------------------- B1 锚面
# 变更一 extend-research-expression-and-lineage：原生假设翻译写法锚。
# 以前 A1/A2 只有统计面写法与（A1）price_below 结构化写法；现在新增
# 第三写法——「登记 → translate → draft DNA → evaluate_condition」全
# 链路真跑（非 JSON 形状验证），锁「翻译产物的求值语义=统计面直比」。

@pytest.fixture()
def _db(tmp_path):
    from core.persistence.db_ext import init_db_ext
    from core.analysis.executor import ensure_research_tables
    d = tmp_path / "q.db"
    init_db_ext(d)
    ensure_research_tables(d)
    return d


def _seg_from(df, request_start):
    """warmup 前扩装载切回请求区间（condition_stats 同口径）。

    load_with_warmup 为 compute_indicators=False（指标列由调用方在含
    warmup 前缀的全 df 上现算——_compute_indicator 后再切区间，与
    condition_stats 内部口径一致）。
    """
    import pandas as pd
    ts = pd.Timestamp(request_start)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return df[df.index >= ts]


def _enrich(df, names_params):
    """在含 warmup 前缀的 df 上现算指标列（编码列名并入）。"""
    from core.features.indicators import _compute_indicator
    for name, params in names_params:
        cols = _compute_indicator(df, name, dict(params))
        for c, s in cols.items():
            df[c] = s
    return df


class TestA1NativeTranslation:
    """A1 第三写法（B1-T16）：close lt bb_lower_20_2 经原生假设翻译
    产物求值——三写法同得 1119。"""

    RANGE = {"symbol": "BTCUSDT", "timeframe": "1h",
             "start": "2024-02-01", "end": "2026-04-01"}

    def _translate_and_count(self, db):
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.persistence.repository.strategy import get_strategy
        from core.strategy.executor import evaluate_condition

        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": "close", "action": "lt",
                     "target": "bb_lower_20_2"}],
                "then_conditions": [
                    {"subject": {"indicator": "EMA", "params": {"period": 20},
                                 "field": "ema"},
                     "action": "gt", "target": 0}]},
            statement="A1 native translation anchor",
            data_ranges=[dict(self.RANGE)])["hypothesis_id"]
        sid = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        genes = json.loads(get_strategy(db, sid)["dna_json"])["signal_genes"]
        entry = [g for g in genes if g["role"] == "entry_trigger"][0]
        # B1 契约：close 主体基因挂目标指标+price_below
        assert entry["indicator"] == "BB"
        assert entry["params"] == {"period": 20, "std": 2}
        assert entry["field"] == "lower"
        assert entry["condition"] == {"type": "price_below"}

        df, request_start = _load_with_condition_refs(
            MARKET_DIR, dict(self.RANGE), None,
            conditions=[{"subject": "close", "action": "lt",
                         "target": "bb_lower_20_2"}])
        _enrich(df, [("BB", {"period": 20, "std": 2})])
        seg = _seg_from(df, request_start)
        mask = evaluate_condition(seg["bb_lower_20_2"], seg["close"],
                                  entry["condition"], seg)
        return int(mask.fillna(False).sum())

    def test_a1_three_writings_equal(self, _db):
        """三写法一致性：统计 lt 写法（既有 1119）/ price_below 结构化
        写法（既有 1119）/ 原生翻译写法（本用例）同得 1119。"""
        assert self._translate_and_count(_db) == 1119


class TestA2NativeTranslation:
    """A2 翻译路径（B1-T17/T19）：RR-006 双条件（EMA5 gt ema_20 ∧
    close lt ema_20）经原生翻译双基因 AND 求值==213；沙盒烟测非零笔；
    隔离验证零闸门拒——整链无插件参与。"""

    RANGE = {"symbol": "BTCUSDT", "timeframe": "4h",
             "start": "2024-05-01", "end": "2026-04-01"}
    CONDS = [
        {"subject": {"indicator": "EMA", "params": {"period": 5},
                     "field": "ema"},
         "action": "gt", "target": "ema_20"},
        {"subject": "close", "action": "lt", "target": "ema_20"},
    ]

    def _register_and_translate(self, db):
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        hid = register_hypothesis(
            db,
            conditions={"when_conditions": self.CONDS,
                        "then_conditions": [
                            {"subject": "close", "action": "gt",
                             "target": "ema_20"}]},
            statement="A2 native translation anchor",
            data_ranges=[dict(self.RANGE)])["hypothesis_id"]
        sid = translate_hypothesis(db, hypothesis_id=hid)["strategy_id"]
        return hid, sid

    def test_a2_translated_genes_and_count(self, _db):
        """双基因 AND 掩码计数==213（翻译产物逐基因求值后 AND）。"""
        from core.persistence.repository.strategy import get_strategy
        from core.strategy.executor import evaluate_condition
        _, sid = self._register_and_translate(_db)
        genes = json.loads(get_strategy(_db, sid)["dna_json"])["signal_genes"]
        entries = [g for g in genes if g["role"] == "entry_trigger"]
        assert len(entries) == 2
        by_ind = {g["indicator"]: g["condition"] for g in entries}
        # 基因一（C6 序列形）：EMA(5) vs ema_20；基因二（B1 价格形态）：
        # close vs ema_20
        assert by_ind["EMA"] in (
            {"type": "gt", "target_indicator": "EMA",
             "target_params": {"period": 20}, "target_field": "ema"},
            {"type": "price_below"})

        df, request_start = _load_with_condition_refs(
            MARKET_DIR, dict(self.RANGE), None, conditions=self.CONDS)
        _enrich(df, [("EMA", {"period": 5}), ("EMA", {"period": 20})])
        seg = _seg_from(df, request_start)
        masks = []
        for g in entries:
            col = "ema_5" if (g["params"].get("period") == 5
                              and g["condition"].get("type") != "price_below"
                              ) else "ema_20"
            masks.append(evaluate_condition(
                seg[col], seg["close"], g["condition"], seg))
        both = masks[0] & masks[1]
        assert int(both.fillna(False).sum()) == 213

    def test_a2_full_chain_sandbox_and_isolated(self, _db):
        """B1-T19 全链路验收锚：翻译 draft 沙盒烟测 total_trades>0；
        隔离验证正常返回（无组件证据闸门拒——纯内置基因不触发该门）。

        区间设计：登记区间即判定并集（研究协议——先偷看不获利），
        隔离验证须用登记区间外数据段；沙盒段（登记段 2024-05~11 双
        条件交集 47 bar）与隔离段（2025-02~08 交集 73 bar）均实证
        非零。出场条件用 B1 写法（close gt ema_20——回到均线上方
        兑现离场），与入场 close lt ema_20 天然互斥，不经「同 bar
        开平互斥保留 exit」规则吞掉全部入场。"""
        from core.analysis.hypothesis_service import register_hypothesis
        from core.analysis.hypothesis_to_strategy import translate_hypothesis
        from core.analysis.validation_service import (
            run_isolated_backtest, run_sandbox_backtest)

        reg_range = {"symbol": "BTCUSDT", "timeframe": "4h",
                     "start": "2024-05-01", "end": "2024-11-01"}
        hid = register_hypothesis(
            _db,
            conditions={"when_conditions": self.CONDS,
                        "then_conditions": [
                            {"subject": "close", "action": "gt",
                             "target": "ema_20"}]},
            statement="A2 full-chain anchor",
            data_ranges=[dict(reg_range)])["hypothesis_id"]
        sid = translate_hypothesis(_db, hypothesis_id=hid)["strategy_id"]

        sb = run_sandbox_backtest(
            _db, MARKET_DIR, strategy_id=sid, symbol="BTCUSDT",
            timeframe="4h", start="2024-05-01", end="2024-11-01")
        assert sb["metrics"]["total_trades"] > 0, "沙盒须产真实非零交易"

        iv = run_isolated_backtest(
            _db, MARKET_DIR, hypothesis_id=hid, strategy_id=sid,
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2025-02-01", "end": "2025-08-01"})
        assert iv["record_id"], "隔离验证被闸门拒（未产记录）"
        assert iv["verdict"] in ("pass", "fail", "inconclusive")
        # warmup 零改动锚：披露面为 {bars, derived, basis}——derived=600
        # 即 EMA 注册表上限 period=200×3（指标挂基因主体被主路径收集的
        # 数值证据；close 主体基因若未挂指标会退化到 floor 200）
        warm = iv.get("warmup") or {}
        assert warm.get("derived") == 600, (
            f"warmup 推导须含挂基因指标（derived 期望 600）: {warm}")
        assert warm.get("basis")
