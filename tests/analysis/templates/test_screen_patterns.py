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
        skipped = r["payload"]["skipped_indicators"]
        assert any(e["name"] == "OI_ZScore" for e in skipped)
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
                if isinstance(c["condition"]["subject"], dict)
                and c["condition"]["subject"]["indicator"] == "RSI"
                and c["condition"]["action"] == "lt"]
        highs = [c for c in single
                 if isinstance(c["condition"]["subject"], dict)
                 and c["condition"]["subject"]["indicator"] == "RSI"
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
            params={"period": c["condition"]["subject"]["params"]["period"]},
            condition=c["condition"], data_ranges=_RANGES,
            forward_bars=h, request_start=None).to_dict()
        fwd_key = f"forward_return_{h}b"
        assert c["samples"] == ref["payload"][fwd_key]["count"]
        assert c["mean"] == pytest.approx(
            round(ref["payload"][fwd_key]["mean"], 6), abs=1e-6)
        assert c["win_rate"] == pytest.approx(
            round(ref["payload"]["win_rate"], 4), abs=1e-4)


class TestMtfContextPassthrough:
    """0.2.14 P-walk2：screen 分支统一接收 mtf ctx（形制一致降低分支
    遗漏），但候选生成恒基准周期形制——ctx 不被消费：传入 ctx 不改变
    扫描结果，候选条件不含 timeframe 键。"""

    def test_候选恒基准形制_mtf上下文不被消费(self):
        from pathlib import Path

        from core.analysis.condition_kernel import MtfEvalContext

        df = _random_walk(1)
        ctx = MtfEvalContext(Path("/nonexistent"), "TESTUSDT", "1h")
        kw = dict(data_ranges=_RANGES, timeframe="1h",
                  features=["RSI"], horizons=[4, 12], top_n=5)
        with_ctx = screen_patterns(df, mtf=ctx, **kw).to_dict()["payload"]
        without = screen_patterns(df, **kw).to_dict()["payload"]
        assert with_ctx["candidates"] == without["candidates"]  # ctx 零影响
        assert all("timeframe" not in c["condition"] for c in with_ctx["candidates"])


class TestGapClosureScreen:
    """阶段四盲区消解：扫描循环容错与边界分支。

    不可达判定（记录，调用图依据）：①pattern 前缀列 probes 发射
    （_emit_conditions_for 的 _PATTERN_PREFIX 分支）——pattern 族指标
    不在 _DEFAULT_PARAMS，主循环永不产出 pattern_ 列，分支为将来
    接入预留；②_co_occurrence 的 mask None continue——内存态 ranked
    的单条件记录恒带 _stat_mask（仅结果 JSON 反序列化缺，不经此路径）。
    """

    def test_摘牌指标显式拒绝(self):
        """Q3 摘牌（2026-10-14）后引用摘牌名 features=["VWAP"] →
        RESEARCH_PARAM_INVALID 显式拒绝（不静默空结果——G5-T04 分入口
        断言之显式 indicator 参数入口）。"""
        from core.analysis.hypothesis_service import ResearchError
        with pytest.raises(ResearchError, match="未知指标.*VWAP"):
            screen_patterns(
                _random_walk(1), data_ranges=_RANGES, timeframe="1h",
                features=["VWAP"], horizons=[4]).to_dict()

    def test_compute异常容错_声明跳过(self, monkeypatch):
        import core.features.indicators as fi
        real = fi._compute_indicator

        def boom(df, name, params):
            if name == "RSI":
                raise RuntimeError("compute failed")
            return real(df, name, params)

        monkeypatch.setattr(fi, "_compute_indicator", boom)
        r = screen_patterns(
            _random_walk(1), data_ranges=_RANGES, timeframe="1h",
            features=["RSI"], horizons=[4]).to_dict()
        assert any(e["name"] == "RSI"
                   for e in r["payload"]["skipped_indicators"])

    def test_stop_check_主循环与组合循环两调用点(self):
        """stop_check 覆盖主循环（逐指标）与组合循环（逐配对）——
        计数式不抛，断言组合段也被走到（调用次数超指标数）。"""
        calls = {"n": 0}

        def _tick():
            calls["n"] += 1

        screen_patterns(
            _trending_with_dips(1200), data_ranges=_RANGES, timeframe="1h",
            features=["RSI", "EMA"], horizons=[4], stop_check=_tick)
        # 指标循环（2 指标）+ 条件循环（多条件）+ 组合循环（≥1 配对）
        assert calls["n"] > 4, "组合循环的 stop_check 未被走到"

    def test_零方差前向收益_p为None不入族(self):
        """恒定 close → pct_change 全 0 → 零方差 p None → 无候选
        （BH 约定：无效检验不混入）。"""
        import numpy as np
        import pandas as pd
        n = 300
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC",
                            name="timestamp")
        df = pd.DataFrame({
            "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
            "close": [100.0] * n, "volume": [10.0] * n}, index=idx)
        r = screen_patterns(
            df, data_ranges=_RANGES, timeframe="1h", features=["RSI"],
            horizons=[4], min_samples=30).to_dict()
        assert r["payload"]["candidates"] == []


