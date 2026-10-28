"""series 档能力矩阵拒绝+exit_sweep 形制（dynamic-stop-loss-series
4.3——tester 锚 C4-D1-D3+C4-A5 分态）。

矩阵：pct 三链放行/atr+trail 回测纸盘放行 live 拒/series 仅回测
放行、纸盘+live 双拒（「后续专项补齐」）；纸盘双层插桩（受理+装载）
沿 live 双插桩镜像；回测放行对照；exit_sweep series entry 空值形制。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import StrategyDNA
from core.strategy.validator import (
    validate_dna, validate_trading_compatibility)
from tests.helpers.data_factory import make_dna

_BB_SRC = {"indicator": "BB", "params": {"period": 20, "std": 2},
           "field": "lower"}


def _series_dna(**kw):
    dna = make_dna(**kw)
    dna.risk_genes.sl_mode = "series"
    dna.risk_genes.sl_series_source = dict(_BB_SRC)
    return dna


class TestMatrix:
    def test_series_paper_rejected(self):
        """D1：纸盘挂载拒绝——reasons 含档位名+后续专项补齐。"""
        reasons = validate_trading_compatibility(_series_dna(), "paper")
        assert reasons, "series 纸盘拒"
        assert any("series" in r and "后续专项补齐" in r
                   for r in reasons), reasons

    def test_series_live_rejected(self):
        """D2：live 链拒绝（同形制）。"""
        reasons = validate_trading_compatibility(_series_dna(), "live")
        assert any("series" in r and "后续专项补齐" in r for r in reasons)

    def test_backtest_no_gate(self):
        """D3：回测放行对照（回测=能力全集基准——validate_dna 不误伤
        +无链路闸门）。"""
        vr = validate_dna(_series_dna())
        assert vr.is_valid, vr.errors  # 全局校验不因链路支持报错

    def test_existing_modes_unchanged(self):
        """E2 跨档回归：pct live/paper 放行；atr/trail live 拒（维持）
        paper 放行（矩阵新行=既有行为定形）。"""
        for mode in ("pct", "atr", "trail"):
            dna = make_dna()
            dna.risk_genes.sl_mode = mode
            assert validate_trading_compatibility(dna, "paper") == [], \
                f"{mode} 纸盘放行（矩阵定形）"
        for mode in ("atr", "trail"):
            dna = make_dna()
            dna.risk_genes.sl_mode = mode
            reasons = validate_trading_compatibility(dna, "live")
            assert reasons, f"{mode} live 拒（维持）"
        assert validate_trading_compatibility(make_dna(), "live") == []

    def test_illegal_link_value_error(self):
        """link 扩枚举后非法 link 值仍 ValueError（编程错误防线）。"""
        with pytest.raises(ValueError):
            validate_trading_compatibility(make_dna(), "bogus")


class TestPaperDoubleInstrumentation:
    def _paper_mount_body(self, strategy_id):
        from core.persistence.repository.strategy import save_strategy
        return strategy_id

    def test_accept_layer_rejects(self, tmp_path, api_client):
        """D1-① 受理层：POST 挂载 series 档策略→create_task 结构化拒
        绝（422 reasons 含档位名+后续专项补齐）。"""
        from core.persistence.db_ext import init_db_ext
        from core.persistence.repository.strategy import save_strategy
        db = tmp_path / "q.db"
        init_db_ext(db)
        api_client.app.state.db_path = db
        save_strategy(db, strategy_id="st_series_paper",
                      dna_json=_series_dna().to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="s",
                      status="active")
        r = api_client.post("/api/paper-trading/tasks", json={
            "strategy_id": "st_series_paper"})
        assert r.status_code == 422, r.text
        blob = json.dumps(r.json(), ensure_ascii=False)
        assert "DNA_TRADING_INCOMPATIBLE" in blob
        assert "series" in blob and "后续专项补齐" in blob

    def test_load_layer_rejects_bypassed_dna(self, tmp_path):
        """D1-② 装载层：受理层被绕过（直改库内 dna_json 为 series 后
        构造任务）→ executor prepare 拒绝——受理/装载双层镜像 live 链。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.persistence.repository import paper
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        task_id = paper.create_task(
            db, strategy_id="st_dirty", dna_snapshot_json=_series_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10000.0, leverage=1)
        row = paper.get_task(db, task_id)
        from core.paper_trading.runtime.feed import FeedRegistry
        registry = FeedRegistry(tmp_path, fetcher=lambda **k: pd.DataFrame())
        registry_dummy = registry  # 拒绝发生在 feed 使用前
        ex = PaperTaskExecutor(
            db, row, registry=registry, push=lambda *a: None,
            on_concluded=lambda *a: None)
        ok = ex.prepare()
        assert ok is False, "装载层须拒 series 档（纵深）"
        fresh = paper.get_task(db, task_id)
        assert "trading_incompatible" in (fresh.get("error_detail") or "")

    def test_backtest_series_runs(self, tmp_path):
        """D3 回测放行对照：series 档策略回测不拒且触发（能力全集）。"""
        from core.backtest.engine import BacktestEngine
        from core.features.indicators import compute_all_indicators
        n = 120
        close = np.full(n, 100.0)
        close[40:60] = np.linspace(100, 85, 20)
        close[60:] = 86.0
        df = pd.DataFrame({
            "open": close * 0.999, "high": close * 1.002,
            "low": close * 0.998, "close": close,
            "volume": np.full(n, 100.0),
        }, index=pd.date_range("2024-01-01", periods=n, freq="4h",
                               tz="UTC", name="timestamp"))
        res = BacktestEngine(db_path=tmp_path / "q.db").run(
            _series_dna(entry_value=45, exit_value=90),
            compute_all_indicators(df))
        assert res.total_trades >= 1, "series 档回测放行+触发"

    def test_paper_silent_pct_fallback_is_red(self, tmp_path, api_client):
        """D1-④ 反面锚：series 档挂纸盘静默按 pct 执行的形态即红——
        双层插桩下挂载必 422（受理层断言即承载；此处锁装载层 prepare
        返回 False 非 None——「拒」而非「异常穿透」）。"""
        from core.persistence.db_ext import init_db_ext
        from core.analysis.executor import ensure_research_tables
        from core.persistence.repository import paper
        from core.paper_trading.runtime.executor import PaperTaskExecutor
        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        task_id = paper.create_task(
            db, strategy_id="st_neg", dna_snapshot_json=_series_dna().to_json(),
            symbol="BTCUSDT", timeframe="4h",
            research_status_snapshot="unverified",
            initial_cash=10000.0, leverage=1)
        row = paper.get_task(db, task_id)
        from core.paper_trading.runtime.feed import FeedRegistry
        registry = FeedRegistry(tmp_path, fetcher=lambda **k: pd.DataFrame())
        ex = PaperTaskExecutor(
            db, row, registry=registry, push=lambda *a: None,
            on_concluded=lambda *a: None)
        assert ex.prepare() is False


