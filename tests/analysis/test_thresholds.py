"""门槛表判定测试（harden-research-test-coverage 1.5 / add-inconclusive-verdict T1）。

覆盖：DEFAULT_PROFILE 三规则边界双向 / 三段闸门五出口（证据闸门→
证伪→通过） / CI 数值锚（独立对照——统计表常数手拼公式，不用被测
实现生成期望） / R3 组合加严闸门线 / 最差聚合三态映射。
"""

import math

import numpy as np
import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.thresholds import (
    DEFAULT_PROFILE,
    _aggregate_verdicts,
    judge,
    tighten_profile,
)


def _metrics(total_return=0.01, max_drawdown=0.10, total_trades=10):
    return {"total_return": total_return, "max_drawdown": max_drawdown,
            "total_trades": total_trades}


def _returns_ok(n=10):
    """同号正收益序列（mean>0、CI 不跨零——pass 路径的合格证据）。"""
    return np.full(n, 0.01)


_CROSS = np.array([0.5, -0.4, 0.45, -0.35, 0.4, 0.42])  # mean=0.17、std 大→CI 跨零


class TestJudgeBoundaries:
    """DEFAULT_PROFILE 三规则（>0 / <0.25 / >=5）的边界值双向（证据充分场景零漂移锚）。"""

    @pytest.mark.parametrize(
        ("metrics", "expected"),
        [
            pytest.param(_metrics(total_return=0.0001), "pass", id="收益微正通过"),
            pytest.param(_metrics(total_return=0.0), "fail", id="收益零边界不通过（严格大于）"),
            pytest.param(_metrics(total_return=-0.001), "fail", id="收益负不通过"),
            pytest.param(_metrics(max_drawdown=0.2499), "pass", id="回撤贴上限通过"),
            pytest.param(_metrics(max_drawdown=0.25), "fail", id="回撤恰 0.25 不通过（严格小于）"),
            pytest.param(_metrics(max_drawdown=0.2501), "fail", id="回撤越界不通过"),
            pytest.param(_metrics(total_trades=5), "pass", id="交易数下界通过（含 5）"),
            pytest.param(_metrics(total_trades=4), "inconclusive", id="交易数不足证据不足（0.2.11）"),
        ],
    )
    def test_boundary_values(self, metrics, expected):
        verdict, details = judge(metrics, trade_returns=_returns_ok(
            max(metrics["total_trades"], 2) if metrics["total_trades"] >= 5 else 10))
        assert verdict == expected, f"{details}"

    def test_missing_metric_fails_closed(self):
        """规则引用的指标缺失 → fail（缺数据不过门，宁可多拒——D4 零回归锚）。"""
        verdict, details = judge({"total_return": 0.1})
        assert verdict == "fail"
        failed = [d for d in details if d["actual"] is None]
        assert len(failed) == 2  # drawdown + trades 缺失
        assert not any(d["rule"] == "ci_95" for d in details), "闸门未到达，无 ci_95 条目"

    def test_threshold_details_exposed(self):
        """判定明细逐条暴露（rule/op/threshold/actual/passed）+ ci_95 增量。"""
        _, details = judge(_metrics(), trade_returns=_returns_ok())
        rules = {d["rule"] for d in details}
        assert rules >= {"total_return", "max_drawdown", "total_trades"}  # ci_95 增量兼容
        rule_rows = [d for d in details if d["rule"] != "ci_95"]
        assert all({"op", "threshold", "actual", "passed"} <= set(d) for d in rule_rows)