class TestGapClosureScreen3:
    """第三批：horizon 超长拒与衍生品真实缺列路径。"""

    def test_horizon超数据长度_422(self):
        with pytest.raises(ResearchError, match="超数据长度"):
            screen_patterns(_random_walk(1, 50), data_ranges=_RANGES,
                            timeframe="1h", features=["RSI"], horizons=[50])

    def test_衍生品真实缺列_compute产空路径(self):
        """OI_ZScore 有默认参数集但 df 缺 oi 列——compute 产空/
        抛错的 produced 空路径（skipped 声明）。"""
        r = screen_patterns(
            _random_walk(1, 300), data_ranges=_RANGES, timeframe="1h",
            features=["OI_ZScore", "RSI"], horizons=[4]).to_dict()
        skipped = r["payload"]["skipped_indicators"]
        assert any(e["name"] == "OI_ZScore" for e in skipped)
        assert all(e["name"] != "RSI" for e in skipped)


class TestGapClosureComboSkip:
    """第三批：组合循环同 subject 跳过分支（构造双尾数据使 RSI 的
    lt/gt 两条件同入 singles top——同 subject 配对必触发跳过）。"""

    def test_同subject双条件入top_组合跳过不炸(self):
        import numpy as np
        import pandas as pd
        n = 3000
        rng = np.random.default_rng(21)
        idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC",
                            name="timestamp")
        # 大幅振荡：RSI 频繁触双尾（lt 30 与 gt 70 命中都过 min_samples）
        swing = np.sin(np.arange(n) / 18.0) * 400
        close = 60000 + swing + rng.normal(0, 40, n)
        df = pd.DataFrame({
            "open": close, "high": close + 60, "low": close - 60,
            "close": close, "volume": rng.uniform(100, 800, n)}, index=idx)
        r = screen_patterns(
            df, data_ranges=_RANGES, timeframe="1h", features=["RSI"],
            horizons=[4, 12], min_samples=30).to_dict()
        p = r["payload"]
        singles = [c["condition"]["subject"]
                   for c in p["candidates"] if not c.get("parts")]
        subjects = [list(s.values())[0] if isinstance(s, dict) else s
                    for s in singles]
        # 构造意图：rsi 列至少两个条件入 singles（lt/gt 双尾）
        assert len(singles) >= 2, "构造未达同 subject 双条件入 top"
        # 组合输出中不存在同 subject 对（同 subject 跳过的结果面）
        for c in p["candidates"]:
            if c.get("parts"):
                parts = c["parts"]
                assert len({str(x.get("subject")) for x in parts}) == len(parts)

    def test_n_significant_toplevel_summary_key(self):
        """0.3.3 G8：顶层 n_significant 汇总——与逐候选 fdr_significant
        计数同源（装配点单点求和），纯噪声下 FDR 全灭一眼可见（=0）。"""
        for seed in (11, 12):
            r = screen_patterns(
                _random_walk(seed), data_ranges=_RANGES, timeframe="1h",
                features=["RSI", "ADX"], horizons=[4, 12],
                top_n=50).to_dict()
            payload = r["payload"]
            manual = sum(1 for c in payload["candidates"]
                         if c["fdr_significant"])
            assert payload["n_significant"] == manual  # 同源一致性
            assert payload["n_significant"] == 0  # 纯噪声全灭直读

    def test_n_significant_not_capped_by_top_n(self):
        """CARD-04 锚：n_significant 全族口径——统计先于 top_n 截断
        （by_fp 指纹去重全量），显著数可超截断清单长度；两档 top_n
        相等（截断无关性对拍，抗数据演化）；不截断档与候选清单逐条
        计数一致（可核对性）。"""
        df = _trending_with_dips(1)
        common = dict(data_ranges=_RANGES, timeframe="1h",
                      features=["RSI", "EMA"], horizons=[4, 12])
        small = screen_patterns(df, top_n=3, **common).to_dict()["payload"]
        large = screen_patterns(df, top_n=100, **common).to_dict()["payload"]
        # 前置：小档截断确实发生（清单被截到 top_n，显著候选大量溢出）
        assert len(small["candidates"]) == 3
        # 封顶解除：全族显著数 > 截断清单长度（修复前此处被封在 3）
        assert small["n_significant"] > 3
        # 截断无关：两档同数据同参数显著数相等
        assert small["n_significant"] == large["n_significant"]
        # 可核对：不截断档（top_n 100 > 全族候选数）与清单逐条计数一致
        assert len(large["candidates"]) <= 100
        manual = sum(1 for c in large["candidates"] if c["fdr_significant"])
        assert large["n_significant"] == manual


