"""对照重放器集成四锚（paper-trading-revamp 组 C4）+ executor 挂点接线（组 B）。

时间锚防回退/signal_counts 对拍/豁免记账（REQ-C-04）/终判解耦——
沿 test_paper_executor._Harness 全回放驱动形制；组 B 挂点=两维真跑
executor 后 first_met_at 首次满足落库+幂等（spec Scenario「首次满足
记录」）。
"""
from __future__ import annotations

import bisect
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.backtest_bridge import run_backtest_on_range
from core.paper_trading.qualification import (
    paper_trigger_set,
    replay_trigger_set,
    run_trigger_consistency,
    build_qualification_section,
)
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna
from tests.paper_trading.test_paper_executor import _Harness


class TestTimeAnchorNoRegress:
    """时间锚同锚防回退：对照在 pt_signal.bar_time 命中、pt_fill.bar_time 不命中。"""

    def test_signal_anchor_vs_fill_anchor_one_bar_gap(self, tmp_path: Path):
        h = _Harness(tmp_path)
        ex = h.make_executor()
        ex.prepare()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-11-01T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        task = paper.get_task(h.db, h.task_id)

        signals = paper.list_signals(h.db, h.task_id, limit=10**6)
        fills = paper.list_fills(h.db, h.task_id, limit=10**6)
        open_fills = [f for f in fills if f["kind"] == "open" and f.get("side")]
        if not (signals and open_fills):
            pytest.skip("构造 DNA 未产信号对——锚需 filled 信号")

        # 断言①：执行锚与信号锚恒差一根（次根执行——结构性事实）
        sig_anchors = {s["bar_time"] for s in signals
                       if s["signal_kind"] == "open"}
        fill_anchors = {f["bar_time"] for f in open_fills}
        assert fill_anchors
        assert not (fill_anchors <= sig_anchors), \
            "pt_fill 执行锚必须存在不属于信号锚的 bar（次根执行差）——若全重合则时间锚防回退断言失义"

        # 断言②：重放对照集=信号锚口径（paper_trigger_set 取 pt_signal.bar_time）
        paper_set = {t for t, _ in paper_trigger_set(signals)}
        assert paper_set <= sig_anchors
        # 断言③（防回退）：若误用 pt_fill 执行锚，集合系统性错位
        fill_set = {f["bar_time"] for f in open_fills}
        assert fill_set != paper_set, "执行锚对照与信号锚对照必须可区分（防实施回退到执行锚）"


class TestSignalCountsParity:
    """同策略同数据：重放集四类计数与回测引擎 signal_counts 逐键一致
    （CARD-04 加强版：长数据晚启动窗——重放窗前余量足 warmup 200；
    bridge 对拍镜像生产 deviation 层形制；删除 skip 容错——装载失败
    应红不应吞）。"""

    def test_replay_counts_match_engine(self, tmp_path: Path, monkeypatch):
        import tests.paper_trading.test_paper_executor as tpe
        monkeypatch.setattr(tpe, "_N_BARS", 500)
        # 晚启动：数据第 205 根起（2024-01-01 起合成 500 根——窗前余量
        # 204 根 > DEVIATION_WARMUP_BARS 200，重放器 warmup 装载可达）
        h = _Harness(tmp_path, dna=make_dna(**_SHORT_DNA_KW), window_bars=200)
        ex = h.make_executor(start_after="2024-02-04T00:00:00+00:00")
        ex.prepare()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-11-01T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        task = paper.get_task(h.db, h.task_id)
        eqs = paper.list_equity_snapshots(h.db, h.task_id, limit=10**6)

        out = run_trigger_consistency(
            h.db, h.registry.data_dir, task,
            paper.list_signals(h.db, h.task_id, limit=10**6))
        # 主锚（加强）：重放器真执行——denominator>0 封死「import 断裂/
        # 装配失败退化 NULL 态被弱断言（>=0）吞掉」的假绿面（CARD-01
        # 修复前本用例曾对此假绿）
        assert out["denominator"] > 0
        assert out["passed"] in (True, False)

        # 对拍面：bridge 侧镜像生产 deviation 层形制（warmup=
        # DEVIATION_WARMUP_BARS 同值；旧用例 warmup=10 偏离生产形制时
        # 实测差 6=RSI 递推收敛差，非实现偏差）
        from core.analysis.backtest_bridge import prepare_replay_inputs
        from core.paper_trading.reporting import DEVIATION_WARMUP_BARS
        from core.strategy.executor import dna_to_signal_set

        metrics, _, _, _ = run_backtest_on_range(
            h.db, h.registry.data_dir, ex.dna, task["symbol"], task["timeframe"],
            eqs[0]["bar_time"], task["last_bar_time"], DEVIATION_WARMUP_BARS)
        bt_df, dfs_tf, _, _ = prepare_replay_inputs(
            h.db, h.registry.data_dir, ex.dna, task["symbol"], task["timeframe"],
            eqs[0]["bar_time"], task["last_bar_time"], DEVIATION_WARMUP_BARS)
        sig_set = dna_to_signal_set(ex.dna, bt_df, dfs_by_timeframe=dfs_tf)
        for key in ("entries", "exits", "adds", "reduces"):
            series = getattr(sig_set, key)
            n = int(series.astype(bool).sum()) if series is not None else 0
            assert n == metrics["signal_counts"][key], (
                f"{key}: replay={n} engine={metrics['signal_counts'][key]}")


