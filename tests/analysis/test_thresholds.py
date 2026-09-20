"""门槛表边界参数化与收紧数值断言（harden-research-test-coverage 1.5）。"""

import pytest

pytestmark = [pytest.mark.unit]

from core.analysis.thresholds import DEFAULT_PROFILE, judge, tighten_profile


def _metrics(total_return=0.01, max_drawdown=0.10, total_trades=10):
    return {"total_return": total_return, "max_drawdown": max_drawdown,
            "total_trades": total_trades}


class TestJudgeBoundaries:
    """DEFAULT_PROFILE 三规则（>0 / <0.25 / >=5）的边界值双向。"""

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
            pytest.param(_metrics(total_trades=4), "fail", id="交易数不足不通过"),
        ],
    )
    def test_boundary_values(self, metrics, expected):
        verdict, details = judge(metrics)
        assert verdict == expected, f"{details}"

    def test_missing_metric_fails_closed(self):
        """规则引用的指标缺失 → fail（缺数据不过门，宁可多拒）。"""
        verdict, details = judge({"total_return": 0.1})
        assert verdict == "fail"
        failed = [d for d in details if d["actual"] is None]
        assert len(failed) == 2  # drawdown + trades 缺失

    def test_threshold_details_exposed(self):
        """判定明细逐条暴露（rule/op/threshold/actual/passed）。"""
        _, details = judge(_metrics())
        assert {d["rule"] for d in details} == {"total_return", "max_drawdown", "total_trades"}
        assert all({"op", "threshold", "actual", "passed"} <= set(d) for d in details)


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
        edge = _metrics(max_drawdown=0.2499, total_trades=5)
        v_default, _ = judge(edge)
        v_tight, _ = judge(edge, tighten_profile(1.2))
        assert v_default == "pass"
        assert v_tight == "fail", "收紧门槛必须实际收紧（否则加严门形同虚设）"
