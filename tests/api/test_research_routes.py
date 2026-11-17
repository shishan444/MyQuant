"""研究域 API 测试（add-ai-research-mcp 3.1-3.5）。

覆盖：登记（幂等/非法 subject/symbol 校验）、证据驱动迁移（幂等/终态拒）、
隔离回测（重叠拒含 judged_ranges、漏报被日志拦截、warmup 声明与不足拒、
干净放行门槛判定、重复幂等）、随机窗口同种子复现、分析落日志、档案字段。
"""

import json
import numpy as np
import pandas as pd
import pytest
from pathlib import Path

pytestmark = [pytest.mark.integration]

from core.persistence.repository.strategy import save_strategy
from core.persistence.repository import research as rr
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)

CONDITIONS = {
    "when_conditions": [{"subject": "close", "action": "gt", "target": 0}],
    "then_conditions": [{"subject": "close", "action": "gt", "target": -1}],
}
RANGES = [{"symbol": "BTCUSDT", "timeframe": "4h",
           "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}]


@pytest.fixture()
def research_env(tmp_path: Path):
    """带 500 bar 4h 数据的库与策略。"""
    rng = np.random.default_rng(9)
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
    close = 60000 + np.cumsum(rng.normal(0, 150, n))
    df = pd.DataFrame({
        "open": close, "high": close + 80, "low": close - 80,
        "close": close, "volume": rng.uniform(100, 1000, n), "trades": 100,
    }, index=idx)
    data_dir = tmp_path / "market"
    data_dir.mkdir()
    df.to_parquet(data_dir / "BTCUSDT_4h.parquet")

    db = tmp_path / "quant.db"
    from core.persistence.db_ext import init_db_ext
    init_db_ext(db)
    gene = SignalGene(
        indicator="RSI", params={"period": 14}, role=SignalRole.ENTRY_TRIGGER,
        field_name="rsi_14",
        condition={"type": "lt", "threshold": 35},
    )
    # 卡 R 起装载挂全量 validate_dna（Q-1）：补恒假 exit（gt 1e9 永不触发——与原无 exit 默认路径行为等效）
    dna = StrategyDNA(
        signal_genes=[gene, SignalGene(
            "RSI", {"period": 14}, SignalRole.EXIT_TRIGGER, "rsi_14",
            {"type": "gt", "threshold": 1e9})],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(),
    )
    save_strategy(db, strategy_id="st_test", dna_json=dna.to_json(),
                  symbol="BTCUSDT", timeframe="4h", name="test")
    return {"data_dir": data_dir, "db": db, "n": n}


def _client(api_client_fixture):
    return api_client_fixture


def _register(client, conditions=CONDITIONS, ranges=RANGES, statement="测试假设"):
    return client.post("/api/research/hypotheses", json={
        "strategy_params": {"stop_loss": 0.05}, "conditions": conditions, "statement": statement, "data_ranges": ranges})


class TestAnalysisAndReport:
    def test_analysis_logs_chain(self, api_client, research_env):
        """分析执行后日志含 analysis 记录（含区间）。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        # U3 起路由接入 warmup 前扩：起点须让出 RSI 的 200-bar warmup 段
        #（数据 2024-01-01 起 500 bar；start=2024-02-10 即 bar 240 > 200）
        resp = api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"indicator": "RSI", "params": {"period": 14},
                       "condition": {"subject": {"indicator": "RSI", "params": {"period": 14}}, "action": "lt", "target": 40}},
            "data_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2024-02-10T00:00:00Z", "end": "2024-03-25T00:00:00Z"},
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["template"] == "condition_stats"
        assert body["sample_size"] >= 30
        assert body["confidence"]["method"] == "wilson_interval"
        # 日志落链
        logs = api_client.get("/api/research/log").json()["items"]
        assert any(e["entry_type"] == "analysis" and e.get("data_ranges")
                   for e in logs)


class TestFallbackSafetyIntegration:
    """3.2 回退安全集成态：v2 策略入库后存量读路径不炸。"""

    def test_v2_strategy_readable_by_legacy_paths(self, api_client, research_env):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        dbp = Path(api_client.app.state.db_path)
        # v2 策略入库（基因与夹具策略差异化——save 按 gene_signature
        # 去重且签名只含条件类型不含阈值：period 20 才使签名不同）
        dna = StrategyDNA(
            signal_genes=[SignalGene("RSI", {"period": 20}, SignalRole.ENTRY_TRIGGER,
                                     "rsi_20", {"type": "lt", "threshold": 30})],
            execution_genes=ExecutionGenes(timeframe="4h"),
            signal_sources=[{"type": "rule"}, {"type": "formula", "ref": "fx_any"}],
            composition={"mode": "gate"})
        save_strategy(dbp, strategy_id="st_v2ro", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="v2-readonly")

        # 存量读路径：策略列表（前端路径）不炸且含该策略
        resp = api_client.get("/api/strategies")
        assert resp.status_code == 200, resp.text
        ids = [s.get("strategy_id") for s in resp.json().get("items", resp.json())] \
            if isinstance(resp.json(), (list, dict)) else []
        assert "st_v2ro" in str(resp.json()), "v2 策略须出现在存量列表"

        # 单策略详情（退化解析路径）
        detail = api_client.get("/api/strategies/st_v2ro")
        assert detail.status_code == 200, detail.text


class TestWatchdogRaceViaApi:
    """3.3 守护竞争终局经 API 轮询口：超时标记后迟到回调不覆盖。"""

    def test_late_success_does_not_override_timeout(self, api_client, research_env):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        dbp = Path(api_client.app.state.db_path)
        import time as _t
        import core.analysis.executor as exec_mod

        old = exec_mod._EXECUTOR
        exec_mod._EXECUTOR = None
        ex = exec_mod.init_executor(dbp, default_timeout_s=0.3)
        try:
            def _slow(stop_check):
                # 窗口须覆盖守护首扫（固定 1.0s 间隔）：deadline 0.3 <
                # 首扫 1.0 < 完成 2.0，消除临界竞争
                _t.sleep(2.0)
                return "late-success"
            out = ex.submit("analysis:slow", _slow, estimated_cost_s=10.0)
            # 轮询到终态
            deadline = _t.time() + 10
            while _t.time() < deadline:
                t = rr.get_analysis_task(dbp, out["task_id"])
                if t["status"] in ("succeeded", "failed"):
                    break
                _t.sleep(0.1)
            # 经 repository 观察终态（API 轮询口同源）
            assert t["status"] == "failed" and t["reason"] == "timeout"
            # 等迟到的成功回调跑完
            _t.sleep(2.5)
            t2 = rr.get_analysis_task(dbp, out["task_id"])
            assert t2["status"] == "failed" and t2["reason"] == "timeout", (
                "迟到成功不得覆盖超时终态（先到者胜）")
        finally:
            if exec_mod._EXECUTOR is not None:
                exec_mod._EXECUTOR.shutdown()
            exec_mod._EXECUTOR = old