class TestQualifyNoMigration:
    """多任务达标不迁移（retire-legacy Q2=A——晋级边退役）：同策略两
    任务先后到达终态且三维判达标，策略状态恒 paper；qualify 边已从
    状态机删除（transit 409 负向锚）。"""

    def test_two_qualified_tasks_strategy_stays_paper(self, tmp_path: Path):
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository import paper
        from core.persistence.repository.strategy import (
            get_strategy, save_strategy)
        from core.strategy.lifecycle.machine import LifecycleError
        from core.strategy.lifecycle.service import transit

        db = tmp_path / "qualify_no_mig.db"
        init_db_ext(db)
        save_strategy(db, strategy_id="st-nm", dna_json=make_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h", status="active",
                      gene_signature="sig_nm")
        assert transit(db, "st-nm", target="paper", trigger="mount",
                       evidence={"note": "fixture"})["migrated"]

        for i in (1, 2):
            tid = f"pt_nm{i}"
            paper.create_task(
                db, task_id=tid, strategy_id="st-nm",
                dna_snapshot_json=make_dna().to_json(),
                symbol="BTCUSDT", timeframe="4h",
                research_status_snapshot="unverified", initial_cash=10_000.0)
            paper.update_task(db, tid, status="running",
                              started_at="2026-11-01T00:00:00+00:00")
            paper.update_task(db, tid, status="completed",
                              stopped_at="2026-11-01T01:00:00+00:00")

        # 两任务终态达标（qualified 语义成立）后策略状态恒 paper
        assert get_strategy(db, "st-nm")["status"] == "paper"

        # qualify 边已删：直调迁移通道 409（防迁移通道静默复活）
        with pytest.raises(LifecycleError) as ei:
            transit(db, "st-nm", target="candidate", trigger="qualify",
                    evidence={"task_id": "pt_nm1"})
        assert ei.value.status == 409
        assert ei.value.code == "STRATEGY_ILLEGAL_TRANSITION"
        assert get_strategy(db, "st-nm")["status"] == "paper"


class TestExemptionAccounting:
    """豁免记账（REQ-C-04）：终判前后 union-balance 逐字节不变+零新 validation_records。"""

    def test_final_judgment_no_research_side_effect(self, tmp_path: Path):
        h = _Harness(tmp_path)
        ex = h.make_executor()
        ex.prepare()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-11-01T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        task = paper.get_task(h.db, h.task_id)

        # 纸盘任务与假设解耦（不挂 hypothesis）——union-balance 语义面
        # 不涉该任务；豁免记账核心锚=研究域零写入：终判前后
        # ValidationRecord 行数不变（零新 validation_records）
        from core.persistence.orm.models import ValidationRecord
        from sqlalchemy import create_engine, select, func
        eng = create_engine(f"sqlite:///{h.db}")

        def _vcount() -> int:
            with eng.connect() as conn:
                return conn.execute(select(func.count()).select_from(ValidationRecord)).scalar()

        before = _vcount()
        section = build_qualification_section(h.db, h.registry.data_dir, task)
        after = _vcount()
        assert before == after, "终判不写研究域（零新 validation_records）"
        assert section["judged_at"]
        from core.persistence.orm.models import ValidationRecord
        from sqlalchemy import create_engine, select, func
        eng = create_engine(f"sqlite:///{h.db}")
        with eng.connect() as conn:
            n = conn.execute(select(func.count()).select_from(ValidationRecord)).scalar()
        assert n == 0


class TestDecoupling:
    """终判解耦：重放数据不足→passed=NULL+qualified=false+任务照常终态。"""

    def test_insufficient_data_null_consistency(self, tmp_path: Path):
        h = _Harness(tmp_path)
        ex = h.make_executor()
        ex.prepare()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-11-01T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)
        task = paper.get_task(h.db, h.task_id)
        assert task["status"] in {"completed", "stopped"}

        # 破坏重放条件：last_bar_time 置空 → run_trigger_consistency 返回 NULL 态
        task_broken = dict(task, last_bar_time=None)
        out = run_trigger_consistency(h.db, h.registry.data_dir, task_broken, [])
        assert out["passed"] is None  # NULL≠false≠true 三态
        # 任务终态不受影响（解耦——终判是观察者）
        assert paper.get_task(h.db, h.task_id)["status"] == task["status"]


# 全平事件 kind 集（executor 计数口径：not in ("open","add","reduce")
# 且 realized_pnl 非 None——win+loss=total 自洽；与最终 task.total_trades
# 逐数互证：close fill 计数==total_trades）
_CLOSE_KINDS = frozenset({
    "close", "exit_sl", "exit_tp", "exit_max_loss",
    "exit_max_hold", "exit_time", "exit_liquidation",
})

