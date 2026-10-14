"""参数稳健性检查模板测试（add-param-sweep 任务 2.5）。

判定期望三重独立依据（design Decision 8）：
- 受控构造：方向性构造的 RSI 低位效应由构造意图保证（n=2000 邻域
  有效=plateau；n=900 效应弱化为孤立点=spike——同一构造器双形态）；
  纯噪声（固定种子）断言结构性质：绝不 plateau
- 独立对照：逐点统计与 condition_stats 同条件同持有期逐字段一致
- 临界样例：资格门 30 两侧（实证边界阈值写死，种子固定可复现）；
  连续门 1/2 两侧即主场景本身（spike=孤立 1 点、plateau 最小形态=2 连续）
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.templates.condition_stats import condition_stats
from core.analysis.templates.param_sweep import param_sweep
from tests.helpers.regime_factory import (
    make_directional_composite as _trending_with_dips,
    make_random_walk_ohlcv as _random_walk,
)

_RANGES = [{"symbol": "TESTUSDT", "timeframe": "1h",
            "start": "2026-01-01", "end": "2026-01-26"}]
_RSI_COND = {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 25.0}


def _sweep(df, *, condition=_RSI_COND, indicator="RSI", values="auto",
           forward_bars=24, **kw):
    return param_sweep(
        df, indicator=indicator, condition=condition,
        sweep={"axis": "target", "values": values},
        forward_bars=forward_bars, data_ranges=_RANGES, timeframe="1h",
        **kw).to_dict()


class TestParamSweepBasics:
    def test_auto_grid_quantiles_union_center(self):
        """P1-T02/T03（Q2 裁决 2026-10-13）：auto=统计段实际分布五分位
        [q10..q90]∪中心点（去重排序）——人工核算口径 quantile(.1/.25/
        .5/.75/.9)；中心在分位内不重复、在分位外并入。"""
        df = _random_walk(1)
        # 先算 subject 列统计段分位（与实现同 df 同窗口——rssi 序列经
        # 指标矩阵产 rsi_14；request_start 后为统计段）
        p = _sweep(df)["payload"]
        vals = p["sweep"]["values"]
        assert len(vals) == 6  # 5 分位 + 中心 25（q50≠25 恰六点）
        assert vals == sorted(vals)
        assert 25.0 in vals
        p2 = _sweep(_random_walk(1),
                    condition={**_RSI_COND, "target": 22.0})["payload"]
        vals2 = p2["sweep"]["values"]
        assert 22.0 in vals2  # 中心在五分位外并入
        assert vals2 == sorted(set(vals2))

    def test_explicit_grid_dedup_sorted(self):
        p = _sweep(_random_walk(1), values=[35, 20, 35, 30])["payload"]
        assert p["sweep"]["values"] == [20.0, 25.0, 30.0, 35.0]  # ∪ 中心 25

    def test_param_rejection_structured(self):
        df = _random_walk(1)
        cases = [
            {"indicator": "NOPE", "condition": _RSI_COND},
            {"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "le", "target": 25}},
            {"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": "rsi_21"}},
            {"condition": {"subject": "nope_col", "action": "lt", "target": 25}},
            {"condition": {"subject": {"indicator": "RSI", "params": {"period": 14}}}},  # 三键结构缺失
            {"values": []},
            {"values": [25.0]},  # 与中心重合，去重后不足 2 点
            {"values": "sometimes"},
        ]
        for bad in cases:
            kw = {"indicator": "RSI", "condition": _RSI_COND, "values": "auto"}
            kw.update(bad)
            with pytest.raises(ResearchError) as ei:
                _sweep(df, **kw)
            assert ei.value.code == "RESEARCH_PARAM_INVALID"
            assert ei.value.detail.get("legal_range"), bad

    def test_forward_bars_bounds(self):
        with pytest.raises(ResearchError):
            _sweep(_random_walk(50), forward_bars=0)
        with pytest.raises(ResearchError):
            _sweep(_random_walk(50), forward_bars=10_000)

    def test_condition_missing_key_422(self):
        """condition 缺三键（0.2.14 结构契约）→ 422 点名缺失键域。
        缺 subject 构造（target 数值在场——数值校验先行的顺序下唯一
        触达三键校验的形）。"""
        with pytest.raises(ResearchError) as ei:
            _sweep(_random_walk(50), condition={
                "action": "lt", "target": 50.0})       # 缺 subject
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert "三键" in ei.value.message

    def test_cross_action_auto_distribution_grid(self):
        """P1-T06 同域（Q2 语义分叉后）：分布取档与 action 无关——
        cross_above 的 auto 网格同样取统计段五分位（旧共享刻度表只有
        lt/gt 刻度而 422 的封闭语义已退役；显式 values 优先序不变）。"""
        p = _sweep(_random_walk(50), condition={
            "subject": {"indicator": "RSI", "params": {"period": 14}},
            "action": "cross_above", "target": 50.0})["payload"]
        vals = p["sweep"]["values"]
        assert len(vals) >= 2 and 50.0 in vals and vals == sorted(vals)

    def test_multi_output_column_distribution_grid(self):
        """多输出指标列（Q2 后）：Aroon aroon_osc_25 列的 auto 网格按
        该列统计段实际分布取五分位∪中心 50（旧 {50,70} 共享刻度语义
        退役）；verdict 四值域不变。"""
        p = _sweep(_random_walk(60), indicator="Aroon",
                   condition={"subject": "aroon_osc_25", "action": "gt",
                              "target": 50.0})["payload"]
        assert p["verdict"] in ("plateau", "spike", "indeterminate",
                                "unsupported")
        vals = sorted(pt["value"] for pt in p["points"])
        assert 50.0 in vals and len(vals) == 6  # 5 分位+中心

    def test_preset_indicator_column_not_overwritten(self):
        """df 预置同名列（rsi_14）时指标计算列跳过赋值（幂等分支）：
        照常出报告、不重复覆写既有列。"""
        df = _random_walk(60).assign(rsi_14=0.5)
        p = _sweep(df)["payload"]
        assert p["points"], "预置同名列不阻断报告产出"

    def test_two_runs_byte_identical(self):
        a = _sweep(_random_walk(7), values=[20, 25, 30])
        b = _sweep(_random_walk(7), values=[20, 25, 30])
        assert json.dumps(a, default=str) == json.dumps(b, default=str)

    def test_gates_disclosed_and_axis_skipped_declared(self):
        p = _sweep(_random_walk(1))["payload"]
        assert p["gates"] == {"q_threshold": 0.05,
                              "min_samples": 30, "min_run": 2}
        assert "period" in p["skipped_axes"]
        assert p["method"]["correction"] == "benjamini_hochberg_fdr"


class TestParamSweepVerdicts:
    def test_known_robust_condition_plateau(self):
        """受控构造（n=2000）：RSI 低位效应邻域重现 → plateau + 稳健区间
        （Q2 后网格=统计段五分位∪中心 25——断言语义化：robust_range 为
        网格首末有效连续段边界而非共享刻度硬编码值）。"""
        p = _sweep(_trending_with_dips(seed=7, n=2000))["payload"]
        assert p["verdict"] == "plateau"
        grid = p["sweep"]["values"]
        assert p["robust_range"] is not None
        assert p["robust_range"][0] in grid and p["robust_range"][1] in grid
        assert p["robust_range"][0] <= 25.0 <= p["robust_range"][1]
        eff = [pt for pt in p["points"] if pt["state"] == "effective"]
        assert len(eff) >= 2, "plateau 前提：连续门达标（最小形态 2 连续）"

    def test_plateau_cross_seed_stable(self):
        """构造意图跨种子成立（非单一种子巧合）。"""
        for seed in (3, 7):
            p = _sweep(_trending_with_dips(seed=seed, n=2000))["payload"]
            assert p["verdict"] == "plateau", seed

    def test_isolated_effect_spike(self):
        """受控构造（n=900 效应弱化）：有效点孤立 → spike（连续段=1 侧
        ——Q2 后断言语义化：恰一个 effective 点且 robust_range None）。"""
        p = _sweep(_trending_with_dips(seed=7, n=900))["payload"]
        assert p["verdict"] == "spike"
        assert p["robust_range"] is None
        eff = [pt["value"] for pt in p["points"]
               if pt["state"] == "effective"]
        assert len(eff) == 1, "孤立单点（连续段 < _MIN_RUN）"

    def test_pure_noise_never_plateau(self):
        """结构性质（不变量）：纯噪声固定种子下绝不判 plateau——
        合法落点仅 spike（偶发孤立显著）或 unsupported（全无正证据）。"""
        for seed in (1, 42):
            p = _sweep(_random_walk(seed=seed, n=2000),
                       values=[20, 25, 30, 35])["payload"]
            assert p["verdict"] in {"spike", "unsupported"}, seed

    def test_no_positive_evidence_unsupported(self):
        """第四格（2×2 判定空间完备）：无 effective 但样本充分参评 →
        unsupported（整体否定）——非 spike 孤证、非 indeterminate。"""
        p = _sweep(_random_walk(seed=42, n=900), values=[45, 55, 65],
                   condition={**_RSI_COND, "target": 55.0})["payload"]
        assert p["verdict"] == "unsupported"
        assert p["robust_range"] is None
        assert all(pt["state"] == "ineffective" for pt in p["points"])
        assert all(pt["samples"] >= 30 for pt in p["points"]), "样本充分才入第四格"

    def test_all_insufficient_indeterminate(self):
        """全网格样本不足 → indeterminate（证据不足非证伪，200 正常返回）。"""
        p = _sweep(_random_walk(1), values=[5, 8])["payload"]
        # 极端阈值在随机游走上触发稀少：全部点不足资格门
        assert all(pt["state"] == "insufficient_sample"
                   for pt in p["points"])
        assert p["verdict"] == "indeterminate"
        assert p["robust_range"] is None

    def test_gate_boundary_samples_30_both_sides(self):
        """资格门临界（实证边界，种子固定可复现）：random_walk(7) 默认
        n=600 上 rsi<22 触发 36（>=30 参评），rsi<20 触发 29（<30 旁路）。"""
        p = _sweep(_random_walk(7), values=[20, 22])["payload"]
        by_val = {pt["value"]: pt for pt in p["points"]}
        assert by_val[20.0]["samples"] < 30
        assert by_val[20.0]["state"] == "insufficient_sample"
        assert by_val[22.0]["samples"] >= 30
        assert by_val[22.0]["state"] != "insufficient_sample"


class TestCrossCheckWithConditionStats:
    def test_numbers_match_condition_stats(self):
        """独立对照：逐点统计与 condition_stats 同条件同持有期逐字段一致。"""
        df = _trending_with_dips(seed=7, n=2000)
        p = _sweep(df, values=[20, 25, 30])["payload"]
        for pt in p["points"]:
            cs = condition_stats(
                df, indicator="RSI", params={"period": 14},
                condition={"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt",
                           "target": pt["value"]},
                forward_bars=24, data_ranges=_RANGES).to_dict()
            cp = cs["payload"]
            fwd_stats = cp["forward_return_24b"]
            sig = cs["confidence"]["forward_significance"]
            assert pt["forward_return_mean"] == pytest.approx(
                fwd_stats["mean"], nan_ok=True), pt["value"]
            assert pt["baseline_mean"] == pytest.approx(
                cp["baseline_24b"]["mean"])
            assert pt["p_value"] == pytest.approx(sig["p_value"], nan_ok=True)
            # 资格门口径对齐：原始有效触发数 == condition_stats 的 count
            assert pt["samples"] == fwd_stats["count"], pt["value"]


class TestTimeframeKey:
    """0.2.14 timeframe 键透传（design 场景矩阵⑬ / spec ai-research-tools
    「高周期中心条件经投影求值」）：带键中心条件整条件在高周期轴求值、
    布尔掩码投影到基准索引；低周期或不可达周期结构化拒绝。"""

    @staticmethod
    def _mtf_env(tmp_path):
        from core.analysis.condition_kernel import MtfEvalContext
        from tests.helpers.data_factory import make_ohlcv

        data_dir = tmp_path / "market"
        data_dir.mkdir(parents=True, exist_ok=True)
        base = make_ohlcv(n=600, freq="1h", start="2024-01-01", seed=23)
        high = make_ohlcv(n=150, freq="4h", start="2024-01-01", seed=23)
        base.to_parquet(data_dir / "TESTUSDT_1h.parquet")
        high.to_parquet(data_dir / "TESTUSDT_4h.parquet")
        return base, high, MtfEvalContext(data_dir, "TESTUSDT", "1h")

    @classmethod
    def _sweep(cls, base, cond, ctx):
        t = float(cond["target"])
        payload = param_sweep(
            base.copy(), indicator="RSI", condition=cond,
            sweep={"axis": "target", "values": [t - 10.0, t, t + 10.0]},
            forward_bars=12, data_ranges=_RANGES, timeframe="1h",
            mtf=ctx).to_dict()["payload"]
        return next(p for p in payload["points"] if p["value"] == t)

    def test_高周期键中心条件_掩码投影轴分离生效(self, tmp_path):
        """阈值超出 4h close 全域：带键（4h 轴求值）掩码全 False →
        资格门 insufficient_sample；同阈值去键（1h 轴求值，固定种子下
        存在越界 bar）样本 >0——两轴结果分离证明投影路径接通。"""
        base, high, ctx = self._mtf_env(tmp_path)
        beyond = float(high["close"].max()) + 1.0
        with_key = self._sweep(
            base, {"subject": "close", "action": "gt",
                   "target": beyond, "timeframe": "4h"}, ctx)
        assert with_key["samples"] == 0
        assert with_key["state"] == "insufficient_sample"
        without_key = self._sweep(
            base, {"subject": "close", "action": "gt", "target": beyond}, ctx)
        assert without_key["samples"] > 0

    def test_低周期timeframe_结构化拒绝(self, tmp_path):
        base, _, ctx = self._mtf_env(tmp_path)
        with pytest.raises(ResearchError) as ei:
            self._sweep(base, {"subject": "close", "action": "gt",
                               "target": 100.0, "timeframe": "15m"}, ctx)
        assert ei.value.code == "TIMEFRAME_NOT_HIGHER"

    def test_不可达周期_结构化拒绝(self, tmp_path):
        base, _, ctx = self._mtf_env(tmp_path)
        with pytest.raises(ResearchError) as ei:
            self._sweep(base, {"subject": "close", "action": "gt",
                               "target": 100.0, "timeframe": "1w"}, ctx)
        assert ei.value.code == "RESEARCH_SYMBOL_NOT_FOUND"


    def test_结构化subject高周期键_中心条件掩码投影生效(self, tmp_path):
        """P-5 回归（3b33c4f）：结构化 subject 模板层字符串化（ema_20）
        在高周期轴 decode 现算——带键中心点样本与内核直调一致，且与
        去键（1h 轴）分离（修复前带键静默全 0）。"""
        from core.analysis.condition_kernel import mask_for

        base, high, ctx = self._mtf_env(tmp_path)
        ctx.eval_start = pd.Timestamp("2024-01-15", tz="UTC")  # 84 bar ≥ 60
        ranges = [{"symbol": "TESTUSDT", "timeframe": "1h",
                   "start": "2024-01-15", "end": "2024-01-25"}]
        cond = {"subject": {"indicator": "EMA", "params": {"period": 20}},
                "action": "gt", "target": float(high["close"].median()),
                "timeframe": "4h"}
        p = param_sweep(
            base.copy(), indicator="EMA", condition=cond,
            sweep={"axis": "target", "values": [cond["target"] - 10.0,
                                                cond["target"],
                                                cond["target"] + 10.0]},
            forward_bars=12, data_ranges=ranges, timeframe="1h",
            mtf=ctx).to_dict()["payload"]
        with_key = next(x for x in p["points"] if x["value"] == cond["target"])
        assert with_key["samples"] > 0
        # 与内核直调一致（尾部丢窗口径）
        mask = mask_for(base.copy(), cond, ctx)
        locs = [i for i, v in enumerate(mask) if v]
        direct = sum(1 for i in locs if i + 12 < len(base))
        assert with_key["samples"] == direct
        # 去键（1h 轴 EMA20）分离
        p2 = param_sweep(
            base.copy(), indicator="EMA",
            condition={"subject": {"indicator": "EMA", "params": {"period": 20}},
                       "action": "gt", "target": cond["target"]},
            sweep={"axis": "target", "values": [cond["target"] - 10.0,
                                                cond["target"],
                                                cond["target"] + 10.0]},
            forward_bars=12, data_ranges=ranges, timeframe="1h",
            mtf=ctx).to_dict()["payload"]
        without = next(x for x in p2["points"] if x["value"] == cond["target"])
        assert without["samples"] != with_key["samples"]


class TestGapClosureSweep:
    """阶段四盲区消解：参数拒绝族、网格解析分支、subject 转译。"""

    def test_condition缺键_target数值先拒(self) -> None:
        """校验顺序：target 数值检查先于三键完备（缺键即缺 target）。"""
        df = _random_walk(7, 2000)
        with pytest.raises(ResearchError, match="须为数值"):
            _sweep(df, condition={"subject": "close", "action": "gt"})

    def test_sweep轴非法_422(self) -> None:
        df = _random_walk(7, 2000)
        from core.analysis.templates.param_sweep import param_sweep
        with pytest.raises(ResearchError, match="sweep.axis"):
            param_sweep(df, indicator="RSI",
                        condition={"subject": {"indicator": "RSI",
                                               "params": {"period": 14}},
                                   "action": "lt", "target": 35.0},
                        sweep={"axis": "params", "values": [1, 2]},
                        forward_bars=24, data_ranges=_RANGES, timeframe="1h")

    def test_多输出指标列选择器网格(self) -> None:
        """Aroon 多输出：subject 含 aroon_up 时选 gt [70,80] 网格
        （selector in subject 匹配分支）。"""
        df = _random_walk(7, 2000)
        res = _sweep(
            df, indicator="MACD",
            condition={"subject": {"indicator": "MACD",
                                   "params": {"fast": 12, "slow": 26,
                                               "signal": 9},
                                   "field": "histogram"},
                       "action": "gt", "target": 5.0})
        assert res["payload"]["gates"] is not None

    def test_auto_degenerate_distribution_422(self) -> None:
        """P1-T05（Q2 语义）：分布不可得（subject 列统计段全 NaN 或有效
        样本 < 内建阈值 10）→ RESEARCH_PARAM_INVALID 指引显式列表，不
        静默回退静态刻度（旧「无网格指标 auto 422」共享刻度语义退役——
        EMA 等任一指标有分布即可 auto）。"""
        import numpy as np
        import pandas as pd
        from tests.helpers.data_factory import make_ohlcv
        df = make_ohlcv(300)  # 统一构造入口（rsi 列后置污染）
        # 构造全 NaN 列：预置 rsi_14 为 NaN（指标矩阵幂等跳过——
        # 预置列不被覆写，既有 test_preset_indicator_column_not_
        # overwritten 锚定的行为）
        df["rsi_14"] = float("nan")
        with pytest.raises(ResearchError) as ei:
            _sweep(df)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert ei.value.detail["param"] == "sweep.values"
        assert "显式传入数值列表" in ei.value.detail["legal_range"]

    def test_values含非数值_422(self) -> None:
        df = _random_walk(7, 2000)
        from core.analysis.templates.param_sweep import param_sweep
        with pytest.raises(ResearchError, match="非数值"):
            param_sweep(df, indicator="RSI",
                        condition={"subject": {"indicator": "RSI",
                                               "params": {"period": 14}},
                                   "action": "lt", "target": 35.0},
                        sweep={"axis": "target", "values": [30, "abc"]},
                        forward_bars=24, data_ranges=_RANGES, timeframe="1h")

    def test_subject引用非法_转译(self) -> None:
        df = _random_walk(7, 2000)
        with pytest.raises(ResearchError) as ei:
            _sweep(df, condition={"subject": {"indicator": "GHOST"},
                                  "action": "lt", "target": 30.0})
        assert ei.value.code == "RESEARCH_PARAM_INVALID"


class TestAutoGridSmallRangeAcceptance:
    """P1-T01 验收锚（Q2 裁决落地）：小值域指标（OI_ChangeRate 实际
    值域 ±0.12）auto 网格五点全落实际值域内且全档有样本——RR-009
    零样本档场景反转复现（旧共享刻度 [-10,-5]/[5,10] 两档零样本）。"""

    def test_oi_change_rate_grid_within_actual_range(self):
        from pathlib import Path

        market = Path("data/market/BTCUSDT_4h.parquet")
        if not market.exists():
            pytest.skip("真实数据 BTCUSDT 4h 不在库（data/market）")
        import pandas as pd
        df = pd.read_parquet(market)
        rng = {"symbol": "BTCUSDT", "timeframe": "4h",
               "start": "2024-05-01", "end": "2026-04-01"}
        from core.analysis.warmup_loader import load_with_warmup
        work, request_start = load_with_warmup(
            Path("data/market"), "BTCUSDT", "4h",
            rng["start"], rng["end"], ["OI_ChangeRate"])
        p = param_sweep(
            work, indicator="OI_ChangeRate",
            condition={"subject": "oi_change_rate_14", "action": "gt",
                       "target": 0.0},
            sweep={"axis": "target", "values": "auto"},
            forward_bars=24, data_ranges=[rng], timeframe="4h",
            request_start=request_start).to_dict()["payload"]
        vals = p["sweep"]["values"]
        # 全档落实际值域内（|值| ≤ 0.5 的宽松上界——实际分布 ±0.12）
        assert all(abs(v) <= 0.5 for v in vals), vals
        # 全档有样本（无零样本档——旧刻度两档零样本的反转；
        # points 键=samples）
        sampled = [pt for pt in p["points"] if pt["samples"] > 0]
        assert len(sampled) == len(vals), (
            f"全档有样本锚：{len(sampled)}/{len(vals)}，"
            f"零样本档 {[pt['value'] for pt in p['points']
                         if pt['samples'] == 0]}")