class TestExitSweepSeriesForm:
    def test_series_entry_empty_stops_legal(self):
        """C3 补：series 档 entry 空 stops+sl_series_source 必携合法；
        非 series 档 entry 空 stops 仍拒+携带来源拒。"""
        from core.analysis.templates.exit_sweep import _validate_grid
        ok = _validate_grid({"modes": [
            {"mode": "series", "stops": [], "take_profits": [0.1],
             "sl_series_source": dict(_BB_SRC)}]})
        assert ok[0]["mode"] == "series"
        # 非 series 空 stops 仍拒
        with pytest.raises(Exception, match="非空列表"):
            _validate_grid({"modes": [
                {"mode": "pct", "stops": [], "take_profits": [0.1]}]})
        # 非 series 携带来源拒
        with pytest.raises(Exception, match="仅 series 档"):
            _validate_grid({"modes": [
                {"mode": "pct", "stops": [0.05], "take_profits": [0.1],
                 "sl_series_source": dict(_BB_SRC)}]})
        # series 无来源拒
        with pytest.raises(Exception, match="sl_series_source"):
            _validate_grid({"modes": [
                {"mode": "series", "stops": [], "take_profits": [0.1]}]})
        # 两形制同在拒
        with pytest.raises(Exception, match="互斥"):
            _validate_grid({"modes": [
                {"mode": "series", "stops": [], "take_profits": [0.1],
                 "sl_series_source": {"indicator": "RSI",
                                      "column": "fx_x"}}]})

    def test_modes_reference_follow_engine(self):
        """modes 引用式自动跟随 ENGINE_SUPPORTED_SL_MODES（零改动
        断言——series 已在合法集）。"""
        from core.analysis.templates.exit_sweep import _validate_grid
        from core.backtest.engine import ENGINE_SUPPORTED_SL_MODES
        assert "series" in ENGINE_SUPPORTED_SL_MODES
        # mode 合法性校验引用式：series 模式名过 ②（无 422）
        ok = _validate_grid({"modes": [
            {"mode": "series", "stops": [], "take_profits": [0.1],
             "sl_series_source": dict(_BB_SRC)}]})
        assert len(ok) == 1