class TestSkippedThreeReasons:
    """S5a/S5b/S5c：skipped_indicators 三因分类（unify-tool-return-contract 任务 7）。"""

    def test_s5a_three_reasons_each_hit_by_name(self):
        """三原因具名各一例（S5a 原义）：VolumeProfile 无默认参数网格
        （no_default_params——注册表成员但不在 _DEFAULT_PARAMS）/OBV
        计算有产出但无覆盖动作（no_covered_actions）/OI_ZScore 计算
        无产出（compute_failed——衍生品列缺失静默无产出，无异常时
        reason_detail 键缺席=「可空」的实现形制）。"""
        r = screen_patterns(
            _random_walk(1, 400), data_ranges=_RANGES, timeframe="1h",
            features=["VolumeProfile", "OBV", "OI_ZScore"],
            horizons=[4]).to_dict()
        by_name = {e["name"]: e
                   for e in r["payload"]["skipped_indicators"]}
        assert by_name["VolumeProfile"]["reason"] == "no_default_params"
        assert by_name["OBV"]["reason"] == "no_covered_actions"
        assert by_name["OI_ZScore"]["reason"] == "compute_failed"
        assert "reason_detail" not in by_name["OI_ZScore"], \
            "静默无产出（无异常）不附 reason_detail"

    def test_s5b_reason_in_closed_enum_for_all(self):
        """S5b 全量枚举封闭：全指标扫描 skipped 元素 reason ∈ 三值
        封闭枚举，元素形制含 name/reason。"""
        r = screen_patterns(
            _random_walk(1, 400), data_ranges=_RANGES, timeframe="1h",
            features="all", horizons=[4]).to_dict()
        skipped = r["payload"]["skipped_indicators"]
        assert skipped, "跳过清单非空"
        for e in skipped:
            assert set(e) >= {"name", "reason"}, f"元素形制：{e}"
            assert e["reason"] in ("no_default_params", "compute_failed",
                                   "no_covered_actions"), f"枚举外值：{e}"

    def test_compute_failed_carries_reason_detail_on_exception(
            self, monkeypatch):
        """compute 抛异常时 reason_detail=首个异常摘要（设计卡 2.5：
        compute_failed 附 reason_detail: str 首个异常摘要，可空）。"""
        import core.features.indicators as indicators_mod

        def _boom(df, name, params):
            raise RuntimeError("boom-探针异常")

        monkeypatch.setattr(indicators_mod, "_compute_indicator", _boom)
        r = screen_patterns(
            _random_walk(1, 300), data_ranges=_RANGES, timeframe="1h",
            features=["RSI"], horizons=[4]).to_dict()
        skipped = r["payload"]["skipped_indicators"]
        assert len(skipped) == 1 and skipped[0]["name"] == "RSI"
        assert skipped[0]["reason"] == "compute_failed"
        assert skipped[0].get("reason_detail") == "boom-探针异常"

    def test_s5c_tool_doc_element_form(self):
        """S5c 工具描述同步元素形制（源码文本断言——工具经闭包注册
        无模块级属性）。"""
        from pathlib import Path
        src = Path("mcpserver/tools.py").read_text("utf-8")
        for token in ("no_default_params", "compute_failed",
                      "no_covered_actions", "{name, reason}"):
            assert token in src, f"工具描述缺元素形制说明: {token}"
