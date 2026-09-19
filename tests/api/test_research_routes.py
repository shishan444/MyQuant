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

from core.persistence.db_ext import save_strategy
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
    dna = StrategyDNA(
        signal_genes=[gene], logic_genes=LogicGenes(),
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
        "conditions": conditions, "statement": statement, "data_ranges": ranges})


class TestHypothesisRegistration:
    def test_register_active_and_log(self, api_client):
        resp = _register(api_client)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["duplicated"] is False
        hid = body["hypothesis_id"]

        got = api_client.get(f"/api/research/hypotheses/{hid}").json()
        assert got["status"] == "active"
        logs = api_client.get("/api/research/log", params={"hypothesis_id": hid}).json()
        assert any(e["entry_type"] == "register" for e in logs["items"])

    def test_duplicate_returns_existing(self, api_client):
        r1 = _register(api_client).json()
        r2 = _register(api_client).json()
        assert r2["duplicated"] is True
        assert r2["hypothesis_id"] == r1["hypothesis_id"]

    def test_invalid_subject_rejected(self, api_client):
        bad = {
            "when_conditions": [{"subject": "nope_column", "action": "gt", "target": 1}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        }
        resp = _register(api_client, conditions=bad)
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_CONDITION_INVALID"

    def test_unknown_symbol_rejected(self, api_client):
        resp = _register(api_client, ranges=[{
            "symbol": "DOGUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}])
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_SYMBOL_NOT_FOUND"

    def test_indicator_column_form_accepted(self, api_client):
        """subject 引用指标输出列形制（ema_50）合法。"""
        good = {
            "when_conditions": [{"subject": "ema_50", "action": "cross_above", "target": "close"}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        }
        resp = _register(api_client, conditions=good)
        assert resp.status_code == 200


class TestHypothesisTransition:
    def test_evidence_driven_transition(self, api_client):
        hid = _register(api_client).json()["hypothesis_id"]
        # 造一条验证记录作为证据
        rr.save_validation_with_log(
            Path(api_client.app.state.db_path), record_id="rec_ev",
            hypothesis_id=hid, strategy_id="st_x", level="isolated_backtest",
            params="{}", result=None, verdict="fail", range_hash="rh1")

        resp = api_client.post(f"/api/research/hypotheses/{hid}/transition",
                               json={"target_status": "refuted", "evidence_ref": "rec_ev"})
        assert resp.status_code == 200 and resp.json()["status"] == "refuted"

        # 同目标幂等返回
        resp2 = api_client.post(f"/api/research/hypotheses/{hid}/transition",
                                json={"target_status": "refuted", "evidence_ref": "rec_ev"})
        assert resp2.status_code == 200 and resp2.json()["idempotent"] is True

        # 异目标终态拒
        resp3 = api_client.post(f"/api/research/hypotheses/{hid}/transition",
                                json={"target_status": "supported", "evidence_ref": "rec_ev"})
        assert resp3.status_code == 409
        assert resp3.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_TERMINAL"

    def test_missing_evidence_rejected(self, api_client):
        hid = _register(api_client).json()["hypothesis_id"]
        resp = api_client.post(f"/api/research/hypotheses/{hid}/transition",
                               json={"target_status": "supported", "evidence_ref": "rec_ghost"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_EVIDENCE_NOT_FOUND"


class TestIsolatedBacktest:
    def _register_hyp(self, api_client):
        return _register(api_client).json()["hypothesis_id"]

    def _request(self, api_client, hid, start, end):
        return api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hid, "strategy_id": "st_test",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": start, "end": end}})

    def test_overlap_rejected_with_judged_ranges(self, api_client, research_env, monkeypatch):
        """请求区间与登记区间重叠 → 409 + judged_ranges 明细。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = self._register_hyp(api_client)
        resp = self._request(api_client, hid,
                             "2024-01-15T00:00:00Z", "2024-02-15T00:00:00Z")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_RANGE_OVERLAP"
        assert detail["detail"]["judged_ranges"], "须附判定并集明细"
        assert detail["detail"]["overlaps"], "须附重叠段"

    def test_unreported_range_intercepted_by_log(self, api_client, research_env, monkeypatch):
        """漏报拦截：声明区间 A，日志另有区间 B → 请求 B 被拒。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        # 登记时只声明 1 月
        hid = _register(api_client, ranges=[{
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}
        ]).json()["hypothesis_id"]
        # 但日志显示其曾分析过 2 月（模拟漏报）
        rr.append_research_log(
            research_env["db"], hypothesis_id=hid, entry_type="analysis",
            data_ranges=json.dumps([{"start": "2024-02-01T00:00:00Z",
                                     "end": "2024-03-01T00:00:00Z"}]))
        resp = self._request(api_client, hid,
                             "2024-02-10T00:00:00Z", "2024-03-10T00:00:00Z")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "RESEARCH_RANGE_OVERLAP"

    def test_clean_range_runs_with_warmup(self, api_client, research_env, monkeypatch):
        """干净区间放行：warmup 声明 + 门槛判定 + 双落。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = self._register_hyp(api_client)
        # 数据 500 bar（1/1 起 83 天）。RSI lookback 14 → warmup max(200,42)=200
        # 请求 3/1 之后（前 354 bar 预热可用 > 200）
        resp = self._request(api_client, hid,
                             "2024-03-01T00:00:00Z", "2024-03-20T00:00:00Z")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["duplicated"] is False
        assert body["verdict"] in ("pass", "fail")
        assert body["warmup"]["bars"] >= 200
        assert "max(200" in body["warmup"]["basis"]
        assert "total_return" in body["metrics"]
        # 双落
        dbp = Path(api_client.app.state.db_path)
        logs = rr.list_research_log(dbp, hypothesis_id=hid)
        assert any(e["entry_type"] == "validate" for e in logs)

    def test_warmup_memory_enables_head_signals(self, api_client, research_env, monkeypatch):
        """M1 隔离语义行为级证明：warmup 历史记忆使请求区间头部的有记忆指标正确。

        构造：warmup 段（bar 0-299）价格恒 62000，请求段（bar 300 起）恒 60000；
        策略 EMA(50) > 61500 触发。有 warmup 时请求首 bar 的 ema 携带历史记忆
        （约 62000）→ 头部即触发交易；无 warmup（截断计算）时 ema 从 60000
        起算 → 永不触发。total_trades >= 1 即证明记忆真实进入了请求区间。
        """
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        import numpy as np
        import pandas as pd
        n = 1000
        idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC", name="timestamp")
        close = np.concatenate([
            np.full(700, 62000.0),   # warmup 段（700 bar > 600 推导值）：高位平台
            np.full(300, 60000.0),   # 请求段：低位平台
        ])
        pd.DataFrame({
            "open": close, "high": close + 10, "low": close - 10,
            "close": close, "volume": np.full(n, 100.0), "trades": 10,
        }, index=idx).to_parquet(research_env["data_dir"] / "BTCUSDT_4h.parquet")

        from core.persistence.db_ext import save_strategy
        gene = SignalGene(
            indicator="EMA", params={"period": 50}, role=SignalRole.ENTRY_TRIGGER,
            field_name="ema_50", condition={"type": "gt", "threshold": 61500})
        dna = StrategyDNA(
            signal_genes=[gene], logic_genes=LogicGenes(),
            execution_genes=ExecutionGenes(timeframe="4h"),
            risk_genes=RiskGenes())
        save_strategy(research_env["db"], strategy_id="st_emamem", dna_json=dna.to_json(),
                      symbol="BTCUSDT", timeframe="4h", name="ema-mem")

        hid = _register(api_client, ranges=[{
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-01-05T00:00:00Z"}]).json()["hypothesis_id"]
        resp = api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hid, "strategy_id": "st_emamem",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-04-26T00:00:00Z", "end": "2024-06-05T00:00:00Z"}})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["warmup"]["bars"] >= 200
        # 行为断言：请求区间头部 ema 携带历史记忆（62000 段）→ 触发交易；
        # 若 warmup 前扩失效（截断计算），ema 从 60000 起算永不触发 → 此处红
        assert body["metrics"]["total_trades"] >= 1, (
            f"warmup 记忆未进入请求区间头部: {body['metrics']}")

    def test_warmup_insufficient_rejected(self, api_client, research_env, monkeypatch):
        """请求区间起点太近数据头部 → warmup 不足 422。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        # 登记区间只占数据最头部（不与请求区间重叠）
        hid = _register(api_client, ranges=[{
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-01-01T00:00:00Z", "end": "2024-01-03T00:00:00Z"}
        ]).json()["hypothesis_id"]
        # 请求 01-03 起：不重叠，但其前仅 12 bar << 200 warmup → 422
        resp = self._request(api_client, hid,
                             "2024-01-03T00:00:00Z", "2024-01-10T00:00:00Z")
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "RESEARCH_WARMUP_INSUFFICIENT"

    def test_duplicate_request_idempotent(self, api_client, research_env, monkeypatch):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = self._register_hyp(api_client)
        args = ("2024-03-01T00:00:00Z", "2024-03-20T00:00:00Z")
        r1 = self._request(api_client, hid, *args).json()
        r2 = self._request(api_client, hid, *args).json()
        assert r2["duplicated"] is True
        assert r2["record_id"] == r1["record_id"]

    def test_terminal_hypothesis_rejected(self, api_client, research_env, monkeypatch):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = self._register_hyp(api_client)
        dbp = Path(api_client.app.state.db_path)
        rr.save_validation_with_log(
            dbp, record_id="rec_t", hypothesis_id=hid, strategy_id="st_test",
            level="isolated_backtest", params="{}", result=None,
            verdict="fail", range_hash="rh_t")
        api_client.post(f"/api/research/hypotheses/{hid}/transition",
                        json={"target_status": "refuted", "evidence_ref": "rec_t"})
        resp = self._request(api_client, hid,
                             "2024-03-01T00:00:00Z", "2024-03-20T00:00:00Z")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_TERMINAL"


class TestRandomWindow:
    def test_same_seed_same_result(self, api_client, research_env, monkeypatch):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        payload = {
            "strategy_id": "st_test", "symbol": "BTCUSDT", "timeframe": "4h",
            "data_start": "2024-03-01T00:00:00Z", "data_end": "2024-03-25T00:00:00Z",
            "window_bars": 10, "n_windows": 3, "seed": 7,
        }
        r1 = api_client.post("/api/research/random-window-test", json=payload).json()
        r2 = api_client.post("/api/research/random-window-test",
                             json={**payload, "seed": 7}).json()
        # 二次请求命中幂等键 → duplicated=True，完整结果在 result 内
        assert r2["duplicated"] is True and r2["record_id"] == r1["record_id"]
        w1 = [(w["start"], round(w["metrics"]["total_return"], 10)) for w in r1["windows"]]
        w2 = [(w["start"], round(w["metrics"]["total_return"], 10))
              for w in r2["result"]["windows"]]
        assert w1 == w2, "同种子同参数必须复现"
        assert r1["summary"]["n_windows"] == 3
        assert "worst_window" in r1["summary"]


class TestAnalysisAndReport:
    def test_analysis_logs_chain(self, api_client, research_env, monkeypatch):
        """分析执行后日志含 analysis 记录（含区间）。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        resp = api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"indicator": "RSI", "params": {"period": 14},
                       "condition": {"subject": "rsi_14", "action": "lt", "target": 40}},
            "data_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                           "start": "2024-01-01T00:00:00Z", "end": "2024-02-10T00:00:00Z"},
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

    def test_validation_report_fields(self, api_client, research_env, monkeypatch):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = _register(api_client).json()["hypothesis_id"]
        api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hid, "strategy_id": "st_test",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-03-01T00:00:00Z", "end": "2024-03-20T00:00:00Z"}})
        resp = api_client.get("/api/research/validation/st_test/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_records"] >= 1
        entry = body["isolated_backtests"][0]
        for key in ("record_id", "verdict", "warmup_bars", "人话"):
            assert key in entry
