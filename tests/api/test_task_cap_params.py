"""任务级封顶参数面（A1）——沙盒/隔离验证四入口的值域校验与透传。

D1：leverage 整数 [1,3]／margin_cap (0,0.60]（需求硬约束 3x/60% 进
参数域封顶）；越界 422 附 legal_range（require_task_cap_params 单源
——四入口同款）；边界右闭（恰 1/恰 3/恰 0.60 合法）；非整数
leverage 拒绝；未传参响应无 task_params 键（存量逐位不变的响应面
锚）；透传正例回显 task_params 三键（含 min 链 effective_leverage）。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]

from core.persistence.repository.strategy import save_strategy
from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)

LEGAL_DNA_KW = dict(position_size=1.0, leverage=2, direction="long")


def _legal_dna(**risk_kw) -> StrategyDNA:
    kw = {**LEGAL_DNA_KW, **risk_kw}
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       "rsi_14", {"type": "lt", "threshold": 999}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       "rsi_14", {"type": "gt", "threshold": 999.5}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(timeframe="4h"),
        risk_genes=RiskGenes(**kw),
    )


def _seed_strategy(db_path, sid="st_task_cap_e2e") -> str:
    dna = _legal_dna()
    save_strategy(
        db_path, strategy_id=sid, dna_json=dna.to_json(),
        symbol="BTCUSDT", timeframe="4h", name=sid,
        gene_signature=f"sig-{sid}")
    return sid


def _seed_market(api_client):
    import numpy as np
    import pandas as pd

    data_dir = api_client.app.state.data_dir
    n = 500
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC",
                        name="timestamp")
    rng = np.random.default_rng(5)
    close = 60000 + np.cumsum(rng.normal(0, 100, n))
    pd.DataFrame({
        "open": close, "high": close + 50, "low": close - 50,
        "close": close, "volume": rng.uniform(100, 800, n),
    }, index=idx).to_parquet(data_dir / "BTCUSDT_4h.parquet")
    yield


@pytest.fixture
def seeded(api_client, db_path):
    sid = _seed_strategy(db_path)
    next(_seed_market(api_client))
    return api_client, sid


SANDBOX_BODY = {
    "strategy_id": "st_x", "symbol": "BTCUSDT", "timeframe": "4h",
    "start": "2024-02-05", "end": "2024-03-01",
}


class TestSandboxValueDomain:
    """越界三档＋非整数拒绝：422 + RESEARCH_PARAM_INVALID + legal_range。"""

    @pytest.mark.parametrize(("field", "value", "legal"), [
        ("leverage", 4, "[1,3]"),
        ("margin_cap", 0.8, "(0,0.60]"),
        ("margin_cap", 0, "(0,0.60]"),
        ("leverage", 2.5, "[1,3]"),
        ("leverage", 0, "[1,3]"),
    ], ids=["lev=4", "cap=0.8", "cap=0", "lev=2.5非整", "lev=0"])
    def test_out_of_range_422_with_legal_range(
            self, api_client, field, value, legal):
        body = {**SANDBOX_BODY, field: value}
        resp = api_client.post("/api/research/backtest-sandbox", json=body)
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["field"] == field
        assert detail["detail"]["legal_range"] == legal

    @pytest.mark.parametrize(("field", "value"), [
        ("leverage", 1), ("leverage", 3), ("margin_cap", 0.60),
        ("margin_cap", 0.01),
    ], ids=["lev=1边界", "lev=3边界", "cap=0.60右闭", "cap=0.01近零"])
    def test_boundary_values_pass(self, seeded, field, value):
        """恰 1/恰 3/恰 0.60/近零正值过值域（右闭两端合法）。"""
        client, sid = seeded
        resp = client.post("/api/research/backtest-sandbox",
                           json={**SANDBOX_BODY, "strategy_id": sid,
                                 field: value})
        assert resp.status_code == 200, f"边界值 {field}={value} 应过值域"


class TestSandboxPassthrough:
    """透传正例：响应回显实际生效值（min 链 effective_leverage）。"""

    def test_task_params_echoed_with_min_chain(self, seeded):
        client, sid = seeded
        resp = client.post("/api/research/backtest-sandbox", json={
            **SANDBOX_BODY, "strategy_id": sid,
            "leverage": 3, "margin_cap": 0.45})
        assert resp.status_code == 200
        body = resp.json()
        tp = body["task_params"]
        assert tp["leverage"] == 3
        assert tp["margin_cap"] == 0.45
        # DNA leverage=2＋任务级 3→有效=2（min 链——只收严不放宽）
        assert tp["effective_leverage"] == 2
        ps = body["metrics"]["position_sizing"]
        assert "capped_by_task_cap" in ps, "③层启用披露第八键"
        assert ps["task_cap"]["effective_leverage"] == 2

    def test_unset_params_no_task_params_key(self, seeded):
        """未传两参：响应无 task_params 键、disclosure 无新增两键
        ——存量响应形制逐位不变的响应面锚。"""
        client, sid = seeded
        resp = client.post("/api/research/backtest-sandbox",
                           json={**SANDBOX_BODY, "strategy_id": sid})
        assert resp.status_code == 200
        body = resp.json()
        assert "task_params" not in body
        assert "position_sizing" not in body["metrics"], \
            "未传两参 metrics 无披露键（存量逐位不变）"


class TestHaltDrawdown:
    """熔断参数值域＋三态回显（D3①——沙盒路 API 面）。"""

    @pytest.mark.parametrize("value", [0, 1, 1.5, -0.1],
                             ids=["0", "1", "1.5", "负值"])
    def test_halt_out_of_range_422(self, api_client, value):
        resp = api_client.post("/api/research/backtest-sandbox", json={
            **SANDBOX_BODY, "halt_drawdown_pct": value})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["field"] == "halt_drawdown_pct"
        assert detail["detail"]["legal_range"] == "(0,1)"

    def test_halt_configured_echoes_false_when_not_triggered(self, seeded):
        """配置未触发：响应 halt_info={halted: false}（未触发也写键）。"""
        client, sid = seeded
        resp = client.post("/api/research/backtest-sandbox", json={
            **SANDBOX_BODY, "strategy_id": sid, "halt_drawdown_pct": 0.9})
        assert resp.status_code == 200
        assert resp.json()["halt_info"] == {"halted": False}

    def test_halt_unset_no_key(self, seeded):
        """未配置：响应无 halt_info 键（三态之「未配置不写键」）。"""
        client, sid = seeded
        resp = client.post("/api/research/backtest-sandbox",
                           json={**SANDBOX_BODY, "strategy_id": sid})
        assert resp.status_code == 200
        assert "halt_info" not in resp.json()


class TestIsolatedRoutesSameValidator:
    """隔离验证三入口（staged/random-window/validate-backtest）与沙盒路
    同一校验单源（A3——同坏输入同 422；require_task_cap_params 四路由
    消费点接线全查）。"""

    @pytest.mark.parametrize(("endpoint", "extra"), [
        ("/api/research/staged-validation",
         {"symbol": "BTCUSDT", "timeframe": "4h",
          "start": "2024-02-05", "end": "2024-03-01"}),
        ("/api/research/random-window-test",
         {"symbol": "BTCUSDT", "timeframe": "4h",
          "data_start": "2024-02-05", "data_end": "2024-03-20"}),
    ], ids=["staged", "random-window"])
    def test_isolated_routes_reject_leverage_4(
            self, api_client, endpoint, extra):
        resp = api_client.post(
            endpoint, json={"strategy_id": "st_x", **extra, "leverage": 4})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["field"] == "leverage"

    def test_validate_backtest_reject_leverage_4(self, api_client):
        """隔离回测入口（嵌套 request_range 形制——第四消费点）：
        同坏输入同 422 附 legal_range。"""
        resp = api_client.post("/api/research/validate-backtest", json={
            "hypothesis_id": "hyp_x", "strategy_id": "st_x",
            "request_range": {"symbol": "BTCUSDT", "timeframe": "4h",
                              "start": "2024-02-05", "end": "2024-03-01"},
            "leverage": 4})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "RESEARCH_PARAM_INVALID"
        assert detail["detail"]["field"] == "leverage"
        assert detail["detail"]["legal_range"] == "[1,3]"


class TestSandboxSingleParamPath:
    """单传一参（部分传参形态——D1「未传两参=③层不生效」的边界格）：
    ③层不构成顶（引擎两参都传才启用），但 task_params 回显实传单键
    （无 effective_leverage——无 task_cap 面可提取）。"""

    def test_margin_cap_only_no_third_layer(self, seeded):
        client, sid = seeded
        resp = client.post("/api/research/backtest-sandbox", json={
            **SANDBOX_BODY, "strategy_id": sid, "margin_cap": 0.45})
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_params"] == {"margin_cap": 0.45}
        assert "effective_leverage" not in body["task_params"], \
            "③层未启用（缺 leverage）不产 min 链面"
        assert "position_sizing" not in body["metrics"], \
            "引擎③层未启用无披露面（与全不传同形）"

    def test_leverage_only_no_third_layer(self, seeded):
        client, sid = seeded
        resp = client.post("/api/research/backtest-sandbox", json={
            **SANDBOX_BODY, "strategy_id": sid, "leverage": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_params"] == {"leverage": 3}
        assert "effective_leverage" not in body["task_params"]
        assert "position_sizing" not in body["metrics"]
