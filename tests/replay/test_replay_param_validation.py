"""回放装载链 0 数值参数校验（CARD-06 回归锚——直调通道收口）。

五参数（fee/slippage/initial_cash/stop_loss_pct/halt_drawdown_pct）
拒绝面+六参数放行面（throttle_seconds 拒绝面见
test_replay_paced_mode.py）有限性+值域：REST 面由
schema ge/le+require_* 前置，直调面（run/run_from_dna/pool worker 四路
汇聚点）此前零校验——nan 落库变 NULL 致止损/熔断静默「未配置」、inf
恒不触发=保护解除面（architect CARD-06 实测定性 P0）。修复后载入即
拒：ReplayError("RESEARCH_PARAM_INVALID")+pt_task 行不建。

值域与既有面逐字对齐（不发明）：fee/slippage [0,1] 闭区间（同
ReplayBacktestRequest schema）、initial_cash [1,1e12] 闭区间（同
schema）、stop_loss_pct/halt_drawdown_pct (0,1) 开区间（同
require_halt_drawdown_pct——0 无意义、1 恒不触发同为非法配置面）。

闭区间端点（合法边界）放行验证用装载失败区分法：合法边界值+不存在
策略 → 异常 code 非 RESEARCH_PARAM_INVALID 即证明校验层放行（1e12
资金/60s 量级真跑不可行，值域放行语义与执行语义分离验证）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.repository import paper
from core.replay import ReplayError, ReplayRunner
from tests.replay._env import ReplayEnv

NAN, INF, NINF = float("nan"), float("inf"), float("-inf")


def _reject_cases():
    """拒绝面参数×病态/越界/开区间端点（值域见模块 docstring）。"""
    return [
        pytest.param("fee", v, id=f"fee={v}")
        for v in (NAN, INF, NINF, -0.001, 1.001)
    ] + [
        pytest.param("slippage", v, id=f"slippage={v}")
        for v in (NAN, INF, NINF, -0.001, 1.001)
    ] + [
        pytest.param("initial_cash", v, id=f"initial_cash={v}")
        for v in (NAN, INF, NINF, 0.999, 1e12 + 1)
    ] + [
        pytest.param("stop_loss_pct", v, id=f"stop_loss_pct={v}")
        for v in (NAN, INF, NINF, 0.0, 1.0, 1.5)
    ] + [
        pytest.param("halt_drawdown_pct", v, id=f"halt={v}")
        for v in (NAN, INF, NINF, 0.0, 1.0, 1.5)
    ]


class TestLoadChainParamValidation:
    """直调面载入即拒（CARD-06 主验收面）。"""

    @pytest.mark.parametrize(("param", "value"), _reject_cases())
    def test_invalid_rejected_no_row(self, tmp_path, param, value):
        """病态/越界/开区间端点：ReplayError 结构化拒+行不建。"""
        env = ReplayEnv(tmp_path, n_bars=60)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=40)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, env.strategy_id, start, end,
                   task_id="param_invalid", **{param: value})
        assert param in str(ei.value), (
            f"拒绝消息应点名参数 {param}: {ei.value}")
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        assert paper.get_task(env.db, "param_invalid") is None, (
            "载入即拒纪律：pt_task 行不建")

    @pytest.mark.parametrize(("param", "value"), [
        pytest.param("fee", 0.0, id="fee=0.0 下端点"),
        pytest.param("fee", 1.0, id="fee=1.0 上端点"),
        pytest.param("slippage", 0.0, id="slippage=0.0 下端点"),
        pytest.param("slippage", 1.0, id="slippage=1.0 上端点"),
        pytest.param("initial_cash", 1.0, id="cash=1.0 下端点"),
        pytest.param("initial_cash", 1e12, id="cash=1e12 上端点"),
        pytest.param("stop_loss_pct", 0.5, id="stop_loss=0.5 区间内"),
        pytest.param("halt_drawdown_pct", 0.5, id="halt=0.5 区间内"),
        pytest.param("throttle_seconds", 60.0, id="throttle=60.0 上端点"),
        pytest.param("throttle_seconds", 1e-6, id="throttle=1e-6 紧邻下界"),
        pytest.param("stop_loss_pct", 1e-9, id="stop_loss=1e-9 紧邻下界"),
        pytest.param("halt_drawdown_pct", 1e-9, id="halt=1e-9 紧邻下界"),
    ])
    def test_legal_edge_accepted_by_validation(
            self, tmp_path, param, value):
        """合法边界放行（区分法）：合法边界值+不存在策略 → 异常
        来自装载链策略域（非 PARAM_INVALID）即证明校验层放行。"""
        env = ReplayEnv(tmp_path, n_bars=60)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=40)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, "st_not_exist_404", start, end,
                   **{param: value})
        assert ei.value.code != "RESEARCH_PARAM_INVALID", (
            f"合法值 {param}={value!r} 被校验层误拒: {ei.value}")
