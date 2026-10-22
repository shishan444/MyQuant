"""layers 传输层强形制红锚（dna-strong-typing-and-disclosure-hygiene 件④
H4-c，design 2.4 / specs strategy-expression「layers 条目传输形制校验」）。

契约出处：DNAModel.layers 类型从 Optional[List[dict]] 改为
Optional[List[TimeframeLayerModel]]（既有模型补引用，extra="forbid"）
——每层须为 dict 且 timeframe 必填字符串、signal_genes 为信号基因
模型列表、logic_genes 为逻辑模型、role 可选字符串，多余键拒绝。
坏形在传输层 422（pydantic ValidationError 既有通道），先于 to_dna()
与持久化——不得透传至领域层以未包装 TypeError 落 500（现状）。

不进传输层的值域（权威单源防双头）：timeframe 值域（TF_SECONDS
白名单）归装载侧 TIMEFRAME_UNKNOWN 族；role 值域归 validator.py
层判据白名单——传输层只保 str 类型形。

存量兼容：TimeframeLayer.to_dict 四键 {timeframe, signal_genes,
logic_genes, role} 与 TimeframeLayerModel 字段一一对应——读库
DNAModel.model_validate 链形制不变，存量零迁移。

红锚语义：拒绝侧（R 组）在强形制落地前为红——现状坏 layers 条目
经 List[dict] 透传后 from_dict 落 500 或怪异透传；护栏侧（G 组）
双态绿（合法四键层读回与创建不受影响）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration]


def _legal_gene() -> dict:
    return {"indicator": "RSI", "params": {"period": 14},
            "role": "entry_trigger", "field": "rsi",
            "condition": {"type": "lt", "threshold": 30}}


def _exit_gene() -> dict:
    return {"indicator": "RSI", "params": {"period": 14},
            "role": "exit_trigger", "field": "rsi",
            "condition": {"type": "gt", "threshold": 70}}


def _legal_layer() -> dict:
    """存量合法四键层（TimeframeLayer.to_dict 形制）——层内 entry+exit
    齐全+role=execution（过 validate_dna 的 MTF 判据：层 entry/exit、
    execution 层在场）。"""
    return {
        "timeframe": "4h",
        "signal_genes": [_legal_gene(), _exit_gene()],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "OR"},
        "role": "execution",
    }


def _dna_dict(layers=None) -> dict:
    return {
        "signal_genes": [
            _legal_gene(),
            {"indicator": "RSI", "params": {"period": 14},
             "role": "exit_trigger", "field": "rsi",
             "condition": {"type": "gt", "threshold": 70}},
        ],
        "logic_genes": {"entry_logic": "AND", "exit_logic": "OR"},
        "execution_genes": {"symbol": "BTCUSDT", "timeframe": "4h"},
        "risk_genes": {"direction": "long", "sl_mode": "pct",
                       "stop_loss": 0.05},
        "layers": layers,
    }


def _strategy_body(layers) -> dict:
    return {"name": "layers-typing", "dna": _dna_dict(layers),
            "symbol": "BTCUSDT", "timeframe": "4h"}


class TestBadLayersRejected422:
    """坏形 layers 条目 → 传输层 422（现状 500——强形制落地前红）。"""

    @pytest.mark.parametrize(
        "layer",
        [
            pytest.param("not-a-dict", id="条目非 dict"),
            pytest.param({"signal_genes": [], "logic_genes": {},
                          "role": None}, id="缺 timeframe"),
            pytest.param({"timeframe": 42, "signal_genes": [],
                          "logic_genes": {}}, id="timeframe 非字符串"),
            pytest.param({"timeframe": "1d", "signal_genes": "oops",
                          "logic_genes": {}}, id="signal_genes 坏形"),
            pytest.param({"timeframe": "1d", "signal_genes": [],
                          "logic_genes": {}, "roll": "structure"},
                         id="多余键拼写错"),
        ],
    )
    def test_bad_layer_entry_422(self, api_client, layer):
        r = api_client.post("/api/strategies",
                            json=_strategy_body([layer]))
        assert r.status_code == 422, (
            f"坏 layers 条目现状返回 {r.status_code}（期望传输层 422 "
            f"而非 500/静默透传）：{r.text[:300]}")

    def test_legal_layers_create_and_readback(self, api_client):
        """护栏：合法四键层创建 201 且读回形制不变（存量零迁移）。"""
        r = api_client.post("/api/strategies",
                            json=_strategy_body([_legal_layer()]))
        assert r.status_code == 201, r.text
        sid = r.json()["strategy_id"]
        got = api_client.get(f"/api/strategies/{sid}").json()
        layers = got["dna"]["layers"]
        assert layers and layers[0]["timeframe"] == "4h"
        assert layers[0]["role"] == "execution"
