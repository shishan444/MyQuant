"""版本 DoD 端到端研究轮（add-research-analysis-tools 0.4.1 任务 7.3，L3）。

四步量化归纳法前三步全程工具化走通一个真实研究轮：
segment_narrative（读区间）→ condition_stats(conditions)（组合频率）
→ counter_profile（反例细分）→ exit_sweep（风险结构设计）
+ R4 余量查询闭环（查询 remaining 段发起隔离验证不被拒）。
经 TestClient 全链路（HTTP 入口+留痕+判定并集）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = [pytest.mark.integration]

from core.analysis.hypothesis_service import ResearchError
from core.analysis.validation_service import (
    query_union_balance, run_isolated_backtest)
from core.persistence.db_ext import init_db_ext
from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes, LogicGenes, RiskGenes, SignalGene, SignalRole,
    StrategyDNA)

SYMBOL = "BTCUSDT"
BASE = {"symbol": SYMBOL, "timeframe": "4h",
        "start": "2023-05-01T00:00:00Z", "end": "2023-08-01T00:00:00Z"}


@pytest.fixture()
def l3_env(tmp_path: Path):
    """三段趋势数据（降-升-降，RSI 常态触发）+ 独立 db。"""
    d = tmp_path / "market"
    d.mkdir()
    rng = np.random.default_rng(23)
    n = 1800
    idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    seg = np.concatenate([
        np.linspace(72000, 58000, 600),
        np.linspace(58000, 75000, 600),
        np.linspace(75000, 66000, 600)])
    close = seg + rng.normal(0, 200, n)
    pd.DataFrame({"open": close, "high": close + 120, "low": close - 120,
                  "close": close, "volume": rng.uniform(100, 900, n),
                  "trades": 40}, index=idx).to_parquet(
        d / f"{SYMBOL}_4h.parquet")
    db = tmp_path / "quant.db"
    init_db_ext(db)
    return d, db


def _wire(client, env):
    client.app.state.db_path = env[1]
    client.app.state.data_dir = env[0]


def _post(client, template, params, rng=None, **extra):
    body = {"template": template, "params": params,
            "data_range": dict(rng or BASE)}
    body.update(extra)
    return client.post("/api/research/analysis", json=body)


class TestResearchRoundDoD:
    def test_full_round_four_templates_then_union_balance(self, api_client,
                                                          l3_env):
        d, db = l3_env
        _wire(api_client, (d, db))

        # ① 读区间：segment_narrative（事件时间线——AI 阅读行情）
        r1 = _post(api_client, "segment_narrative",
                   {"indicators": ["BB", "MACD"]})
        assert r1.status_code == 200, r1.text
        p1 = r1.json()["payload"]
        assert p1["events"], "三段趋势区间应有事件"
        types = {e["type"] for e in p1["events"]}
        assert types & {"band_position", "indicator_cross", "sustained_run"}

        # ② 组合频率：condition_stats(conditions)（两条件 AND）
        conds = [
            {"subject": {"indicator": "RSI", "params": {"period": 14},
                         "field": "rsi"}, "action": "lt", "target": 45},
            {"subject": "close", "action": "gt", "target": 60000.0},
        ]
        r2 = _post(api_client, "condition_stats",
                   {"conditions": conds, "min_samples": 5,
                    "forward_bars": 12})
        assert r2.status_code == 200, r2.text
        p2 = r2.json()["payload"]
        assert p2["hit_count"] > 0
        assert p2["conditions"] == conds

        # ③ 反例细分：counter_profile（触发分组+特征差异）
        r3 = _post(api_client, "counter_profile",
                   {"conditions": conds, "direction": "long",
                    "min_samples": 5, "forward_bars": 12})
        assert r3.status_code == 200, r3.text
        p3 = r3.json()["payload"]
        assert p3["groups"]["conform"]["count"] + \
            p3["groups"]["counter"]["count"] > 0
        assert p3["features"], "特征差异清单非空"

        # ④ 风险结构设计：exit_sweep（退出网格——检查型不挂假设）。
        # 条件走翻译链：基础列 subject（close）不可表达为指标信号源
        # （既有翻译边界），用指标条件
        grid = {"modes": [{"mode": "pct", "stops": [0.01, 0.02, 0.03],
                           "take_profits": [0.02, 0.04, 0.06]}]}
        r4 = _post(api_client, "exit_sweep",
                   {"conditions": [conds[0]], "direction": "long",
                    "grid": grid})
        assert r4.status_code == 200, r4.text
        p4 = r4.json()["payload"]
        assert len(p4["results"]) == 9
        assert p4["verdict"] in ("plateau", "indeterminate", "spike",
                                 "unsupported")

        # ⑤ R4 闭环：登记假设 → 查询余量 → remaining 段隔离验证不被拒
        from core.analysis.hypothesis_service import register_hypothesis
        hyp = register_hypothesis(
            db, conditions={"when_conditions": conds,
                            "then_conditions": [dict(conds[0])]},
            statement="L3 研究轮假设",
            data_ranges=[{"symbol": SYMBOL, "timeframe": "4h",
                          "start": "2023-02-01T00:00:00Z",
                          "end": "2023-03-01T00:00:00Z"}])["hypothesis_id"]
        balance = api_client.get("/api/research/union-balance", params={
            "symbol": SYMBOL, "timeframe": "4h", "hypothesis_id": hyp})
        assert balance.status_code == 200, balance.text
        remaining = balance.json()["remaining"]
        assert remaining, "登记区间外应有剩余"
        seg = next(r for r in remaining
                   if r["start"] < BASE["start"] and r["end"] > BASE["end"])
        save_strategy(db, strategy_id="st_l3",
                      dna_json=StrategyDNA(
                          signal_genes=[SignalGene(
                              "RSI", {"period": 14},
                              SignalRole.ENTRY_TRIGGER, "rsi_14",
                              {"type": "lt", "threshold": 45}),
                              SignalGene(
                              "RSI", {"period": 14},
                              SignalRole.EXIT_TRIGGER, "rsi_14",
                              {"type": "gt", "threshold": 1e9})],
                          logic_genes=LogicGenes(),
                          execution_genes=ExecutionGenes(
                              symbol=SYMBOL, timeframe="4h"),
                          risk_genes=RiskGenes()).to_json(),
                      symbol=SYMBOL, timeframe="4h")
        result = run_isolated_backtest(
            db, d, hypothesis_id=hyp, strategy_id="st_l3",
            request_range={"symbol": SYMBOL, "timeframe": "4h",
                           "start": BASE["start"], "end": BASE["end"]})
        assert result["verdict"] in ("pass", "fail", "inconclusive")
        assert seg["start"] <= BASE["start"]

        # ⑥ 留痕全貌：四模板留痕三型（analysis×2 + analysis×1 + exit_sweep
        # 检查型）；exit_sweep 区间不进判定并集（第⑤步隔离验证可过即证）
        import sqlite3
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT entry_type, ref_id, hypothesis_id FROM research_log "
                "ORDER BY log_id").fetchall()
        entries = {(r[0], r[1]) for r in rows}
        assert ("analysis", "segment_narrative") in entries
        assert ("analysis", "condition_stats") in entries
        assert ("analysis", "counter_profile") in entries
        assert ("exit_sweep", "exit_sweep") in entries
        # exit_sweep 条目不挂假设（查询剩余段隔离验证已被允许执行）
        for et, ref in entries:
            if et == "exit_sweep":
                row = next(r for r in rows if r[0] == et)
                assert row[2] is None

    def test_mcp_capability_doc_lists_new_capabilities(self):
        """MCP 能力自述对拍（7.1）：六项新能力与参数形制在自述中。"""
        from mcpserver.capability import build_capability_doc
        text = build_capability_doc(
            ["union_balance", "query_indicator", "run_analysis"])
        for token in ("批量读数（0.4.1）",
                      "组合条件（0.4.1 起；regime_split 2026-10-14 起）",
                      "区间事件摘要（0.4.1 segment_narrative）",
                      "判定并集余量查询（0.4.1 union_balance）",
                      "反例画像（0.4.1 counter_profile）",
                      "退出参数扫描（0.4.1 exit_sweep）",
                      "本模板不挂假设"):
            assert token in text, f"能力自述缺: {token}"
        assert "- union_balance" in text
