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
        # summary 组全覆盖：ReplayResult 执行语义字段（status/
        # stop_reason/win_count/loss_count/total_trades/total_pnl/
        # final_balance/final_equity/bars_processed/last_bar_time/
        # halted_at/halt_reason/first_met_at——身份组 task_id/db_path
        # 两档天然不同不比；start/end 同输入构造性相同）
        assert paced.status == base.status
        assert paced.stop_reason == base.stop_reason
        assert paced.win_count == base.win_count
        assert paced.loss_count == base.loss_count
        assert paced.total_trades == base.total_trades
        assert paced.total_pnl == base.total_pnl
        assert paced.final_balance == base.final_balance
        assert paced.final_equity == base.final_equity
        assert paced.bars_processed == base.bars_processed
        assert paced.last_bar_time == base.last_bar_time
        assert paced.halted_at == base.halted_at
        assert paced.halt_reason == base.halt_reason
        assert paced.first_met_at == base.first_met_at
        # 明细组逐位（沿 ReplayResult 取数面四通道之三——fills/
        # equity/signals 全行投影比对；严格 == 非 approx：同输入同
        # 代码路径确定性计算应 bit 相等；id/task_id/created_at 为
        # 环境差键（自增/两档任务名/墙钟时间戳）排除）
        from core.persistence.repository import paper
        drop = {"id", "task_id"}
        for lister, name in (
            (paper.list_fills, "fills"),
            (paper.list_equity_snapshots, "equity"),
        ):
            a = [{k: v for k, v in r.items() if k not in drop}
                 for r in lister(env.db, "pace_base", limit=5000)]
            b = [{k: v for k, v in r.items() if k not in drop}
                 for r in lister(env.db, "pace_th", limit=5000)]
            assert a == b, f"{name} 明细逐位一致（{name} 档间漂移）"
        sig_drop = {"id", "task_id", "created_at"}
        sig_a = [{k: v for k, v in r.items() if k not in sig_drop}
                 for r in paper.list_signals(env.db, "pace_base", limit=5000)]
        sig_b = [{k: v for k, v in r.items() if k not in sig_drop}
                 for r in paper.list_signals(env.db, "pace_th", limit=5000)]
        assert sig_a == sig_b, "signals 明细逐位一致（signals 档间漂移）"

    @pytest.mark.parametrize("bad", [
        0.0, -0.01, -1.0,
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="inf"),
        pytest.param(float("-inf"), id="-inf"),
        pytest.param(60.01, id="over-upper-60.01"),
    ])
    def test_throttle_invalid_rejected(self, tmp_path, bad):
        """参数校验：(0,60] 值域外显式拒（0/负/nan/inf/越上界——
        不静默当 None；nan/inf 此前穿透：nan 进 sleep 中程炸裸
        ValueError、inf 永久阻塞，CARD-06/F1 收口）。"""
        env = ReplayEnv(tmp_path, n_bars=120)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=80)
        with pytest.raises(ReplayError) as ei:
            rr.run(env.db, env.strategy_id, start, end,
                   task_id="pace_invalid",
                   throttle_seconds=bad)
        assert "throttle_seconds" in str(ei.value)
        assert ei.value.code == "RESEARCH_PARAM_INVALID"
        # 载入即拒纪律：pt_task 行不建（CARD-06 验收面）
        from core.persistence.repository import paper
        assert paper.get_task(env.db, "pace_invalid") is None