class TestThreeGatePaths:
    """三段闸门五出口（0.2.11）：证据闸门 / 通道防御 / CI / 证伪 / 通过。"""

    def test_sample_insufficient_inconclusive(self):
        """样本不足 → inconclusive，ci_95 条目不存在（未算 CI）。"""
        verdict, details = judge(_metrics(total_trades=4),
                                 trade_returns=np.full(4, 0.01))
        assert verdict == "inconclusive"
        assert not any(d["rule"] == "ci_95" for d in details)

    def test_missing_returns_engineering_fail(self):
        """total_trades≥5 但序列缺失 → fail（工程错误——数据通道断裂）。"""
        verdict, details = judge(_metrics(total_trades=5))
        assert verdict == "fail"
        assert not any(d["rule"] == "ci_95" for d in details)

    def test_length_mismatch_engineering_fail(self):
        """序列长度与 total_trades 不匹配 → fail（通道断裂防御）。"""
        verdict, _ = judge(_metrics(total_trades=5), trade_returns=np.full(4, 0.01))
        assert verdict == "fail"

    def test_ci_crosses_zero_inconclusive(self):
        """CI 跨零 → inconclusive（方向不可区分于噪声），ci_95 条目存在。"""
        verdict, details = judge(_metrics(total_trades=6), trade_returns=_CROSS)
        assert verdict == "inconclusive"
        ci = next(d for d in details if d["rule"] == "ci_95")
        assert ci["crosses_zero"] is True

    def test_ci_clear_pass_and_falsify(self):
        """CI 不跨零时走既有规则：达标 pass / 亏损 fail（证伪零回归）。"""
        assert judge(_metrics(total_trades=6),
                     trade_returns=_returns_ok(6))[0] == "pass"
        assert judge(_metrics(total_return=-0.01, total_trades=6),
                     trade_returns=np.full(6, -0.01))[0] == "fail"

    def test_r3_gate_line_follows_profile(self):
        """R3 行为锚：tighten 后闸门线 6——组合场景 5 笔判 inconclusive（错义修正）。"""
        verdict, _ = judge(_metrics(total_trades=5), tighten_profile(1.2),
                           trade_returns=_returns_ok(5))
        assert verdict == "inconclusive"


class TestCiNumericAnchor:
    """CI 数值锚——独立对照：t 分位数取统计表常数，测试内手拼公式。"""

    def test_ci_values_match_manual_formula(self):
        _, details = judge(_metrics(total_trades=6), trade_returns=_CROSS)
        ci = next(d for d in details if d["rule"] == "ci_95")
        n = 6
        mean = float(np.mean(_CROSS))
        std = float(np.std(_CROSS, ddof=1))
        t_crit = 2.5706  # 统计表 t_{0.975,5}
        half = t_crit * std / math.sqrt(n)
        assert ci["n"] == n
        assert ci["mean"] == pytest.approx(mean)
        assert ci["std"] == pytest.approx(std)
        assert ci["half_width"] == pytest.approx(half, rel=1e-4)
        assert ci["lower"] == pytest.approx(mean - half, rel=1e-4)
        assert ci["upper"] == pytest.approx(mean + half, rel=1e-4)
        assert ci["crosses_zero"] is True  # lower<0<upper（mean 0.17、half≈0.44）


class TestAggregateVerdicts:
    """最差聚合三态映射（random_window 总判定与 staged 汇总唯一实现）。"""

    @pytest.mark.parametrize(
        ("verdicts", "expected"),
        [
            (["pass", "pass"], "pass"),
            (["pass", "inconclusive"], "inconclusive"),
            (["inconclusive", "inconclusive"], "inconclusive"),
            (["pass", "inconclusive", "fail"], "fail"),
            (["fail"], "fail"),
            ([], "inconclusive"),  # 防御值：无证据=不定论（审查 F4）
        ],
    )
    def test_priority(self, verdicts, expected):
        assert _aggregate_verdicts(verdicts) == expected


class TestTightenProfile:
    def test_tightened_numeric_values(self):
        """N2 收紧数值：回撤上限 0.25/1.2、交易数 5*1.2、收益阈值不变（0 锚）。"""
        prof = tighten_profile(1.2)
        rules = prof["rules"]
        assert rules["max_drawdown"]["value"] == pytest.approx(0.25 / 1.2)
        assert rules["total_trades"]["value"] == pytest.approx(5 * 1.2)
        assert rules["total_return"]["value"] == 0.0, "收益零锚收紧后仍为零（0 乘不变）"

    def test_tightened_actually_tightens(self):
        """收紧后恰在上边界原通过的数据变不通过（语义验证）。"""
        edge = _metrics(max_drawdown=0.2499, total_trades=7)
        v_default, _ = judge(edge, trade_returns=_returns_ok(7))
        v_tight, _ = judge(edge, tighten_profile(1.2), trade_returns=_returns_ok(7))
        assert v_default == "pass"
        assert v_tight == "fail", "收紧门槛必须实际收紧（否则加严门形同虚设）"


class TestGapClosureThresholds:
    def test_ci_entry_样本不足None(self) -> None:
        import numpy as np
        from core.analysis.thresholds import _ci_entry
        assert _ci_entry(np.array([0.01])) is None  # n<2 无 CI

    def test_tighten_乘数作用于spec值(self) -> None:
        from core.analysis.thresholds import tighten_profile
        prof = tighten_profile(2.0)
        assert prof["name"] == "default_tightened_x2.0"
        assert any(isinstance(v, (int, float)) and v > 0
                   for spec in prof.get("rules", {}).values()
                   for v in (spec.values() if isinstance(spec, dict) else []))
