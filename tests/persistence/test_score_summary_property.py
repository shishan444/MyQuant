"""_score_summary 数学不变量属性验证（阶段四闸门第 4 步——属性补强）。

被测：core/persistence/repository/strategy.py::_score_summary（CARD-05
落地的五数概括纯计算函数——批量删除预览 score_summary 的计算核心）。

不变量（任意输入恒成立）：
1. 排序不变性：summary 与输入顺序无关；
2. 守恒：count + null_count == 输入总行数；
3. 单调链：非空域上 min ≤ p25 ≤ median ≤ p75 ≤ max；
4. 单元素退化：五数全同值（TESTING_V3 确定样例+属性双面）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.repository.strategy import _score_summary


_SAMPLES = [
    pytest.param([], id="空列表"),
    pytest.param([None, None], id="全 NULL"),
    pytest.param([5.0], id="单元素"),
    pytest.param([1.0, 2.0], id="两元素"),
    pytest.param([10.0, 20.0, 30.0, 40.0, 50.0], id="五元素升序"),
    pytest.param([30.0, 10.0, 50.0, 20.0, 40.0], id="五元素乱序"),
    pytest.param([None, 3.0, 7.0, None, 5.0], id="NULL 混合"),
    pytest.param([-2.5, 0.0, 2.5], id="负数与零"),
    pytest.param([4.0, 4.0, 4.0, 4.0], id="重复值"),
]


@pytest.mark.parametrize("scores", _SAMPLES)
def test_summary_invariants_hold_for_all_inputs(scores):
    """不变量 1-3：顺序无关+计数守恒+五数单调链。"""
    base = _score_summary(list(scores))
    # 1 排序不变性（排序前后 dict 相等）
    reordered = _score_summary(sorted(
        [s for s in scores if s is not None], reverse=True)
        + [s for s in scores if s is None])
    assert reordered == base, "summary 须与输入顺序无关"
    # 2 守恒：count+null_count==行数
    assert base["count"] + base["null_count"] == len(scores)
    assert base["count"] == sum(1 for s in scores if s is not None)
    # 3 单调链（非空域）
    if base["count"] > 0:
        assert base["min"] <= base["p25"] <= base["median"] \
            <= base["p75"] <= base["max"], base
        assert base["min"] == min(s for s in scores if s is not None)
        assert base["max"] == max(s for s in scores if s is not None)
    else:
        assert base["min"] is None and base["max"] is None \
            and base["p25"] is None and base["median"] is None \
            and base["p75"] is None, "空域五数全 None"


def test_single_element_degenerates_to_same_value():
    """不变量 4：单元素五数全同值（确定样例）。"""
    s = _score_summary([7.7])
    assert s == {"count": 1, "min": 7.7, "p25": 7.7, "median": 7.7,
                 "p75": 7.7, "max": 7.7, "null_count": 0}


@pytest.mark.parametrize("v", [0.0, -1.5, 1e9, 3.33333])
def test_single_element_degenerate_property(v):
    """不变量 4 属性面：任意单值退化全同值。"""
    s = _score_summary([v])
    assert s["min"] == s["p25"] == s["median"] == s["p75"] == s["max"] == v
