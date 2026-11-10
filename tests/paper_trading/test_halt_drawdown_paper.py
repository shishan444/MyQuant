"""纸盘账户级硬熔断（sandbox-and-report-supplement A5——D3②）。

halt_drawdown_pct（PtTask 级覆写，NULL=不启用）：权益自峰值回撤达
阈值（≥含等号）拦截开新仓（持仓/既有平仓照常）；触发落运行态记录
（halted_at/halt_reason）可查询；未配置任务行为逐位不变。

确定性曲线（两段）：bar0-19=100（入场段——微震下行使 RSI<70 于
bar13 触发、bar14 开盘 long 满仓 100 股@100）→bar20-59=84（回撤
段：持仓权益 8400——自峰值 10000 回撤 16%＞15% 触发；SL 20% 线=80
未触发，持仓存活至触发点）。DNA=long＋entry RSI<70＋exit 恒假
（无信号出场）＋SL/TP=20%（值域上限——宽于回撤不抢先出场）。

拦截可观测面分两层：触发记录（用例 A——回撤中落 halted_at）与
拦截行为（用例 B——恢复态带 halted_at 的任务全部 open 被拦）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.unit]

from core.data.storage import save_parquet
from core.features.indicators import compute_all_indicators
from core.paper_trading.runtime.executor import PaperTaskExecutor
from core.paper_trading.runtime.feed import FeedRegistry
from core.persistence.db_ext import init_db_ext
from core.persistence.repository import paper
from tests.helpers.data_factory import make_dna

_N = 60
_REPLAY_ALL = "2000-01-01T00:00:00+00:00"


def _price_df() -> pd.DataFrame:
    """两段基准 100×10→125×50＋每 bar ±0.2% 正弦微震（恒定价格会使
    RSI 全 NaN，微震保证 RSI 有定义）。"""
    idx = pd.date_range("2024-01-01", periods=_N, freq="4h", tz="UTC",
                        name="timestamp")
    base = np.array([100.0] * 20 + [84.0] * 40)
    p = base * (1.0 + 0.0005 * np.sin(np.arange(_N) * 1.7))
    return pd.DataFrame({
        "open": p, "high": p * 1.001, "low": p * 0.999, "close": p,
        "volume": np.full(_N, 100.0)}, index=idx)


def _entry_dna():
    """short＋entry RSI>70（首段高位 bar1-8 信号）＋exit 恒假
    （RSI<0.5 无值满足——无信号出场，持仓跨全程）。"""
    return make_dna(entry_condition="lt", entry_value=70,
                    exit_condition="gt", exit_value=99.5,
                    direction="long", stop_loss=0.2, take_profit=0.2,
                    position_size=1.0)


class _HaltHarness:
    def __init__(self, tmp_path: Path, *, halt_drawdown_pct=None,
                 with_halted_state=False, dna=None):
        self.data_dir = tmp_path / "market"
        self.data_dir.mkdir()
        self.df = _price_df()
        save_parquet(self.df, self.data_dir / "BTCUSDT_4h.parquet")
        self.enhanced = compute_all_indicators(self.df)
        self.db = tmp_path / "paper.db"
        init_db_ext(self.db)
        self.task_id = paper.create_task(
            self.db, strategy_id="strat-halt",
            dna_snapshot_json=(dna if dna is not None
                               else _entry_dna()).to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10_000.0, leverage=1,
            window_bars=_N,
            halt_drawdown_pct=halt_drawdown_pct)
        if with_halted_state:
            # 恢复态注入：模拟「已触发任务重启」（halted_at 非空——
            # executor 从 task 行恢复单向置位态）
            from sqlalchemy import text
            from core.persistence.orm.engine import engine_for
            with engine_for(self.db).begin() as conn:
                conn.execute(text(
                    "UPDATE pt_task SET halted_at=:t, halt_reason=:r"
                    " WHERE task_id=:id"), {
                    "t": "2024-01-02T00:00:00+00:00",
                    "r": "equity_drawdown_from_peak_ge_halt_drawdown_pct",
                    "id": self.task_id})
        self.registry = FeedRegistry(
            self.data_dir,
            rest_fetcher=lambda *a, **k: pd.DataFrame(),
            funding_refresher=lambda **k: pd.DataFrame())
        from core.paper_trading.runtime.feed import FeedSubscription
        dummy = FeedSubscription(on_frames=lambda df, idx: None)
        self.registry.acquire("BTCUSDT", "4h", dummy).reconcile()

    def run_all(self) -> dict:
        row = paper.get_task(self.db, self.task_id)
        ex = PaperTaskExecutor(
            self.db, row, registry=self.registry, start_after=_REPLAY_ALL)
        assert ex.prepare() is True
        paper.update_task(self.db, self.task_id, status="running",
                          started_at="2026-11-09T00:00:00+00:00")
        ex.process_frames(self.enhanced, 0)
        return paper.get_task(self.db, self.task_id)


class TestHaltTriggeredPaper:
    def test_triggered_records_runtime_state(self, tmp_path):
        """用例 A（触发记录）：阈值 0.15、回撤 16%（long 满仓
        10000→8400）——halted_at/halt_reason 落 pt_task 运行态；
        触发前的首笔 open 照常成交（触发时点在持仓中）。"""
        h = _HaltHarness(tmp_path, halt_drawdown_pct=0.15)
        task = h.run_all()
        assert task["halted_at"] is not None, "触发落运行态记录"
        assert task["halt_reason"] == (
            "equity_drawdown_from_peak_ge_halt_drawdown_pct")
        fills = paper.list_fills(h.db, h.task_id, limit=1000)
        opens = [f for f in fills if f["kind"] == "open"]
        assert opens, "触发前的开仓照常成交（触发在持仓后）"
        assert all(f["bar_time"] <= task["halted_at"] for f in opens)

    def test_not_triggered_below_threshold(self, tmp_path):
        """阈值 0.3（回撤 16%＜30% 不触发）：halted_at 恒 NULL。"""
        h = _HaltHarness(tmp_path, halt_drawdown_pct=0.3)
        task = h.run_all()
        assert task["halted_at"] is None
        assert task["halt_reason"] is None

    def test_halted_state_blocks_all_open_intents(self, tmp_path):
        """用例 B（拦截行为）：恢复态 halted 任务（halted_at 已置位）
        的全部 open 意图被拦——零 open 成交＋每意图落 rejected 信号行
        （reject_reason=halted_drawdown，kind 恒 open——出场族不经
        拦截分支）。"""
        h = _HaltHarness(tmp_path, halt_drawdown_pct=0.15,
                         with_halted_state=True)
        task = h.run_all()
        fills = paper.list_fills(h.db, h.task_id, limit=1000)
        assert not [f for f in fills if f["kind"] == "open"], \
            "halted 置位后零开仓成交"
        signals = paper.list_signals(h.db, h.task_id, limit=2000)
        rejects = [s for s in signals
                   if s.get("reject_reason") == "halted_drawdown"]
        assert rejects, "拦截落 rejected 信号行（显式拒因）"
        assert all(s["signal_kind"] == "open" for s in rejects)
        # 运行态不回落（单向置位——重启续态）
        assert task["halted_at"] == "2024-01-02T00:00:00+00:00"

    def test_exit_fills_after_runtime_halt(self, tmp_path):
        """用例 C（平仓照常正向锚——spec scenario「后续开仓请求被
        拦截（平仓照常）」的平仓侧行为证明；用例 B 恢复态只证拦截面，
        本用例补运行中触发后的平仓面）：SL 10% 线在 bar20 价格跳 84
        时触发平仓（exit_sl 照常成交），同 bar 熔断判定触发（回撤
        16%≥5%）——平仓成交时点 ≥ halted_at；触发后的开仓意图全部
        落 rejected（halted_drawdown）而 exit 族零拒绝。"""
        dna = make_dna(entry_condition="lt", entry_value=70,
                       exit_condition="gt", exit_value=99.5,
                       direction="long", stop_loss=0.1, take_profit=0.2,
                       position_size=1.0)
        h = _HaltHarness(tmp_path, halt_drawdown_pct=0.05, dna=dna)
        task = h.run_all()
        assert task["halted_at"] is not None, "回撤 16%≥5% 触发熔断"
        fills = paper.list_fills(h.db, h.task_id, limit=1000)
        exits = [f for f in fills if f["kind"].startswith("exit")]
        assert exits, "触发后 SL 平仓照常成交（exit_sl）"
        assert all(f["bar_time"] >= task["halted_at"] for f in exits), \
            "平仓发生在熔断触发时点或之后（非触发前存量成交）"
        signals = paper.list_signals(h.db, h.task_id, limit=2000)
        halted_rejects = [s for s in signals
                          if s.get("reject_reason") == "halted_drawdown"]
        assert halted_rejects, "触发后开仓意图被拦（运行中拦截面）"
        assert all(s["signal_kind"] == "open" for s in halted_rejects), \
            "熔断拦截只落 open 意图（exit 族零拒绝）"


class TestUnconfiguredUnchanged:
    def test_unset_task_null_columns_and_no_interception(self, tmp_path):
        """未配置（NULL）：halted_at/halt_reason 恒 NULL、无拦截拒绝行
        ——存量行为逐位不变锚（判定短路不触达；纸盘域既有 791 项测试
        全绿为主证据——它们全部跑在含本列的库上）。"""
        h = _HaltHarness(tmp_path)
        task = h.run_all()
        assert task["halt_drawdown_pct"] is None
        assert task["halted_at"] is None
        assert task["halt_reason"] is None
        signals = paper.list_signals(h.db, h.task_id, limit=2000)
        assert not [s for s in signals
                    if s.get("reject_reason") == "halted_drawdown"]
        fills = paper.list_fills(h.db, h.task_id, limit=1000)
        assert any(f["kind"] == "open" for f in fills), "开仓照常"


class TestApiSurface:
    """API 面：创建带参＋查询三键（halted 状态可查询）。"""

    def test_create_with_halt_and_query_fields(self, tmp_path):
        from fastapi.testclient import TestClient

        from MyQuant.api.app import create_app
        from tests.helpers.data_factory import make_dna as _mk

        db = tmp_path / "api.db"
        init_db_ext(db)
        from core.persistence.repository.strategy import save_strategy
        save_strategy(db, strategy_id="st_halt_api",
                      dna_json=_mk().to_json(), symbol="BTCUSDT",
                      timeframe="4h", name="halt-api")
        with TestClient(create_app(db_path=db,
                                   data_dir=tmp_path / "d")) as c:
            resp = c.post("/api/paper-trading/tasks", json={
                "strategy_id": "st_halt_api", "halt_drawdown_pct": 0.15})
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["halt_drawdown_pct"] == 0.15
            assert body["halted_at"] is None, "未触发恒 None 可查询"
            got = c.get(f"/api/paper-trading/tasks/{body['task_id']}").json()
            assert got["halt_drawdown_pct"] == 0.15
            assert "halt_reason" in got

    def test_create_rejects_out_of_range(self, tmp_path):
        from fastapi.testclient import TestClient

        from MyQuant.api.app import create_app
        from tests.helpers.data_factory import make_dna as _mk

        db = tmp_path / "api2.db"
        init_db_ext(db)
        from core.persistence.repository.strategy import save_strategy
        save_strategy(db, strategy_id="st_halt_api2",
                      dna_json=_mk().to_json(), symbol="BTCUSDT",
                      timeframe="4h", name="halt-api2")
        with TestClient(create_app(db_path=db,
                                   data_dir=tmp_path / "d2")) as c:
            for bad in (0, 1, 1.5):
                resp = c.post("/api/paper-trading/tasks", json={
                    "strategy_id": "st_halt_api2",
                    "halt_drawdown_pct": bad})
                assert resp.status_code == 422, f"{bad} 应拒绝"
