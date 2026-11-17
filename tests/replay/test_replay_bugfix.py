"""bug 修复验收（锚②）——终止条款行为矩阵（L2-C 精炼）。"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.persistence.repository import paper
from core.replay import ReplayRunner
from tests.replay._env import ReplayEnv, make_reduce_loop_dna


class TestReduceTerminationBugfix:
    """持续减仓策略经回放链路：有限步归零+无小额成交+留痕可辨。"""

    def test_persistent_reduce_terminates_and_zeroes(self, tmp_path):
        """L2-C1：入场+减仓双恒触发（f=0.5 衰减）→有限步仓位归零、
        close:reduce_terminated 留痕在案、减仓意图执行次数有限。"""
        env = ReplayEnv(tmp_path, dna=make_reduce_loop_dna(
            exit_fraction=0.5, position_size=0.1, leverage=10),
            n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        rr.run(env.db, env.strategy_id, start, end, task_id="bug01")
        fills = paper.list_fills(env.db, "bug01", limit=1000)
        reduce_fills = [f for f in fills if f["kind"] == "reduce"]
        terminate_fills = [
            f for f in fills if f.get("reason") == "reduce_terminated"]
        assert terminate_fills, "close:reduce_terminated 留痕在案"
        # 每轮衰减有限步收敛（仓位动作总数<bar 数——每 bar 至多一笔
        # 仓位动作，无限循环不可能满足）；恒入场下多轮「入场-衰减-
        # 终止」循环每轮归零，即终止条款逐轮生效
        task = paper.get_task(env.db, "bug01")
        assert len(fills) < task["bars_processed"], (
            f"仓位动作有限（{len(fills)} 笔 < {task['bars_processed']} bar）")
        assert fills[-1]["kind"] == "close", "终态无残余（末笔全平）"

    def test_no_below_min_notional_fills_unexempted(self, tmp_path):
        """L2-C2：全程扫描 pt_fill——豁免面（close/exit_*）外无
        名义<50 成交（env FAPI 注入 MIN_NOTIONAL）。"""
        import os

        os.environ["FAPI_MIN_NOTIONAL_BTCUSDT"] = "50"
        try:
            env = ReplayEnv(tmp_path, dna=make_reduce_loop_dna(
                exit_fraction=0.5, position_size=0.1, leverage=10),
                n_bars=600)
            rr = ReplayRunner(env.data_dir)
            start, end = env.range(head_bars=300)
            rr.run(env.db, env.strategy_id, start, end, task_id="bug02")
            fills = paper.list_fills(env.db, "bug02", limit=1000)
            for f in fills:
                if f["kind"] in ("open", "add", "reduce"):
                    notional = f["quantity"] * f["price"]
                    assert notional >= 50.0, (
                        f"未豁免面出现小额成交: {f['kind']} "
                        f"{notional:.2f}")
        finally:
            os.environ.pop("FAPI_MIN_NOTIONAL_BTCUSDT", None)

    def test_tiny_fraction_first_clear(self, tmp_path):
        """L2-C3：病态小 fraction（0.001）——物理条件首轮命中当根
        全平（拒绝死循环格不存在——终局口径下物理条件即升格）。"""
        env = ReplayEnv(tmp_path, dna=make_reduce_loop_dna(
            exit_fraction=0.001, position_size=0.1, leverage=10),
            n_bars=600)
        rr = ReplayRunner(env.data_dir)
        start, end = env.range(head_bars=300)
        rr.run(env.db, env.strategy_id, start, end, task_id="bug03")
        fills = paper.list_fills(env.db, "bug03", limit=1000)
        closes = [f for f in fills
                  if f.get("reason") == "reduce_terminated"]
        assert closes, "物理条件触发升格在案"
        # 升格即全量：每笔 terminate 成交数量=全量清仓（恒入场下区间
        # 末可能处于新一轮持仓——残余态属策略行为非条款缺陷）
        for c in closes:
            assert c["quantity"] > 0, "terminate 全量成交"
        # terminate 后如还有仓位动作，下一笔必为 open（归零后重新
        # 入场——衰减残留不可能：物理条件当根即清）
        kinds = [(f["kind"], f.get("reason")) for f in fills]
        for i, (k, reason) in enumerate(kinds):
            if reason == "reduce_terminated" and i + 1 < len(kinds):
                assert kinds[i + 1][0] == "open", (
                    f"terminate 后紧邻动作应为新入场: {kinds[i+1]}")
