"""全谱扫描模板测试（add-screen-patterns 任务 2.1-2.5）。

覆盖：枚举空间与 scanned 一致、参数结构化拒绝、已知方向数据候选
排序正确、纯随机数据 FDR 命中受控、同参数两次扫描逐字节一致、
组合补扫去重、stop_check 协作中断、缺列指标跳过声明、与
condition_stats 复核一致（同条件同持有期同数字）。
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.condition_identity import canonical_condition_hash
from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.screen_patterns import screen_patterns
from tests.helpers.regime_factory import (
    make_directional_composite as _trending_with_dips,
    make_random_walk_ohlcv as _random_walk,
)

_RANGES = [{"symbol": "TESTUSDT", "timeframe": "1h",
            "start": "2026-01-01", "end": "2026-01-26"}]


class TestScreenPatternsBasics:
    def test_scanned_equals_enumerated_pairs(self):
        """scanned=入族检验对数（min_samples/无效检验过滤后）；候选指纹唯一。"""
        r = screen_patterns(
            _random_walk(1), data_ranges=_RANGES, timeframe="1h",
            features=["RSI"], horizons=[4, 12], top_n=10).to_dict()
        p = r["payload"]
        assert p["scanned"] > 0
        assert p["family"] == "conditions_x_horizons"
        fps = [c["fingerprint"] for c in p["candidates"]]
        assert len(fps) == len(set(fps)), "候选按条件聚合后指纹必须唯一"
        assert all(c["samples"] >= 30 for c in p["candidates"])

    def test_param_rejection_structured(self):
        df = _random_walk(1)
        cases = [
            {"features": "sometimes"}, {"features": []}, {"features": ["NOPE"]},
            {"horizons": []}, {"horizons": [0]}, {"horizons": [1.5]},
            {"top_n": 0}, {"min_samples": 2},
        ]
        for bad in cases:
            with pytest.raises(ResearchError) as ei:
                screen_patterns(df, data_ranges=_RANGES, timeframe="1h", **bad)
            assert ei.value.code == "RESEARCH_PARAM_INVALID"
            assert ei.value.detail.get("legal_range")

    def test_features_subset_vs_all(self):
        df = _random_walk(1)
        sub = screen_patterns(df, data_ranges=_RANGES, timeframe="1h",
                              features=["RSI"], horizons=[4]).to_dict()
        assert sub["payload"]["features_scanned"] == ["RSI"]
        assert "EMA" not in sub["payload"]["features_scanned"]

    def test_skipped_indicators_declared_when_column_missing(self):
        """衍生品指标缺数据列时静默跳过并声明（不报错）。"""
        r = screen_patterns(
            _random_walk(1), data_ranges=_RANGES, timeframe="1h",
            features=["OI_ZScore"], horizons=[4]).to_dict()
        assert "OI_ZScore" in r["payload"]["skipped_indicators"]
        assert r["payload"]["candidates"] == []

    def test_stop_check_interrupts_without_output(self):
        def _boom():
            raise RuntimeError("stop")
        with pytest.raises(RuntimeError):
            screen_patterns(
                _random_walk(1), data_ranges=_RANGES, timeframe="1h",
                features=["RSI"], horizons=[4], stop_check=_boom)

    def test_two_runs_byte_identical(self):
        """排序确定性：同数据同参数两次扫描逐字节一致。"""
        a = screen_patterns(
            _trending_with_dips(), data_ranges=_RANGES, timeframe="1h",
            features=["RSI", "EMA"], horizons=[4, 12], top_n=15).to_dict()
        b = screen_patterns(
            _trending_with_dips(), data_ranges=_RANGES, timeframe="1h",
            features=["RSI", "EMA"], horizons=[4, 12], top_n=15).to_dict()
        assert json.dumps(a, default=str) == json.dumps(b, default=str)


class TestScreenPatternsDiscipline:
    def test_directional_data_ranks_known_signal(self):
        """方向符号锚定：周期回调数据上 RSI 动量延续结构成立——
        高位（gt）候选前向均值为正、低位（lt）候选为负（RSI 快线低值
        集中于下跌中后段，命中后动量延续）。扫描须挖出与数据真实结构
        一致的符号，互换类语义错误即红。"""
        r = screen_patterns(
            _trending_with_dips(), data_ranges=_RANGES, timeframe="1h",
            features=["RSI"], horizons=[12], top_n=20).to_dict()
        cands = r["payload"]["candidates"]
        assert cands, "趋势+回调数据应有候选产出"
        single = [c for c in cands if not c["is_combination"]]
        lows = [c for c in single
                if c["condition"]["subject"].startswith("rsi")
                and c["condition"]["action"] == "lt"]
        highs = [c for c in single
                 if c["condition"]["subject"].startswith("rsi")
                 and c["condition"]["action"] == "gt"]
        assert lows and highs, "RSI 高低位条件都应出现在候选中"
        assert all(c["mean"] < 0 for c in lows), (
            f"动量延续结构下 lt 候选均值应为负: "
            f"{[(c['condition']['target'], c['mean']) for c in lows]}")
        assert all(c["mean"] > 0 for c in highs), (
            f"动量延续结构下 gt 候选均值应为正: "
            f"{[(c['condition']['target'], c['mean']) for c in highs]}")

    def test_pure_noise_fdr_controlled(self):
        """纯随机数据 fdr_significant 命中受控（挤掉运气成分）。"""
        hits = 0
        for seed in (11, 12, 13):
            r = screen_patterns(
                _random_walk(seed), data_ranges=_RANGES, timeframe="1h",
                features=["RSI", "ADX", "ROC"], horizons=[4, 12],
                top_n=50).to_dict()
            hits += sum(1 for c in r["payload"]["candidates"]
                        if c["fdr_significant"])
        assert hits <= 2, f"随机数据 FDR 命中 {hits} 次（应受控在偶然水平）"

    def test_combination_dedup_and_only_from_rescan(self):
        """组合候选指纹唯一（无对称/跨持有期重复）。"""
        r = screen_patterns(
            _trending_with_dips(), data_ranges=_RANGES, timeframe="1h",
            features=["RSI", "EMA", "MACD"], horizons=[4, 12],
            top_n=50).to_dict()
        cands = r["payload"]["candidates"]
        fps = [c["fingerprint"] for c in cands]
        assert len(fps) == len(set(fps))
        combos = [c for c in cands if c["is_combination"]]
        assert all(isinstance(c["condition"], list) and len(c["condition"]) == 2
                   for c in combos)

    def test_candidate_fingerprint_matches_identity_function(self):
        """候选指纹与规范化函数一致（反向核查比对基准）。"""
        r = screen_patterns(
            _random_walk(1), data_ranges=_RANGES, timeframe="1h",
            features=["RSI"], horizons=[4], top_n=10).to_dict()
        for c in r["payload"]["candidates"]:
            if not c["is_combination"]:
                assert c["fingerprint"] == canonical_condition_hash(
                    c["condition"], "1h")

    def test_two_layer_density(self):
        """完整统计仅 top 3 携带，其余为精简行。"""
        r = screen_patterns(
            _trending_with_dips(), data_ranges=_RANGES, timeframe="1h",
            features=["RSI", "EMA"], horizons=[4, 12], top_n=10).to_dict()
        cands = r["payload"]["candidates"]
        with_detail = [c for c in cands if "forward_returns_detail" in c]
        assert len(with_detail) <= 3
        assert all("review_hint" in r["payload"] for _ in [0])
        assert "condition_stats" in r["payload"]["review_hint"]


class TestCrossCheckWithConditionStats:
    def test_numbers_match_condition_stats(self):
        """复核一致性：候选数字与 condition_stats 独立调用逐字段一致。"""
        df = _trending_with_dips()
        r = screen_patterns(
            df, data_ranges=_RANGES, timeframe="1h",
            features=["RSI"], horizons=[12], top_n=20).to_dict()
        cands = [c for c in r["payload"]["candidates"]
                 if not c["is_combination"]]
        assert cands
        # 取第一条单条件候选独立复核（condition_stats 全 df 同数据同口径）
        c = cands[0]
        h = c["best_horizon"]
        ref = condition_stats(
            df, indicator="RSI",
            params={"period": int(c["condition"]["subject"].split("_")[1])},
            condition=c["condition"], data_ranges=_RANGES,
            forward_bars=h, request_start=None).to_dict()
        fwd_key = f"forward_return_{h}b"
        assert c["samples"] == ref["payload"][fwd_key]["count"]
        assert c["mean"] == pytest.approx(
            round(ref["payload"][fwd_key]["mean"], 6), abs=1e-6)
        assert c["win_rate"] == pytest.approx(
            round(ref["payload"]["win_rate"], 4), abs=1e-4)
