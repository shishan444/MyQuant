"""手工 DNA 通道值域校验（review-042 CARD-12）。

修复前现象：validate_dna 在 api/ 零调用——POST /api/strategies 与
POST /api/trading/tasks（含 /api/live-trading/tasks）的 DNA 无任何
值域校验：NaN/越域/bool 收窄（True→1.0）经 Pydantic 全过落库，
引擎 sl=ep×(1−1.0)=0 恒不触发（看似设了止损实为无止损）；
exit_fraction=NaN 传播权益曲线致隔离验证判定失真。

锚组（三入口同防线）：
- NaN / 越域 / bool 收窄各一 422 锚（detail 含 errors 清单）
- 合法 DNA 通过锚（防线不误伤）
- live 创建点 trail 早拒锚（装载即拒前移——落库前 422）
- live 创建点值域锚（live 链与 paper 链同防线）
"""

import json

import pytest

pytestmark = [pytest.mark.integration]

from core.strategy.dna import (
    ExecutionGenes,
    LogicGenes,
    RiskGenes,
    SignalGene,
    SignalRole,
    StrategyDNA,
)


def _genes_dna(**risk) -> StrategyDNA:
    """合法结构的最小 DNA（entry+exit 基因齐备），risk 覆写值域参数。"""
    return StrategyDNA(
        signal_genes=[
            SignalGene("RSI", {"period": 14}, SignalRole.ENTRY_TRIGGER,
                       condition={"type": "lt", "threshold": 30}),
            SignalGene("RSI", {"period": 14}, SignalRole.EXIT_TRIGGER,
                       condition={"type": "gt", "threshold": 70}),
        ],
        logic_genes=LogicGenes(),
        execution_genes=ExecutionGenes(),
        risk_genes=RiskGenes(**risk),
    )


def _dna_dict(**risk) -> dict:
    """POST /api/strategies 的 dna 段（to_dict 形制+值域覆写）。"""
    d = _genes_dna().to_dict()
    d["risk_genes"].update(risk)
    return d


def _strategy_body(**risk) -> dict:
    return {"name": "card12", "dna": _dna_dict(**risk),
            "symbol": "BTCUSDT", "timeframe": "4h"}


class TestStrategyCreateValidation:
    def _post_raw(self, api_client, **risk):
        """原始体 POST（NaN 非 JSON 合法字面量——Python json.dumps/
        loads 默认 allow_nan 接受，正是穿透面；TestClient 的 json=
        走严格序列化会先拒）。"""
        body = json.dumps(_strategy_body(**risk))  # allow_nan 默认开
        return api_client.post(
            "/api/strategies", content=body.encode(),
            headers={"Content-Type": "application/json"})

    def test_nan_rejected(self, api_client):
        """NaN 值域锚：stop_loss=NaN 422（Pydantic 放行，validate_dna 拦）。"""
        r = self._post_raw(api_client, stop_loss=float("nan"))
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "DNA_VALIDATION_FAILED"
        assert any("nan" in e.lower() for e in detail["errors"])

    def test_out_of_range_rejected(self, api_client):
        """越域锚：stop_loss=1.0（pct 档上界 0.2）422——引擎
        sl=ep×(1−1.0)=0 恒不触发的「假止损」源头。"""
        r = api_client.post("/api/strategies",
                            json=_strategy_body(stop_loss=1.0))
        assert r.status_code == 422
        assert any("stop loss" in e.lower() for e in r.json()["detail"]["errors"])

    def test_bool_narrowing_rejected(self, api_client):
        """bool 收窄锚：stop_loss=True 经 Pydantic 收窄 1.0——422 而非
        静默落库为越域值。"""
        r = api_client.post("/api/strategies",
                            json=_strategy_body(stop_loss=True))
        assert r.status_code == 422

    def test_valid_dna_passes(self, api_client):
        """合法 DNA 通过锚（防线不误伤正常创建）。"""
        r = api_client.post("/api/strategies", json=_strategy_body())
        assert r.status_code == 201, r.text

    def test_scaling_nan_rejected(self, api_client):
        """分批键 NaN 锚：exit_fraction=NaN 422（reduce_amount=position×
        NaN 传播权益曲线——隔离验证判定失真源头）。"""
        r = self._post_raw(api_client, exit_fraction=float("nan"))
        assert r.status_code == 422
        assert any("exit_fraction" in e for e in r.json()["detail"]["errors"])


class TestTradingTaskValidation:
    def test_nan_rejected(self, api_client):
        """模拟盘任务创建值域锚：dna_json 携带 NaN 值域 422。"""
        dna = _genes_dna(stop_loss=float("nan"))
        r = api_client.post("/api/trading/tasks",
                            json={"dna_json": dna.to_json(),
                                  "symbol": "BTCUSDT", "timeframe": "4h"})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DNA_VALIDATION_FAILED"

    def test_valid_dna_passes(self, api_client):
        dna = _genes_dna()
        r = api_client.post("/api/trading/tasks",
                            json={"dna_json": dna.to_json(),
                                  "symbol": "BTCUSDT", "timeframe": "4h"})
        assert r.status_code == 201, r.text


class TestLiveTaskValidation:
    def test_nan_rejected(self, api_client):
        """live 任务创建值域锚：与 paper 链同防线。"""
        dna = _genes_dna(stop_loss=float("nan"))
        r = api_client.post("/api/live-trading/tasks",
                            json={"symbol": "BTCUSDT", "timeframe": "4h",
                                  "dna_json": dna.to_json()})
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DNA_VALIDATION_FAILED"

    def test_trail_early_rejected(self, api_client):
        """live 创建点 trail 早拒锚：装载即拒前移——trail 在 Runner
        装载点才拒（任务已落库才失败），现创建点落库前 422。"""
        dna = _genes_dna(sl_mode="trail")
        r = api_client.post("/api/live-trading/tasks",
                            json={"symbol": "BTCUSDT", "timeframe": "4h",
                                  "dna_json": dna.to_json()})
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "DNA_TRADING_INCOMPATIBLE"
        assert any("trail" in e.lower() for e in detail["errors"])

    def test_valid_dna_passes(self, api_client):
        dna = _genes_dna()
        r = api_client.post("/api/live-trading/tasks",
                            json={"symbol": "BTCUSDT", "timeframe": "4h",
                                  "dna_json": dna.to_json()})
        assert r.status_code == 201, r.text


class TestBehaviorTightening:
    def test_structurally_incomplete_dna_rejected(self, api_client):
        """行为收紧锚：无 exit 信号的最小裸 DNA 此前可创建，现按
        validate_dna 语义拒（spec 面向完整策略）——422 结构化。"""
        d = _dna_dict()
        d["signal_genes"] = [g for g in d["signal_genes"]
                             if "entry" in g["role"]]
        r = api_client.post("/api/strategies",
                            json={"name": "bare", "dna": d,
                                  "symbol": "BTCUSDT", "timeframe": "4h"})
        assert r.status_code == 422
        assert any("exit" in e.lower()
                   for e in r.json()["detail"]["errors"])
