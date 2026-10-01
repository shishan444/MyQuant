"""TD 现状容忍隔离文件（阶段四 V3 化 D 类锚点）。

收录已裁决「现状容忍、呈批待决策」的行为锚——与规格锚分文件
隔离，决策落地后同步改锚（TD 台账关联项集中于此，避免散落在
能力文件中被误当规格语义）。

TD 项 1（CARD-03 附注，lead 裁决）：组合策略加严门在显式
default 档时不强制收紧——`effective_profile = threshold_profile or
tighten_profile()` 使 plugin_sources 分支仅对 None（缺省）收紧，
显式 {"name": "default"} 的组合策略走 DEFAULT_PROFILE。设计卡 6.5
意图（组合一律加严）与现状有出入，容忍待裁。
"""

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.executor import ensure_research_tables
from core.analysis.hypothesis_service import register_hypothesis
from core.analysis.validation_service import run_isolated_backtest
from core.persistence.db_ext import init_db_ext, save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA,
)


def _df(n: int = 900, seed: int = 6):
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-10-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 110, n))
    return pd.DataFrame({
        "open": close, "high": close + 60, "low": close - 60,
        "close": close, "volume": rng.uniform(100, 800, n), "trades": 40,
    }, index=idx)


import pandas as pd  # noqa: E402


class TestTDCombinationDefaultNotTightened:
    """TD 现状锚：组合策略 + 显式 default 档 → 不强制收紧。

    现状语义：`threshold_profile or tighten_profile()` 中显式
    DEFAULT_PROFILE 为 truthy → effective = DEFAULT（非 tightened）；
    仅缺省 None 时组合分支收紧。若 TD 裁决改为「组合一律加严」，
    本锚同步更新。"""

    @pytest.fixture()
    def env(self, tmp_path: Path):
        d = tmp_path / "market"
        d.mkdir(exist_ok=True)
        _df().to_parquet(d / "BTCUSDT_4h.parquet")
        db = tmp_path / "q.db"
        init_db_ext(db)
        ensure_research_tables(db)
        from core.analysis.plugin_service import register_formula_plugin
        register_formula_plugin(db, name="td_gate",
                                expression="zscore(close, 20)")
        return db, d

    def _run(self, db, d, profile, tag):
        from core.analysis.registry_meta import available_signal_columns
        col = next(c["column_key"] for c in available_signal_columns(db)
                   if c["source"] == "formula")
        # 组合加严门：分量级 pass 证据（每分量独立验证记录）
        from core.persistence.repository import research as rr
        rr.save_validation_with_log(
            db, record_id=f"rec_comp_{tag}", hypothesis_id="adhoc",
            strategy_id=None, level="component",
            params=json.dumps({"column": col}), result=None,
            verdict="pass", range_hash=f"rh_td_{tag}")
        hid = register_hypothesis(
            db,
            conditions={
                "when_conditions": [
                    {"subject": {"indicator": "RSI",
                                 "params": {"period": 14}},
                     "action": "lt", "target": 45}],
                "then_conditions": [
                    {"subject": "close", "action": "gt", "target": 0}],
            },
            statement=f"TD 锚 {tag}",
            data_ranges=[{"symbol": "BTCUSDT", "timeframe": "4h",
                          "start": "2022-10-05T00:00:00Z",
                          "end": "2022-10-20T00:00:00Z"}])["hypothesis_id"]
        dna = StrategyDNA(
            signal_genes=[SignalGene(
                "RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                "rsi_14", {"type": "lt", "threshold": 45})],
            logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes(),
            signal_sources=[{"type": "formula", "ref": col}])
        sid = f"st_td_{tag}"
        save_strategy(db, strategy_id=sid, dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h",
                      gene_signature=f"sig_td_{tag}")
        return run_isolated_backtest(
            db, d, hypothesis_id=hid, strategy_id=sid,
            request_range={"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2023-01-01", "end": "2023-02-01"},
            threshold_profile=profile)

    def test_显式default_组合不收紧_与显式tightened判读可区分(self, env):
        db, d = env
        out_default = self._run(db, d, {"name": "default"}, "d")
        out_tight = self._run(db, d, {"name": "tightened"}, "t")
        # 现状锚：两档各自落档名正确（default / default_tightened_x1.2）
        from core.persistence.repository import research as rr
        recs = rr.list_validation_records(db)
        names = [json.loads(r["params"]).get("threshold_profile")
                 for r in recs]
        assert "default" in names
        assert "default_tightened_x1.2" in names
        # 显式 default 的组合路径未二次收紧（effective=DEFAULT 而非
        # tightened）——落档名即证据（若强制收紧应落 tightened 名）
        assert out_default["record_id"] and out_tight["record_id"]
