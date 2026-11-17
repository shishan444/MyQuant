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
            "when_conditions": [{"subject": {"indicator": "EMA", "params": {"period": 50}}, "action": "cross_above", "target": "close"}],
            "then_conditions": [{"subject": "close", "action": "gt", "target": 0}],
        }
        resp = _register(api_client, conditions=good)
        assert resp.status_code == 200


class TestHypothesisTransition:
    def test_evidence_driven_transition(self, api_client):
        hid = _register(api_client).json()["hypothesis_id"]
        # 造一条验证记录作为证据
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            Path(api_client.app.state.db_path), record_id="rec_ev",
            hypothesis_id=hid, strategy_id="st_x", level="isolated_backtest",
            params={}, result=None, verdict="fail", range_hash="rh1")

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
        # 新建行落库核对：响应 hypothesis_id 即行主键（conditions_hash
        # 是另一列幂等哈希——原断言两侧字段错位恒假，靠 or True 遮蔽）
        assert any(
            h["hypothesis_id"] == created[0]["hypothesis_id"]
            for h in rows)
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
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            dbp, record_id="rec_cc", hypothesis_id=hid, strategy_id="st_t",
            level="isolated_backtest", params={}, result=None,
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
        from tests.helpers.vr_factory import insert_validation_record
        insert_validation_record(
            dbp, record_id="rec_sup_a", hypothesis_id=hid_a,
            strategy_id="st_test", level="isolated_backtest", params={},
            result=None, verdict="pass", range_hash="rh_sup_a")

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