# 做空高频组合（可复现）：合成数据（seed=7）前段下行——做空持续浮盈
# （equity>initial_cash 早早恒真）+RSI 50/50 往返全平（120 根 7 次）。
# 对照：默认做多 DNA 全程浮亏+50 根仅 1 次全平——两维不可同时满足
_SHORT_DNA_KW = dict(direction="short", entry_condition="lt",
                    entry_value=50, exit_condition="gt", exit_value=50)


class TestFirstMetWiring:
    """executor 挂点接线（组 B）：两维真跑 executor 后 first_met_at
    首次满足写入该 bar_time（幂等单次）——spec Scenario「首次满足记录」。"""

    @staticmethod
    def _expected_first_met(h, initial_cash: float):
        """独立重演期望值：pt_equity 逐 bar × 全平 fill 累计数。

        全平 fill 以 bar_time<=当 bar 计数（八步序出场先于快照/挂点
        判定，同 bar 结算计入当根）；首个 equity>initial_cash（严格）
        AND 累计>=5（含等）的 bar_time，无则 None。
        """
        eqs = paper.list_equity_snapshots(h.db, h.task_id, limit=10**6)
        fills = paper.list_fills(h.db, h.task_id, limit=10**6)
        closes = sorted(f["bar_time"] for f in fills
                        if f["kind"] in _CLOSE_KINDS)
        for row in eqs:
            n_closed = bisect.bisect_right(closes, row["bar_time"])
            if row["equity"] > initial_cash and n_closed >= 5:
                return row["bar_time"]
        return None

    def _run_harness(self, tmp_path: Path, *, dna=None, window_bars=120):
        h = _Harness(tmp_path, dna=dna, window_bars=window_bars)
        ex = h.make_executor()
        ex.prepare()
        paper.update_task(h.db, h.task_id, status="running",
                          started_at="2026-11-01T00:00:00+00:00")
        return h, ex

    def test_first_met_written_on_first_qualifying_bar(self, tmp_path: Path):
        """首次两维同时满足 → first_met_at=该 bar_time（红锚：当前
        白名单丢弃 task_state.first_met_at，实测恒 None）。"""
        h, ex = self._run_harness(tmp_path, dna=make_dna(**_SHORT_DNA_KW))
        ex.process_frames(h.enhanced, 0)
        task = h.task()
        expected = self._expected_first_met(h, float(task["initial_cash"]))
        # 有效性前置：本组合确须进入两维同时满足区（否则断言失义）
        assert expected is not None
        assert task["first_met_at"] == expected

    def test_first_met_null_when_never_qualified(self, tmp_path: Path):
        """从未同时满足 → first_met_at 保持 NULL（默认做多 DNA 组合）。"""
        h, ex = self._run_harness(tmp_path, window_bars=50)
        ex.process_frames(h.enhanced, 0)
        task = h.task()
        expected = self._expected_first_met(h, float(task["initial_cash"]))
        assert expected is None  # 有效性前置：两维确实从未同时满足
        assert task["first_met_at"] is None

    def test_first_met_on_final_bar_survives_terminal(self, tmp_path: Path):
        """首满足恰为窗口末根（终局 bar）→ 值仍须落库（红锚·第二层：
        挂点在本根 _persist_bar 之后置内存值，终局后无下一根 bar 承载
        写库——白名单修复后该场景仍丢失；变异态实证 wb=85 对照组正常）。
        window_bars=84=第 5 次全平 bar（01-14T20，seed=7 合成数据第 84
        根）——首满足与窗口到点同根。"""
        h, ex = self._run_harness(tmp_path, dna=make_dna(**_SHORT_DNA_KW),
                                  window_bars=84)
        ex.process_frames(h.enhanced, 0)
        task = h.task()
        eqs = paper.list_equity_snapshots(h.db, h.task_id, limit=10**6)
        expected = self._expected_first_met(h, float(task["initial_cash"]))
        # 有效性前置：首个满足 bar 恰为末根（否则场景失义）
        assert expected == eqs[-1]["bar_time"]
        assert task["first_met_at"] == expected

    def test_first_met_immutable_across_restart(self, tmp_path: Path):
        """已写不改写（幂等读路径）：first_met_at 已在库 → 恢复续跑
        不覆写（恢复沿 task 行续判；写入路径由首用例承证）。"""
        from sqlalchemy import update

        from core.persistence.orm.engine import engine_for
        from core.persistence.orm.models import PtTask

        h, ex = self._run_harness(tmp_path, dna=make_dna(**_SHORT_DNA_KW))
        # 前 80 根（切片帧独立成跑：信号组装按传入帧全帧形制）
        ex.process_frames(h.enhanced.iloc[:80], 0)
        preset = "2026-01-01T00:00:00+00:00"
        with engine_for(h.db).begin() as conn:
            conn.execute(update(PtTask).where(
                PtTask.task_id == h.task_id).values(first_met_at=preset))
        # 恢复：新 executor 从 task 行读 first_met_at 续判（81-120 根
        # 两维仍满足——不改写已写值）
        ex2 = h.make_executor()
        ex2.prepare()
        ex2.process_frames(h.enhanced, 80)
        assert h.task()["first_met_at"] == preset
