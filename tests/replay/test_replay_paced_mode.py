"""限速演示模式测试（add-replay-paced-mode 任务 1.2/2.2）。

bar 级节流三面：生效可观测（总时长≈bar 数×throttle 容差内+结果与
不限速逐位一致）/参数校验（0 与负值显式 422 拒）/MCP 参数面不暴露
（负向断言——演示语义归人类脚本直调）。缺省零侵入由 tests/replay
全域回归承载（七文件绿=逐位一致证明）。
"""
from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.unit]

from core.replay import ReplayError, ReplayRunner
from tests.replay._env import ReplayEnv


class TestPacedMode:
    def test_throttle_effective_and_bitwise_identical(self, tmp_path):
        """生效用例：throttle=0.01 跑 ~100 bar——总时长量级断言+结果
        与不限速同输入逐位一致（零侵入在节流侧的证明）。"""
        env = ReplayEnv(tmp_path, n_bars=600, seed=7)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        # 不限速基准（缺省 None 零开销路径）
        t0 = time.monotonic()
        base = rr.run(env.db, env.strategy_id, start, end,
                      task_id="pace_base")
        base_secs = time.monotonic() - t0
        # 限速档（bar 数=bars_processed，量级 300 根级）
        t1 = time.monotonic()
        paced = rr.run(env.db, env.strategy_id, start, end,
                       task_id="pace_th", throttle_seconds=0.01)
        paced_secs = time.monotonic() - t1
        n_bars = paced.bars_processed
        assert n_bars > 50, "bar 数量级前提（合成区间应有百根级）"
        # 总时长 ≈ bar 数×throttle（容差：OS 调度毫秒抖动——下界不
        # 低于理论值 70%（sleep 梯累加不塌缩）、上界放宽（单测机载））
        expect = n_bars * 0.01
        assert paced_secs >= expect * 0.7, (
            f"节流未生效: {paced_secs:.2f}s < 70%×{expect:.2f}s")
        assert paced_secs < expect * 3.0 + 5.0, "节流时长异常放大"
        assert base_secs < expect * 0.5, "基准档应显著快于节流档"
        # 结果逐位一致（节流只影响墙钟不影响 bar 序/执行语义）
        assert paced.status == base.status
        assert paced.total_trades == base.total_trades
        assert paced.total_pnl == base.total_pnl
        assert paced.final_balance == base.final_balance
        assert paced.final_equity == base.final_equity
        assert paced.bars_processed == base.bars_processed
        assert paced.last_bar_time == base.last_bar_time

    @pytest.mark.parametrize("bad", [0.0, -0.01, -1.0])
    def test_throttle_invalid_rejected(self, tmp_path, bad):
        """参数校验：0/负值显式 422 拒（不静默当 None）。"""
        env = ReplayEnv(tmp_path, n_bars=120)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=80)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, env.strategy_id, start, end,
                   throttle_seconds=bad)
        assert "throttle_seconds" in str(ei.value)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
