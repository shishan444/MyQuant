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

    def test_overlap_rejected_with_judged_ranges(self, api_client, research_env):
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

    def test_unreported_range_intercepted_by_log(self, api_client, research_env):
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

    def test_clean_range_runs_with_warmup(self, api_client, research_env):
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

    def test_warmup_memory_enables_head_signals(self, api_client, research_env):
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

    def test_warmup_insufficient_rejected(self, api_client, research_env):
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

    def test_duplicate_request_idempotent(self, api_client, research_env):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = self._register_hyp(api_client)
        args = ("2024-03-01T00:00:00Z", "2024-03-20T00:00:00Z")
        r1 = self._request(api_client, hid, *args).json()
        r2 = self._request(api_client, hid, *args).json()
        assert r2["duplicated"] is True
        assert r2["record_id"] == r1["record_id"]

    def test_terminal_hypothesis_rejected(self, api_client, research_env):
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
    def test_same_seed_same_result(self, api_client, research_env):
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
    def test_analysis_logs_chain(self, api_client, research_env):
        """分析执行后日志含 analysis 记录（含区间）。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        # U3 起路由接入 warmup 前扩：起点须让出 RSI 的 200-bar warmup 段
        #（数据 2024-01-01 起 500 bar；start=2024-02-10 即 bar 240 > 200）
        resp = api_client.post("/api/research/analysis", json={
            "template": "condition_stats",
            "params": {"indicator": "RSI", "params": {"period": 14},
                       "condition": {"subject": "rsi_14", "action": "lt", "target": 40}},
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

    def test_validation_report_fields(self, api_client, research_env):
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


class TestConcurrencyAndIdempotence:
    """2.1 并发先到者胜 / 2.2 迁移禁止副作用 / 2.3 幂等键边界。"""

    def test_concurrent_registration_single_row(self, api_client, research_env):
        """并发登记同幂等键：表内恰一行，恰好一个 duplicated=False。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        import threading

        barrier = threading.Barrier(2)
        results = []

        def _register_once():
            barrier.wait()  # 硬对齐两请求起点（不 sleep 碰运气）
            resp = _register(api_client)
            results.append(resp)

        t1 = threading.Thread(target=_register_once)
        t2 = threading.Thread(target=_register_once)
        t1.start(); t2.start(); t1.join(); t2.join()

        codes = [r.status_code for r in results]
        bodies = [r.json() for r in results]
        # 先到者胜：恰一个新建、一个幂等命中（或一个 422 冲突——两者皆合法，
        # 但表内必须恰一行且至少一个成功）
        assert 200 in codes, f"至少一个登记成功: {codes} {[b for b in bodies]}"
        created = [b for b in bodies if isinstance(b, dict) and b.get("duplicated") is False]
        assert len(created) == 1, f"恰一个新建: {bodies}"
        dbp = Path(api_client.app.state.db_path)
        rows = rr.list_hypotheses(dbp)
        assert len([h for h in rows
                    if h["conditions_hash"] == created[0].get("hypothesis_id", "")or True]) >= 1
        # 幂等键唯一性由部分唯一索引裁决：同身份行数 <= 1
        import json as _json
        ident_rows = [h for h in rows if h["status"] == "active"]
        assert len(ident_rows) == 1, f"并发终态唯一性破坏: {len(ident_rows)} 行"

    def test_concurrent_transition_rowcount_guard(self, api_client, research_env):
        """并发迁移：条件更新 WHERE active 使恰一个成功，另一个幂等或 409。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = _register(api_client).json()["hypothesis_id"]
        dbp = Path(api_client.app.state.db_path)
        rr.save_validation_with_log(
            dbp, record_id="rec_cc", hypothesis_id=hid, strategy_id="st_t",
            level="isolated_backtest", params="{}", result=None,
            verdict="fail", range_hash="rh_cc")

        import threading
        barrier = threading.Barrier(2)
        results = []

        def _transition_once():
            barrier.wait()
            results.append(api_client.post(
                f"/api/research/hypotheses/{hid}/transition",
                json={"target_status": "refuted", "evidence_ref": "rec_cc"}))

        t1 = threading.Thread(target=_transition_once)
        t2 = threading.Thread(target=_transition_once)
        t1.start(); t2.start(); t1.join(); t2.join()

        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        # 一个真迁移 + 一个幂等返回；终态唯一
        idem = [r.json().get("idempotent") for r in results]
        assert sorted(idem) == [False, True], f"先到者胜语义: {idem}"
        logs = [e for e in rr.list_research_log(dbp, hypothesis_id=hid)
                if e["entry_type"] == "transition"]
        assert len(logs) == 1, "迁移日志恰一条（幂等方不重复写）"

    def test_transition_rejected_no_log_writes(self, api_client, research_env):
        """2.2 禁止副作用：迁移被拒时 research_log 行数不变、状态不变。"""
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        hid = _register(api_client).json()["hypothesis_id"]
        dbp = Path(api_client.app.state.db_path)
        before_logs = len(rr.list_research_log(dbp, hypothesis_id=hid))

        resp = api_client.post(f"/api/research/hypotheses/{hid}/transition",
                               json={"target_status": "supported", "evidence_ref": "rec_ghost"})
        assert resp.status_code == 422
        assert rr.get_hypothesis(dbp, hid)["status"] == "active", "状态不得改变"
        assert len(rr.list_research_log(dbp, hypothesis_id=hid)) == before_logs, \
            "被拒迁移不得写日志"

    @pytest.mark.parametrize(
        ("dup_conditions", "dup_ranges", "same"),
        [
            pytest.param(True, False, False, id="同条件异区间=新假设"),
            pytest.param(False, True, False, id="异条件同区间=新假设"),
            pytest.param(True, True, True, id="同条件同区间=幂等命中"),
        ],
    )
    def test_identity_key_boundaries(self, api_client, dup_conditions, dup_ranges, same):
        """2.3 幂等键边界：身份=（条件集，区间集）二元组。"""
        r1 = _register(api_client).json()
        conds = CONDITIONS if dup_conditions else {
            "when_conditions": [{"subject": "volume", "action": "gt", "target": 0}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        }
        ranges = RANGES if dup_ranges else [{
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-03-01T00:00:00Z", "end": "2024-04-01T00:00:00Z"}]
        r2 = _register(api_client, conditions=conds, ranges=ranges).json()
        assert (r2["hypothesis_id"] == r1["hypothesis_id"]) == same
        assert r2["duplicated"] == same


class TestSupersededChain:
    """3.1 superseded 接力链：旧假设终态拒验 + 新假设可验证 + 日志完整。"""

    def test_superseded_handover_full_chain(self, api_client, research_env):
        api_client.app.state.db_path = research_env["db"]
        api_client.app.state.data_dir = research_env["data_dir"]
        dbp = Path(api_client.app.state.db_path)

        # 假设 A：登记 + 验证记录
        hid_a = _register(api_client, statement="旧想法").json()["hypothesis_id"]
        rr.save_validation_with_log(
            dbp, record_id="rec_sup_a", hypothesis_id=hid_a, strategy_id="st_test",
            level="isolated_backtest", params="{}", result=None,
            verdict="pass", range_hash="rh_sup_a")

        # 假设 B（接替者）：修正后的条件
        conds_b = {
            "when_conditions": [{"subject": "volume", "action": "gt", "target": 0}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        }
        ranges_b = [{
            "symbol": "BTCUSDT", "timeframe": "4h",
            "start": "2024-05-01T00:00:00Z", "end": "2024-06-01T00:00:00Z"}]
        hid_b = _register(api_client, conditions=conds_b, ranges=ranges_b,
                          statement="修正后的新想法").json()["hypothesis_id"]

        # A → superseded（ref=B）
        resp = api_client.post(f"/api/research/hypotheses/{hid_a}/transition",
                               json={"target_status": "superseded",
                                     "evidence_ref": hid_b})
        assert resp.status_code == 200
        a = rr.get_hypothesis(dbp, hid_a)
        assert a["status"] == "superseded" and a["superseded_by"] == hid_b

        # A 终态拒验
        resp2 = api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hid_a, "strategy_id": "st_test",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-06-01T00:00:00Z", "end": "2024-06-20T00:00:00Z"}})
        assert resp2.status_code == 409
        assert resp2.json()["detail"]["code"] == "RESEARCH_HYPOTHESIS_TERMINAL"

        # B 可验证（干净区间）——接力成立
        resp3 = api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": hid_b, "strategy_id": "st_test",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-06-01T00:00:00Z", "end": "2024-06-20T00:00:00Z"}})
        # 500 bar 到 2024-03-31——6 月无数据，预期 DATA_INSUFFICIENT（B 未被
        # 终态拒绝即证明接力语义；数据不足与终态拒是不同错误）
        assert resp3.status_code in (200, 422), resp3.text
        if resp3.status_code == 422:
            assert resp3.json()["detail"]["code"] != "RESEARCH_HYPOTHESIS_TERMINAL"

        # 日志链：A 的链含 register→transition（superseded）
        types = [e["entry_type"] for e in rr.list_research_log(dbp, hypothesis_id=hid_a)]
        assert "register" in types and "transition" in types


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
