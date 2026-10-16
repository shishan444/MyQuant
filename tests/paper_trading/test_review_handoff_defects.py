"""reviewer 转交缺陷特征用例（A1/A2，2026-10-16 实施验证期）。

来源：quality-reviewer 正确性兜底两项转交（附复现思路与置信度），
backend-tester 按契约推导期望值建特征用例——当前预期红（缺陷确证），
修复后转绿即回归锚；期望值不从实现反推。

A1 position_bars 混域（置信度 90）：
    reporting.py position_bars = bars_processed − position_entry_bar_seq
    混了两个计数域——bars_processed 是任务感知计数（executor 对
    start_after 之前的历史 bar 早退不加），entry_bar_seq 是 feed 全量
    DataFrame 行号。生产形态（任务创建晚于大量历史 bar）恒得负值。
    契约依据：design §6.1 evidence.position_bars=持仓时长披露（根数，
    非负）；正确语义参照 kernel bars_held（同 df 行号域相减+1，
    account.py:52）。

A2 atr 模式 TP 披露值公式域错（置信度 85）：
    ledger._initial_stop 的分派 `is_tp or sl_mode != "atr"` 使 atr 模式
    TP 披露值走 pct 域（entry×(1+tp)）；判定权威 atr 模式 TP=
    entry+tp×ATR（intents.py:144、engine.py 同形制）。纯披露面
    （pt_task.position_tp_price 与 REST/MCP 透出），判定行为不受影响。
    契约依据：design §3.4 SL/TP 三模式触发价 atr=entry±倍数×ATR——
    披露值即开仓时点判定线，两域必须一致。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]

from core.paper_trading.kernel.fills import MarketFill
from core.paper_trading.kernel.ledger import Ledger
from core.paper_trading.kernel.types import Intent, RiskParams
from core.paper_trading.reporting import build_report
from core.persistence.repository import paper
from tests.paper_trading.test_executor import _Harness

_ZERO_COST = MarketFill(fee_rate=0.0, slippage_rate=0.0)


class TestAtrTpDisclosureDomain:
    """A2：atr 模式 TP 披露值须与判定线同域（entry+tp×ATR）。"""

    def test_atr_tp_disclosure_uses_atr_domain(self):
        """atr 模式开仓：TP 披露=entry+tp×ATR=115（实际 pct 域 400——红）。"""
        params = RiskParams(
            stop_loss=2.0, take_profit=3.0, position_size=1.0,
            leverage=1, direction="long", sl_mode="atr", atr_period=14)
        ledger = Ledger(10_000.0, params)
        from core.paper_trading.kernel.types import ClosedBar
        bar = ClosedBar(
            seq=0, open=100.0, high=100.0, low=100.0, close=100.0, atr=5.0)
        fill = ledger.execute(
            Intent(kind="open", direction="long"), bar, _ZERO_COST,
            entry_atr=5.0)
        assert fill is not None  # 触达探针：开仓成交发生

        pos = ledger.position
        # 触达探针：SL 侧走 atr 域（entry−sl×ATR=90）——证明 atr 分支
        # 被触达且构造有效（若 SL 也是 pct 域则本用例构造错误）
        assert pos.sl_price == pytest.approx(100.0 - 2.0 * 5.0)
        # 主断言（契约推导）：TP 判定线=entry+tp×ATR=115，披露值须一致
        assert pos.tp_price == pytest.approx(100.0 + 3.0 * 5.0)


class TestPositionBarsSameDomain:
    """A1：evidence.position_bars 须为同域持仓根数（非负）。"""

    def test_position_bars_positive_with_history_prefix(self, tmp_path):
        """start_after 晚于 120 根历史：持仓根数=15（缺陷值 −105）。"""
        h = _Harness(tmp_path, window_bars=20)
        start_after = h.df.index[120].isoformat()
        ex = h.make_executor(start_after=start_after)
        assert ex.prepare() is True
        # 通道适配（tester 2026-10-16）：直驱 process_frames 须先经
        # 合法迁移置 running——缺陷⑤守门后 update_task 拒 pending→
        # completed 直迁（生产链路 run() 本就先置 running，不受影响；
        # 与 test_reporting._seed_task 迁移链适配同构）
        paper.update_task(
            h.db, h.task_id, status="running",
            started_at="2026-10-16T00:00:00+00:00")
        ex.process_frames(h.enhanced, 0)

        task = h.task()
        # 触达探针（三值并排固定域差异事实）：
        # 任务段=行 120..139（window=20）→ bars_processed=20（任务感知域）；
        # 任务段内首个 entry 信号在行 124（seed=7 信号分布实测），
        # 次根行 125 成交 → entry 行号 125（feed 全量行号域）
        assert task["status"] == "completed"
        assert task["bars_processed"] == 20
        fills = paper.list_fills(h.db, h.task_id, limit=1000)
        assert fills, "任务段内须有成交（行 124 有 entry 信号）"
        assert task["position_entry_bar_seq"] == 125, (
            f"entry_bar_seq={task['position_entry_bar_seq']}")

        report = build_report(h.db, h.data_dir, h.task_id)
        # 主断言（契约推导，bars_held 同形制）：入场成交 bar 计第 1 根，
        # 行 125..139 → 持仓 15 根（缺陷值 = 20−125 = −105）
        assert report["evidence"]["position_bars"] == 15
